"""Environment regeneration preserves literal constraints and genuine Silence checks."""

from pathlib import Path

from scripts import srd_spell_environment as environment
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime


def test_regeneration_preserves_boolean_requirements_and_verbal_source_guards():
    source = (
        Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    ).read_text(encoding="utf-8")
    assert source.count("\nprocedures:") == 1
    authored = """
  environment_literal_constraint_probe:
    parameters: []
    effects:
      - require:
          condition: true
          error: Retained literal true.
      - condition: 'true'
        on_true:
          - require:
              condition: false
              error: Retained nested literal false.
"""
    source = source.replace("\nprocedures:", authored + "\nprocedures:", 1)
    original = _load_runtime(source)["tools"]["environment_literal_constraint_probe"]
    emitted = _load_runtime(environment.extend_runtime(source))["tools"]
    assert emitted["environment_literal_constraint_probe"] == original
    assert original["effects"][0]["require"]["condition"] is True
    assert original["effects"][1]["on_true"][0]["require"]["condition"] is False
    silence_error = "Silence makes a Verbal component impossible here."
    assert any(
        effect.get("require", {}).get("error") == silence_error
        for branch in environment._effect_lists(emitted["cast_spell"]["effects"])
        for effect in branch
    )
