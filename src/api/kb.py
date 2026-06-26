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
    filters: dict = {}


@router.post("/{name}/sources", tags=["kb"])
def kb_add_source(name: str, req: SourceCreateRequest) -> dict[str, Any]:
    from src.db.corpus import add_source, open_corpus
    folder = _get_kb_folder(name)
    if not Path(req.path).exists():
        raise HTTPException(status_code=422, detail=f"Path does not exist: {req.path}")
    conn = open_corpus(folder / "corpus.db")
    try:
        source_id = add_source(conn, req.path, req.file_type, req.recursive, req.filters)
        return {"id": source_id, "path": req.path}
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

    candidates = apply_source_filters(candidates, req.filters)

    by_type: dict[str, int] = {}
    for p in candidates:
        ft = detect_file_type(p.suffix) or "other"
        by_type[ft] = by_type.get(ft, 0) + 1

    return {"total": len(candidates), "by_type": by_type}


@router.get("/{name}/sources/panel", include_in_schema=False)
def kb_sources_panel(name: str, request: Request):
    from fastapi.templating import Jinja2Templates
    from src.db.corpus import get_file_sets, get_sources, open_corpus
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
            d["filters_summary"] = ", ".join(parts) if parts else ""
            sources.append(d)
        sets = [dict(r) for r in get_file_sets(conn)]
    finally:
        conn.close()
    tpl_dir = Path(__file__).parent.parent.parent / "templates"
    tpl = Jinja2Templates(directory=str(tpl_dir))
    return tpl.TemplateResponse(request, "partials/sources_panel.html", {
        "kb": name,
        "sources": sources,
        "sets": sets,
    })


class SetCreateRequest(BaseModel):
    name: str
    description: str = ""
    scope: dict = {}


@router.get("/{name}/sets", tags=["kb"])
def kb_list_sets(name: str) -> list[dict[str, Any]]:
    from src.db.corpus import get_file_sets, open_corpus
    folder = _get_kb_folder(name)
    conn = open_corpus(folder / "corpus.db")
    try:
        return [dict(r) for r in get_file_sets(conn)]
    finally:
        conn.close()


@router.post("/{name}/sets", tags=["kb"])
def kb_create_set(name: str, req: SetCreateRequest) -> dict[str, Any]:
    from src.db.corpus import create_file_set, open_corpus
    folder = _get_kb_folder(name)
    conn = open_corpus(folder / "corpus.db")
    try:
        # Resolve scope to file IDs
        scope = req.scope
        source_id = scope.get("source_id")
        file_type = scope.get("file_type")
        set_filter_id = scope.get("set_id")

        sql = "SELECT id FROM files WHERE 1=1"
        params: list = []
        if source_id is not None:
            sql += " AND source_id = ?"
            params.append(source_id)
        if file_type:
            sql += " AND file_type = ?"
            params.append(file_type)
        if set_filter_id is not None:
            sql += " AND id IN (SELECT file_id FROM file_set_members WHERE set_id = ?)"
            params.append(set_filter_id)

        rows = conn.execute(sql, params).fetchall()
        file_ids = [r["id"] for r in rows]

        try:
            set_id = create_file_set(conn, req.name, req.description, file_ids)
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise HTTPException(status_code=422, detail=f"Set name already exists: {req.name}") from exc
            raise
        return {"id": set_id, "file_count": len(file_ids)}
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
