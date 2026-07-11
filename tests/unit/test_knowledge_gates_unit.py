"""Unit tests for src/pipeline/knowledge_gates.py — pure logic, no DB."""
from src.pipeline.knowledge_gates import (
    STAGE_REQUIRES,
    TAG_CATEGORY_REQUIRES,
    report_stage_skipped,
    stage_is_enabled,
    tag_category_is_enabled,
)


class _FakeProgress:
    def __init__(self):
        self.updates: list[tuple[int, int, str]] = []
        self.done_called = False

    def update(self, current, total, message=""):
        self.updates.append((current, total, message))

    def done(self):
        self.done_called = True


# ---------------------------------------------------------------------------
# stage_is_enabled
# ---------------------------------------------------------------------------

def test_stage_with_no_requirement_always_enabled():
    assert stage_is_enabled("describe", frozenset())
    assert stage_is_enabled("hash", frozenset({"people"}))


def test_gated_stage_enabled_when_requirement_met():
    assert stage_is_enabled("face", frozenset({"people", "places"}))


def test_gated_stage_disabled_when_requirement_unmet():
    assert not stage_is_enabled("face", frozenset({"places"}))
    assert not stage_is_enabled("face", frozenset())


def test_all_stage_requires_entries_are_single_category_except_none_multi():
    # Sanity check on the table itself: every gated stage in this sprint
    # requires exactly one category.
    for stage, required in STAGE_REQUIRES.items():
        assert len(required) == 1, stage


# ---------------------------------------------------------------------------
# tag_category_is_enabled
# ---------------------------------------------------------------------------

def test_tag_category_with_no_requirement_always_enabled():
    assert tag_category_is_enabled("technical", frozenset())
    assert tag_category_is_enabled("tonality", frozenset())


def test_calendar_tag_requires_dates():
    assert tag_category_is_enabled("calendar", frozenset({"dates"}))
    assert not tag_category_is_enabled("calendar", frozenset({"people", "places"}))


def test_life_event_tag_requires_people_and_dates():
    assert tag_category_is_enabled("life_event", frozenset({"people", "dates"}))
    assert not tag_category_is_enabled("life_event", frozenset({"people"}))
    assert not tag_category_is_enabled("life_event", frozenset({"dates"}))
    assert not tag_category_is_enabled("life_event", frozenset())


def test_tag_category_requires_table_matches_stage_requires_life_event():
    assert TAG_CATEGORY_REQUIRES["life_event"] == frozenset({"people", "dates"})


# ---------------------------------------------------------------------------
# report_stage_skipped
# ---------------------------------------------------------------------------

def test_report_stage_skipped_signals_progress_and_returns_dict():
    progress = _FakeProgress()
    result = report_stage_skipped(progress, "face", frozenset({"places"}))

    assert result["skipped"] is True
    assert "people" in result["skipped_reason"]
    assert result["files_processed"] == 0
    assert progress.done_called
    assert len(progress.updates) == 1
    assert "Skipped" in progress.updates[0][2]
