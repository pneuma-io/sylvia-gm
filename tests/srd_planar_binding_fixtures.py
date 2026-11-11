"""Planar Binding commits actual long casting and retained-source obedience."""

import json
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_control_binding import DURATION_ROUNDS, PUBLIC_TOOLS, extend_runtime

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.test_srd_extended_casting import _begin
from tests.srd_illusion_removals_fixtures import _prepare
from tests.test_srd_npc_spell_casting import _act
from tests.srd_shared_source_adoption_fixtures import _case as _source_case

SRD = cards.SRD


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    root = os.environ.get("FC14_BINDING_RUNTIME_ROOT")
    if root:
        monkeypatch.setattr(budget, "ROOT", Path(root))
    budget._mechanic.cache_clear()
    monkeypatch.setattr(
        cards,
        "_spell_definitions",
        lambda: budget._runtime_definition((budget.ROOT / "runtime/ruleset.yaml").read_bytes())[
            "definitions"
        ]["spells"],
    )
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    yield
    budget._mechanic.cache_clear()


def _case(slot=5, creature_type="fiend"):
    state, data, params = _source_case("blink")
    params.update(
        spell="planar_binding",
        target="guard",
        slot_resource=f"spell_slot_{slot}",
        material_item_ids=_prepare(state, data, "planar_binding"),
    )
    state[f"{SRD}.entities"]["guard"].update(creature_type=creature_type, languages=["Common"])
    state[f"{SRD}.entities"]["hero"]["languages"] = ["Common"]
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["charisma"] = -100
    return state, data, params


def _start(state, data, params):
    return budget._run(
        "begin_srd_spell_cast", state, participant_data=data, **_begin(params), target="guard"
    )


def _finish(state, data, params):
    budget._run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        elapsed_rounds=600,
        uninterrupted=True,
    )
    return budget._run("cast_spell_outside_combat", state, participant_data=data, **params)


def _source(state):
    return next(
        {**row, "id": source_id}
        for source_id, row in state[f"{SRD}.state"]["timed_condition_sources"].items()
        if row["spell_id"] == "planar_binding"
    )


























def _archive():
    primary, reference = bundled_packages._srd_drafts()
    content = (budget.ROOT / "runtime/ruleset.yaml").read_bytes()
    runtime = yaml.load(content, Loader=yaml.CSafeLoader)
    record = deepcopy(primary.record)
    for declaration in record["tools"]:
        declaration["input_schema"] = _tool_schema(
            runtime["tools"][declaration["name"]], tool_name=declaration["name"]
        )
    sample = next(d for d in record["tools"] if d["name"] == "issue_srd_dominated_order")
    for name in PUBLIC_TOOLS:
        if not any(d["name"] == name for d in record["tools"]):
            declaration = deepcopy(sample)
            declaration.update(
                name=name, input_schema=_tool_schema(runtime["tools"][name], tool_name=name)
            )
            record["tools"].append(declaration)
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": content}
    declaration_path = record["content_declarations"]
    content_declarations = json.loads(payloads[declaration_path])
    links = next(
        row["links"]["tools"]
        for row in content_declarations["inclusions"]
        if "issue_srd_dominated_order" in row["links"].get("tools", [])
    )
    links.extend(sorted(PUBLIC_TOOLS - set(links)))
    links.sort()
    payloads[declaration_path] = canonical_json_bytes(content_declarations)
    return bundled_packages._archive(
        (NormalizedPackageDraft(record, payloads, primary.format_version), reference)
    )
