"""Stage 4 — Suggest: Level A (spaCy linguistic) + Level B (NPMI co-occurrence graph) + Level C (LLM cluster labelling)."""
import json
import logging
import math
import threading
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from src.config import Config
from src.pipeline.progress import ProgressReporter

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_C = """\
You are a vocabulary curator for a domain-specific media archive. Given a thematic cluster \
of related terms from a co-occurrence analysis, propose new canonical vocabulary terms \
that would be good additions to the knowledge base.

Respond with valid JSON only — no markdown, no explanation:
{"terms": ["term1", "term2"], "reasoning": "1-2 sentences on what this cluster represents"}

Rules:
- terms: 3-8 new terms NOT already in the vocabulary; precise, domain-appropriate labels
- Do not include terms already listed in EXISTING VOCABULARY
- reasoning: explain the cluster theme and why these terms were chosen\
"""


def _compute_npmi(term_counts: dict, pair_counts: dict, doc_count: int) -> dict:
    """Return {(a, b): npmi} for all pairs; values in [-1, 1]."""
    npmi_scores: dict = {}
    for (a, b), c_ab in pair_counts.items():
        p_ab = c_ab / doc_count
        p_a = term_counts[a] / doc_count
        p_b = term_counts[b] / doc_count
        if p_ab <= 0 or p_a <= 0 or p_b <= 0:
            continue
        pmi = math.log(p_ab / (p_a * p_b))
        normaliser = -math.log(p_ab)
        if normaliser == 0:
            npmi_scores[(a, b)] = 1.0
            continue
        npmi_scores[(a, b)] = pmi / normaliser
    return npmi_scores


