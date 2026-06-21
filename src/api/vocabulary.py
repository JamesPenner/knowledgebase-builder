from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.deps import resolve_kb

router = APIRouter()


class AddTermRequest(BaseModel):
    kb: str
    term: str
    synonyms_json: str = "[]"


@router.get("/list", tags=["vocabulary"])
def list_vocabulary(
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict[str, Any]:
    _, kb_path = paths
    from src.db.kb import get_vocabulary_terms, open_kb

    kb_conn = open_kb(kb_path)
    terms = get_vocabulary_terms(kb_conn)
    kb_conn.close()
    return {"terms": [dict(t) for t in terms]}


@router.post("/add", tags=["vocabulary"])
def add_vocabulary(
    req: AddTermRequest,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict[str, Any]:
    _, kb_path = paths
    from src.db.kb import add_vocabulary_term, bump_kb_version, open_kb

    kb_conn = open_kb(kb_path)
    term_id = add_vocabulary_term(kb_conn, req.term, req.synonyms_json)
    bump_kb_version(kb_conn, "vocabulary_term_added")
    kb_conn.commit()
    kb_conn.close()
    return {"id": term_id, "term": req.term}


@router.delete("/{term_id}", tags=["vocabulary"])
def delete_vocabulary(
    term_id: int,
    paths: tuple[Path, Path] = Depends(resolve_kb),
) -> dict[str, str]:
    _, kb_path = paths
    from src.db.kb import bump_kb_version, open_kb

    kb_conn = open_kb(kb_path)
    row = kb_conn.execute("SELECT term FROM vocabulary WHERE id=?", (term_id,)).fetchone()
    if not row:
        kb_conn.close()
        raise HTTPException(404, f"Vocabulary term {term_id} not found")
    kb_conn.execute("DELETE FROM vocabulary WHERE id=?", (term_id,))
    bump_kb_version(kb_conn, "vocabulary_term_deleted")
    kb_conn.commit()
    kb_conn.close()
    return {"status": "deleted"}
