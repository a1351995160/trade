import json

from chanlun_trader.research_factory.promising_followup_scope import is_one_shot_followup


def test_governed_promising_followup_inherits_one_shot_policy(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "research_ai_design_policy.json").write_text(json.dumps({
        "policies": {"PROMISING_FOLLOWUP": "ONE_SHOT"},
        "objective_overrides": {},
    }), encoding="utf-8")
    objectives = tmp_path / "data" / "research" / "research_factory" / "objectives"
    objectives.mkdir(parents=True)
    objective_id = "RESEARCH_OBJECTIVE_NEW_FOLLOWUP"
    (objectives / f"{objective_id}.json").write_text(json.dumps({
        "objective_id": objective_id,
        "governance_action": "START_PROMISING_FOLLOWUP_OBJECTIVE",
    }), encoding="utf-8")

    assert is_one_shot_followup(tmp_path, objective_id) is True


def test_explicit_objective_override_has_priority(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    objective_id = "RESEARCH_OBJECTIVE_OVERRIDE"
    (config / "research_ai_design_policy.json").write_text(json.dumps({
        "policies": {"PROMISING_FOLLOWUP": "ONE_SHOT"},
        "objective_overrides": {objective_id: "ITERATIVE"},
    }), encoding="utf-8")

    assert is_one_shot_followup(tmp_path, objective_id) is False
