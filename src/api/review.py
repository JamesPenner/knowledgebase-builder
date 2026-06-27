import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.deps import resolve_kb

router = APIRouter()


class DecideRequest(BaseModel):
    kb: str
    item_id: int
    action: str
    value: dict | None = None


class BulkDecideRequest(BaseModel):
    kb: str
    action: str  # accept_all | ignore_all | reject_all


@router.get("/normalise/pending", tags=["review"])
def get_normalise_pending(
    limit: int = 50,
    offset: int = 0,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict[str, Any]:
    corpus_path, _ = paths
    from src.db.corpus import get_analyse_token_counts, get_pending_analyse_tokens, open_corpus

    conn = open_corpus(corpus_path)
    tokens = get_pending_analyse_tokens(conn, limit, offset)
    counts = get_analyse_token_counts(conn)
    conn.close()

    items = [dict(t) for t in tokens]
    return {"items": items, "total": counts["total"], "reviewed": counts["reviewed"]}


@router.post("/normalise/decide", tags=["review"])
def post_normalise_decide(
    req: DecideRequest,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict[str, str]:
    from src.db.corpus import open_corpus, set_token_decided
    from src.db.kb import (
        add_capture_rule,
        add_correction,
        add_reject_token,
        add_to_stoplist,
        bump_kb_version,
        open_kb,
    )

    corpus_path, kb_path = paths
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    action = req.action
    value = req.value or {}

    if action == "accept":
        pass  # no KB rule — token is kept as-is by normalize

    elif action == "capture":
        add_capture_rule(
            kb_conn,
            pattern=value.get("pattern", ""),
            label=value.get("label", ""),
            extract_as=value.get("extract_as", ""),
            format_str=value.get("format_str", ""),
            value_type=value.get("value_type", ""),
            keep_token=bool(value.get("keep_token", False)),
        )
        bump_kb_version(kb_conn, "capture_rule_added")

    elif action == "ignore":
        token_row = corpus_conn.execute(
            "SELECT token FROM analyse_tokens WHERE id=?", (req.item_id,)
        ).fetchone()
        if token_row:
            add_to_stoplist(kb_conn, token_row["token"])
            bump_kb_version(kb_conn, "stoplist_updated")

    elif action == "correct":
        token_row = corpus_conn.execute(
            "SELECT token FROM analyse_tokens WHERE id=?", (req.item_id,)
        ).fetchone()
        if token_row:
            add_correction(
                kb_conn,
                raw_term=token_row["token"],
                canonical_term=value.get("canonical_term", ""),
                correction_kind=value.get("correction_kind", "typo"),
            )
            bump_kb_version(kb_conn, "correction_added")

    elif action == "reject":
        token_row = corpus_conn.execute(
            "SELECT token FROM analyse_tokens WHERE id=?", (req.item_id,)
        ).fetchone()
        if token_row:
            add_reject_token(kb_conn, pattern=token_row["token"], is_regex=False, label=token_row["token"])
            bump_kb_version(kb_conn, "reject_token_added")

    elif action != "accept":
        corpus_conn.close()
        kb_conn.close()
        raise HTTPException(400, f"Unknown action: {action!r}")

    set_token_decided(corpus_conn, req.item_id)
    corpus_conn.close()
    kb_conn.close()
    return {"status": "ok"}


@router.post("/normalise/bulk", tags=["review"])
def post_normalise_bulk(
    req: BulkDecideRequest,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict[str, Any]:
    from src.db.corpus import (
        get_all_pending_tokens,
        open_corpus,
        set_all_pending_decided,
    )
    from src.db.kb import add_reject_token, add_to_stoplist, bump_kb_version, open_kb

    corpus_path, kb_path = paths
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    if req.action == "accept_all":
        count = set_all_pending_decided(corpus_conn)

    elif req.action == "ignore_all":
        rows = get_all_pending_tokens(corpus_conn)
        for row in rows:
            add_to_stoplist(kb_conn, row["token"])
        if rows:
            bump_kb_version(kb_conn, "stoplist_updated")
        count = set_all_pending_decided(corpus_conn)

    elif req.action == "reject_all":
        rows = get_all_pending_tokens(corpus_conn)
        for row in rows:
            add_reject_token(kb_conn, pattern=row["token"], is_regex=False, label=row["token"])
        if rows:
            bump_kb_version(kb_conn, "reject_token_added")
        count = set_all_pending_decided(corpus_conn)

    else:
        corpus_conn.close()
        kb_conn.close()
        raise HTTPException(400, f"Unknown bulk action: {req.action!r}")

    corpus_conn.close()
    kb_conn.close()
    return {"status": "ok", "count": count}


@router.get("/normalise/decisions", tags=["review"])
def get_normalise_decisions(
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict[str, Any]:
    _, kb_path = paths
    from src.db.kb import get_decisions, open_kb

    kb_conn = open_kb(kb_path)
    decisions = get_decisions(kb_conn)
    kb_conn.close()
    return {"decisions": decisions}


@router.delete("/normalise/decisions/{decision_id}", tags=["review"])
def delete_normalise_decision(
    decision_id: str,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    corpus_path, kb_path = paths

    # decision_id is "table:rowid" e.g. "capture_rules:5"
    try:
        table, row_id_str = decision_id.rsplit(":", 1)
        row_id = int(row_id_str)
    except ValueError:
        raise HTTPException(400, f"Invalid decision id: {decision_id!r}")

    from src.db.corpus import open_corpus, set_token_pending
    from src.db.kb import delete_decision, get_decisions, open_kb

    kb_conn = open_kb(kb_path)
    try:
        delete_decision(kb_conn, table, row_id)
    except ValueError as exc:
        kb_conn.close()
        raise HTTPException(400, str(exc)) from exc
    kb_conn.close()

    # Revert the associated analyse_token to pending.
    # We identify the token by looking at the record before deletion;
    # since deletion already happened, we use a heuristic: find any decided
    # token matching the pattern for the table.
    # Simpler: set all decided tokens back to pending and re-sync from kb decisions.
    # For now, re-open and set based on which tokens no longer have a decision.
    corpus_conn = open_corpus(corpus_path)
    kb_conn2 = open_kb(kb_path)
    remaining_decisions = get_decisions(kb_conn2)
    kb_conn2.close()

    decided_tokens = {d["token"] for d in remaining_decisions}

    # Find decided tokens in corpus that are no longer in any decision
    decided_rows = corpus_conn.execute(
        "SELECT id, token FROM analyse_tokens WHERE status='decided'"
    ).fetchall()
    for row in decided_rows:
        if row["token"] not in decided_tokens:
            set_token_pending(corpus_conn, row["id"])

    corpus_conn.close()
    return {}


# ---------------------------------------------------------------------------
# Suggest review
# ---------------------------------------------------------------------------

class SuggestDecideRequest(BaseModel):
    kb: str
    candidate_id: int
    action: str
    value: dict | None = None


@router.get("/suggest/pending", tags=["review"])
def get_suggest_pending(
    limit: int = 50,
    offset: int = 0,
    source: str | None = None,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    corpus_path, _ = paths
    from src.db.corpus import get_candidate_counts, get_pending_candidates, open_corpus

    conn = open_corpus(corpus_path)
    candidates = get_pending_candidates(conn, limit=limit, offset=offset, source_filter=source)
    counts = get_candidate_counts(conn)
    conn.close()
    return {"items": [dict(c) for c in candidates], "counts": counts}


@router.post("/suggest/decide", tags=["review"])
def post_suggest_decide(
    req: SuggestDecideRequest,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    from src.db.corpus import open_corpus, set_candidate_status
    from src.db.kb import add_to_stoplist, add_vocabulary_term, bump_kb_version, open_kb

    corpus_path, kb_path = paths
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    row = corpus_conn.execute(
        "SELECT term FROM candidates WHERE id=?", (req.candidate_id,)
    ).fetchone()
    if not row:
        corpus_conn.close()
        kb_conn.close()
        raise HTTPException(404, "Candidate not found")

    term = row["term"]
    action = req.action
    value = req.value or {}

    if action == "accept":
        add_vocabulary_term(kb_conn, term)
        bump_kb_version(kb_conn, "vocabulary_term_added")
        set_candidate_status(corpus_conn, req.candidate_id, "accepted")

    elif action == "ignore":
        add_to_stoplist(kb_conn, term, source="domain")
        set_candidate_status(corpus_conn, req.candidate_id, "rejected")

    elif action == "correct":
        corrected_to = value.get("corrected_to", "")
        if corrected_to:
            add_vocabulary_term(kb_conn, corrected_to)
            bump_kb_version(kb_conn, "vocabulary_term_added")
        set_candidate_status(corpus_conn, req.candidate_id, "corrected", corrected_to=corrected_to)

    elif action == "reject":
        set_candidate_status(corpus_conn, req.candidate_id, "rejected")

    else:
        corpus_conn.close()
        kb_conn.close()
        raise HTTPException(400, f"Unknown action: {action!r}")

    corpus_conn.commit()
    kb_conn.commit()
    corpus_conn.close()
    kb_conn.close()
    return {"status": "ok"}


@router.get("/suggest/decisions", tags=["review"])
def get_suggest_decisions(
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    _, kb_path = paths
    from src.db.kb import get_vocabulary_terms, open_kb

    kb_conn = open_kb(kb_path)
    terms = get_vocabulary_terms(kb_conn)
    kb_conn.close()
    return {"terms": [dict(t) for t in terms]}


@router.delete("/suggest/decisions/{term}", tags=["review"])
def delete_suggest_decision(
    term: str,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    from src.db.corpus import open_corpus, set_candidate_status
    from src.db.kb import delete_vocabulary_term, open_kb

    corpus_path, kb_path = paths
    kb_conn = open_kb(kb_path)
    delete_vocabulary_term(kb_conn, term)
    kb_conn.commit()
    kb_conn.close()

    corpus_conn = open_corpus(corpus_path)
    accepted_rows = corpus_conn.execute(
        "SELECT id FROM candidates WHERE term=? AND status='accepted'", (term,)
    ).fetchall()
    for row in accepted_rows:
        set_candidate_status(corpus_conn, row["id"], "pending")
    corpus_conn.commit()
    corpus_conn.close()
    return {}


# ---------------------------------------------------------------------------
# New Terms review (third human touchpoint)
# ---------------------------------------------------------------------------

class NewTermsDecideRequest(BaseModel):
    kb: str
    term: str
    action: str
    value: dict | None = None


@router.get("/new-terms/pending", tags=["review"])
def get_new_terms_pending(
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    corpus_path, kb_path = paths
    from src.db.corpus import get_new_terms_candidates, open_corpus
    from src.db.kb import open_kb

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    items = get_new_terms_candidates(corpus_conn, kb_conn)
    corpus_conn.close()
    kb_conn.close()
    return {"items": items, "total": len(items)}


@router.post("/new-terms/decide", tags=["review"])
def post_new_terms_decide(
    req: NewTermsDecideRequest,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    from src.db.corpus import merge_new_term_into_tags, open_corpus
    from src.db.kb import (
        add_correction,
        add_reject_token,
        add_to_stoplist,
        add_vocabulary_term,
        bump_kb_version,
        open_kb,
    )

    corpus_path, kb_path = paths
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    term = req.term
    action = req.action
    value = req.value or {}

    if action == "accept":
        add_vocabulary_term(kb_conn, term, source="new_terms")
        bump_kb_version(kb_conn, "vocabulary_term_added")
        kb_conn.commit()
        merge_new_term_into_tags(corpus_conn, term)

    elif action == "ignore":
        add_to_stoplist(kb_conn, term, source="domain")
        kb_conn.commit()

    elif action == "reject":
        add_reject_token(kb_conn, pattern=term, is_regex=False, label=term)
        kb_conn.commit()

    elif action == "correct":
        correct_to = value.get("correct_to", "")
        if correct_to:
            add_correction(kb_conn, raw_term=term, canonical_term=correct_to)
            add_vocabulary_term(kb_conn, correct_to)
            bump_kb_version(kb_conn, "vocabulary_term_added")
        kb_conn.commit()

    else:
        corpus_conn.close()
        kb_conn.close()
        raise HTTPException(400, f"Unknown action: {action!r}")

    corpus_conn.close()
    kb_conn.close()
    return {"status": "ok"}


@router.get("/new-terms/decisions", tags=["review"])
def get_new_terms_decisions(
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    _, kb_path = paths
    from src.db.kb import get_vocabulary_terms, open_kb

    kb_conn = open_kb(kb_path)
    terms = [
        dict(r) for r in get_vocabulary_terms(kb_conn)
        if r["source"] == "new_terms"
    ]
    kb_conn.close()
    return {"decisions": terms}


@router.delete("/new-terms/decisions/{term}", tags=["review"])
def delete_new_terms_decision(
    term: str,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    from src.db.kb import delete_vocabulary_term, open_kb

    _, kb_path = paths
    kb_conn = open_kb(kb_path)
    delete_vocabulary_term(kb_conn, term)
    kb_conn.commit()
    kb_conn.close()
    return {}


# ---------------------------------------------------------------------------
# Speaker cluster review
# ---------------------------------------------------------------------------

class SpeakerDecideRequest(BaseModel):
    cluster_id: int
    action: str
    person_id: int | None = None
    new_name: str | None = None


@router.get("/speakers/clip", tags=["review"])
def get_speaker_clip(
    file_id: int,
    start_ms: int,
    end_ms: int,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> StreamingResponse:
    """Slice and stream an audio clip via ffmpeg."""
    from src.config import load_config
    from src.db.corpus import open_corpus

    corpus_path, _ = paths
    corpus_conn = open_corpus(corpus_path)
    row = corpus_conn.execute("SELECT path FROM files WHERE id = ?", (file_id,)).fetchone()
    corpus_conn.close()
    if row is None:
        raise HTTPException(404, "file not found")

    config = load_config(Path("config.yaml") if Path("config.yaml").exists() else None)
    start_s = start_ms / 1000.0
    duration_s = (end_ms - start_ms) / 1000.0

    result = subprocess.run(
        [config.ffmpeg, "-v", "quiet",
         "-i", row["path"],
         "-ss", str(start_s), "-t", str(duration_s),
         "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
         "-f", "wav", "pipe:1"],
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise HTTPException(500, "ffmpeg extraction failed")

    return StreamingResponse(
        iter([result.stdout]),
        media_type="audio/wav",
        headers={"Content-Disposition": "inline"},
    )


@router.get("/speakers/pending", tags=["review"])
def get_speakers_pending(
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    from src.db.corpus import get_pending_speaker_clusters, open_corpus
    from src.db.kb import get_all_people, open_kb

    corpus_path, kb_path = paths
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    items = [dict(r) for r in get_pending_speaker_clusters(corpus_conn)]
    people = [dict(r) for r in get_all_people(kb_conn)]
    corpus_conn.close()
    kb_conn.close()
    return {"items": items, "total": len(items), "people": people}


@router.get("/speakers/decisions", tags=["review"])
def get_speakers_decisions(
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    from src.db.corpus import get_assigned_speaker_clusters, open_corpus
    from src.db.kb import get_all_people, open_kb

    corpus_path, kb_path = paths
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    decisions = [dict(r) for r in get_assigned_speaker_clusters(corpus_conn)]
    people_map = {r["id"]: r["preferred_name"] for r in get_all_people(kb_conn)}
    corpus_conn.close()
    kb_conn.close()
    return {"decisions": decisions, "people": people_map}


@router.post("/speakers/decide", tags=["review"])
def post_speakers_decide(
    req: SpeakerDecideRequest,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    from src.db.corpus import (
        assign_speaker_cluster,
        get_voice_speaker_clusters,
        open_corpus,
    )
    from src.db.kb import merge_voice_centroid, open_kb, upsert_person

    corpus_path, kb_path = paths
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    if req.action != "assign":
        corpus_conn.close()
        kb_conn.close()
        raise HTTPException(400, f"Unknown action: {req.action!r}")

    person_id = req.person_id
    label: str
    if person_id is not None:
        row = kb_conn.execute(
            "SELECT preferred_name FROM people WHERE id = ?", (person_id,)
        ).fetchone()
        if row is None:
            corpus_conn.close()
            kb_conn.close()
            raise HTTPException(404, "person not found")
        label = row["preferred_name"]
    elif req.new_name:
        person_id = upsert_person(kb_conn, req.new_name)
        label = req.new_name
    else:
        corpus_conn.close()
        kb_conn.close()
        raise HTTPException(400, "person_id or new_name required")

    clusters = {r["id"]: r for r in get_voice_speaker_clusters(corpus_conn)}
    cluster = clusters.get(req.cluster_id)
    if cluster is None:
        corpus_conn.close()
        kb_conn.close()
        raise HTTPException(404, "cluster not found")

    merge_voice_centroid(kb_conn, person_id, bytes(cluster["centroid"]), cluster["member_count"])
    kb_conn.commit()

    assign_speaker_cluster(corpus_conn, req.cluster_id, person_id, label)
    corpus_conn.commit()
    corpus_conn.close()
    kb_conn.close()
    return {"status": "ok"}


@router.delete("/speakers/decisions/{cluster_id}", tags=["review"])
def delete_speaker_decision(
    cluster_id: int,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict:
    from src.db.corpus import open_corpus, unassign_speaker_cluster

    corpus_path, _ = paths
    corpus_conn = open_corpus(corpus_path)
    unassign_speaker_cluster(corpus_conn, cluster_id)
    corpus_conn.commit()
    corpus_conn.close()
    return {}
