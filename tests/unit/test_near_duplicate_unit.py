"""Unit tests for KB.P20 Near-Duplicate Grouping — _group_near_duplicates."""
import sqlite3


def _make_row(file_id: int, path: str, phash_int: int, score: float | None):
    """Build a sqlite3.Row-like dict usable by _group_near_duplicates."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (id INT, path TEXT, phash TEXT, score REAL)")
    phash_hex = format(phash_int, "016x")
    conn.execute("INSERT INTO t VALUES (?,?,?,?)", (file_id, path, phash_hex, score))
    return conn.execute("SELECT * FROM t").fetchone()


def _rows(*args):
    """args: list of (file_id, path, phash_int, score) tuples."""
    return [_make_row(*a) for a in args]


class TestGroupNearDuplicates:
    def test_empty_input_returns_empty(self):
        from src.stages.export import _group_near_duplicates
        assert _group_near_duplicates([], threshold=10) == []

    def test_identical_phashes_grouped(self):
        from src.stages.export import _group_near_duplicates
        rows = _rows((1, "/a.jpg", 0xFFFF000000000000, 7.0),
                     (2, "/b.jpg", 0xFFFF000000000000, 6.0))
        result = _group_near_duplicates(rows, threshold=10)
        assert len(result) == 2
        assert result[0]["group_id"] == result[1]["group_id"] == 1

    def test_distance_10_within_threshold(self):
        from src.stages.export import _group_near_duplicates
        base = 0xFFFF000000000000
        # Flip 10 bits: XOR with a value that has 10 set bits
        flipped = base ^ ((1 << 10) - 1)
        rows = _rows((1, "/a.jpg", base, 7.0),
                     (2, "/b.jpg", flipped, 6.0))
        result = _group_near_duplicates(rows, threshold=10)
        assert len(result) == 2
        assert result[0]["group_id"] == 1

    def test_distance_11_above_threshold_no_group(self):
        from src.stages.export import _group_near_duplicates
        base = 0xFFFF000000000000
        flipped = base ^ ((1 << 11) - 1)
        rows = _rows((1, "/a.jpg", base, 7.0),
                     (2, "/b.jpg", flipped, 6.0))
        result = _group_near_duplicates(rows, threshold=10)
        assert result == []

    def test_three_files_two_dups_one_outlier(self):
        from src.stages.export import _group_near_duplicates
        base = 0x0000000000000000
        near = base ^ 1          # distance 1 from base
        far  = 0xFFFFFFFFFFFFFFFF  # distance 64 from base
        rows = _rows((1, "/a.jpg", base, 8.0),
                     (2, "/b.jpg", near, 7.0),
                     (3, "/c.jpg", far,  6.0))
        result = _group_near_duplicates(rows, threshold=10)
        paths_in_result = {r["path"] for r in result}
        assert "/a.jpg" in paths_in_result
        assert "/b.jpg" in paths_in_result
        assert "/c.jpg" not in paths_in_result

    def test_singleton_not_written(self):
        from src.stages.export import _group_near_duplicates
        rows = _rows((1, "/a.jpg", 0xAAAAAAAAAAAAAAAA, 7.0))
        result = _group_near_duplicates(rows, threshold=10)
        assert result == []

    def test_rank_1_is_highest_score(self):
        from src.stages.export import _group_near_duplicates
        base = 0x0000000000000000
        near = base ^ 1
        rows = _rows((1, "/low.jpg",  base, 5.0),
                     (2, "/high.jpg", near, 9.0))
        result = _group_near_duplicates(rows, threshold=10)
        rank1 = next(r for r in result if r["rank"] == 1)
        assert rank1["path"] == "/high.jpg"

    def test_hamming_distance_zero_for_seed(self):
        from src.stages.export import _group_near_duplicates
        base = 0xF0F0F0F0F0F0F0F0
        near = base ^ 3
        rows = _rows((1, "/a.jpg", base, 8.0),
                     (2, "/b.jpg", near, 7.0))
        result = _group_near_duplicates(rows, threshold=10)
        rank1 = next(r for r in result if r["rank"] == 1)
        assert rank1["hamming_distance"] == 0

    def test_confidence_is_1_for_identical(self):
        from src.stages.export import _group_near_duplicates
        rows = _rows((1, "/a.jpg", 0xABCD, 7.0),
                     (2, "/b.jpg", 0xABCD, 6.0))
        result = _group_near_duplicates(rows, threshold=10)
        rank1 = next(r for r in result if r["rank"] == 1)
        assert rank1["confidence"] == 1.0
        assert rank1["hamming_distance"] == 0

    def test_confidence_formula_for_nonzero_distance(self):
        from src.stages.export import _group_near_duplicates
        base = 0x0000000000000000
        flipped = base ^ ((1 << 8) - 1)  # 8 bits set → distance 8
        rows = _rows((1, "/a.jpg", base, 7.0),
                     (2, "/b.jpg", flipped, 6.0))
        result = _group_near_duplicates(rows, threshold=10)
        # rank 1 is /a.jpg (higher score), rank 2 is /b.jpg (distance 8)
        rank2 = next(r for r in result if r["rank"] == 2)
        expected_conf = round(1.0 - 8 / 64, 4)
        assert rank2["confidence"] == expected_conf
        assert rank2["hamming_distance"] == 8

    def test_group_ids_increment(self):
        from src.stages.export import _group_near_duplicates
        # Two separate groups, well separated from each other
        rows = _rows(
            (1, "/a.jpg", 0x0000000000000000, 8.0),
            (2, "/b.jpg", 0x0000000000000001, 7.0),  # near /a
            (3, "/c.jpg", 0xFFFFFFFFFFFFFFFF, 6.0),
            (4, "/d.jpg", 0xFFFFFFFFFFFFFFFE, 5.0),  # near /c
        )
        result = _group_near_duplicates(rows, threshold=10)
        group_ids = sorted({r["group_id"] for r in result})
        assert group_ids == [1, 2]

    def test_threshold_zero_only_identical_grouped(self):
        from src.stages.export import _group_near_duplicates
        rows = _rows((1, "/a.jpg", 0xAAAA, 7.0),
                     (2, "/b.jpg", 0xAAAA, 6.0),
                     (3, "/c.jpg", 0xAAAB, 5.0))  # distance 1 from a
        result = _group_near_duplicates(rows, threshold=0)
        assert len(result) == 2  # only /a and /b grouped
        paths = {r["path"] for r in result}
        assert "/c.jpg" not in paths

    def test_nima_score_none_when_zero_score(self):
        from src.stages.export import _group_near_duplicates
        rows = _rows((1, "/a.jpg", 0xAAAA, None),
                     (2, "/b.jpg", 0xAAAA, None))
        result = _group_near_duplicates(rows, threshold=10)
        assert all(r["nima_score"] is None for r in result)
