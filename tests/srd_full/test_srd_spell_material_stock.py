"""Material payment changes quantity without erasing another component's source facts."""

import os
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_spell_materials import _materials_case


def test_real_material_payment_preserves_attuned_fork_and_ordinary_quantity(monkeypatch):
    parent = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-spell-material-stock/retained-native-parent.yaml")
    ).read_bytes()
    assert sha256(parent).hexdigest() == (
        "fe066d1251fb874d6cb62f0fcb8eb925d4db40d56df0a3c8a8831158ce2624bc"
    )
    text = parent.decode()
    if not os.environ.get("FC14_MATERIAL_STOCK_UNPATCHED"):
        from scripts.srd_full.srd_spell_material_stock import bind_material_stock

        text = bind_material_stock(text)
        assert bind_material_stock(text) == text
    runtime = _load_runtime(text)
    name = "resolve_srd_spell_materials"
    mechanic = materialize_package_mechanic(
        package_id=budget.SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=text.encode(),
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )
    monkeypatch.setattr(budget, "_mechanic", lambda _name: mechanic)
    state, _, _ = _materials_case()
    hero = state[f"{budget.SRD}.entities"]["hero"]
    fork = {
        "id": "fork",
        "inventory_item": "metal_fork",
        "kind": "specific",
        "components": ["plane_shift.1"],
        "quantity": 1,
        "value_cp": 25000,
        "attuned_plane": "material",
    }
    hero["inventory"].append("metal_fork")
    hero["spell_materials"].append(deepcopy(fork))
    dust = deepcopy(hero["spell_materials"][0])
    values = {
        "caster_state": "hero",
        "spell": "stoneskin",
        "item_ids": ["dust"],
        "materials_available": True,
        "components_waived": False,
        "target_count": 1,
        "permitted_focus_kinds": [],
    }
    before = deepcopy(state)
    budget._run(name, state, **values, validate_only=True)
    assert state == before
    budget._run(name, state, **values)
    assert hero is not state[f"{budget.SRD}.entities"]["hero"]
    hero = state[f"{budget.SRD}.entities"]["hero"]
    assert hero["spell_materials"] == [dust | {"quantity": 1}, fork]
    assert hero["inventory"] == ["diamond_dust", "metal_fork"]
    budget._run(name, state, **values)
    hero = state[f"{budget.SRD}.entities"]["hero"]
    assert hero["spell_materials"] == [dust | {"quantity": 0}, fork]
    assert hero["inventory"] == ["metal_fork"]
