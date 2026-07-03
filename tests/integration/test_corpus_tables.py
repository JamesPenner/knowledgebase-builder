"""Verify _CORPUS_DERIVED_TABLES coverage and remove_source/reset_corpus_files divergence."""

from src.db.corpus import _CORPUS_DERIVED_TABLES


def test_derived_tables_excludes_file_set_members():
    assert "file_set_members" not in _CORPUS_DERIVED_TABLES, (
        "file_set_members must NOT be in _CORPUS_DERIVED_TABLES — "
        "set membership survives source removal"
    )


def test_reset_corpus_includes_file_set_members(tmp_path):
    """reset_corpus_files should clear file_set_members; remove_source (cascade) should not."""
    import inspect
    from src.db.corpus import reset_corpus_files, remove_source

    reset_src = inspect.getsource(reset_corpus_files)
    remove_src = inspect.getsource(remove_source)

    assert "file_set_members" in reset_src, (
        "reset_corpus_files must include file_set_members in its table list"
    )
    assert "file_set_members" not in remove_src, (
        "remove_source (cascade) must NOT clear file_set_members"
    )


def test_derived_tables_not_empty():
    assert len(_CORPUS_DERIVED_TABLES) >= 20
