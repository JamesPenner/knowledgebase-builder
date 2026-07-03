"""LLM-powered vocabulary suggestion functions — synonym, semantic, thematic, and taxonomy."""
import json
import logging

logger = logging.getLogger(__name__)


def _require_text_model(config) -> bool:
    if not config.text_model:
        logger.debug("text_model not configured; skipping LLM vocab suggestion")
        return False
    return True

_DEFAULT_SYNONYMS_PROMPT = (
    "You are a vocabulary management assistant. Suggest synonyms for the given term. "
    "Return ONLY valid JSON: {\"synonyms\": [\"term1\", \"term2\"]}. "
    "Exclude any terms already in the provided vocabulary list. "
    "If no synonyms exist, return {\"synonyms\": []}."
)

_DEFAULT_SEMANTIC_PROMPT = (
    "You are a vocabulary management assistant. Identify groups of interchangeable terms. "
    "Return ONLY valid JSON: "
    "{\"groups\": [{\"canonical\": \"preferred_term\", \"terms\": [\"variant1\"]}]}. "
    "If no groupings exist, return {\"groups\": []}."
)

_DEFAULT_THEMATIC_PROMPT = (
    "You are a vocabulary management assistant. Identify thematic groupings. "
    "Return ONLY valid JSON: "
    "{\"groups\": [{\"canonical\": \"UmbrellaCategory\", \"terms\": [\"specific1\"]}]}. "
    "If no groupings exist, return {\"groups\": []}."
)

_DEFAULT_TAXONOMY_PROMPT = (
    "You are a vocabulary management assistant. Propose a 2–3 level topic hierarchy. "
    "Return ONLY valid JSON: "
    "[{\"name\": \"TopLevel\", \"children\": [{\"name\": \"MidLevel\", \"children\": [{\"name\": \"LeafTerm\"}]}]}]. "
    "Only use terms from the vocabulary list as leaf nodes. Return [] if the vocabulary is too small to group."
)


def suggest_synonyms(kb_conn, config, term: str) -> int:
    """Suggest synonyms for a single vocabulary term via LLM. Returns count of proposals added."""
    if not _require_text_model(config):
        return 0
    from src.db.kb import add_vocab_proposal, get_vocabulary_terms, load_stage_prompt
    from src.llm.session import TextSession

    all_terms = [r["term"] for r in get_vocabulary_terms(kb_conn)]
    system = load_stage_prompt(kb_conn, "vocab_suggest", "synonyms", default=_DEFAULT_SYNONYMS_PROMPT)
    vocab_list = ", ".join(sorted(all_terms)) if all_terms else "(none)"
    user = f"Term: {term}\n\nExisting vocabulary (exclude these from suggestions): {vocab_list}"

    try:
        with TextSession(config.text_model, n_gpu_layers=config.text_gpu_layers) as session:
            raw = session.generate(system, user, max_tokens=512, temperature=0.1)
    except Exception as exc:
        logger.error("LLM failed for vocab synonym suggest: %s", exc)
        return 0

    try:
        data = json.loads(raw)
        synonyms = [
            s.strip() for s in data.get("synonyms", [])
            if isinstance(s, str) and s.strip()
        ]
    except (ValueError, KeyError, AttributeError):
        logger.warning("Failed to parse LLM synonym response for term '%s'", term)
        return 0

    if not synonyms:
        return 0

    source_detail = f"canonical:{term} | synonyms for '{term}'"
    result = add_vocab_proposal(kb_conn, [term] + synonyms, "llm_synonym", source_detail)
    return 1 if result else 0


def suggest_semantic_groupings(kb_conn, config) -> int:
    """Suggest semantic groupings across the whole vocabulary. Returns count of proposals added."""
    if not _require_text_model(config):
        return 0
    from src.db.kb import add_vocab_proposal, get_vocabulary_terms, load_stage_prompt
    from src.llm.session import TextSession

    all_terms = [r["term"] for r in get_vocabulary_terms(kb_conn)]
    if len(all_terms) < 2:
        return 0

    system = load_stage_prompt(kb_conn, "vocab_suggest", "semantic", default=_DEFAULT_SEMANTIC_PROMPT)
    user = "Vocabulary terms:\n" + "\n".join(f"- {t}" for t in sorted(all_terms))

    try:
        with TextSession(config.text_model, n_gpu_layers=config.text_gpu_layers) as session:
            raw = session.generate(system, user, max_tokens=1024, temperature=0.1)
    except Exception as exc:
        logger.error("LLM failed for vocab semantic suggest: %s", exc)
        return 0

    try:
        data = json.loads(raw)
        groups = data.get("groups", [])
    except (ValueError, KeyError, AttributeError):
        logger.warning("Failed to parse LLM semantic grouping response")
        return 0

    added = 0
    for g in groups:
        canonical = (g.get("canonical") or "").strip()
        variants = [
            v.strip() for v in g.get("terms", [])
            if isinstance(v, str) and v.strip()
        ]
        if not canonical or not variants:
            continue
        source_detail = f"canonical:{canonical} | semantic grouping"
        if add_vocab_proposal(kb_conn, [canonical] + variants, "llm_semantic", source_detail):
            added += 1
    return added


