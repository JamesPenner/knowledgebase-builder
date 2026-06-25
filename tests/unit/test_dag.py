import pytest

from src.pipeline.dag import (
    DEPENDENCIES,
    INVALIDATES,
    STAGE_DESCRIPTIONS,
    STAGE_GROUPS,
    resolve_plan,
)


def _stages(plan: list) -> list[str]:
    return [s for s in plan if isinstance(s, str)]


def _touchpoints(plan: list) -> list[str]:
    return [s["touchpoint"] for s in plan if isinstance(s, dict)]


def test_ingest_plan_is_single_step():
    plan = resolve_plan("ingest", set())
    assert plan == ["ingest"]


def test_analyse_plan_includes_ingest():
    plan = resolve_plan("analyse", set())
    stages = _stages(plan)
    assert stages.index("ingest") < stages.index("analyse")


def test_touchpoint_inserted_before_normalize():
    plan = resolve_plan("normalize", set())
    touchpoints = _touchpoints(plan)
    assert "normalise_review" in touchpoints

    # touchpoint must appear before 'normalize' in the plan list
    tp_index = next(i for i, s in enumerate(plan) if isinstance(s, dict) and s["touchpoint"] == "normalise_review")
    stage_index = plan.index("normalize")
    assert tp_index < stage_index


def test_completed_stages_excluded_from_plan():
    plan = resolve_plan("normalize", completed={"ingest", "analyse"})
    stages = _stages(plan)
    assert "ingest" not in stages
    assert "analyse" not in stages
    assert "normalize" in stages


def test_completed_touchpoint_excluded_from_plan():
    plan = resolve_plan("normalize", completed={"ingest", "analyse", "normalise_review"})
    touchpoints = _touchpoints(plan)
    assert "normalise_review" not in touchpoints
    assert _stages(plan) == ["normalize"]


def test_suggest_plan_includes_full_chain():
    plan = resolve_plan("suggest", set())
    stages = _stages(plan)
    for expected in ("ingest", "analyse", "normalize", "hash", "describe", "transcribe", "suggest"):
        assert expected in stages


def test_unknown_stage_raises_value_error():
    with pytest.raises(ValueError, match="Unknown stage"):
        resolve_plan("nonexistent_stage", set())


# ---------------------------------------------------------------------------
# validate in DAG
# ---------------------------------------------------------------------------

def test_validate_in_dependencies():
    assert "validate" in DEPENDENCIES
    assert DEPENDENCIES["validate"] == ["hash"]


def test_validate_in_invalidates():
    assert "validate" in INVALIDATES
    assert INVALIDATES["validate"] == []


def test_resolve_plan_includes_validate_after_hash():
    plan = resolve_plan("validate", set())
    stages = _stages(plan)
    assert "hash" in stages
    assert "validate" in stages
    assert stages.index("hash") < stages.index("validate")


# ---------------------------------------------------------------------------
# STAGE_GROUPS
# ---------------------------------------------------------------------------

def _all_grouped_stages() -> list[str]:
    return [s for grp in STAGE_GROUPS for s in grp["stages"]]


def test_stage_groups_covers_all_dag_stages():
    grouped = set(_all_grouped_stages())
    for stage in DEPENDENCIES:
        assert stage in grouped, f"Stage {stage!r} missing from STAGE_GROUPS"


def test_stage_groups_no_duplicates():
    stages = _all_grouped_stages()
    assert len(stages) == len(set(stages))


def test_validate_in_metadata_group():
    metadata = next(g for g in STAGE_GROUPS if g["id"] == "metadata")
    assert "validate" in metadata["stages"]


def test_stage_groups_has_required_ids():
    ids = {g["id"] for g in STAGE_GROUPS}
    for expected in ("discovery", "metadata", "ml_analysis", "enrichment", "vocabulary", "output"):
        assert expected in ids


def test_stage_groups_each_has_required_keys():
    for grp in STAGE_GROUPS:
        for key in ("id", "label", "description", "stages"):
            assert key in grp, f"Group {grp.get('id')!r} missing key {key!r}"
        assert isinstance(grp["stages"], list)
        assert len(grp["stages"]) > 0


# ---------------------------------------------------------------------------
# STAGE_DESCRIPTIONS
# ---------------------------------------------------------------------------

def test_stage_descriptions_covers_all_stages():
    for stage in DEPENDENCIES:
        assert stage in STAGE_DESCRIPTIONS, f"Stage {stage!r} missing from STAGE_DESCRIPTIONS"
        assert isinstance(STAGE_DESCRIPTIONS[stage], str)
        assert len(STAGE_DESCRIPTIONS[stage]) > 10


def test_validate_description_present():
    assert "validate" in STAGE_DESCRIPTIONS
    desc = STAGE_DESCRIPTIONS["validate"]
    assert "exist" in desc.lower() or "change" in desc.lower()
