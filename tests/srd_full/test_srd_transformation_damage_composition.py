"""The real named form attack survives regeneration over qualified damage aliases."""

from copy import deepcopy

import pytest
import yaml
from scripts import srd_transformation_spells as transformations

from tests import test_srd_action_budget as budgets
from tests import test_srd_spell_wave6_transformations as forms


@pytest.fixture(scope="module")
def qualified_form_package(tmp_path_factory):
    """Retain the actual no-Curse packet owners under their downstream qualified names."""
    text = (budgets.ROOT / "runtime/ruleset.yaml").read_text(encoding="utf-8")
    runtime = transformations.actor.defense._load_runtime(text)
    tools = runtime["tools"]
    for original, qualified in (
        ("apply_srd_damage", "apply_srd_qualifying_damage"),
        ("apply_srd_composite_damage", "apply_srd_qualifying_composite_damage"),
    ):
        tools[qualified] = deepcopy(tools[original])
    npc = tools["resolve_adjudicated_creature_attack"]
    for effects in transformations._walk_lists(npc["effects"]):
        for effect in effects:
            if effect.get("call") == "apply_srd_damage":
                effect["call"] = "apply_srd_qualifying_damage"
    aliased = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    regenerated = transformations.extend_runtime(aliased)
    generated = transformations.actor.defense._load_runtime(regenerated)
    # Exercise the actual source-owner consumer and leaf; other owners remain unchanged.
    for name in ("resolve_adjudicated_creature_attack", "apply_srd_form_attack_damage"):
        tools[name] = generated["tools"][name]
    packet = tools["apply_srd_form_attack_damage"]
    assert "apply_srd_qualifying_composite_damage" in str(packet["effects"])
    changed = {
        "project_srd_transformation": deepcopy(generated["tools"]["project_srd_transformation"])
    }
    transformations._compose_consumers(tools, changed)
    assert (
        changed["resolve_adjudicated_creature_attack"]
        == tools["resolve_adjudicated_creature_attack"]
    )
    package = tmp_path_factory.mktemp("qualified-form")
    (package / "runtime").mkdir(parents=True)
    (package / "runtime/ruleset.yaml").write_text(
        yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False), encoding="utf-8"
    )
    (package / "package-record.json").write_bytes(
        (budgets.ROOT / "package-record.json").read_bytes()
    )
    return package


@pytest.fixture
def qualified_form_runtime(qualified_form_package, monkeypatch):
    budgets._mechanic.cache_clear()
    package = qualified_form_package
    monkeypatch.setattr(budgets, "ROOT", package)
    yield
    budgets._mechanic.cache_clear()


@pytest.mark.parametrize(
    "seed,defense,temp_hp,armor",
    [
        (0, "none", 0, 1),
        (5, "none", 3, 1),
        (5, "poison_immune", 0, 1),
        (5, "piercing_resistant", 0, 1),
        (0, "none", 0, 30),
    ],
)
def test_regenerated_qualified_form_attack_keeps_typed_poison_and_one_lifecycle(
    qualified_form_runtime, monkeypatch, seed, defense, temp_hp, armor
):
    forms.test_source_poison_attack_has_one_damage_event_with_actual_critical_and_type_defenses(
        monkeypatch, seed, defense, temp_hp, armor
    )


def test_regenerated_qualified_scorpion_keeps_fixed_primary_and_critical_poison(
    qualified_form_runtime, monkeypatch
):
    forms.test_scorpion_critical_doubles_only_actual_poison_dice(monkeypatch)