def suggest_thematic_groupings(kb_conn, config) -> int:
    """Suggest thematic rollup groupings across the whole vocabulary. Returns count added."""
    if not _require_text_model(config):
        return 0
    from src.db.kb import add_vocab_proposal, get_vocabulary_terms, load_stage_prompt
    from src.llm.session import TextSession

    all_terms = [r["term"] for r in get_vocabulary_terms(kb_conn)]
    if len(all_terms) < 2:
        return 0

    system = load_stage_prompt(kb_conn, "vocab_suggest", "thematic", default=_DEFAULT_THEMATIC_PROMPT)
    user = "Vocabulary terms:\n" + "\n".join(f"- {t}" for t in sorted(all_terms))

    try:
        with TextSession(config.text_model, n_gpu_layers=config.text_gpu_layers) as session:
            raw = session.generate(system, user, max_tokens=1024, temperature=0.1)
    except Exception as exc:
        logger.error("LLM failed for vocab thematic suggest: %s", exc)
        return 0

    try:
        data = json.loads(raw)
        groups = data.get("groups", [])
    except (ValueError, KeyError, AttributeError):
        logger.warning("Failed to parse LLM thematic grouping response")
        return 0

    added = 0
    for g in groups:
        canonical = (g.get("canonical") or "").strip()
        variants = [
            v.strip() for v in g.get("terms", [])
            if isinstance(v, str) and v.strip()
        ]
        if not canonical or not variants:
            continue
        source_detail = f"canonical:{canonical} | thematic grouping"
        if add_vocab_proposal(kb_conn, [canonical] + variants, "llm_thematic", source_detail):
            added += 1
    return added


def _paths_to_tree(paths: list[str]) -> dict:
    """Convert a list of '::'-separated path strings into a nested dict/list tree.

    2-part paths  ("Wildlife::Bear")      → {"Wildlife": ["Bear", ...]}
    3-part paths  ("Nature::Wildlife::Bear") → {"Nature": {"Wildlife": ["Bear", ...]}}
    """
    tree: dict = {}
    for path in paths:
        parts = path.split("::")
        if len(parts) == 2:
            top, leaf = parts
            tree.setdefault(top, []).append(leaf)
        elif len(parts) == 3:
            top, mid, leaf = parts
            if top not in tree:
                tree[top] = {}
            tree[top].setdefault(mid, []).append(leaf)
    return tree


def suggest_taxonomy(kb_conn, config) -> int:
    """Propose a vocabulary hierarchy via LLM and store in taxonomy_proposals. Returns 1 on success."""
    if not _require_text_model(config):
        return 0
    from src.db.kb import get_vocabulary_terms, load_stage_prompt, save_taxonomy_proposal
    from src.llm.session import TextSession

    all_terms = [r["term"] for r in get_vocabulary_terms(kb_conn)]
    if len(all_terms) < 2:
        return 0

    system = load_stage_prompt(kb_conn, "vocab_suggest", "taxonomy", default=_DEFAULT_TAXONOMY_PROMPT)
    user = "Vocabulary terms:\n" + "\n".join(f"- {t}" for t in sorted(all_terms))

    try:
        with TextSession(config.text_model, n_gpu_layers=config.text_gpu_layers) as session:
            raw = session.generate(system, user, max_tokens=1536, temperature=0.1)
    except Exception as exc:
        logger.error("LLM failed for taxonomy suggest: %s", exc)
        return 0

    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("expected list")
    except (ValueError, TypeError):
        logger.warning("Failed to parse LLM taxonomy response")
        return 0

    save_taxonomy_proposal(kb_conn, json.dumps(data))
    return 1
