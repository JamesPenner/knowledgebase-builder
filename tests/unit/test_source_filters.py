from pathlib import Path

from src.stages.ingest import apply_source_filters


def _paths(*names: str) -> list[Path]:
    return [Path(n) for n in names]


def test_empty_filters_is_noop():
    files = _paths("a.jpg", "b.png", "c.mp4")
    result = apply_source_filters(files, {})
    assert result == files


def test_glob_filter_matches_pattern():
    files = _paths("2024-01.jpg", "2024-02.jpg", "2023-01.jpg", "notes.txt")
    result = apply_source_filters(files, {"glob": "2024-*"})
    assert [f.name for f in result] == ["2024-01.jpg", "2024-02.jpg"]


def test_glob_filter_no_match():
    files = _paths("a.jpg", "b.jpg")
    result = apply_source_filters(files, {"glob": "2024-*"})
    assert result == []


def test_count_limit_truncates():
    files = _paths("a.jpg", "b.jpg", "c.jpg", "d.jpg")
    result = apply_source_filters(files, {"count_limit": 2})
    assert len(result) == 2
    assert result[0].name == "a.jpg"


def test_glob_then_count_limit():
    files = _paths("2024-01.jpg", "2024-02.jpg", "2024-03.jpg", "2023-01.jpg")
    result = apply_source_filters(files, {"glob": "2024-*", "count_limit": 2})
    assert len(result) == 2
    assert all(f.name.startswith("2024-") for f in result)


def test_count_limit_zero_gives_empty():
    files = _paths("a.jpg", "b.jpg")
    result = apply_source_filters(files, {"count_limit": 0})
    assert result == []


def test_unknown_key_ignored():
    files = _paths("a.jpg", "b.jpg")
    result = apply_source_filters(files, {"min_size": 1000, "unknown": "value"})
    assert result == files


def test_empty_file_list():
    result = apply_source_filters([], {"glob": "*.jpg", "count_limit": 10})
    assert result == []
