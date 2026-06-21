from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.deps import resolve_kb

router = APIRouter()


class AddSourceRequest(BaseModel):
    kb: str
    path: str
    file_type: str = "all"
    recursive: bool = True


@router.get("", tags=["sources"])
def list_sources(paths: tuple[Path, Path] = Depends(resolve_kb)) -> dict[str, Any]:
    corpus_path, _ = paths
    from src.db.corpus import get_sources, open_corpus

    conn = open_corpus(corpus_path)
    sources = [dict(s) for s in get_sources(conn)]
    conn.close()
    return {"sources": sources}


@router.post("", tags=["sources"])
def add_source_endpoint(req: AddSourceRequest) -> dict[str, Any]:
    from src.db.corpus import add_source, open_corpus
    from src.db.registry import get_kb_path, open_registry

    try:
        reg = open_registry(Path("."))
        folder = get_kb_path(reg, req.kb)
    except ValueError as exc:
        from fastapi import HTTPException
        raise HTTPException(404, str(exc)) from exc

    conn = open_corpus(folder / "corpus.db")
    source_id = add_source(conn, req.path, req.file_type, req.recursive)
    conn.close()
    return {"id": source_id, "path": req.path, "file_type": req.file_type, "recursive": req.recursive}
