import pytest

from src.pipeline.dag import resolve_plan


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
