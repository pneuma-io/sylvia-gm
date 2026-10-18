"""Honest imported source/item fixtures built through public CampaignCreate.

Constructor derived from the frozen foundation fixture; runtime commands remain Case.
No engine monkeypatch, private effect call or post-creation source injection.
"""
import io, json, zipfile
from copy import deepcopy
from tests.test_srd_lite_foundation import Case, ARCHIVE, FULL, VARIANT_ID
from tests.test_srd_attack_budget import _combat_case
from sylvia_gm.application.authority import CampaignCreate
from sylvia_gm.domain.authority import Participant, ParticipantValidationStatus, StateValue, WriterRole

# Frozen constructor source SHA256: 39bda4a78bcdafc2c6ffc24b2f749e5544f99ceef09a48deace06e833d9f0570
class ImportedCase(Case):
    def __init__(self, path, spell, *, guard_ac=1, positions=None, entity_changes=None, participant_changes=None, initial_lasting_changes=None, scene_objects=(), scene_metadata_changes=None, scene_zones=(), scene_geometry=(), archive_bytes=None):
        self.path = path
        self.manager = None
        selected_archive = ARCHIVE.read_bytes() if archive_bytes is None else archive_bytes
        with zipfile.ZipFile(io.BytesIO(selected_archive)) as package:
            self.record = next(
                row
                for row in json.loads(package.read("manifest.json"))["packages"]
                if row["id"] == VARIANT_ID
            )
            inventory = json.loads(
                package.read("packages/" + VARIANT_ID + "/content/spell-status-inventory.json")
            )
            header = next(
                row["casting_requirements"]
                for row in inventory["spells"]
                if row["identity"] == spell
            )
        source = "cleric" if spell in {"bless", "cure_wounds", "healing_word", "inflict_wounds", "guiding_bolt"} else "wizard"
        ability = "wisdom" if source == "cleric" else "intelligence"
        state, data = _combat_case([{"id": source, "level": 17}])
        data.update(
            name="Lite caster",
            species="human",
            background="sage",
            size_rank=1,
            saving_throw_proficiencies=["intelligence", "wisdom"],
            origin_feats=["magic_initiate"],
        )
        data["abilities"][ability] = 18
        data["spellcasting_grants"] = [{"source": source, "ability": ability, "spells": [spell]}]
        if source == "wizard":
            data["wizard_spellbook"] = [spell] if header["level"] else []
        data.update(deepcopy(participant_changes or {}))
        entities = state[FULL + ".entities"]
        entities["hero"].update(
            name="Lite caster",
            prepared_spells=[spell],
            spell_slot_cast_this_turn=False,
            resources={f"spell_slot_{level}": 3 for level in range(1, 10)},
            initiative_modifier=20,
            melee_reach=5,
            ammunition={},
            spell_materials=[],
            constitution_modifier=2,
        )
        for identity in ("guard", "other", "extra"):
            entities[identity] = deepcopy(entities["guard"])
            entities[identity].update(
                name=identity,
                hp=1000,
                max_hp=1000,
                temp_hp=0,
                speed=30,
                armor_class=guard_ac,
                creature_type="humanoid",
                melee_reach=5,
                initiative_modifier=0,
                available_reactions=["opportunity_attack"],
                reaction_available=True,
                saving_throw_modifiers=dict.fromkeys(data["abilities"], 0),
                attitudes={"hero": "friendly"},
                stable=False,
                death_successes=0,
                death_failures=0,
                stable_recovery_hours_remaining=0,
            )
        materials = [
            {
                "id": f"material_{index}",
                "inventory_item": f"{spell}_material_{index}",
                "kind": "specific",
                "components": [part["id"]],
                "quantity": part["quantity"] * 3,
                "value_cp": part["minimum_value_cp"],
            }
            for index, part in enumerate(header["material_parts"], 1)
        ]
        entities["hero"]["spell_materials"] = materials
        entities["hero"]["inventory"] += [item["inventory_item"] for item in materials]
        for identity, changes in (entity_changes or {}).items():
            entities[identity].update(changes)
        state[FULL + ".combat"].update(
            encounter_active=False,
            turn_active=False,
            active_actor=None,
            phase="exploration",
            action_available=False,
        )
        positions = positions or {
            "hero": [0, 0, 0],
            "guard": [5, 0, 0],
            "other": [-5, 0, 0],
            "extra": [20, 10, 0],
        }
        scene = {
            "format": 1,
            "map": {
                "id": "lite",
                "revision": 0,
                "bounds": {"min": [-100, -100, -100], "max": [100, 100, 100]},
                "grid": {"cell_size": 5, "unit": "ft", "origin": [-100, -100]},
                "layers": [{"id": "world", "min_elevation": -100, "max_elevation": 100}],
            },
            "entities": [
                {
                    "id": identity,
                    "map_id": "lite",
                    "position": point,
                    "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
                    "height": 6,
                    "facing": [1, 0],
                    "active": True,
                    "movement": "blocking",
                    "visibility": "opaque",
                    "metadata": {
                        "participant_ref": identity,
                        "plane": "material",
                        "lighting": "bright",
                        "creature_type": "humanoid",
                    },
                }
                for identity, point in positions.items()
            ],
            "geometry": [],
            "zones": [],
            "terrain": [],
        }
        state[FULL + ".state"] = {"lasting_tick": 0, "turn_effects": [], "spatial_scene": scene}
        self.parameters = {
            "spell": spell,
            "caster": "hero",
            "caster_state": "hero",
            "target": "guard",
            "slot_resource": f"spell_slot_{header['level']}" if header["level"] else "cantrip",
            "target_distance": 5,
            "caster_can_see_target": True,
            "caster_can_hear_target": True,
            "caster_can_touch_target": True,
            "target_willing": True,
            "cover": "none",
            "targets_max_distance": 25,
            "caster_can_see_all_targets": True,
            "all_targets_have_clear_path": True,
            "all_targets_willing": True,
            "caster_can_touch_all_targets": True,
            "origin_distance": 0,
            "caster_can_see_origin": True,
            "origin_has_clear_path": True,
            "all_attack_targets_have_clear_path": True,
            "verbal_component_available": True,
            "somatic_component_available": True,
            "material_component_available": True,
            "material_item_ids": [item["id"] for item in materials],
        }
        # Explicit imported fixture facts, applied only before public campaign creation.
        state[FULL + '.state'].update(deepcopy(initial_lasting_changes or {}))
        for row in state[FULL + '.state']['spatial_scene']['entities']:
            row['metadata'].update(deepcopy((scene_metadata_changes or {}).get(row['id'], {})))
        state[FULL + '.state']['spatial_scene']['entities'].extend(deepcopy(scene_objects))
        state[FULL + '.state']['spatial_scene']['zones'].extend(deepcopy(scene_zones))
        state[FULL + '.state']['spatial_scene']['geometry'].extend(deepcopy(scene_geometry))
        from tests.admitted_library_fixture import prepare_library_database, CANDIDATE_ID
        prepare_library_database(self.path, selected_archive)
        self.open()
        installed = self.owner.package_candidate(CANDIDATE_ID)
        exact = next(row for row in installed.package_lock.entries if row.package_id == VARIANT_ID)
        artifact = self.record["participant_artifacts"][0]
        self.owner.create_campaign(
            CampaignCreate(
                campaign_id="lite",
                display_name="SRD Lite foundation proof",
                writer=WriterRole.DM,
                package_lock=installed.package_lock,
                turn_id="turn",
                participants=(
                    Participant(
                        participant_id="hero",
                        kind="player_character",
                        package_id=VARIANT_ID,
                        package_version=exact.version,
                        package_sha256=exact.sha256,
                        schema_id=artifact["schema_id"],
                        schema_sha256=artifact["schema_sha256"],
                        validation_status=ParticipantValidationStatus.VALIDATED,
                        data=data,
                    ),
                ),
                initial_state=tuple(
                    StateValue(VARIANT_ID, key.removeprefix(FULL + "."), value)
                    for key, value in state.items()
                ),
            )
        )
