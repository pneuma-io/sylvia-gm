"""Retained full-SRD behavior through immutable baseline admission and actual public actions."""
import io,json,hashlib
from dataclasses import replace
from pathlib import Path
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand,AuthorityOwner,CampaignCreate
from sylvia_gm.domain.authority import ActionStatus,Participant,ParticipantValidationStatus,StateValue,WriterRole
from sylvia_gm.spatial import admit_spatial_scene
from tests.test_srd_spell_result_cards import _spell_case
from tests.lite_fixture_archive import archive_closed_database
SRD="sylvia.srd-lite.bg3"
ROOT=Path(__file__).resolve().parents[1]
BASELINE=ROOT.parent/'inputs/baseline.sylvia-gm-package'
assert hashlib.sha256(BASELINE.read_bytes()).hexdigest()=='70d48f4589984231989512cd5180e137f92a19fe800820085a2ddeabd7b53615'

def _create_spell_campaign(
    owner, spell, *, participant_overrides=None, entity_overrides=None,
    target_overrides=None,
):
    """Admit one synthetic caster through the installed package and exact character schema."""
    package = owner.install_package_archive(
        io.BytesIO(BASELINE.read_bytes()),
        install_id="casting",
    )
    record = json.loads((ROOT / "rulesets/srd-lite/package-record.json").read_text())
    artifact = next(a for a in record["participant_artifacts"] if a["kind"] == "player_character")
    exact = next(e for e in package.package_lock.entries if e.package_id == SRD)
    state, data, parameters = _spell_case(spell)
    data.update(
        species="human",
        background="sage",
        size_rank=1,
        saving_throw_proficiencies=["intelligence", "wisdom"],
        origin_feats=["magic_initiate"],
    )
    state[f"{SRD}.combat"].update(
        encounter_active=False,
        turn_active=False,
        active_actor=None,
        phase="exploration",
        action_available=False,
    )
    state[f"{SRD}.entities"]["guard"].update(hp=1, max_hp=100, temp_hp=0)
    state[f"{SRD}.entities"]["guard"].update(target_overrides or {})
    state[f"{SRD}.entities"]["other"].update(
        hp=1000,
        max_hp=1000,
        melee_reach=5,
        available_reactions=["opportunity_attack"],
    )
    for entity in state[f"{SRD}.entities"].values():
        entity["initiative_modifier"] = 0
    state[f"{SRD}.entities"]["hero"].update(melee_reach=5, initiative_modifier=20)
    data.update(participant_overrides or {})
    state[f"{SRD}.entities"]["hero"].update(entity_overrides or {})
    scene = {
        "format": 1,
        "map": {"id": "full-control", "revision": 0,
            "bounds": {"min": [-100, -100, -100], "max": [100, 100, 100]},
            "grid": {"cell_size": 5, "unit": "ft", "origin": [-100, -100]},
            "layers": [{"id": "world", "min_elevation": -100, "max_elevation": 100}]},
        "entities": [{"id": identity, "map_id": "full-control", "position": point,
            "footprint": {"kind": "box", "half_extents": [0.4, 0.4]}, "height": 6,
            "facing": [1, 0], "active": True, "movement": "blocking", "visibility": "opaque",
            "metadata": {"participant_ref": identity, "plane": "material", "lighting": "bright"}}
            for identity, point in {"hero": [0,0,0], "guard": [5,0,0], "other": [-5,0,0]}.items()],
        "geometry": [], "zones": [], "terrain": [],
    }
    admit_spatial_scene(scene)
    state.setdefault(f"{SRD}.state", {}).update(spatial_scene=scene)
    owner.create_campaign(
        CampaignCreate(
            campaign_id="casting",
            display_name="Public casting",
            writer=WriterRole.DM,
            package_lock=package.package_lock,
            turn_id="casting-turn",
            participants=(
                Participant(
                    participant_id="hero",
                    kind="player_character",
                    package_id=SRD,
                    package_version=exact.version,
                    package_sha256=exact.sha256,
                    schema_id=artifact["schema_id"],
                    schema_sha256=artifact["schema_sha256"],
                    validation_status=ParticipantValidationStatus.VALIDATED,
                    data=data,
                ),
            ),
            initial_state=tuple(
                StateValue(SRD, key.removeprefix(f"{SRD}."), value) for key, value in state.items()
            ),
        )
    )
    return state, data, parameters


def test_immutable_full_srd_public_longstrider_replay_reopen_and_normal_expiry(tmp_path):
    try:
        database = tmp_path / "lasting-speed.db"
        with AuthorityOwner.open(database, "casting-owner") as owner:
            _, _, parameters = _create_spell_campaign(owner, "longstrider")
            parameters["targets"] = ["guard"]
            command = ActionCommand(
                campaign_id="casting", action_id="longstrider", idempotency_key="longstrider",
                owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
                turn_id="casting-turn", package_id=SRD,
                tool_name="cast_spell_outside_combat", parameters=parameters,
            )
            mechanics = StatelessMcpMechanics(owner)
            receipt = mechanics.execute_action(command)
            assert receipt.status is ActionStatus.COMMITTED
            saved = owner.snapshot("casting")
            assert mechanics.execute_action(command) == receipt
            assert owner.snapshot("casting") == saved
            assert saved.state[f"{SRD}.entities"]["guard"]["speed"] == 40
        with AuthorityOwner.open(database, "casting-owner") as reopened:
            mechanics = StatelessMcpMechanics(reopened)
            assert reopened.snapshot("casting").state == saved.state
            elapsed = mechanics.execute_action(replace(
                command, action_id="elapsed", idempotency_key="elapsed",
                expected_revision=saved.revision, tool_name="advance_srd_lasting_time",
                parameters={"elapsed_rounds": 600},
            ))
            assert elapsed.status is ActionStatus.COMMITTED
            assert reopened.snapshot("casting").state[f"{SRD}.entities"]["guard"]["speed"] == 30
    
    finally:
        for original in [database, *sorted((database.parent / "migration-sources").glob(database.stem + ".pre-migration-*.sqlite3"))]:
            if original.exists():
                archive_closed_database(original, database.parent)
