"""Players cannot declare that combat has ended; the GM retains that engine operation."""

import pytest

from sylvia_gm.application.launch_compatibility import player_ui_surfaces
from sylvia_gm.domain.authority import PackageLock, PackageLockEntry, PackageRole, owned_payload
from sylvia_gm.domain.errors import CommandRejected
from tests.test_lite_panel_combat import NS, case, command, execute


@pytest.mark.parametrize("version", ["0.3.12", "0.3.14", "0.3.15"])
def test_withdrawal_preserves_legacy_pins_and_unrelated_ruleset_controls(version):
    legacy = (owned_payload({"id": "encounter", "actions": [
        {"id": "renamed-end", "label": "A different label", "intent": "end_combat_encounter"},
        {"id": "end-turn", "intent": "end_combat_turn"},
    ]}),)
    def lock(identity):
        return PackageLock((PackageLockEntry(identity, version, "a" * 64, PackageRole.PRIMARY,
                                             0, (), ()),))
    pinned = lock(NS)
    projected = player_ui_surfaces(legacy, pinned)
    assert [action["id"] for action in projected[0]["actions"]] == ["end-turn"]
    assert pinned.entries[0].version == version
    assert pinned.entries[0].sha256 == "a" * 64
    assert len(legacy[0]["actions"]) == 2
    assert player_ui_surfaces(legacy, lock("another.ruleset")) == legacy


def test_end_encounter_is_gm_only_and_player_rejection_preserves_damage_and_resources(tmp_path):
    value = case(tmp_path / "boundary.sqlite3", "magic_missile")
    try:
        execute(value, command(value, "cast-magic_missile", "spent-before-encounter-end"))
        before = value.snapshot()
        assert before.state[f"{NS}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert before.state[f"{NS}.entities"]["guard"]["hp"] < 1000
        panels = value.owner.campaign_panels("lite")
        assert not any(
            action["intent"] == "end_combat_encounter"
            for surface in panels["surfaces"] for action in surface["actions"]
        )
        assert "end_combat_encounter" in {tool.name for tool in value.owner.catalog("lite")}
        with pytest.raises(CommandRejected, match="unavailable"):
            value.owner.execute_panel_action(
                campaign_id="lite", surface_id="dnd-encounter", panel_action_id="end-combat",
                operation_id="withdrawn-player-end", expected_revision=before.revision,
            )
        assert value.snapshot() == before
        value.act("end_combat_encounter", end_reason="fighting_stopped")
        after = value.snapshot()
        assert after.state[f"{NS}.combat"]["encounter_active"] is False
        for identity, entity in before.state[f"{NS}.entities"].items():
            for field in ("hp", "temp_hp", "resources"):
                assert after.state[f"{NS}.entities"][identity].get(field) == entity.get(field)
        assert len(after.actions) == len(before.actions) + 1
        assert after.actions[-1].tool_name == "end_combat_encounter"
        assert after.actions[-1].parameters["end_reason"] == "fighting_stopped"
    finally:
        value.close()
