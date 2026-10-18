"""Admitted campaign proof that ending one source preserves another caster's expiry."""
from copy import deepcopy
from dataclasses import replace
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import CampaignCreate
from sylvia_gm.domain.authority import StateValue, WriterRole, owned_payload, thaw_json_value
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_foundation import Case, VARIANT_ID


class MultiCasterCase(Case):
    def __init__(self, path, spell):
        self.campaign_id = "lite"
        super().__init__(path, spell)
        initial = self.snapshot()
        state = thaw_json_value(initial.state)
        original = initial.participants[0]
        data = thaw_json_value(original.data)
        data["name"] = "Independent companion"
        companion = replace(original, participant_id="companion", data=owned_payload(data))
        actors = state[VARIANT_ID + ".entities"]
        actors["companion"] = deepcopy(actors["hero"])
        actors["companion"]["name"] = "Independent companion"
        for identity in ("guard", "other", "extra"):
            actors[identity]["attitudes"]["companion"] = "friendly"
        scene = state[VARIANT_ID + ".state"]["spatial_scene"]
        proxy = deepcopy(next(row for row in scene["entities"] if row["id"] == "hero"))
        proxy.update(id="companion", position=[-1, 0, 0])
        proxy["metadata"]["participant_ref"] = "companion"
        scene["entities"].append(proxy)
        assert all(key.startswith(VARIANT_ID + ".") for key in state)
        self.owner.create_campaign(CampaignCreate(
            campaign_id="lite-multicasters", display_name="Independent Lite concentration proof",
            writer=WriterRole.DM, package_lock=initial.package_lock, turn_id="turn",
            participants=(original, companion),
            initial_state=tuple(StateValue(VARIANT_ID, key.removeprefix(VARIANT_ID + "."), value) for key, value in state.items()),
        ))
        self.campaign_id = "lite-multicasters"

    def snapshot(self):
        return self.owner.snapshot(self.campaign_id)

    def command(self, tool, parameters, identity=None):
        return replace(super().command(tool, parameters, identity), campaign_id=self.campaign_id)


@pytest.fixture
def multi_case(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    opened = []

    def make(spell):
        case = MultiCasterCase(tmp_path / f"{len(opened)}.db", spell)
        opened.append(case)
        return case

    yield make
    for case in opened:
        archive_closed_case(case)


@pytest.mark.parametrize("spell,rounds", [("true_strike", 2), ("bless", 10), ("cloud_of_daggers", 10)])
def test_independent_caster_survives_other_source_end_then_expires_and_reopens(multi_case, spell, rounds):
    case = multi_case(spell)
    first = {"target": "guard"}
    second = {"caster": "companion", "caster_state": "companion", "target": "other"}
    if spell == "bless":
        first["targets"] = ["guard"]
        second["targets"] = ["other"]
    if spell == "cloud_of_daggers":
        first["environment_origin"] = [30, 30, 3]
        second["environment_origin"] = [40, 30, 3]
    expected_resources = dict(case.entities()["companion"]["resources"])
    if spell != "true_strike":
        expected_resources[f"spell_slot_{2 if spell == 'cloud_of_daggers' else 1}"] -= 1
    case.cast(**first)
    case.cast(**second)
    key = "lite_true_strike_casters" if spell == "true_strike" else "lite_concentration_casters"
    assert case.lasting()[key] == ["hero", "companion"]
    assert case.entities()["hero"]["concentration_spell_id"] == spell
    assert case.entities()["companion"]["concentration_spell_id"] == spell
    if spell == "cloud_of_daggers":
        cloud_sources = {identity: source["caster"] for identity, source in case.lasting()["persistent_spell_areas"].items() if source["spell"] == spell}
        assert set(cloud_sources.values()) == {"hero", "companion"}
    receipt, command = case.act("end_concentration", caster_state="hero")
    assert case.lasting()[key] == ["companion"]
    assert not case.entities()["hero"].get("concentration_spell_id")
    assert case.entities()["companion"]["concentration_spell_id"] == spell
    if spell == "cloud_of_daggers":
        assert all(case.lasting()["persistent_spell_areas"][identity]["active"] is (caster == "companion") for identity, caster in cloud_sources.items())
    before = case.snapshot()
    assert case.execute(command) == receipt and case.snapshot().state == before.state
    case.reopen()
    assert case.execute(command) == receipt and case.snapshot().state == before.state
    assert case.lasting()[key] == ["companion"]
    case.act("advance_srd_lasting_time", elapsed_rounds=rounds)
    assert case.lasting()[key] == []
    assert not case.entities()["companion"].get("concentration_spell_id")
    assert case.entities()["companion"]["resources"] == expected_resources
    if spell == "true_strike":
        assert case.entities()["companion"].get("lite_true_strike") is None
    if spell == "cloud_of_daggers":
        assert all(case.lasting()["persistent_spell_areas"][identity]["active"] is False for identity in cloud_sources)
    after = case.snapshot()
    case.reopen()
    assert case.snapshot().state == after.state and case.snapshot().package_lock == before.package_lock
