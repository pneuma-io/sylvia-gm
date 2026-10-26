"""Repeated observer generation preserves the real Search check and its saved target DC."""

from functools import lru_cache
from pathlib import Path

from scripts.srd_full.srd_divination_continuation import _observer_consumers
from scripts.srd_full.srd_divination_spells import _normalize
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm.application import package_runtime
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_spell_wave4_divination import _case
from tests.srd_full.test_srd_spell_wave7_divination import SRD, _search_turn

ROOT = Path(__file__).resolve().parents[2]


def test_repeat_observer_generation_executes_one_real_search_against_saved_hide_dc(monkeypatch):
    text = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text(encoding="utf-8")
    tools = _load_runtime(text)["tools"]
    previous = None
    for _ in range(3):
        changed = {}
        _observer_consumers(changed, tools)
        _normalize(changed)
        search = changed["search_srd_action"]
        rolls = [effect for effect in search["effects"] if effect.get("call") == "d20_test"]
        assert len(rolls) == 1
        assert rolls[0]["params"]["target_number"] == "{_search_divination_dc}"
        if previous is not None:
            assert search == previous
        previous = search
        tools.update(changed)
        text = _replace_tools(text, changed)
    raw = text.encode()

    @lru_cache
    def mechanic(name):
        return materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="a" * 64,
            runtime_bytes=raw,
            declaration=ToolDeclaration(
                name=name,
                capability="dnd.srd.5.2.1.action-economy",
                input_schema=owned_payload(budget._tool_schema(tools[name], tool_name=name)),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    original = package_runtime.execute_tool
    observed = []

    def execute(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0] == "d20_test" and result.success:
            observed.append(dict(result.context))
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", execute)
    state, data, _ = _case("mind_spike")
    state[f"{SRD}.entities"]["guard"].update(
        hidden=True, hidden_check_total=80, conditions=["invisible"]
    )
    _search_turn(state)
    budget._run(
        "search_srd_action",
        state,
        participant_data=data,
        actor="hero",
        actor_state="hero",
        target_state="guard",
        skill="perception",
        target_number=1,
        proficiency_factor="none",
        circumstantial_modifier=0,
        advantage=False,
        disadvantage=False,
    )
    assert len(observed) == 1
    assert observed[0]["_target_number"] == 80
    assert observed[0]["_d20_test"].sides == 20
    assert observed[0]["_outcome"] == "failure"
    target = state[f"{SRD}.entities"]["hero"]["last_search_target"]
    assert target["hidden_from_observer"] is True
    assert target["location_known"] is False and target["position"] is None
    assert state[f"{SRD}.combat"]["action_available"] is False
