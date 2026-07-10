"""Unit tests for ClusterAssignment and write_cluster_csv (KB.AJ1)."""
import csv

from src.pipeline.clusters import ClusterAssignment, write_cluster_csv


def test_cluster_assignment_defaults():
    a = ClusterAssignment(file_path="a.jpg", person_id=None, score=0.5)
    assert a.cluster_id is None
    assert a.extra == {}


def test_cluster_assignment_is_frozen():
    a = ClusterAssignment(file_path="a.jpg", person_id=1, score=0.9)
    try:
        a.person_id = 2
        assert False, "expected FrozenInstanceError"
    except AttributeError:
        pass


def test_write_cluster_csv_writes_header_and_rows(tmp_path):
    assignments = [
        ClusterAssignment(file_path="a.jpg", person_id=3, score=0.9, extra={"region_index": 0, "bbox": "[0,0,1,1]"}),
        ClusterAssignment(file_path="b.jpg", person_id=None, score=0.4, extra={"region_index": 1, "bbox": "[1,1,2,2]"}),
    ]
    out = tmp_path / "out.csv"
    write_cluster_csv(
        out,
        assignments,
        ["file_path", "region_index", "person_id", "similarity", "bbox"],
        lambda a: {
            "file_path": a.file_path,
            "region_index": a.extra["region_index"],
            "person_id": a.person_id,
            "similarity": a.score,
            "bbox": a.extra["bbox"],
        },
    )

    with open(out, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == ["file_path", "region_index", "person_id", "similarity", "bbox"]
        rows = list(reader)

    assert len(rows) == 2
    assert rows[0]["file_path"] == "a.jpg"
    assert rows[0]["person_id"] == "3"
    assert rows[1]["person_id"] == ""


def test_write_cluster_csv_empty_assignments_writes_header_only(tmp_path):
    out = tmp_path / "out.csv"
    write_cluster_csv(out, [], ["file_path", "person_id"], lambda a: {})
    with open(out, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == ["file_path", "person_id"]
        assert list(reader) == []