def _run_level_a(
    corpus_conn,
    kb_conn,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> None:
    import spacy
    from src.db.corpus import (
        get_enrichment_text_for_file,
        upsert_candidate,
    )
    from src.db.kb import get_stoplist_terms, get_vocabulary_terms

    nlp = spacy.load("en_core_web_sm", disable=["parser", "ner"])

    exclusion: set[str] = {r["term"] for r in get_vocabulary_terms(kb_conn)}
    exclusion |= get_stoplist_terms(kb_conn)

    file_rows = corpus_conn.execute("SELECT id FROM files ORDER BY id").fetchall()
    total = len(file_rows)
    term_file_ids: dict[str, set[int]] = defaultdict(set)

    for i, row in enumerate(file_rows):
        if cancel_event.is_set():
            return
        progress.update(i, total, f"Level A: processing file {i + 1}/{total}")

        file_id = row["id"]
        text_parts = [get_enrichment_text_for_file(corpus_conn, file_id)]

        desc = corpus_conn.execute(
            "SELECT description_normalized, description_raw FROM descriptions WHERE file_id=?", (file_id,)
        ).fetchone()
        if desc:
            text_parts.append(desc["description_normalized"] or desc["description_raw"] or "")

        tag_rows = corpus_conn.execute(
            "SELECT tag FROM file_derived_tags WHERE file_id=?", (file_id,)
        ).fetchall()
        if tag_rows:
            text_parts.append(" ".join(r["tag"] for r in tag_rows))

        text = " ".join(p for p in text_parts if p)
        if not text.strip():
            continue

        doc = nlp(text)
        for token in doc:
            if token.pos_ in ("NOUN", "PROPN") and not token.is_stop and len(token.lemma_) > 2:
                lemma = token.lemma_.lower()
                if lemma not in exclusion:
                    term_file_ids[lemma].add(file_id)

        for chunk in doc.noun_chunks:
            phrase = chunk.lemma_.lower().strip()
            if len(phrase) > 3 and phrase not in exclusion:
                term_file_ids[phrase].add(file_id)

    min_files = config.suggest_min_files
    progress.update(total, total, "Level A: writing candidates")

    batch = 0
    for term, file_ids in term_file_ids.items():
        if len(file_ids) < min_files:
            continue
        for fid in file_ids:
            upsert_candidate(corpus_conn, fid, term, "level_a")
            batch += 1
            if batch % 500 == 0:
                corpus_conn.commit()
    corpus_conn.commit()


def _run_level_b(
    corpus_conn,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> None:
    import networkx as nx
    import community as community_louvain
    from src.db.corpus import iter_file_term_sets, upsert_candidate

    file_count_row = corpus_conn.execute(
        "SELECT COUNT(DISTINCT file_id) as n FROM candidates WHERE source='level_a' AND status='pending'"
    ).fetchone()
    total_docs = file_count_row["n"] if file_count_row else 0

    term_counts: dict[str, int] = defaultdict(int)
    pair_counts: dict[tuple, int] = defaultdict(int)
    doc_count = 0

    progress.update(0, total_docs, "Level B: counting co-occurrences")
    for term_set in iter_file_term_sets(corpus_conn):
        if cancel_event.is_set():
            return
        doc_count += 1
        for t in term_set:
            term_counts[t] += 1
        for pair in combinations(sorted(term_set), 2):
            pair_counts[pair] += 1
        progress.update(doc_count, total_docs, f"Level B: counting co-occurrences {doc_count}/{total_docs}")

    if doc_count == 0:
        return

    npmi_scores = _compute_npmi(term_counts, pair_counts, doc_count)
    min_weight = config.npmi_min_weight

    n_terms = len(term_counts)
    progress.update(0, n_terms, f"Level B: building NPMI graph ({n_terms} terms)")

    G = nx.Graph()
    for (a, b), score in npmi_scores.items():
        if score >= min_weight:
            G.add_edge(a, b, weight=score)

    if G.number_of_nodes() == 0:
        return

    partition = community_louvain.best_partition(G)

    for term, community_id in partition.items():
        upsert_candidate(corpus_conn, None, term, "level_b", cluster_id=str(community_id))

    corpus_conn.commit()


def _build_level_c_prompt(
    cluster_terms: list[str],
    file_texts: list[str],
    vocab_terms: list[str],
    focus: str,
) -> str:
    parts = []
    if focus:
        parts.append(f"DOMAIN FOCUS: {focus}")
    parts.append(f"EXISTING VOCABULARY (do not propose these):\n{', '.join(vocab_terms) if vocab_terms else '(none)'}")
    parts.append(f"CLUSTER TERMS:\n{', '.join(cluster_terms)}")
    if file_texts:
        sample = "\n---\n".join(file_texts[:5])
        parts.append(f"SAMPLE FILE TEXTS:\n---\n{sample}\n---")
    parts.append("JSON RESPONSE:")
    return "\n\n".join(parts)


def _parse_level_c_response(raw: str) -> tuple[list[str], str]:
    try:
        data = json.loads(raw)
        terms = [str(t) for t in (data.get("terms") or [])]
        reasoning = str(data.get("reasoning") or "")
        return terms, reasoning
    except (json.JSONDecodeError, AttributeError):
        return [], ""


def _run_level_c(
    corpus_conn,
    kb_conn,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
) -> None:
    if not config.text_model:
        logger.warning("Level C: no text_model configured — skipped")
        return

    from src.db.corpus import delete_pending_candidates, upsert_candidate
    from src.db.kb import get_vocabulary_terms

    cluster_rows = corpus_conn.execute(
        "SELECT DISTINCT cluster_id FROM candidates WHERE source='level_b' AND status='pending'"
    ).fetchall()
    if not cluster_rows:
        return

    delete_pending_candidates(corpus_conn, "level_c")
    corpus_conn.commit()

    vocab_terms = [r["term"] for r in get_vocabulary_terms(kb_conn)]
    vocab_set = set(vocab_terms)

    try:
        import llama_cpp as _llama
    except ImportError:
        logger.error("Level C: llama_cpp not installed — skipped")
        return

    try:
        llm = _llama.Llama(
            model_path=config.text_model,
            n_gpu_layers=config.text_gpu_layers,
            verbose=False,
        )
    except Exception as exc:
        logger.error("Level C: failed to load text model %s: %s", config.text_model, exc)
        return

    total = len(cluster_rows)
    from src.db.corpus import get_enrichment_text_for_file

    for i, cluster_row in enumerate(cluster_rows):
        if cancel_event.is_set():
            return
        cluster_id = cluster_row["cluster_id"]
        progress.update(i, total, f"Level C: cluster {i + 1}/{total}")

        cluster_terms = [
            r["term"]
            for r in corpus_conn.execute(
                "SELECT term FROM candidates WHERE source='level_b' AND cluster_id=? AND status='pending'",
                (cluster_id,),
            ).fetchall()
        ]
        if not cluster_terms:
            continue

        placeholders = ",".join("?" * len(cluster_terms))
        file_id_rows = corpus_conn.execute(
            f"SELECT DISTINCT file_id FROM candidates"
            f" WHERE source='level_a' AND term IN ({placeholders}) LIMIT 5",
            cluster_terms,
        ).fetchall()

        file_texts: list[str] = []
        for frow in file_id_rows:
            fid = frow["file_id"]
            parts = [get_enrichment_text_for_file(corpus_conn, fid)]
            desc = corpus_conn.execute(
                "SELECT description_normalized, description_raw FROM descriptions WHERE file_id=?",
                (fid,),
            ).fetchone()
            if desc:
                parts.append(desc["description_normalized"] or desc["description_raw"] or "")
            text = " ".join(p for p in parts if p).strip()
            if text:
                file_texts.append(text)

        prompt = _build_level_c_prompt(cluster_terms, file_texts, vocab_terms, config.focus)
        full_prompt = f"<s>[INST] <<SYS>>\n{_SYSTEM_PROMPT_C}\n<</SYS>>\n\n{prompt} [/INST]"

        try:
            output = llm(full_prompt, max_tokens=512, temperature=0.2, stop=["</s>"])
            raw = output["choices"][0]["text"].strip()
            terms, reasoning = _parse_level_c_response(raw)
            for term in terms:
                if term and term not in vocab_set:
                    upsert_candidate(corpus_conn, None, term, "level_c", cluster_id=cluster_id, notes=reasoning)
            corpus_conn.commit()
        except Exception as exc:
            logger.warning("Level C: cluster %s failed: %s", cluster_id, exc)


def run_suggest(
    corpus_path: Path,
    kb_path: Path,
    config: Config,
    progress: ProgressReporter,
    cancel_event: threading.Event,
    levels=None,
) -> None:
    from src.db.corpus import open_corpus, update_pipeline_checkpoint
    from src.db.kb import open_kb

    if levels is None:
        levels = ("a", "b")
    levels = set(levels)

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    try:
        if "a" in levels:
            _run_level_a(corpus_conn, kb_conn, config, progress, cancel_event)

        if "b" in levels and not cancel_event.is_set():
            _run_level_b(corpus_conn, config, progress, cancel_event)

        if "c" in levels and not cancel_event.is_set():
            _run_level_c(corpus_conn, kb_conn, config, progress, cancel_event)

        if not cancel_event.is_set():
            files_processed = corpus_conn.execute(
                "SELECT COUNT(DISTINCT file_id) FROM candidates WHERE source='level_a'"
            ).fetchone()[0]
            update_pipeline_checkpoint(corpus_conn, "suggest", files_processed, 0, 0)
            corpus_conn.commit()
            progress.done()
    finally:
        corpus_conn.close()
        kb_conn.close()
