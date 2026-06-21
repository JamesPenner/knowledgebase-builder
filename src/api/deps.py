from pathlib import Path

from fastapi import HTTPException, Query

_registry_root: Path = Path(".")


def set_registry_root(root: Path) -> None:
    global _registry_root
    _registry_root = root


def resolve_kb(kb: str = Query(..., description="KB name")) -> tuple[Path, Path]:
    """FastAPI dependency: resolve KB name → (corpus_path, kb_path)."""
    from src.db.registry import get_kb_path, open_registry

    try:
        reg = open_registry(_registry_root)
        folder = get_kb_path(reg, kb)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return folder / "corpus.db", folder / "knowledge.db"
