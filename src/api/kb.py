from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


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
