from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from src.api.deps import resolve_kb
from src.pipeline.dag import DEPENDENCIES, STAGE_DESCRIPTIONS, STAGE_GROUPS, TOUCHPOINT_BEFORE

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_HX_TRIGGER_BOTH = '{"pendingChanged": null, "decisionsChanged": null}'
_HX_TRIGGER_DECISIONS = '{"decisionsChanged": null}'


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@router.get("/", include_in_schema=False)
def index():
    from src.db.registry import list_kbs, open_registry
    reg = open_registry(Path("."))
    kbs = list_kbs(reg)
    reg.close()
    active = next((k for k in kbs if k["is_active"]), None) or (kbs[0] if kbs else None)
    if active:
        return RedirectResponse(f"/pipeline?kb={active['name']}")
    return RedirectResponse("/pipeline")


@router.get("/pipeline", include_in_schema=False)
def pipeline_page(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import (
        get_analyse_token_counts,
        get_candidate_counts,
        get_describe_counts,
        get_file_sets,
        get_new_terms_candidates,
        get_pipeline_checkpoints,
        get_sources,
        get_transcribe_counts,
        open_corpus,
    )
    from src.db.kb import open_kb

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    try:
        checkpoint_rows = get_pipeline_checkpoints(corpus_conn)
        checkpoints: dict[str, dict] = {r["stage"]: dict(r) for r in checkpoint_rows}
        stage_counts = {
            "describe": get_describe_counts(corpus_conn),
            "transcribe": get_transcribe_counts(corpus_conn),
        }
        token_counts = get_analyse_token_counts(corpus_conn)
        candidate_counts = get_candidate_counts(corpus_conn)
        new_terms = get_new_terms_candidates(corpus_conn, kb_conn)
        sources = [{"id": r["id"], "path": r["path"], "file_count": r["file_count_ingested"] or 0} for r in get_sources(corpus_conn)]
        sets = [{"id": r["id"], "name": r["name"], "file_count": r["file_count"]} for r in get_file_sets(corpus_conn)]
    finally:
        corpus_conn.close()
        kb_conn.close()

    # Touchpoint completion
    _norm_pending = token_counts["total"] - token_counts["reviewed"]
    touchpoints = {
        "normalise_review": {
            "completed": token_counts["total"] > 0 and token_counts["reviewed"] == token_counts["total"],
            "url": f"/review/normalise?kb={kb_name}",
            "manage_url": f"/knowledge/capture-rules?kb={kb_name}",
            "label": "Normalise Review",
            "description": "Approve or reject candidate vocabulary terms before continuing",
            "pending": _norm_pending,
            "total": token_counts["total"],
        },
        "suggest_review": {
            "completed": candidate_counts["total"] > 0 and candidate_counts["pending"] == 0,
            "url": f"/review/suggest?kb={kb_name}",
            "label": "Suggest Review",
            "description": "Review proposed vocabulary terms before retagging",
            "pending": candidate_counts["pending"],
            "total": candidate_counts["total"],
        },
        "new_terms_review": {
            "completed": len(new_terms) == 0 and bool(checkpoints.get("retag")),
            "url": f"/review/new-terms?kb={kb_name}",
            "label": "New Terms Review",
            "description": "Accept or reject terms proposed by the retag stage",
            "pending": len(new_terms),
            "total": len(new_terms),
        },
    }

    # Per-stage dependency state
    def _stage_state(stage: str) -> str:
        if stage in checkpoints:
            return "done"
        deps = DEPENDENCIES.get(stage, [])
        if all(d in checkpoints for d in deps):
            return "ready"
        return "blocked"

    def _blocking_dep(stage: str) -> str | None:
        return next((d for d in DEPENDENCIES.get(stage, []) if d not in checkpoints), None)

    # Enrich groups with per-stage info
    groups = []
    for grp in STAGE_GROUPS:
        stages = []
        for name in grp["stages"]:
            cp = checkpoints.get(name)
            sc = stage_counts.get(name)
            stages.append({
                "name": name,
                "description": STAGE_DESCRIPTIONS.get(name, ""),
                "deps": DEPENDENCIES.get(name, []),
                "state": _stage_state(name),
                "blocking_dep": _blocking_dep(name),
                "checkpoint": cp,
                "stage_counts": sc,
                "touchpoint_before": TOUCHPOINT_BEFORE.get(name),
                "manage_url": f"/knowledge/capture-rules?kb={kb_name}" if name == "normalize" else None,
            })
        groups.append({**grp, "stages": stages})

    return templates.TemplateResponse(request, "pipeline.html", {
        "kb": kb_name,
        "groups": groups,
        "touchpoints": touchpoints,
        "checkpoints_json": list(checkpoints.keys()),
        "stage_descriptions": STAGE_DESCRIPTIONS,
        "sources": sources,
        "sets": sets,
        # keep legacy keys so any other code that uses them doesn't break
        "checkpoints": checkpoints,
        "all_stages": list(DEPENDENCIES.keys()),
        "stage_counts": stage_counts,
    })


@router.get("/review/normalise", include_in_schema=False)
def normalise_review_page(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import (
        get_analyse_token_counts,
        get_grouped_analyse_tokens,
        open_corpus,
    )
    from src.db.kb import get_decisions, open_kb
    conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    groups = get_grouped_analyse_tokens(conn)
    counts = get_analyse_token_counts(conn)
    decisions = get_decisions(kb_conn)
    conn.close()
    kb_conn.close()
    return templates.TemplateResponse(request, "normalise_review.html", {
        "kb": kb_name,
        "groups": groups,
        "counts": counts,
        "decisions": decisions,
    })


@router.get("/kb/new", include_in_schema=False)
def kb_new_page(request: Request):
    return templates.TemplateResponse(request, "kb_new.html", {"kb": ""})


@router.post("/kb/new", include_in_schema=False)
async def kb_new_submit(request: Request):
    from fastapi.responses import RedirectResponse
    form = await request.form()
    name = (form.get("name") or "").strip()
    template = form.get("template") or "general-media"
    source_path = (form.get("source_path") or "").strip()
    source_file_type = form.get("source_file_type") or "all"
    source_recursive = form.get("source_recursive") == "true"
    source_glob = (form.get("glob_pattern") or "").strip()
    source_limit_str = (form.get("count_limit") or "").strip()

    import re
    if not name or not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return templates.TemplateResponse(request, "kb_new.html", {
            "kb": "",
            "error": "Name must be alphanumeric with hyphens/underscores only",
        }, status_code=422)

    from pathlib import Path as P
    from src.cli.kb import (
        _load_general_media_seed,
        _populate_reference_files,
        _write_library_yaml,
        _write_metrics_yaml,
    )
    from src.db.corpus import open_corpus as _open_corpus_new
    from src.db.kb import open_kb as _open_kb_new
    from src.db.registry import list_kbs, open_registry, register_kb, set_active

    kb_root = P("knowledge-bases")
    kb_root.mkdir(exist_ok=True)
    kb_folder = kb_root / name
    if kb_folder.exists():
        return templates.TemplateResponse(request, "kb_new.html", {
            "kb": "",
            "error": f"KB already exists: {name}",
        }, status_code=422)

    kb_folder.mkdir()
    (kb_folder / "reference").mkdir()
    (kb_folder / "reference" / "registers").mkdir()
    (kb_folder / "seed").mkdir()
    _open_corpus_new(kb_folder / "corpus.db").close()
    _open_kb_new(kb_folder / "knowledge.db").close()
    _write_library_yaml(kb_folder / "library.yaml")
    _write_metrics_yaml(kb_folder / "metrics.yaml")
    _populate_reference_files(kb_folder)

    if template == "general-media":
        _load_general_media_seed(kb_folder / "knowledge.db")

    reg = open_registry(P("."))
    try:
        register_kb(reg, name, kb_folder.resolve())
    except ValueError as exc:
        return templates.TemplateResponse(request, "kb_new.html", {
            "kb": "",
            "error": str(exc),
        }, status_code=422)

    existing = list_kbs(reg)
    if len(existing) == 1:
        set_active(reg, name)
    reg.close()

    if source_path:
        from src.db.corpus import add_source, open_corpus
        filters: dict = {}
        if source_glob:
            filters["glob"] = source_glob
        if source_limit_str:
            try:
                filters["count_limit"] = int(source_limit_str)
            except ValueError:
                pass
        conn = open_corpus(kb_folder / "corpus.db")
        add_source(conn, source_path, source_file_type, source_recursive, filters)
        conn.close()

    return RedirectResponse(f"/pipeline?kb={name}", status_code=303)


@router.get("/corpus-stats", include_in_schema=False)
def corpus_stats_page(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import get_corpus_stats, open_corpus
    from src.db.kb import open_kb
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    try:
        stats = get_corpus_stats(corpus_conn, kb_conn)
    finally:
        corpus_conn.close()
        kb_conn.close()
    return templates.TemplateResponse(request, "corpus_stats.html", {
        "kb": kb_name,
        "stats": stats,
    })


@router.get("/knowledge/locations", include_in_schema=False)
def knowledge_locations_page(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, _ = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import get_gps_clusters, open_corpus
    conn = open_corpus(corpus_path)
    clusters = [dict(c) for c in get_gps_clusters(conn)]
    conn.close()
    return templates.TemplateResponse(request, "locations.html", {
        "kb": kb_name,
        "clusters": clusters,
    })


# ---------------------------------------------------------------------------
# Partial routes (HTMX swap targets)
# ---------------------------------------------------------------------------

@router.get("/review/normalise/partials/pending", include_in_schema=False)
def pending_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, _ = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import (
        get_analyse_token_counts,
        get_grouped_analyse_tokens,
        open_corpus,
    )
    conn = open_corpus(corpus_path)
    groups = get_grouped_analyse_tokens(conn)
    counts = get_analyse_token_counts(conn)
    conn.close()
    return templates.TemplateResponse(request, "partials/pending_groups.html", {
        "kb": kb_name,
        "groups": groups,
        "counts": counts,
    })


@router.get("/review/normalise/partials/decisions", include_in_schema=False)
def decisions_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    _, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.kb import get_decisions, open_kb
    kb_conn = open_kb(kb_path)
    decisions = get_decisions(kb_conn)
    kb_conn.close()
    return templates.TemplateResponse(request, "partials/decisions_panel.html", {
        "kb": kb_name,
        "decisions": decisions,
    })


@router.get("/knowledge/locations/registry", include_in_schema=False)
def knowledge_registry_page(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    _, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    return templates.TemplateResponse(request, "location_registry.html", {"kb": kb_name})


@router.get("/knowledge/locations/registry/partials/entry-list", include_in_schema=False)
def registry_entry_list_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    _, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.kb import (
        find_location_near_duplicates,
        get_entity_location_tables,
        get_entity_table_entries,
        open_kb,
    )
    kb_conn = open_kb(kb_path)
    raw_tables = get_entity_location_tables(kb_conn)
    tables = []
    for t in raw_tables:
        entries = [dict(e) for e in get_entity_table_entries(kb_conn, t["name"])]
        near_dups = find_location_near_duplicates(entries)
        dup_ids = set()
        dup_partners: dict[int, list[dict]] = {}
        for d in near_dups:
            dup_ids.add(d["a_id"])
            dup_ids.add(d["b_id"])
            dup_partners.setdefault(d["a_id"], []).append({"id": d["b_id"], "score": d["score"]})
            dup_partners.setdefault(d["b_id"], []).append({"id": d["a_id"], "score": d["score"]})
        tables.append({
            "name": t["name"],
            "match_type": t["match_type"],
            "entries": entries,
            "dup_ids": dup_ids,
            "dup_partners": dup_partners,
        })
    kb_conn.close()
    return templates.TemplateResponse(request, "partials/registry_entry_list.html", {
        "kb": kb_name,
        "tables": tables,
    })


@router.get("/knowledge/locations/registry/partials/edit-form", include_in_schema=False)
def registry_edit_form_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    _, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    table = request.query_params.get("table", "")
    entry_id_str = request.query_params.get("id", "")
    merge_with_str = request.query_params.get("merge_with", "")
    from src.db.kb import get_entity_table_entries, open_kb
    kb_conn = open_kb(kb_path)
    try:
        entry_id = int(entry_id_str) if entry_id_str else None
        merge_with_id = int(merge_with_str) if merge_with_str else None
        entry = None
        merge_target = None
        if entry_id is not None:
            rows = get_entity_table_entries(kb_conn, table)
            for r in rows:
                if r["id"] == entry_id:
                    entry = dict(r)
                if merge_with_id is not None and r["id"] == merge_with_id:
                    merge_target = dict(r)
    except (ValueError, TypeError):
        entry = None
        merge_target = None
    kb_conn.close()
    return templates.TemplateResponse(request, "partials/registry_edit_form.html", {
        "kb": kb_name,
        "table": table,
        "entry": entry,
        "merge_target": merge_target,
    })


@router.get("/knowledge/locations/partials/cluster-list", include_in_schema=False)
def cluster_list_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import get_gps_clusters, open_corpus
    from src.db.kb import get_entity_table_keys, open_kb
    corpus_conn = open_corpus(corpus_path)
    clusters = [dict(c) for c in get_gps_clusters(corpus_conn)]
    corpus_conn.close()
    kb_conn = open_kb(kb_path)
    try:
        promoted_labels = set(get_entity_table_keys(kb_conn, "gps_cluster_locations", "location"))
    except Exception:
        promoted_labels = set()
    kb_conn.close()
    for c in clusters:
        c["promoted"] = c["label"] in promoted_labels
    return templates.TemplateResponse(request, "partials/cluster_list.html", {
        "kb": kb_name,
        "clusters": clusters,
    })


# ---------------------------------------------------------------------------
# Form action handlers (HTMX form posts)
# ---------------------------------------------------------------------------

@router.post("/review/normalise/decide", include_in_schema=False)
async def ui_normalise_decide(
    item_id: int = Form(...),
    action: str = Form(...),
    extract_as: str = Form(""),
    pattern: str = Form(""),
    value_type: str = Form(""),
    keep_token: str = Form("false"),
    label: str = Form(""),
    format_str: str = Form(""),
    canonical_term: str = Form(""),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
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

    if action == "accept":
        pass  # no KB rule — token is kept as-is by normalize
    elif action == "capture":
        add_capture_rule(
            kb_conn,
            pattern=pattern,
            label=label or extract_as,
            extract_as=extract_as,
            format_str=format_str,
            value_type=value_type,
            keep_token=keep_token.lower() == "true",
        )
        bump_kb_version(kb_conn, "capture_rule_added")
    elif action == "ignore":
        token_row = corpus_conn.execute(
            "SELECT token FROM analyse_tokens WHERE id=?", (item_id,)
        ).fetchone()
        if token_row:
            add_to_stoplist(kb_conn, token_row["token"])
            bump_kb_version(kb_conn, "stoplist_updated")
    elif action == "correct":
        token_row = corpus_conn.execute(
            "SELECT token FROM analyse_tokens WHERE id=?", (item_id,)
        ).fetchone()
        if token_row:
            add_correction(
                kb_conn,
                raw_term=token_row["token"],
                canonical_term=canonical_term,
                correction_kind="typo",
            )
            bump_kb_version(kb_conn, "correction_added")
    elif action == "reject":
        token_row = corpus_conn.execute(
            "SELECT token FROM analyse_tokens WHERE id=?", (item_id,)
        ).fetchone()
        if token_row:
            add_reject_token(kb_conn, pattern=token_row["token"], is_regex=False, label=token_row["token"])
            bump_kb_version(kb_conn, "reject_token_added")

    set_token_decided(corpus_conn, item_id)
    corpus_conn.close()
    kb_conn.close()
    return Response(content="", headers={"HX-Trigger": _HX_TRIGGER_BOTH})


@router.post("/review/normalise/bulk", include_in_schema=False)
async def ui_normalise_bulk(
    action: str = Form(...),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.corpus import (
        get_all_pending_tokens,
        open_corpus,
        set_all_pending_decided,
    )
    from src.db.kb import add_reject_token, add_to_stoplist, bump_kb_version, open_kb

    corpus_path, kb_path = paths
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    if action == "accept_all":
        set_all_pending_decided(corpus_conn)

    elif action == "ignore_all":
        rows = get_all_pending_tokens(corpus_conn)
        for row in rows:
            add_to_stoplist(kb_conn, row["token"])
        if rows:
            bump_kb_version(kb_conn, "stoplist_updated")
        set_all_pending_decided(corpus_conn)

    elif action == "reject_all":
        rows = get_all_pending_tokens(corpus_conn)
        for row in rows:
            add_reject_token(kb_conn, pattern=row["token"], is_regex=False, label=row["token"])
        if rows:
            bump_kb_version(kb_conn, "reject_token_added")
        set_all_pending_decided(corpus_conn)

    corpus_conn.close()
    kb_conn.close()
    return Response(content="", headers={"HX-Trigger": _HX_TRIGGER_BOTH})


@router.get("/review/suggest", include_in_schema=False)
def suggest_review_page(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import get_candidate_counts, get_pending_candidates, has_level_b_clusters, open_corpus
    from src.db.kb import get_vocabulary_terms, open_kb

    conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    candidates = get_pending_candidates(conn, limit=100, offset=0)
    counts = get_candidate_counts(conn)
    terms = get_vocabulary_terms(kb_conn)
    level_b_exists = has_level_b_clusters(conn)
    conn.close()
    kb_conn.close()
    return templates.TemplateResponse(request, "suggest_review.html", {
        "kb": kb_name,
        "candidates": [dict(c) for c in candidates],
        "counts": counts,
        "vocabulary": [dict(t) for t in terms],
        "has_level_b_clusters": level_b_exists,
    })


@router.get("/review/suggest/partials/queue", include_in_schema=False)
def suggest_queue_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, _ = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import get_candidate_counts, get_pending_candidates, open_corpus

    conn = open_corpus(corpus_path)
    candidates = get_pending_candidates(conn, limit=100, offset=0)
    counts = get_candidate_counts(conn)
    conn.close()
    return templates.TemplateResponse(request, "partials/candidates_queue.html", {
        "kb": kb_name,
        "candidates": [dict(c) for c in candidates],
        "counts": counts,
    })


@router.get("/review/suggest/partials/vocabulary", include_in_schema=False)
def suggest_vocabulary_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    _, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.kb import get_vocabulary_terms, open_kb

    kb_conn = open_kb(kb_path)
    terms = get_vocabulary_terms(kb_conn)
    kb_conn.close()
    return templates.TemplateResponse(request, "partials/vocabulary_panel.html", {
        "kb": kb_name,
        "vocabulary": [dict(t) for t in terms],
    })


@router.post("/review/suggest/decide", include_in_schema=False)
async def ui_suggest_decide(
    candidate_id: int = Form(...),
    action: str = Form(...),
    corrected_to: str = Form(""),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.corpus import open_corpus, set_candidate_status
    from src.db.kb import add_to_stoplist, add_vocabulary_term, bump_kb_version, open_kb

    corpus_path, kb_path = paths
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    row = corpus_conn.execute(
        "SELECT term FROM candidates WHERE id=?", (candidate_id,)
    ).fetchone()

    if row:
        term = row["term"]
        if action == "accept":
            add_vocabulary_term(kb_conn, term)
            bump_kb_version(kb_conn, "vocabulary_term_added")
            set_candidate_status(corpus_conn, candidate_id, "accepted")
        elif action == "ignore":
            add_to_stoplist(kb_conn, term, source="domain")
            set_candidate_status(corpus_conn, candidate_id, "rejected")
        elif action == "correct" and corrected_to:
            add_vocabulary_term(kb_conn, corrected_to)
            bump_kb_version(kb_conn, "vocabulary_term_added")
            set_candidate_status(corpus_conn, candidate_id, "corrected", corrected_to=corrected_to)
        elif action == "reject":
            set_candidate_status(corpus_conn, candidate_id, "rejected")

    corpus_conn.commit()
    kb_conn.commit()
    corpus_conn.close()
    kb_conn.close()
    return Response(content="", headers={"HX-Trigger": _HX_TRIGGER_BOTH})


@router.delete("/review/suggest/decisions/{term}", include_in_schema=False)
def ui_delete_suggest_decision(
    term: str,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.corpus import open_corpus, set_candidate_status
    from src.db.kb import delete_vocabulary_term, open_kb

    corpus_path, kb_path = paths
    kb_conn = open_kb(kb_path)
    delete_vocabulary_term(kb_conn, term)
    kb_conn.commit()
    kb_conn.close()

    corpus_conn = open_corpus(corpus_path)
    for row in corpus_conn.execute(
        "SELECT id FROM candidates WHERE term=? AND status='accepted'", (term,)
    ).fetchall():
        set_candidate_status(corpus_conn, row["id"], "pending")
    corpus_conn.commit()
    corpus_conn.close()
    return Response(content="", headers={"HX-Trigger": _HX_TRIGGER_BOTH})


@router.get("/review/new-terms", include_in_schema=False)
def new_terms_review_page(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import get_new_terms_candidates, open_corpus
    from src.db.kb import get_vocabulary_terms, open_kb

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    items = get_new_terms_candidates(corpus_conn, kb_conn)
    decisions = [dict(t) for t in get_vocabulary_terms(kb_conn) if t["source"] == "new_terms"]
    corpus_conn.close()
    kb_conn.close()
    return templates.TemplateResponse(request, "new_terms_review.html", {
        "kb": kb_name,
        "items": items,
        "decisions": decisions,
        "counts": {"pending": len(items), "accepted": len(decisions)},
    })


@router.get("/review/new-terms/partials/queue", include_in_schema=False)
def new_terms_queue_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import get_new_terms_candidates, open_corpus
    from src.db.kb import open_kb

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    items = get_new_terms_candidates(corpus_conn, kb_conn)
    corpus_conn.close()
    kb_conn.close()
    return templates.TemplateResponse(request, "partials/new_terms_queue.html", {
        "kb": kb_name,
        "items": items,
    })


@router.get("/review/new-terms/partials/decisions", include_in_schema=False)
def new_terms_decisions_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    _, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.kb import get_vocabulary_terms, open_kb

    kb_conn = open_kb(kb_path)
    decisions = [dict(t) for t in get_vocabulary_terms(kb_conn) if t["source"] == "new_terms"]
    kb_conn.close()
    return templates.TemplateResponse(request, "partials/new_terms_decisions.html", {
        "kb": kb_name,
        "decisions": decisions,
    })


@router.post("/review/new-terms/decide", include_in_schema=False)
async def ui_new_terms_decide(
    term: str = Form(...),
    action: str = Form(...),
    corrected_to: str = Form(""),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
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
    elif action == "correct" and corrected_to:
        add_correction(kb_conn, raw_term=term, canonical_term=corrected_to)
        add_vocabulary_term(kb_conn, corrected_to)
        bump_kb_version(kb_conn, "vocabulary_term_added")
        kb_conn.commit()

    corpus_conn.commit()
    corpus_conn.close()
    kb_conn.close()
    return Response(content="", headers={"HX-Trigger": _HX_TRIGGER_BOTH})


@router.delete("/review/new-terms/decisions/{term}", include_in_schema=False)
def ui_delete_new_terms_decision(
    term: str,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.kb import delete_vocabulary_term, open_kb

    _, kb_path = paths
    kb_conn = open_kb(kb_path)
    delete_vocabulary_term(kb_conn, term)
    kb_conn.commit()
    kb_conn.close()
    return Response(content="", headers={"HX-Trigger": _HX_TRIGGER_BOTH})


@router.delete("/review/normalise/decisions/{decision_id}", include_in_schema=False)
def ui_delete_decision(
    decision_id: str,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.corpus import open_corpus, set_token_pending
    from src.db.kb import delete_decision, get_decisions, open_kb

    corpus_path, kb_path = paths

    try:
        table, row_id_str = decision_id.rsplit(":", 1)
        row_id = int(row_id_str)
    except ValueError:
        return Response(content="", status_code=400)

    kb_conn = open_kb(kb_path)
    try:
        delete_decision(kb_conn, table, row_id)
    except ValueError:
        kb_conn.close()
        return Response(content="", status_code=400)
    kb_conn.close()

    corpus_conn = open_corpus(corpus_path)
    kb_conn2 = open_kb(kb_path)
    remaining = {d["token"] for d in get_decisions(kb_conn2)}
    kb_conn2.close()

    for row in corpus_conn.execute(
        "SELECT id, token FROM analyse_tokens WHERE status='decided'"
    ).fetchall():
        if row["token"] not in remaining:
            set_token_pending(corpus_conn, row["id"])
    corpus_conn.close()

    return Response(content="", headers={"HX-Trigger": _HX_TRIGGER_BOTH})


@router.post("/review/normalise/reassign", include_in_schema=False)
async def ui_normalise_reassign(
    decision_id: str = Form(...),
    new_action: str = Form(...),
    canonical_term: str = Form(""),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.kb import (
        add_correction,
        add_reject_token,
        add_to_stoplist,
        bump_kb_version,
        delete_decision,
        get_decision_token,
        open_kb,
    )

    _, kb_path = paths

    try:
        table, row_id_str = decision_id.rsplit(":", 1)
        row_id = int(row_id_str)
    except ValueError:
        return Response(content="", status_code=400)

    kb_conn = open_kb(kb_path)

    token_text = get_decision_token(kb_conn, table, row_id)
    if token_text is None:
        kb_conn.close()
        return Response(content="", status_code=404)

    try:
        delete_decision(kb_conn, table, row_id)
    except ValueError:
        kb_conn.close()
        return Response(content="", status_code=400)

    if new_action == "ignore":
        add_to_stoplist(kb_conn, token_text)
        bump_kb_version(kb_conn, "stoplist_updated")
    elif new_action == "correct":
        add_correction(kb_conn, raw_term=token_text, canonical_term=canonical_term, correction_kind="typo")
        bump_kb_version(kb_conn, "correction_added")
    elif new_action == "reject":
        add_reject_token(kb_conn, pattern=token_text, is_regex=False, label=token_text)
        bump_kb_version(kb_conn, "reject_token_added")
    # new_action == "accept": no KB rule needed; token stays decided in corpus

    kb_conn.close()
    return Response(content="", headers={"HX-Trigger": _HX_TRIGGER_DECISIONS})


@router.get("/health", include_in_schema=False)
def health_page(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.config import load_config
    from src.db.corpus import open_corpus
    from src.db.kb import open_kb
    from src.db.registry import get_kb_path, open_registry
    from src.health import run_checks

    reg = open_registry(Path("."))
    try:
        kb_folder = get_kb_path(reg, kb_name)
    except ValueError:
        kb_folder = corpus_path.parent
    reg.close()

    config = load_config(Path("config.yaml"), kb_folder / "config.yaml")

    corpus_conn = kb_conn = None
    if corpus_path.exists() and kb_path.exists():
        corpus_conn = open_corpus(corpus_path)
        kb_conn = open_kb(kb_path)

    checks = run_checks(config, corpus_conn, kb_conn, kb_folder)

    if corpus_conn:
        corpus_conn.close()
    if kb_conn:
        kb_conn.close()

    groups = [
        {"label": "Environment (Required)", "checks": [c for c in checks if c.severity == "error"]},
        {"label": "Optional Tools", "checks": [c for c in checks if c.id in {"vision_model", "text_model", "spacy_model", "field_map"}]},
        {"label": "KB State", "checks": [c for c in checks if c.id in {"sources", "corpus_files", "vocabulary", "focus", "unknown_fields"}]},
        {"label": "KB Scaffold Files", "checks": [c for c in checks if c.id in {"library_yaml", "exiftool_config", "dates_yaml", "derive_rules_yaml", "taxonomy_yaml"}]},
    ]

    return templates.TemplateResponse(request, "health.html", {
        "kb": kb_name,
        "groups": groups,
    })


# ---------------------------------------------------------------------------
# Speaker cluster review UI
# ---------------------------------------------------------------------------

@router.get("/review/speakers", include_in_schema=False)
def _speaker_review_301(request: Request):
    kb = request.query_params.get("kb", "")
    url = f"/knowledge/people/speakers?kb={kb}" if kb else "/knowledge/people/speakers"
    return RedirectResponse(url=url, status_code=301)


@router.get("/review/speakers/partials/queue", include_in_schema=False)
def speaker_queue_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import get_pending_speaker_clusters, open_corpus
    from src.db.kb import get_all_people, open_kb

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    pending = get_pending_speaker_clusters(corpus_conn)
    people = get_all_people(kb_conn)
    corpus_conn.close()
    kb_conn.close()
    return templates.TemplateResponse(request, "partials/speaker_clusters_queue.html", {
        "kb": kb_name,
        "pending": [dict(r) for r in pending],
        "people": [dict(r) for r in people],
    })


@router.get("/review/speakers/partials/decisions", include_in_schema=False)
def speaker_decisions_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import get_assigned_speaker_clusters, open_corpus
    from src.db.kb import get_all_people, open_kb

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    assigned = get_assigned_speaker_clusters(corpus_conn)
    people_map = {r["id"]: r["preferred_name"] for r in get_all_people(kb_conn)}
    corpus_conn.close()
    kb_conn.close()
    return templates.TemplateResponse(request, "partials/speaker_clusters_decisions.html", {
        "kb": kb_name,
        "assigned": [dict(r) for r in assigned],
        "people_map": people_map,
    })


@router.post("/review/speakers/decide", include_in_schema=False)
async def ui_speaker_decide(
    cluster_id: int = Form(...),
    action: str = Form(...),
    person_id: str = Form(""),
    new_name: str = Form(""),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.corpus import (
        assign_speaker_cluster,
        get_voice_speaker_clusters,
        open_corpus,
    )
    from src.db.kb import merge_voice_centroid, open_kb, upsert_person

    corpus_path, kb_path = paths
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    pid: int | None = int(person_id) if person_id.strip() else None
    label: str = ""

    if action == "assign":
        if pid is not None:
            row = kb_conn.execute(
                "SELECT preferred_name FROM people WHERE id = ?", (pid,)
            ).fetchone()
            label = row["preferred_name"] if row else ""
        elif new_name.strip():
            pid = upsert_person(kb_conn, new_name.strip())
            label = new_name.strip()
        else:
            corpus_conn.close()
            kb_conn.close()
            return Response(content="person_id or new_name required", status_code=400)

        clusters = {r["id"]: r for r in get_voice_speaker_clusters(corpus_conn)}
        cluster = clusters.get(cluster_id)
        if cluster is not None and cluster["centroid"] is not None:
            merge_voice_centroid(kb_conn, pid, bytes(cluster["centroid"]), cluster["member_count"])
        kb_conn.commit()

        assign_speaker_cluster(corpus_conn, cluster_id, pid, label)
        corpus_conn.commit()

    corpus_conn.close()
    kb_conn.close()
    return Response(content="", headers={"HX-Trigger": _HX_TRIGGER_BOTH})


@router.delete("/review/speakers/decisions/{cluster_id}", include_in_schema=False)
def ui_speaker_unassign(
    cluster_id: int,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.corpus import open_corpus, unassign_speaker_cluster

    corpus_path, _ = paths
    corpus_conn = open_corpus(corpus_path)
    unassign_speaker_cluster(corpus_conn, cluster_id)
    corpus_conn.commit()
    corpus_conn.close()
    return Response(content="", headers={"HX-Trigger": _HX_TRIGGER_BOTH})


_HX_TRIGGER_REGISTRY = '{"registryChanged": null}'


@router.post("/knowledge/locations/registry/update", include_in_schema=False)
async def ui_registry_update(
    table: str = Form(...),
    entry_id: int = Form(...),
    location: str = Form(""),
    latitude: str = Form(""),
    longitude: str = Form(""),
    threshold_m: str = Form(""),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.kb import open_kb, update_entity_table_entry
    _, kb_path = paths
    fields: dict = {}
    if location:
        fields["location"] = location
    if latitude:
        fields["latitude"] = latitude
    if longitude:
        fields["longitude"] = longitude
    if threshold_m:
        fields["threshold_m"] = threshold_m
    if not fields:
        return Response(content="<p style='color:#f87171'>No fields provided.</p>",
                        media_type="text/html")
    kb_conn = open_kb(kb_path)
    try:
        update_entity_table_entry(kb_conn, table, entry_id, fields)
    except ValueError as exc:
        kb_conn.close()
        return Response(content=f"<p style='color:#f87171'>{exc}</p>", media_type="text/html")
    kb_conn.close()
    return Response(
        content="<p style='color:#4ade80'>Saved.</p>",
        media_type="text/html",
        headers={"HX-Trigger": _HX_TRIGGER_REGISTRY},
    )


@router.post("/knowledge/locations/registry/delete", include_in_schema=False)
async def ui_registry_delete(
    table: str = Form(...),
    entry_id: int = Form(...),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.kb import delete_entity_table_entry, open_kb
    _, kb_path = paths
    kb_conn = open_kb(kb_path)
    try:
        delete_entity_table_entry(kb_conn, table, entry_id)
    except ValueError as exc:
        kb_conn.close()
        return Response(content=f"<p style='color:#f87171'>{exc}</p>", media_type="text/html")
    kb_conn.close()
    return Response(
        content="<p class='empty-state'>Entry deleted.</p>",
        media_type="text/html",
        headers={"HX-Trigger": _HX_TRIGGER_REGISTRY},
    )


@router.post("/knowledge/locations/registry/merge", include_in_schema=False)
async def ui_registry_merge(
    table: str = Form(...),
    keep_id: int = Form(...),
    drop_id: int = Form(...),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.kb import merge_entity_table_entries, open_kb
    _, kb_path = paths
    if keep_id == drop_id:
        return Response(content="<p style='color:#f87171'>keep_id and drop_id must differ.</p>",
                        media_type="text/html")
    kb_conn = open_kb(kb_path)
    try:
        merge_entity_table_entries(kb_conn, table, keep_id, drop_id)
    except ValueError as exc:
        kb_conn.close()
        return Response(content=f"<p style='color:#f87171'>{exc}</p>", media_type="text/html")
    kb_conn.close()
    return Response(
        content="<p style='color:#4ade80'>Merged.</p>",
        media_type="text/html",
        headers={"HX-Trigger": _HX_TRIGGER_REGISTRY},
    )


# ---------------------------------------------------------------------------
# Face cluster review (KB.Q3)
# ---------------------------------------------------------------------------

@router.get("/knowledge/people/faces", include_in_schema=False)
def face_review_page(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import (
        get_assigned_face_clusters,
        get_pending_face_clusters,
        open_corpus,
    )
    from src.db.kb import get_all_people, open_kb

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    pending = get_pending_face_clusters(corpus_conn)
    assigned = get_assigned_face_clusters(corpus_conn)
    people = get_all_people(kb_conn)
    people_map = {r["id"]: r["preferred_name"] for r in people}
    corpus_conn.close()
    kb_conn.close()
    return templates.TemplateResponse(request, "face_review.html", {
        "kb": kb_name,
        "pending": [dict(r) for r in pending],
        "assigned": [dict(r) for r in assigned],
        "people": [dict(r) for r in people],
        "people_map": people_map,
        "counts": {"pending": len(pending), "assigned": len(assigned)},
    })


@router.get("/knowledge/people/faces/partials/queue", include_in_schema=False)
def face_queue_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import get_pending_face_clusters, open_corpus
    from src.db.kb import get_all_people, open_kb

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    pending = get_pending_face_clusters(corpus_conn)
    people = get_all_people(kb_conn)
    corpus_conn.close()
    kb_conn.close()
    return templates.TemplateResponse(request, "partials/face_clusters_queue.html", {
        "kb": kb_name,
        "pending": [dict(r) for r in pending],
        "people": [dict(r) for r in people],
    })


@router.get("/knowledge/people/faces/partials/assigned", include_in_schema=False)
def face_assigned_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import get_assigned_face_clusters, open_corpus
    from src.db.kb import get_all_people, open_kb

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    assigned = get_assigned_face_clusters(corpus_conn)
    people_map = {r["id"]: r["preferred_name"] for r in get_all_people(kb_conn)}
    corpus_conn.close()
    kb_conn.close()
    return templates.TemplateResponse(request, "partials/face_clusters_assigned.html", {
        "kb": kb_name,
        "assigned": [dict(r) for r in assigned],
        "people_map": people_map,
    })


@router.post("/review/faces/decide", include_in_schema=False)
async def ui_face_decide(
    cluster_id: int = Form(...),
    action: str = Form(...),
    person_id: str = Form(""),
    new_name: str = Form(""),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.corpus import assign_face_cluster, open_corpus, unassign_face_cluster
    from src.db.kb import open_kb, upsert_person

    corpus_path, kb_path = paths
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    if action == "assign":
        pid: int | None = int(person_id) if person_id.strip() else None
        label: str = ""

        if pid is not None:
            row = kb_conn.execute(
                "SELECT preferred_name FROM people WHERE id = ?", (pid,)
            ).fetchone()
            label = row["preferred_name"] if row else ""
        elif new_name.strip():
            pid = upsert_person(kb_conn, new_name.strip())
            label = new_name.strip()
        else:
            corpus_conn.close()
            kb_conn.close()
            return Response(content="person_id or new_name required", status_code=400)

        assign_face_cluster(corpus_conn, cluster_id, pid, label)
        corpus_conn.commit()

    elif action == "unassign":
        unassign_face_cluster(corpus_conn, cluster_id)
        corpus_conn.commit()

    corpus_conn.close()
    kb_conn.close()
    return Response(content="", headers={"HX-Trigger": _HX_TRIGGER_BOTH})


# ---------------------------------------------------------------------------
# People registry (KB.Q4)
# ---------------------------------------------------------------------------

@router.get("/knowledge/people", include_in_schema=False)
def people_registry_page(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import open_corpus
    from src.db.kb import get_people_with_cluster_counts, open_kb

    kb_conn = open_kb(kb_path)
    corpus_conn = open_corpus(corpus_path)
    people = get_people_with_cluster_counts(kb_conn, corpus_conn)
    kb_conn.close()
    corpus_conn.close()
    return templates.TemplateResponse(request, "people_registry.html", {
        "kb": kb_name,
        "people": people,
    })


@router.get("/knowledge/people/partials/person-list", include_in_schema=False)
def people_list_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import open_corpus
    from src.db.kb import get_people_with_cluster_counts, open_kb

    kb_conn = open_kb(kb_path)
    corpus_conn = open_corpus(corpus_path)
    people = get_people_with_cluster_counts(kb_conn, corpus_conn)
    kb_conn.close()
    corpus_conn.close()
    return templates.TemplateResponse(request, "partials/person_list.html", {
        "kb": kb_name,
        "people": people,
    })


@router.get("/knowledge/people/partials/person-detail/{person_id}", include_in_schema=False)
def person_detail_partial(
    request: Request,
    person_id: int,
    paths: tuple[Path, Path] = Depends(resolve_kb),
):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import open_corpus
    from src.db.kb import get_all_people, get_people_with_cluster_counts, open_kb

    kb_conn = open_kb(kb_path)
    corpus_conn = open_corpus(corpus_path)
    people_with_counts = get_people_with_cluster_counts(kb_conn, corpus_conn)
    all_people = get_all_people(kb_conn)
    kb_conn.close()
    corpus_conn.close()
    person = next((p for p in people_with_counts if p["id"] == person_id), None)
    others = [dict(p) for p in all_people if p["id"] != person_id]
    return templates.TemplateResponse(request, "partials/person_detail.html", {
        "kb": kb_name,
        "person": person,
        "others": others,
    })


_HX_TRIGGER_PEOPLE = '{"peopleChanged": null}'


@router.post("/knowledge/people/add", include_in_schema=False)
async def ui_person_add(
    preferred_name: str = Form(...),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.kb import open_kb
    name = preferred_name.strip()
    if not name:
        return Response(content="<p style='color:#f87171'>Name is required.</p>", media_type="text/html")
    _, kb_path = paths
    kb_conn = open_kb(kb_path)
    existing = kb_conn.execute("SELECT id FROM people WHERE preferred_name = ?", (name,)).fetchone()
    if existing:
        kb_conn.close()
        return Response(content="<p style='color:#f87171'>Name already exists.</p>", media_type="text/html")
    kb_conn.execute("INSERT INTO people (preferred_name) VALUES (?)", (name,))
    kb_conn.commit()
    kb_conn.close()
    return Response(
        content="<p style='color:#4ade80'>Added.</p>",
        media_type="text/html",
        headers={"HX-Trigger": _HX_TRIGGER_PEOPLE},
    )


@router.post("/knowledge/people/{person_id}/edit", include_in_schema=False)
async def ui_person_edit(
    person_id: int,
    preferred_name: str = Form(...),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.kb import open_kb
    name = preferred_name.strip()
    if not name:
        return Response(content="<p style='color:#f87171'>Name is required.</p>", media_type="text/html")
    _, kb_path = paths
    kb_conn = open_kb(kb_path)
    row = kb_conn.execute("SELECT id FROM people WHERE id = ?", (person_id,)).fetchone()
    if row is None:
        kb_conn.close()
        return Response(content="<p style='color:#f87171'>Person not found.</p>", media_type="text/html")
    kb_conn.execute("UPDATE people SET preferred_name = ? WHERE id = ?", (name, person_id))
    kb_conn.commit()
    kb_conn.close()
    return Response(
        content="<p style='color:#4ade80'>Saved.</p>",
        media_type="text/html",
        headers={"HX-Trigger": _HX_TRIGGER_PEOPLE},
    )


@router.post("/knowledge/people/{person_id}/delete", include_in_schema=False)
async def ui_person_delete(
    person_id: int,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    corpus_path, kb_path = paths
    from src.db.corpus import open_corpus
    from src.db.kb import delete_person, open_kb
    kb_conn = open_kb(kb_path)
    corpus_conn = open_corpus(corpus_path)
    try:
        delete_person(kb_conn, corpus_conn, person_id)
    except KeyError:
        kb_conn.close()
        corpus_conn.close()
        return Response(content="<p style='color:#f87171'>Person not found.</p>", media_type="text/html")
    except ValueError as exc:
        kb_conn.close()
        corpus_conn.close()
        return Response(content=f"<p style='color:#f87171'>{exc}</p>", media_type="text/html")
    kb_conn.close()
    corpus_conn.close()
    return Response(
        content="<p class='empty-state'>Deleted.</p>",
        media_type="text/html",
        headers={"HX-Trigger": _HX_TRIGGER_PEOPLE},
    )


@router.post("/knowledge/people/{person_id}/merge", include_in_schema=False)
async def ui_person_merge(
    person_id: int,
    merge_from_id: int = Form(...),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    corpus_path, kb_path = paths
    from src.db.corpus import open_corpus
    from src.db.kb import merge_people, open_kb
    kb_conn = open_kb(kb_path)
    corpus_conn = open_corpus(corpus_path)
    try:
        merge_people(kb_conn, corpus_conn, person_id, merge_from_id)
    except ValueError as exc:
        kb_conn.close()
        corpus_conn.close()
        return Response(content=f"<p style='color:#f87171'>{exc}</p>", media_type="text/html")
    except KeyError as exc:
        kb_conn.close()
        corpus_conn.close()
        return Response(content=f"<p style='color:#f87171'>{exc}</p>", media_type="text/html")
    kb_conn.close()
    corpus_conn.close()
    return Response(
        content="<p style='color:#4ade80'>Merged.</p>",
        media_type="text/html",
        headers={"HX-Trigger": _HX_TRIGGER_PEOPLE},
    )


# ---------------------------------------------------------------------------
# Speaker review at new Knowledge section URL (KB.Q4)
# ---------------------------------------------------------------------------

@router.get("/knowledge/people/speakers", include_in_schema=False)
def speaker_review_page_new(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import (
        get_assigned_speaker_clusters,
        get_pending_speaker_clusters,
        open_corpus,
    )
    from src.db.kb import get_all_people, open_kb

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    pending = get_pending_speaker_clusters(corpus_conn)
    assigned = get_assigned_speaker_clusters(corpus_conn)
    people = get_all_people(kb_conn)
    people_map = {r["id"]: r["preferred_name"] for r in people}
    corpus_conn.close()
    kb_conn.close()
    return templates.TemplateResponse(request, "speaker_review.html", {
        "kb": kb_name,
        "pending": [dict(r) for r in pending],
        "assigned": [dict(r) for r in assigned],
        "people": [dict(r) for r in people],
        "people_map": people_map,
        "counts": {"pending": len(pending), "assigned": len(assigned)},
    })


@router.get("/knowledge/people/speakers/partials/queue", include_in_schema=False)
def speaker_queue_partial_new(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import get_pending_speaker_clusters, open_corpus
    from src.db.kb import get_all_people, open_kb

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    pending = get_pending_speaker_clusters(corpus_conn)
    people = get_all_people(kb_conn)
    corpus_conn.close()
    kb_conn.close()
    return templates.TemplateResponse(request, "partials/speaker_clusters_queue.html", {
        "kb": kb_name,
        "pending": [dict(r) for r in pending],
        "people": [dict(r) for r in people],
    })


@router.get("/knowledge/people/speakers/partials/decisions", include_in_schema=False)
def speaker_decisions_partial_new(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    corpus_path, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.corpus import get_assigned_speaker_clusters, open_corpus
    from src.db.kb import get_all_people, open_kb

    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)
    assigned = get_assigned_speaker_clusters(corpus_conn)
    people_map = {r["id"]: r["preferred_name"] for r in get_all_people(kb_conn)}
    corpus_conn.close()
    kb_conn.close()
    return templates.TemplateResponse(request, "partials/speaker_clusters_decisions.html", {
        "kb": kb_name,
        "assigned": [dict(r) for r in assigned],
        "people_map": people_map,
    })


@router.post("/knowledge/people/speakers/decide", include_in_schema=False)
async def ui_speaker_decide_new(
    cluster_id: int = Form(...),
    action: str = Form(...),
    person_id: str = Form(""),
    new_name: str = Form(""),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.corpus import (
        assign_speaker_cluster,
        get_voice_speaker_clusters,
        open_corpus,
    )
    from src.db.kb import merge_voice_centroid, open_kb, upsert_person

    corpus_path, kb_path = paths
    corpus_conn = open_corpus(corpus_path)
    kb_conn = open_kb(kb_path)

    pid: int | None = int(person_id) if person_id.strip() else None
    label: str = ""

    if action == "assign":
        if pid is not None:
            row = kb_conn.execute(
                "SELECT preferred_name FROM people WHERE id = ?", (pid,)
            ).fetchone()
            label = row["preferred_name"] if row else ""
        elif new_name.strip():
            pid = upsert_person(kb_conn, new_name.strip())
            label = new_name.strip()
        else:
            corpus_conn.close()
            kb_conn.close()
            return Response(content="person_id or new_name required", status_code=400)

        clusters = {r["id"]: r for r in get_voice_speaker_clusters(corpus_conn)}
        cluster = clusters.get(cluster_id)
        if cluster is not None and cluster["centroid"] is not None:
            merge_voice_centroid(kb_conn, pid, bytes(cluster["centroid"]), cluster["member_count"])
        kb_conn.commit()

        assign_speaker_cluster(corpus_conn, cluster_id, pid, label)
        corpus_conn.commit()

    corpus_conn.close()
    kb_conn.close()
    return Response(content="", headers={"HX-Trigger": _HX_TRIGGER_BOTH})


@router.delete("/knowledge/people/speakers/decisions/{cluster_id}", include_in_schema=False)
def ui_speaker_unassign_new(
    cluster_id: int,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.corpus import open_corpus, unassign_speaker_cluster

    corpus_path, _ = paths
    corpus_conn = open_corpus(corpus_path)
    unassign_speaker_cluster(corpus_conn, cluster_id)
    corpus_conn.commit()
    corpus_conn.close()
    return Response(content="", headers={"HX-Trigger": _HX_TRIGGER_BOTH})


# ---------------------------------------------------------------------------
# Prompt library page (KB.S5)
# ---------------------------------------------------------------------------

@router.get("/knowledge/prompts", include_in_schema=False)
def prompt_library_page(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    _, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.kb import list_stage_prompts, open_kb
    kb_conn = open_kb(kb_path)
    all_prompts = list_stage_prompts(kb_conn)
    kb_conn.close()

    grouped: dict[str, dict[str, list]] = {}
    for p in all_prompts:
        grouped.setdefault(p["stage"], {}).setdefault(p["prompt_key"], []).append(p)

    return templates.TemplateResponse(request, "prompt_library.html", {
        "kb": kb_name,
        "grouped": grouped,
    })


# ---------------------------------------------------------------------------
# Capture Rules manager
# ---------------------------------------------------------------------------

_HX_TRIGGER_RULES = '{"rulesChanged": null}'


@router.get("/knowledge/capture-rules", include_in_schema=False)
def capture_rules_page(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    _, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    return templates.TemplateResponse(request, "capture_rules.html", {"kb": kb_name})


@router.get("/knowledge/capture-rules/partials/list", include_in_schema=False)
def capture_rules_list_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    _, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    from src.db.kb import list_capture_rules, open_kb
    kb_conn = open_kb(kb_path)
    rules = list_capture_rules(kb_conn)
    kb_conn.close()
    return templates.TemplateResponse(request, "partials/capture_rule_list.html", {
        "kb": kb_name,
        "rules": rules,
    })


@router.get("/knowledge/capture-rules/partials/form", include_in_schema=False)
def capture_rules_form_partial(request: Request, paths: tuple[Path, Path] = Depends(resolve_kb)):
    _, kb_path = paths
    kb_name = request.query_params.get("kb", "")
    rule_id_str = request.query_params.get("rule_id", "")
    rule = None
    if rule_id_str:
        from src.db.kb import list_capture_rules, open_kb
        kb_conn = open_kb(kb_path)
        rules = list_capture_rules(kb_conn)
        kb_conn.close()
        try:
            rule_id = int(rule_id_str)
            rule = next((r for r in rules if r["id"] == rule_id), None)
        except ValueError:
            pass
    return templates.TemplateResponse(request, "partials/capture_rule_form.html", {
        "kb": kb_name,
        "rule": rule,
    })


@router.post("/knowledge/capture-rules/add", include_in_schema=False)
async def ui_capture_rule_add(
    request: Request,
    pattern: str = Form(...),
    extract_as: str = Form(...),
    label: str = Form(""),
    format_str: str = Form(""),
    value_type: str = Form(""),
    keep_token: str = Form("false"),
    date_precision: str = Form(""),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.kb import add_capture_rule, open_kb
    _, kb_path = paths
    kb_conn = open_kb(kb_path)
    try:
        existing = kb_conn.execute(
            "SELECT id FROM capture_rules WHERE pattern=?", (pattern,)
        ).fetchone()
        if existing:
            kb_conn.close()
            return Response(
                content="<p style='color:#dc2626'>A rule with that pattern already exists.</p>",
                media_type="text/html",
            )
        add_capture_rule(
            kb_conn,
            pattern=pattern,
            label=label,
            extract_as=extract_as,
            format_str=format_str,
            value_type=value_type,
            keep_token=keep_token.lower() == "true",
            date_precision=date_precision or None,
        )
    finally:
        kb_conn.close()
    return Response(
        content="<p style='color:#4ade80'>Rule added.</p>",
        media_type="text/html",
        headers={"HX-Trigger": _HX_TRIGGER_RULES},
    )


@router.post("/knowledge/capture-rules/{rule_id}/edit", include_in_schema=False)
async def ui_capture_rule_edit(
    rule_id: int,
    pattern: str = Form(...),
    extract_as: str = Form(...),
    label: str = Form(""),
    format_str: str = Form(""),
    value_type: str = Form(""),
    keep_token: str = Form("false"),
    date_precision: str = Form(""),
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.kb import open_kb, update_capture_rule
    _, kb_path = paths
    kb_conn = open_kb(kb_path)
    try:
        existing = kb_conn.execute(
            "SELECT id FROM capture_rules WHERE id=?", (rule_id,)
        ).fetchone()
        if not existing:
            kb_conn.close()
            return Response(content="<p style='color:#dc2626'>Rule not found.</p>",
                            media_type="text/html", status_code=404)
        update_capture_rule(
            kb_conn,
            rule_id=rule_id,
            pattern=pattern,
            label=label,
            extract_as=extract_as,
            format_str=format_str,
            value_type=value_type,
            keep_token=keep_token.lower() == "true",
            date_precision=date_precision or None,
        )
    finally:
        kb_conn.close()
    return Response(
        content="<p style='color:#4ade80'>Rule updated.</p>",
        media_type="text/html",
        headers={"HX-Trigger": _HX_TRIGGER_RULES},
    )


@router.post("/knowledge/capture-rules/{rule_id}/delete", include_in_schema=False)
async def ui_capture_rule_delete(
    rule_id: int,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> Response:
    from src.db.kb import delete_capture_rule, open_kb
    _, kb_path = paths
    kb_conn = open_kb(kb_path)
    try:
        existing = kb_conn.execute(
            "SELECT id FROM capture_rules WHERE id=?", (rule_id,)
        ).fetchone()
        if not existing:
            kb_conn.close()
            return Response(content="<p style='color:#dc2626'>Rule not found.</p>",
                            media_type="text/html", status_code=404)
        delete_capture_rule(kb_conn, rule_id)
    finally:
        kb_conn.close()
    return Response(
        content="<p style='color:#4ade80'>Rule deleted.</p>",
        media_type="text/html",
        headers={"HX-Trigger": _HX_TRIGGER_RULES},
    )
