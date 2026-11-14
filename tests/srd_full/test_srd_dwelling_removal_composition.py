"""Actual dwelling casts retire through common removal and their saved return owners."""

import json
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_mansion_returns import bind_runtime_consumers as bind_mansion
from scripts.srd_full.srd_reaction_spells import normalize_pending_spell_guards
from scripts.srd_full.srd_rope_trick_returns import bind_runtime_consumers as bind_rope
from scripts.srd_full.srd_spell_recovery import extend_runtime as emit_recovery
from scripts.srd_full.srd_spell_removal_adoption import extend_tools as bind_removal
from scripts.srd_full.srd_spell_removals import _tools as removal_tools
from scripts.srd_full.srd_spell_source_instances import extend_runtime as emit_sources
from scripts.srd_full.srd_spell_travel import extend_runtime as emit_travel
from scripts.srd_full.srd_spell_travel_dwelling_lifetime import emit_runtime as emit_lifetime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from tests import test_srd_mansion_returns as mansion
from tests import test_srd_public_spell_casting as public
from tests import test_srd_rope_trick_returns as returns
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_rope as rope
from tests.srd_full.test_srd_spell_travel import SRD, _case, _point

BASE = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-dwelling-removal-composition/input-1"))


@pytest.fixture
def removal_scope(monkeypatch):
    monkeypatch.setattr(
        returns,
        "PATH",
        BASE / "fc14-spells-20260930-wave8/portals/.scratch/rope32-runtime-immutable/runtime.yaml",
    )
    monkeypatch.setattr(
        returns,
        "ACTUAL",
        BASE / "fc14-spells-20260930-wave27/build/rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml",
    )
    original = returns._runtime
    original.cache_clear()

    @lru_cache
    def composed():
        runtime, raw = original()
        ordinary_d20 = {name: tool for name, tool in runtime["tools"].items() if "d20" in name}
        assert ordinary_d20
        actual_raw = returns.ACTUAL.read_bytes()
        assert sha256(actual_raw).hexdigest() == returns.ACTUAL_SHA
        actual = _load_runtime(actual_raw)
        for name in mansion._closure(actual["tools"], {"study_srd_action"}):
            if name not in runtime["tools"]:
                runtime["tools"][name] = deepcopy(actual["tools"][name])
        # Retain admitted schema values in the generator's compact row layout.
        # A general YAML dump expands these rows past the source emitter's header.
        schemas = runtime.pop("definition_row_schemas")
        raw = (
            yaml.dump(runtime, Dumper=_RuntimeDumper, sort_keys=False)
            + "definition_row_schemas: "
            + json.dumps(schemas)
            + "\n"
        ).encode()
        text = emit_sources(
            emit_lifetime(emit_travel(emit_recovery(normalize_pending_spell_guards(raw.decode()))))
        )
        current = _load_runtime(text)
        changed = removal_tools(current["tools"])
        for name in ("cast_srd_recovery", "dispel_srd_target_sources"):
            changed[name] = deepcopy(current["tools"][name])
        bind_removal(current["tools"], changed)
        current["tools"].update(changed)
        # Production derives this public clone from the final paid cast owner.
        current["tools"]["cast_spell_outside_combat"] = deepcopy(current["tools"]["cast_spell"])
        text = bind_rope(bind_mansion(yaml.dump(current, Dumper=_RuntimeDumper, sort_keys=False)))
        changed = _load_runtime(text)
        assert {name: changed["tools"][name] for name in ordinary_d20} == ordinary_d20
        return changed, yaml.dump(changed, Dumper=_RuntimeDumper, sort_keys=False).encode()

    monkeypatch.setattr(returns, "_runtime", composed)
    yield from returns.returns_scope.__wrapped__(monkeypatch)
    original.cache_clear()


@pytest.mark.parametrize("spell", ["magnificent_mansion", "rope_trick"])
def test_paid_exact_dismissal_returns_actual_occupant_and_retries(
    removal_scope, tmp_path, monkeypatch, spell
):
    monkeypatch.setattr(
        public,
        "_spell_case",
        dwelling._mansion_case if spell == "magnificent_mansion" else returns._paid_case,
    )
    database = tmp_path / "dwelling-dismiss.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        source = (
            mansion._paid_inside(owner)
            if spell == "magnificent_mansion"
            else returns._enter_paid(owner)
        )
        before = owner.snapshot("casting")
        resources = before.state[f"{SRD}.entities"]["hero"]["resources"]
        receipt, command = returns._act(
            owner,
            "dismiss_srd_spell",
            {"caster_state": "hero", "cast_id": source["cast_id"]},
            "exact-dismiss",
        )
        saved = owner.snapshot("casting")
        current = next(
            row
            for row in saved.state[f"{SRD}.state"]["extradimensional_sources"]
            if row["id"] == source["id"]
        )
        assert current["active"] is False and current["return_pending"] is False
        body = rope._body(saved.state, "hero")
        assert body["metadata"]["plane"] == source["origin_plane"]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"] == resources
        if spell == "rope_trick":
            assert saved.state[f"{SRD}.entities"]["hero"]["scene_fall"]["status"] == "pending"
        else:
            assert _point(saved.state, "hero")[0] == pytest.approx(4.875)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        assert owner.snapshot("casting") == saved


def test_paid_dispel_inner_entrance_returns_rope_occupant_to_saved_fall(
    removal_scope, tmp_path, monkeypatch
):
    def prepared_case(spell):
        state, data, parameters = returns._paid_case(spell)
        hero = state[f"{SRD}.entities"]["hero"]
        hero["prepared_spells"].append("dispel_magic")
        data["wizard_spellbook"].append("dispel_magic")
        next(g for g in data["spellcasting_grants"] if g["source"] == "wizard")["spells"].append(
            "dispel_magic"
        )
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", prepared_case)
    database = tmp_path / "dwelling-dispel.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        source = returns._enter_paid(owner)
        before = owner.snapshot("casting")
        hero = before.state[f"{SRD}.entities"]["hero"]
        _, _, parameters = _case("dispel_magic")
        parameters.pop("target", None)
        parameters.update(
            recovery_choice="dwelling",
            recovery_source_id=source["id"],
            recovery_surface=source["inner_id"],
            slot_resource="spell_slot_3",
        )
        receipt, command = returns._act(
            owner, "cast_spell_outside_combat", parameters, "paid-dispel-door"
        )
        saved = owner.snapshot("casting")
        actor = saved.state[f"{SRD}.entities"]["hero"]
        assert actor["resources"]["spell_slot_3"] == hero["resources"]["spell_slot_3"] - 1
        current = rope._source(saved.state)
        assert current["active"] is False and current["return_pending"] is False
        assert rope._body(saved.state, "hero")["metadata"]["plane"] == source["origin_plane"]
        assert actor["scene_fall"]["status"] == "pending"
        assert actor["hp"] == hero["hp"] and actor["temp_hp"] == hero["temp_hp"]
        card = receipt.result["result_card"]
        assert (
            next(row["value"] for row in card["details"] if row["label"] == "Source")
            == source["id"]
        )
        assert "Actual Dispel check" in str(card)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        assert owner.snapshot("casting") == saved
