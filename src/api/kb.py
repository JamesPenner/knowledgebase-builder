import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


def _get_kb_folder(name: str) -> Path:
    from src.db.registry import get_kb_path, open_registry
    try:
        reg = open_registry(Path("."))
        return get_kb_path(reg, name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/", tags=["kb"])
def list_kbs_endpoint() -> dict:
    from src.db.registry import list_kbs, open_registry
    reg = open_registry(Path("."))
    kbs = list_kbs(reg)
    reg.close()
    return {"kbs": [{"name": r["name"], "is_active": bool(r["is_active"]), "created_at": r["created_at"]} for r in kbs]}


@router.get("/{name}/stats", tags=["kb"])
def kb_stats(name: str) -> dict[str, Any]:
    from src.db.corpus import get_corpus_stats, open_corpus
    from src.db.kb import open_kb
    from src.db.registry import get_kb_path, open_registry

    try:
        reg = open_registry(Path("."))
        folder = get_kb_path(reg, name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    corpus_conn = open_corpus(folder / "corpus.db")
    kb_conn = open_kb(folder / "knowledge.db")
    try:
        return get_corpus_stats(corpus_conn, kb_conn)
    finally:
        corpus_conn.close()
        kb_conn.close()


@router.get("/{name}/sources", tags=["kb"])
def kb_sources(name: str) -> list[dict[str, Any]]:
    from src.db.corpus import get_sources, open_corpus
    from src.db.registry import get_kb_path, open_registry

    try:
        reg = open_registry(Path("."))
        folder = get_kb_path(reg, name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    conn = open_corpus(folder / "corpus.db")
    try:
        rows = get_sources(conn)
        return [
            {"id": r["id"], "path": r["path"], "file_count": r["file_count_ingested"]}
            for r in rows
        ]
    finally:
        conn.close()


class SourceCreateRequest(BaseModel):
    path: str
    file_type: str = "all"
    recursive: bool = True
    incremental: bool = False
    filters: dict = {}
    modified_after: str | None = None
    exclude_patterns: list[str] = []


@router.post("/{name}/sources", tags=["kb"])
def kb_add_source(name: str, req: SourceCreateRequest) -> dict[str, Any]:
    from src.db.corpus import add_source, open_corpus
    from src.pipeline.filter_spec import FilterSpec
    folder = _get_kb_folder(name)
    if not Path(req.path).exists():
        raise HTTPException(status_code=422, detail=f"Path does not exist: {req.path}")
    conn = open_corpus(folder / "corpus.db")
    try:
        spec = FilterSpec(
            glob=req.filters.get("glob"),
            count_limit=req.filters.get("count_limit"),
            modified_after=req.modified_after,
            exclude_patterns=req.exclude_patterns,
        )
        source_id = add_source(conn, req.path, req.file_type, req.recursive, spec.to_dict(), req.incremental)
        return {"id": source_id, "path": req.path}
    finally:
        conn.close()


class SourceUpdateRequest(BaseModel):
    file_type: str = "all"
    recursive: bool = True
    incremental: bool = False
    filters: dict = {}
    modified_after: str | None = None
    exclude_patterns: list[str] = []


@router.patch("/{name}/sources/{source_id}", tags=["kb"])
def kb_update_source(name: str, source_id: int, req: SourceUpdateRequest) -> dict[str, Any]:
    from src.db.corpus import open_corpus, update_source
    from src.pipeline.filter_spec import FilterSpec
    folder = _get_kb_folder(name)
    conn = open_corpus(folder / "corpus.db")
    try:
        spec = FilterSpec(
            glob=req.filters.get("glob"),
            count_limit=req.filters.get("count_limit"),
            modified_after=req.modified_after,
            exclude_patterns=req.exclude_patterns,
        )
        found = update_source(conn, source_id, req.file_type, req.recursive, spec.to_dict(), req.incremental)
        if not found:
            raise HTTPException(status_code=404, detail=f"Source {source_id} not found")
        return {"id": source_id, "updated": True}
    finally:
        conn.close()


@router.delete("/{name}/sources/{source_id}", tags=["kb"])
def kb_remove_source(name: str, source_id: int, cascade: bool = False) -> dict[str, Any]:
    from src.db.corpus import open_corpus, remove_source
    folder = _get_kb_folder(name)
    conn = open_corpus(folder / "corpus.db")
    try:
        deleted = remove_source(conn, source_id, cascade=cascade)
        return {"deleted_files": deleted}
    finally:
        conn.close()


@router.post("/{name}/sources/preview", tags=["kb"])
def kb_preview_source(name: str, req: SourceCreateRequest) -> dict[str, Any]:
    import os
    from src.pipeline.filter_spec import FilterSpec
    from src.stages.ingest import apply_source_filters, detect_file_type
    # name is not used — preview only scans the filesystem
    src_path = Path(req.path)
    if not src_path.exists():
        raise HTTPException(status_code=422, detail=f"Path does not exist: {req.path}")

    candidates: list[Path] = []
    for dirpath, _dirs, filenames in os.walk(str(src_path)):
        for fname in filenames:
            p = Path(os.path.join(dirpath, fname))
            detected = detect_file_type(p.suffix)
            if req.file_type == "all" or detected == req.file_type:
                candidates.append(p)
        if not req.recursive:
            break

    spec = FilterSpec(
        glob=req.filters.get("glob"),
        count_limit=req.filters.get("count_limit"),
        modified_after=req.modified_after,
        exclude_patterns=req.exclude_patterns,
    )
    candidates = apply_source_filters(candidates, spec.to_dict())

    by_type: dict[str, int] = {}
    for p in candidates:
        ft = detect_file_type(p.suffix) or "other"
        by_type[ft] = by_type.get(ft, 0) + 1

    return {"total": len(candidates), "by_type": by_type}


@router.get("/{name}/sources/panel", include_in_schema=False)
def kb_sources_panel(name: str, request: Request):
    from fastapi.templating import Jinja2Templates
    from src.db.corpus import get_sources, open_corpus
    folder = _get_kb_folder(name)
    conn = open_corpus(folder / "corpus.db")
    try:
        raw_sources = get_sources(conn)
        sources = []
        for r in raw_sources:
            d = dict(r)
            try:
                filters = json.loads(d.get("filters_json") or "{}")
            except (ValueError, TypeError):
                filters = {}
            parts = []
            if filters.get("glob"):
                parts.append(f"glob: {filters['glob']}")
            if filters.get("count_limit") is not None:
                parts.append(f"limit: {filters['count_limit']}")
            if filters.get("modified_after"):
                parts.append(f"after: {filters['modified_after']}")
            if filters.get("exclude_patterns"):
                parts.append(f"exclude: {', '.join(filters['exclude_patterns'])}")
            d["filters_summary"] = ", ".join(parts) if parts else ""
            d["f_glob"] = filters.get("glob") or ""
            d["f_count_limit"] = filters.get("count_limit") or ""
            d["f_modified_after"] = filters.get("modified_after") or ""
            d["f_exclude_patterns"] = ", ".join(filters.get("exclude_patterns") or [])
            d["incremental"] = bool(d.get("incremental"))
            sources.append(d)
    finally:
        conn.close()
    tpl_dir = Path(__file__).parent.parent.parent / "templates"
    tpl = Jinja2Templates(directory=str(tpl_dir))
    return tpl.TemplateResponse(request, "partials/sources_panel.html", {
        "kb": name,
        "sources": sources,
    })


@router.get("/{name}/folders", tags=["kb"])
def kb_folders(name: str, source_id: int | None = None) -> dict[str, Any]:
    from src.db.corpus import get_distinct_folders, open_corpus
    folder = _get_kb_folder(name)
    conn = open_corpus(folder / "corpus.db")
    try:
        return {"folders": get_distinct_folders(conn, source_id)}
    finally:
        conn.close()


@router.get("/{name}/sets/preview", tags=["kb"])
def kb_sets_preview(
    name: str,
    source_id: int | None = None,
    folder_prefix: str | None = None,
    file_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    name_pattern: str | None = None,
) -> dict[str, Any]:
    from src.db.corpus import count_files_matching, open_corpus
    from src.pipeline.filter_spec import CorpusFilterSpec
    folder = _get_kb_folder(name)
    conn = open_corpus(folder / "corpus.db")
    spec = CorpusFilterSpec(
        source_id=source_id,
        folder_prefix=folder_prefix,
        file_type=file_type,
        date_from=date_from,
        date_to=date_to,
        name_pattern=name_pattern,
    )
    try:
        return {"file_count": count_files_matching(conn, spec)}
    finally:
        conn.close()


class SetCreateRequest(BaseModel):
    name: str
    description: str = ""
    source_id: int | None = None
    folder_prefix: str | None = None
    file_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    name_pattern: str | None = None


@router.get("/{name}/sets", tags=["kb"])
def kb_list_sets(name: str) -> list[dict[str, Any]]:
    from src.db.corpus import get_file_sets, open_corpus
    folder = _get_kb_folder(name)
    conn = open_corpus(folder / "corpus.db")
    try:
        return get_file_sets(conn)
    finally:
        conn.close()


@router.get("/{name}/sets/panel", include_in_schema=False)
def kb_sets_panel(name: str, request: Request):
    from fastapi.templating import Jinja2Templates
    from src.db.corpus import get_file_sets, open_corpus
    folder = _get_kb_folder(name)
    conn = open_corpus(folder / "corpus.db")
    try:
        sets = get_file_sets(conn)
    finally:
        conn.close()
    tpl_dir = Path(__file__).parent.parent.parent / "templates"
    tpl = Jinja2Templates(directory=str(tpl_dir))
    return tpl.TemplateResponse(request, "partials/sets_panel.html", {
        "kb": name,
        "sets": sets,
    })


@router.get("/{name}/sets/{set_id}", tags=["kb"])
def kb_get_set(name: str, set_id: int) -> dict[str, Any]:
    from src.db.corpus import get_file_set, open_corpus
    folder = _get_kb_folder(name)
    conn = open_corpus(folder / "corpus.db")
    try:
        row = get_file_set(conn, set_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Set not found")
        return row
    finally:
        conn.close()


@router.post("/{name}/sets", tags=["kb"])
def kb_create_set(name: str, req: SetCreateRequest) -> dict[str, Any]:
    from src.db.corpus import count_files_matching, create_file_set, open_corpus
    from src.pipeline.filter_spec import CorpusFilterSpec
    folder = _get_kb_folder(name)
    conn = open_corpus(folder / "corpus.db")
    spec = CorpusFilterSpec(
        source_id=req.source_id,
        folder_prefix=req.folder_prefix,
        file_type=req.file_type,
        date_from=req.date_from,
        date_to=req.date_to,
        name_pattern=req.name_pattern,
    )
    try:
        try:
            set_id = create_file_set(conn, req.name, req.description, spec)
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise HTTPException(status_code=422, detail=f"Set name already exists: {req.name}") from exc
            raise
        file_count = count_files_matching(conn, spec)
        return {"id": set_id, "file_count": file_count, "criteria_summary": spec.summary()}
    finally:
        conn.close()


@router.delete("/{name}/sets/{set_id}", tags=["kb"])
def kb_delete_set(name: str, set_id: int) -> dict[str, str]:
    from src.db.corpus import delete_file_set, open_corpus
    folder = _get_kb_folder(name)
    conn = open_corpus(folder / "corpus.db")
    try:
        delete_file_set(conn, set_id)
        return {"status": "deleted"}
    finally:
        conn.close()


@router.get("/{name}/health", tags=["kb"])
def kb_health(name: str) -> dict[str, Any]:
    from src.config import load_config
    from src.db.corpus import open_corpus
    from src.db.kb import open_kb
    from src.db.registry import get_kb_path, open_registry
    from src.health import run_checks

    try:
        reg = open_registry(Path("."))
        folder = get_kb_path(reg, name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    config = load_config(Path("config.yaml"), folder / "config.yaml")

    corpus_conn = kb_conn = None
    corpus_path = folder / "corpus.db"
    kb_path = folder / "knowledge.db"
    if corpus_path.exists() and kb_path.exists():
        corpus_conn = open_corpus(corpus_path)
        kb_conn = open_kb(kb_path)

    try:
        checks = run_checks(config, corpus_conn, kb_conn, folder)
        return {"checks": [
            {"id": c.id, "label": c.label, "severity": c.severity,
             "ok": c.ok, "detail": c.detail, "fix": c.fix}
            for c in checks
        ]}
    finally:
        if corpus_conn:
            corpus_conn.close()
        if kb_conn:
            kb_conn.close()
