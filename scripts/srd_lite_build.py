"""Compose an owned Format4 variant through the existing package admission seam."""

import hashlib
import io
import json
import re
import zipfile
from copy import deepcopy
from pathlib import Path

import yaml

from sylvia_gm.application._yaml_bounds import validate_yaml_graph
from sylvia_gm.application.package_library import validate_admitted_package_runtimes
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.domain.package_library import (
    canonical_json_bytes,
    parse_content_declarations_bytes,
    validate_content_declaration_links,
)
from sylvia_gm.infrastructure.package_archive import admit_package_archive
from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations
from sylvia_gm.runtime._runtime import RulesetRuntime
from sylvia_gm.application.package_runtime import _runtime_parameter_schema

from .srd_lite_catalogue import SELECTED, VARIANT_ID, VARIANT_VERSION, build_catalogue
from .srd_lite_daggers import bind_daggers
from .srd_lite_healing import bind_healing
from .srd_lite_missiles import bind_missiles
from .srd_lite_false_life import bind_false_life
from .srd_lite_attacks import bind_attacks, bind_attack_facts
from .srd_lite_leveled_attack_facts import bind_leveled_attack_facts
from .srd_lite_death_counter_bounds import bind_death_counter_bounds
from .srd_lite_pending_player_preparations import bind_pending_player_preparations
from .srd_lite_range_victory import bind_range_and_victory
from .srd_lite_launch_damage import LAUNCH_DAMAGE, bind_launch_damage
from .srd_lite_launch_chromatic_orb import bind_launch_chromatic_orb
from .srd_launch_mark_observer import bind_srd_saved_hit_mark_observer
from .srd_launch_self_target import bind_owned_self_targets
from .srd_original_counterspell_dispatch import bind_original_counterspell_dispatch
from .srd_retained_weapon_supports import bind_retained_weapon_supports
from .srd_launch_control_reuse import PROFILE_IDS as CONTROL_PROFILE_IDS, validate_reused_profiles
from .srd_lite_retained_spell_actions import RETAINED_SPELL_ACTIONS, bind_retained_spell_actions
from .srd_lite_retained_observations import RETAINED_OBSERVATIONS, bind_retained_observations
from .srd_lite_offensive_cantrips import OFFENSIVE, bind_offensive_cantrips
from .srd_lite_offensive_statuses import bind_offensive_statuses
from .srd_lite_defenses import bind_defenses, bind_defense_public_tools
from .srd_lite_ray_reactions import bind_ray_reactions
from .srd_lite_areas import bind_areas
from .srd_lite_zones import bind_zones, profile_schema
from .srd_lite_faerie_zone import bind_faerie_zone
from .srd_lite_lightning_zone import bind_lightning_zone, expose_lightning_recast
from .srd_lite_insect_zone import bind_insect_zone
from .srd_lite_zone_cards import bind_zone_cards
from .srd_lite_utility_support import bind_utility_profiles, bind_utility_support, expose_utility_operations
from .srd_lite_travel_support import bind_travel_profiles, bind_travel_support
from .srd_lite_mobility_support import _bind_spell_identity
from .srd_lite_blade_ward_support import bind_blade_ward_support, bind_blade_ward_dismissal
from .build_srd_5_2_1_spells import _tool_schema
from .srd_lite_policy import (
    CAST_ROOTS,
    bind_boundary,
    bind_exposed_operations,
    rebind_owned_identity,
)
from .srd_lite_profiles import bind_profiles
from .srd_lite_progression_support import expose_original_stable_recovery
from .srd_lite_first_aid import expose_first_aid
from .srd_lite_retained_control_followups import bind_retained_control_followups, RETAINED_CONTROL_FOLLOWUPS
from .srd_parameter_declarations import compact_runtime
from .srd_runtime_defaults import compact_runtime_defaults, restore_tool_defaults
from .srd_lite_character_validation import compose_character_validation
from .srd_lite_reachable_tools import omit_unreachable_private_tools
from .srd_timed_actor_defenses import _RuntimeDumper
from .srd_lite_integrated_worker_repairs import bind_worker_repairs, bind_worker_attack_sight
from .srd_lite_concentration_cards import bind_spell_card_names
from .srd_scope96_guardian_entry import bind_guardian_movement_card
from .srd_lite_visual_rolls import bind_recorded_spell_roll_widgets
from .srd_lite_steed_healing_touch import expose_steed_healing_touch
from .srd_lite_steed_fell_glare import expose_steed_fell_glare
from .srd_lite_steed_fey_step import expose_steed_fey_step
from .srd_lite_steed_mount import MOUNT, DISMOUNT
from .srd_lite_steed_turns import bind_steed_turns
from .srd_lite_steed_rider_movement import bind_steed_rider_movement
from .srd_lite_steed_disappearance import bind_steed_disappearance
from .srd_lite_retained_optional_tail import bind_retained_optional_tail, bind_cast_willing_default
from .srd_lite_fall_card_names import bind_fall_card_names
from .srd_lite_misty_step_card import bind_misty_step_card
from .srd_lite_steed_cast_cards import bind_steed_cast_cards
from .srd_lite_steed_teleport_links import bind_steed_teleport_links
from .srd_lite_steed_balance import bind_steed_balance
from .srd_lite_mount_pending_cards import bind_mount_pending_cards
from .srd_lite_sleet_terrain import bind_sleet_ground_terrain
from .srd_lite_web_fire_card import bind_web_fire_cast_feedback
from .srd_lite_web_turn_card import bind_web_turn_feedback
from .srd_cone_qualification import cone_tools
from .srd_lite_coordinate_map import MAP_PANEL_ID, bind_coordinate_map


def count_nodes(value):
    if isinstance(value, dict):
        return 1 + sum(map(count_nodes, value.values()))
    if isinstance(value, list):
        return 1 + sum(map(count_nodes, value))
    return 1


def _exact_profile_schema(profile):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [key for key in profile if key != "family"],
        "properties": {
            key: {
                "type": "boolean"
                if isinstance(value, bool)
                else "integer"
                if isinstance(value, int)
                else "string",
                "enum": [value],
            }
            for key, value in profile.items()
            if key != "family"
        },
    }


def _restrict_choices(value):
    """The existing admitted schemas own Builder and import choices, too."""
    if isinstance(value, dict):
        properties = value.get("properties", {})
        for key in ("prepared_spells", "wizard_spellbook"):
            if key in properties:
                properties[key]["items"]["enum"] = sorted(SELECTED)
        grant_properties = (
            properties.get("spellcasting_grants", {}).get("items", {}).get("properties", {})
        )
        if "spells" in grant_properties:
            grant_properties["spells"]["items"]["enum"] = sorted(SELECTED)
        if all(key in properties for key in ("source", "ability", "spells")):
            properties["spells"]["items"]["enum"] = sorted(SELECTED)
        for child in value.values():
            _restrict_choices(child)
    elif isinstance(value, list):
        for child in value:
            _restrict_choices(child)


def compose(runtime, root, output, progress):
    catalogue = build_catalogue()
    baseline = root / "inputs/baseline.sylvia-gm-package"
    assert (
        hashlib.sha256(baseline.read_bytes()).hexdigest()
        == "70d48f4589984231989512cd5180e137f92a19fe800820085a2ddeabd7b53615"
    )
    with zipfile.ZipFile(baseline) as package:
        original = json.loads(package.read("manifest.json"))["packages"]
        baseline_drafts = [
            NormalizedPackageDraft(
                {k: v for k, v in entry.items() if k not in ("files", "content_sha256")},
                {
                    row["path"]: package.read("packages/" + entry["id"] + "/" + row["path"])
                    for row in entry["files"]
                },
                4,
            )
            for entry in original
        ]
        with materialize_package_archive(baseline_drafts) as check:
            if (
                check.closure_sha256
                != "d8ef0ed8d6c6b7169e734957dc3a8c8df8f4165f19b327a4d8f813cb52f953dc"
            ):
                raise ValueError("Selected accepted dependency closure drift")
            if (
                hashlib.sha256(check.archive.read()).hexdigest()
                != hashlib.sha256(baseline.read_bytes()).hexdigest()
            ):
                raise ValueError(
                    "Accepted baseline does not reproduce through ordinary materialization"
                )
        progress.phase("accepted baseline package and closure reproduced exactly")
        primary = next(r for r in original if r["id"] == "sylvia.srd-lite.bg3")
        record = {
            k: deepcopy(v) for k, v in primary.items() if k not in ("files", "content_sha256")
        }
        # Ninety-five approved profiles are original immutable SRD owners.
        # Cloud of Daggers is the retained separately authored sphere adaptation.
        # Source selection is not a gameplay qualification claim.
        retained_original_launch = {identity: {
            'profile': deepcopy(runtime['definitions']['spells'][identity]),
            'casting': deepcopy(runtime['definitions']['spell_casting'][identity]),
        } for identity in sorted(SELECTED - {'cloud_of_daggers'})}
        if len(retained_original_launch) != 95 or len(SELECTED) != 96:
            raise ValueError('The current launch requires95 original SRD owners plus the approved Cloud adaptation')
        retained_launch_damage = bind_launch_damage(runtime)
        retained_weapon_originals = {identity: {
            'profile': deepcopy(runtime['definitions']['spells'][identity]),
            'casting': deepcopy(runtime['definitions']['spell_casting'][identity]),
        } for identity in ('shillelagh','hex','hunters_mark','counterspell','divine_smite','fire_shield')}
        owned_public_operations = set()
        worker_parameter_text = package.read('packages/sylvia.srd-lite.bg3/runtime/ruleset.yaml').decode('utf-8')
        worker_parameter_fragment = re.findall(r'^parameter_declarations:.*?(?=^\S|\Z)', worker_parameter_text, re.M | re.S)
        if len(worker_parameter_fragment) != 1:
            raise ValueError('Original worker parameter pool changed')
        worker_pool = yaml.load(worker_parameter_fragment[0], Loader=yaml.CSafeLoader)['parameter_declarations']
        bind_worker_repairs(runtime, record, SELECTED, worker_pool)
        if 'find_steed' in SELECTED:
            owned_public_operations.add(expose_steed_healing_touch(runtime, record))
            owned_public_operations.add(expose_steed_fell_glare(runtime, record))
            owned_public_operations.add(expose_steed_fey_step(runtime, record))
            owned_public_operations.update((MOUNT, DISMOUNT))
        del worker_parameter_text, worker_parameter_fragment, worker_pool
        # The selected original SRD spells own these later consumers. Preserve
        # their exact original bodies, preconditions, metadata and parameters;
        # undo only this builder's pending guard/tail omission below.
        retained_consumers = {
            'control_srd_mage_hand': {'mage_hand'},
            'operate_srd_information_proxy': {'arcane_eye'},
            'traverse_srd_tree_stride': {'tree_stride'},
            'escape_srd_web': {'web'},
            'command_srd_summoned_creature': {'summon_dragon'},
            'resolve_srd_summon_breath': {'summon_dragon'},
            'ask_srd_divination_question': {'commune', 'contact_other_plane'},
            'query_srd_information_spell': {'arcane_eye'},
            'dismiss_srd_etherealness': {'etherealness'},
        }
        # Selected later actions use the original SRD source, not a denied owner patch.
        retained_consumers.update(RETAINED_CONTROL_FOLLOWUPS)
        bind_retained_control_followups(runtime, SELECTED)
        retained_consumers.update(RETAINED_SPELL_ACTIONS)
        bind_retained_spell_actions(runtime, SELECTED)
        retained_consumers.update(RETAINED_OBSERVATIONS)
        bind_retained_observations(runtime, SELECTED)
        for operation, spell_owners in retained_consumers.items():
            if spell_owners & SELECTED:
                if operation not in runtime['tools'] or not runtime['tools'][operation].get('effects'):
                    raise ValueError('Original retained spell consumer is missing: ' + operation)
                if operation not in {row['name'] for row in record['tools']}:
                    raise ValueError('Original retained consumer declaration is missing: ' + operation)
                owned_public_operations.add(operation)
        if 'chromatic_orb' in SELECTED:
            orb_reuse = bind_launch_chromatic_orb(runtime, record)
            owned_public_operations.update(orb_reuse['allowed_operations'])
        bind_worker_attack_sight(runtime, SELECTED)
        bind_spell_card_names(runtime, catalogue['profiles'])
        owned_public_operations.update({'set_srd_respiration', 'prepare_srd_spell_object'})
        if 'dragons_breath' in SELECTED:
            owned_public_operations.add('use_srd_dragon_breath')
        if {'hex','hunters_mark'} & SELECTED:
            owned_public_operations.add('transfer_srd_attack_mark')
        if 'divine_smite' in SELECTED:
            owned_public_operations.add('begin_srd_after_hit_smite')
        if {'shield','counterspell'} & SELECTED:
            owned_public_operations.add('cast_srd_reaction_spell')
        validate_reused_profiles(runtime)
        retained_launch_controls = {identity: {
            'profile': deepcopy(runtime['definitions']['spells'][identity]),
            'casting': deepcopy(runtime['definitions']['spell_casting'][identity]),
        } for identity in CONTROL_PROFILE_IDS}
        retained_offensive = {identity: {
            'profile':deepcopy(runtime['definitions']['spells'][identity]),
            'casting':deepcopy(runtime['definitions']['spell_casting'][identity]),
        } for identity in OFFENSIVE}
        retained_progression_originals = {identity: {
            'profile':deepcopy(runtime['definitions']['spells'][identity]),
            'casting':deepcopy(runtime['definitions']['spell_casting'][identity]),
        } for identity in ('true_strike','spare_the_dying','thaumaturgy','mending')}
        bind_profiles(runtime, reuse_original_true_strike=True, reuse_original_bless=True)
        # Preserve the existing approved authored Cloud sphere/profile/header.
        # There is no Cloud row in the immutable339-spell original SRD baseline.
        bind_daggers(runtime)
        if 'guardian_of_faith' in SELECTED:
            bind_guardian_movement_card(runtime)
        # Historical legacy healing, attacks, defenses, areas and utility binders
        # remain on disk for later work; the original paid SRD owners run here.
        bind_attack_facts(runtime)
        if OFFENSIVE & SELECTED:
            bind_offensive_cantrips(runtime)
            bind_offensive_statuses(runtime)
        # Current assigned damage profiles keep the complete immutable SRD rows.
        # This fresh runtime calls the non-idempotent Chain helper exactly once.
        for identity in LAUNCH_DAMAGE & SELECTED:
            retained = retained_launch_damage[identity]
            if runtime['definitions']['spells'][identity] != retained['profile'] or runtime['definitions']['spell_casting'][identity] != retained['casting']:
                raise ValueError('Assigned launch damage profile changed after SRD reuse: ' + identity)
        for identity in CONTROL_PROFILE_IDS & SELECTED:
            retained = retained_launch_controls[identity]
            if runtime['definitions']['spells'][identity] != retained['profile'] or runtime['definitions']['spell_casting'][identity] != retained['casting']:
                raise ValueError('Assigned launch control profile changed after SRD reuse: ' + identity)
        for identity in OFFENSIVE & SELECTED:
            retained = retained_offensive[identity]
            if runtime['definitions']['spells'][identity] != retained['profile'] or runtime['definitions']['spell_casting'][identity] != retained['casting']:
                raise ValueError('Assigned cantrip original SRD profile changed: ' + identity)
        for identity, retained in retained_progression_originals.items():
            if runtime['definitions']['spells'][identity] != retained['profile'] or runtime['definitions']['spell_casting'][identity] != retained['casting']:
                raise ValueError('Original progression cantrip changed: ' + identity)
        for identity, retained in retained_weapon_originals.items():
            if identity in SELECTED and (
                runtime['definitions']['spells'][identity] != retained['profile'] or
                runtime['definitions']['spell_casting'][identity] != retained['casting']
            ):
                raise ValueError('Retained original weapon/reaction profile changed: ' + identity)
        for identity, retained in retained_original_launch.items():
            if runtime['definitions']['spell_casting'][identity] != retained['casting']:
                raise ValueError('Original launch casting header changed: ' + identity)
            if runtime['definitions']['spells'][identity] != retained['profile']:
                raise ValueError('Original launch mechanical definition changed: ' + identity)
        guarded = bind_boundary(runtime, catalogue, SELECTED)
        # Keep the real ephemeral canonical identity without legacy mobility rules.
        bind_srd_saved_hit_mark_observer(runtime)
        if 'shillelagh' in SELECTED:
            bind_owned_self_targets(runtime)
        bind_original_counterspell_dispatch(runtime, selected=SELECTED)
        _bind_spell_identity(runtime)
        # Reviewed raw Fire Shield owners, then only four returned resolved
        # unarmed Smite owners. Preserve this builder's complete original union.
        if 'parameter_declarations' in runtime:
            raise ValueError('Main expected its original resolved parameter boundary')
        # The loader expands the baseline pool before composition. Reintroduce
        # only the exact native optional Character File row consumed by the
        # reviewed raw binder; retain every already composed Main descriptor.
        native_parameter_text = package.read(
            'packages/sylvia.srd-lite.bg3/runtime/ruleset.yaml').decode('utf-8')
        native_parameter_fragments = re.findall(
            r'^parameter_declarations:.*?(?=^\S|\Z)', native_parameter_text, re.M | re.S)
        if len(native_parameter_fragments) != 1:
            raise ValueError('Original native parameter pool ownership changed')
        native_pool = yaml.load(native_parameter_fragments[0], Loader=yaml.CSafeLoader)['parameter_declarations']
        rest_parameter_id = 'param_target_character_115ee703de5226a5'
        runtime['parameter_declarations'] = {
            rest_parameter_id: deepcopy(native_pool[rest_parameter_id])}
        del native_parameter_text, native_parameter_fragments, native_pool
        weapon_support_changes = bind_retained_weapon_supports(runtime, SELECTED)
        weapon_public_names = {'resolve_unarmed_strike': 'request_after_hit_smite',
                               'complete_srd_rest': 'target_character'}
        weapon_view = restore_tool_defaults(resolve_parameter_declarations(runtime))
        # Only these two genuine rest owners contain the appended original ID.
        # Expand it at this boundary; unrelated Main tools remain untouched.
        for rest_owner in ('complete_srd_rest', 'recover_srd_rest_resources'):
            runtime['tools'][rest_owner] = weapon_view['tools'][rest_owner]
        runtime.pop('parameter_declarations')
        weapon_public = RulesetRuntime(None, {'tools': {
            name: weapon_view['tools'][name] for name in weapon_public_names}})
        for public_name, optional_name in weapon_public_names.items():
            declaration = next(row for row in record['tools'] if row['name'] == public_name)
            prior = declaration['input_schema']
            produced = _runtime_parameter_schema(weapon_public.tools[public_name])
            if set(produced['properties']) != set(prior['properties']) | {optional_name}:
                raise ValueError('Reviewed optional public property ownership drift: ' + public_name)
            if produced['required'] != prior['required'] or optional_name in prior['properties']:
                raise ValueError('Original public required list changed: ' + public_name)
            prior['properties'][optional_name] = deepcopy(produced['properties'][optional_name])
        del weapon_view, weapon_public
        # Preserve actual access and visibility while undoing only this builder's
        # pending-operation guard on the reviewed retained public consumers.
        owned_public_metadata = {
            name: deepcopy(runtime['tools'][name].get('metadata', {}))
            for name in owned_public_operations
        }
        for declaration in record["tools"]:
            if declaration["name"] in CAST_ROOTS:
                declaration["input_schema"] = _tool_schema(runtime["tools"][declaration["name"]])
            if declaration["name"] in guarded:
                declaration["input_schema"]["properties"]["spell"].pop("enum", None)
        expose_original_stable_recovery(record)
        expose_first_aid(runtime, record)
        record["version"] = "0.3.1"
        denied = bind_exposed_operations(runtime, record)
        for name in sorted(owned_public_operations):
            if name not in denied:
                continue
            tool = runtime['tools'][name]
            expected = {'require': {
                'condition': 'false',
                'error': 'This spell operation is pending or excluded in the SRD Lite foundation.',
            }}
            if not tool.get('effects') or tool['effects'][0] != expected:
                raise ValueError('Owned public operation pending guard drift: ' + name)
            tool['effects'].pop(0)
            prior_metadata = owned_public_metadata[name]
            if 'visible_to_player' in prior_metadata:
                tool['metadata']['visible_to_player'] = prior_metadata['visible_to_player']
            else:
                tool['metadata'].pop('visible_to_player', None)
            denied.remove(name)
        # Preserve contracts and exactly the already-unconditional rejection.
        denied_guard = {"require": {
            "condition": "false",
            "error": "This spell operation is pending or excluded in the SRD Lite foundation.",
        }}
        for name in denied:
            tool = runtime["tools"][name]
            effects = tool.get("effects")
            if not isinstance(effects, list) or not effects or effects[0] != denied_guard:
                raise ValueError("Lite denied-operation guard drift: " + name)
            runtime["tools"][name] = {**tool, "effects": [deepcopy(denied_guard)]}
        progress.phase("unreachable pending-operation tails omitted under identical rejection", count=len(denied))
        runtime['tools'].update(cone_tools())
        from .srd_lite_coordinate_areas import bind_coordinate_cones
        bind_coordinate_cones(runtime)
        from .srd_lite_ward_body import bind_ward_recipient_bodies
        bind_ward_recipient_bodies(runtime)
        from .srd_lite_guardian_body import bind_guardian_recipient_body, bind_guardian_turn_feedback, bind_guardian_movement_body
        bind_guardian_recipient_body(runtime)
        bind_guardian_movement_body(runtime)
        bind_guardian_turn_feedback(runtime)
        from .srd_lite_insect_feedback import bind_insect_feedback
        bind_insect_feedback(runtime)
        bind_recorded_spell_roll_widgets(runtime)
        from .srd_lite_followup_roll_widgets import bind_followup_roll_widgets
        bind_followup_roll_widgets(runtime)
        from .srd_lite_retained_area_map import bind_retained_area_map
        bind_retained_area_map(runtime)
        bind_retained_optional_tail(runtime)
        bind_cast_willing_default(runtime)
        bind_fall_card_names(runtime)
        if 'misty_step' in SELECTED:
            bind_misty_step_card(runtime)
        if 'find_steed' in SELECTED:
            owned_public_operations.update(bind_steed_turns(runtime, record))
            bind_steed_rider_movement(runtime)
            bind_steed_disappearance(runtime)
            bind_steed_cast_cards(runtime)
            bind_steed_teleport_links(runtime)
            owned_public_operations.update(bind_steed_balance(runtime,record))
            bind_mount_pending_cards(runtime)
        bind_sleet_ground_terrain(runtime)
        bind_web_fire_cast_feedback(runtime)
        bind_web_turn_feedback(runtime)
        from .srd_lite_instant_area import bind_instant_combat_areas
        bind_instant_combat_areas(runtime)
        from .srd_lite_combat_target_facts import bind_combat_target_facts
        scalar_geometry = bind_combat_target_facts(runtime)
        progress.phase("saved scalar combat recipient facts bound", spells=len(scalar_geometry))
        from .srd_lite_multi_target_facts import bind_multi_target_facts
        multi_geometry = bind_multi_target_facts(runtime)
        progress.phase("saved multi-recipient combat facts bound", spells=len(multi_geometry))
        from .srd_lite_cantrip_combat_scope import bind_cantrip_combat_scope
        cantrip_scopes = bind_cantrip_combat_scope(runtime)
        progress.phase("cantrip scenes restricted to combat; actual cover and hearing bound", tools=cantrip_scopes)
        from .srd_lite_cleanup_scene_scope import bind_cleanup_scene_scope
        cleanup_scopes = bind_cleanup_scene_scope(runtime)
        progress.phase("empty spatial cleanup no longer requires a combat map", tools=cleanup_scopes)
        from .srd_lite_allocated_target_facts import bind_allocated_target_facts
        allocated_geometry = bind_allocated_target_facts(runtime)
        progress.phase("saved ray and dart combat allocation facts bound", spells=allocated_geometry)
        from .srd_lite_combat_scene_setup import bind_combat_scene_setup
        scene_entries = bind_combat_scene_setup(runtime, record)
        progress.phase("encounter entry admits its initial coordinate scene before initiative", tools=scene_entries)
        from .srd_lite_flame_strike_geometry import bind_flame_strike_combat_cylinder
        flame_geometry = bind_flame_strike_combat_cylinder(runtime)
        progress.phase("saved Flame Strike combat Cylinder facts bound", tools=flame_geometry)
        from .srd_lite_control_area_geometry import bind_control_combat_areas
        control_geometry = bind_control_combat_areas(runtime)
        progress.phase("saved Fear Cone and Sleep Sphere combat facts bound", tools=control_geometry)
        from .srd_lite_empty_control_areas import bind_paid_empty_control_areas
        empty_controls = bind_paid_empty_control_areas(runtime)
        progress.phase("empty combat control areas retain real caster concentration", tools=empty_controls)
        from .srd_lite_special_recipient_facts import bind_special_recipient_facts
        special_recipients = bind_special_recipient_facts(runtime)
        progress.phase("scaled stabilization and ordinary Dispel combat facts bound", tools=special_recipients)
        from .srd_lite_summon_combat_sight import bind_summon_combat_sight
        summon_sight = bind_summon_combat_sight(runtime)
        progress.phase("summoned Dragon combat placement reads saved point illumination", tools=summon_sight)
        from .srd_lite_exploration_objects import bind_exploration_objects, ADMIT as OBJECT_ADMISSION
        exploration_objects = bind_exploration_objects(runtime, record)
        owned_public_operations.add(OBJECT_ADMISSION)
        progress.phase("Light and Mending preserve exploration objects without a combat map", tools=exploration_objects)
        from .srd_lite_fire_bolt_objects import bind_fire_bolt_objects
        fire_bolt_objects = bind_fire_bolt_objects(runtime, record)
        progress.phase("Fire Bolt uses native object attacks and fuel facts", tools=fire_bolt_objects)
        from .srd_lite_misty_step_exploration import bind_misty_step_exploration
        exploration_travel = bind_misty_step_exploration(runtime, record)
        progress.phase("Misty Step preserves established exploration travel and native combat range", tools=exploration_travel)
        from .srd_lite_exploration_steed_mount import bind_exploration_steed_mount
        exploration_mount = bind_exploration_steed_mount(runtime, record)
        progress.phase("exploration mounting uses current fiction and native Speed without map budgets", tools=exploration_mount)
        from .srd_lite_exploration_created_actors import bind_exploration_created_actors, PROXIMITY
        exploration_actors = bind_exploration_created_actors(runtime, record)
        owned_public_operations.add(PROXIMITY)
        progress.phase("paid exploration creatures retain their actual source and later combat bodies", tools=exploration_actors)
        from .srd_lite_exploration_mage_hand import bind_exploration_mage_hand
        exploration_hand = bind_exploration_mage_hand(runtime)
        progress.phase("Mage Hand exploration controls retain native cargo and lifetime", tools=exploration_hand)
        from .srd_lite_thaumaturgy_exploration import bind_thaumaturgy_exploration
        exploration_wonders = bind_thaumaturgy_exploration(runtime)
        progress.phase("Thaumaturgy uses current exploration facts and retained object consequences", tools=exploration_wonders)
        from .srd_lite_exploration_dispel import bind_exploration_dispel
        exploration_dispel = bind_exploration_dispel(runtime)
        progress.phase("Dispel uses current exploration recipients and exact retained Light sources", tools=exploration_dispel)
        from .srd_lite_dispel_decision_widgets import bind_dispel_decision_widgets
        dispel_feedback = bind_dispel_decision_widgets(runtime)
        progress.phase("Dispel cards show actual source decisions and recorded casting checks", tools=dispel_feedback)
        from .srd_lite_steed_exploration_abilities import bind_steed_exploration_abilities
        exploration_abilities = bind_steed_exploration_abilities(runtime, record)
        progress.phase("Steed abilities preserve native mechanics while exploration uses current fiction", tools=exploration_abilities)
        from .srd_lite_exploration_steed_loss import FACTS, bind_exploration_steed_loss
        exploration_loss = bind_exploration_steed_loss(runtime, record)
        owned_public_operations.add(FACTS)
        progress.phase("Steed loss retains native falls while exploration uses current facts", tools=exploration_loss)
        from .srd_lite_combat_scene_reentry import bind_combat_scene_reentry, bind_combat_only_movement
        combat_movement = bind_combat_only_movement(runtime)
        progress.phase("coordinate movement uses combat positions and budgets only", tools=combat_movement)
        scene_reentries = bind_combat_scene_reentry(runtime)
        progress.phase("later encounters preserve authoritative positions and portable spell consequences", tools=scene_reentries)
        from .srd_lite_pending_area_preview import bind_pending_area_preview
        pending_areas = bind_pending_area_preview(runtime)
        progress.phase("qualified known combat areas project pending map feedback", tools=pending_areas)
        from .srd_lite_persistent_spell_map import KNOWN, bind_persistent_spell_map
        persistent_map = bind_persistent_spell_map(runtime)
        progress.phase("known native spell bodies and persistent extents project map feedback", tools=persistent_map)
        from .srd_lite_instant_area_pruning import omit_unselected_instant_area_branches
        omitted_area_arms = omit_unselected_instant_area_branches(runtime)
        progress.phase("excluded immediate-area spell branches omitted", branches=omitted_area_arms)
        from .srd_lite_family_dispatch import omit_unselected_family_dispatch
        omitted_family_arms = omit_unselected_family_dispatch(runtime)
        progress.phase("excluded selected-catalogue family dispatch omitted", branches=omitted_family_arms)
        for declaration in record['tools']:
            # Keep existing derived references while exposing the forwarded visibility fact.
            public_tool = runtime['tools'][declaration['name']]
            if any(parameter['name'] == KNOWN for parameter in public_tool['parameters']):
                declaration['input_schema']['properties'][KNOWN] = deepcopy(
                    _tool_schema(public_tool)['properties'][KNOWN]
                )
            if declaration['name'] in CAST_ROOTS | {'control_srd_mage_hand'} or any(
                parameter.get('name') in ('target_distance', 'targets_max_distance') and parameter.get('type') == 'number'
                for parameter in runtime['tools'][declaration['name']]['parameters']
            ):
                declaration['input_schema'] = _tool_schema(runtime['tools'][declaration['name']])
        from .srd_lite_exact_body_factoring import bind_srd_lite_exact_body_factoring
        bind_srd_lite_exact_body_factoring(runtime)
        omitted_private = omit_unreachable_private_tools(runtime, record)
        progress.phase('private owners unreachable from admitted public contracts omitted', count=len(omitted_private))
        from .srd_lite_combat_scene_reentry import omit_combat_movement_exploration_budget
        movement_segment = omit_combat_movement_exploration_budget(runtime, record)
        omitted_budget = omit_unreachable_private_tools(runtime, record)
        progress.phase('guarded combat movement omits its private exploration budget',
                       segment=movement_segment, unreachable=len(omitted_budget))
        identity_paths = rebind_owned_identity(runtime) + rebind_owned_identity(record)
        variants = runtime["definition_row_schemas"]["definitions.spells"]["variants"]
        variants["spell_environment"] = _exact_profile_schema(
            runtime["definitions"]["spells"]["cloud_of_daggers"]
        )
        needed = {row["family"] for row in runtime["definitions"]["spells"].values()}
        runtime["definition_row_schemas"]["definitions.spells"]["variants"] = {
            key: value for key, value in variants.items() if key in needed
        }
        profile_schema(runtime)
        record.update(id=VARIANT_ID, version=VARIANT_VERSION)
        bind_coordinate_map(record)
        record["metadata"]["label"] = (
            "SRD Lite - reduced spell list; no consumables "
            f"({len(SELECTED)} selected profiles; verification pending)"
        )
        _restrict_choices(record)
        _restrict_choices(runtime["tools"])
        repertoire_note = ('Selected spell headers restrict eligible choices. Full complete-grant and '
                           'earned-level requirements remain unchanged; initial character validation '
                           'describes available choices and reports current repertoire shortfalls. '
                           'Pending profiles cannot fill missing choices. Gameplay verification remains pending.')
        # Model-facing descriptions belong to runtime YAML, not portable tool declarations.
        for name in ('level_up_srd_character', 'validate_srd_spell_grants'):
            runtime['tools'][name]['description'] += '\n' + repertoire_note
        for artifact in record["participant_artifacts"]:
            artifact["schema_id"] = VARIANT_ID + "." + artifact["kind"].replace("_", "-")
            artifact["schema"]["$id"] = artifact["schema_id"]
            artifact.pop(
                "schema_sha256", None
            )  # ordinary materializer binds the changed complete contract
            if artifact["kind"] == "player_character":
                artifact["initial_state_validation"] = compose_character_validation(
                    runtime, artifact["initial_state_validation"]
                )
                for field in ('lite_false_life', 'lite_false_life_prepared'):
                    artifact['entity_state_schema']['properties'][field] = {'type': 'boolean'}
                artifact['entity_state_schema']['properties']['lite_false_life_prepared_source'] = {'type': 'string'}
                artifact["entity_state_schema"]["properties"]["lite_true_strike"] = {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "properties": {
                        "target": {"type": "string"},
                        "expires_after_turn": {"type": "integer"},
                        "expires_tick": {"type": "integer"},
                    },
                    "required": ["target", "expires_after_turn", "expires_tick"],
                }
        progress.phase(
            "variant owners and selection boundary composed",
            guarded=guarded,
            denied_operations=len(denied),
        )
        # Existing profitable declaration sharing preserves the unchanged expanded cap.
        bind_leveled_attack_facts(runtime)
        bind_death_counter_bounds(runtime)
        bind_pending_player_preparations(runtime)
        bind_range_and_victory(runtime)
        # Public declarations must retain the same continuous-distance contract.
        for tool in record["tools"]:
            parameter = tool["input_schema"].get("properties", {}).get("attack_distance")
            if parameter and parameter.get("type") == "integer":
                parameter["type"] = "number"
        from .srd_lite_panel_combat import bind_combat_controls
        bind_combat_controls(runtime, record)
        from .srd_lite_opportunity_movement import (
            bind_opportunity_movement,
            link_opportunity_movement_content,
        )
        bind_opportunity_movement(runtime, record)
        from .srd_lite_panel_combat import bind_queue_response_controls
        bind_queue_response_controls(runtime, record)
        from .srd_lite_result_completion import bind_result_completion
        bind_result_completion(runtime, record)
        from .srd_lite_reaction_grants import bind_reaction_decisions, bind_reaction_grants
        bind_reaction_grants(runtime, record)
        bind_reaction_decisions(runtime, record)
        from .srd_lite_starting_events import bind_starting_events
        bind_starting_events(runtime, record)
        runtime.pop("parameter_declarations", None)
        rendered = yaml.dump(
            runtime, Dumper=_RuntimeDumper, sort_keys=False, allow_unicode=True, width=100
        )
        rendered = compact_runtime(compact_runtime_defaults(rendered))
        runtime_bytes = rendered.encode("utf-8")
        output.mkdir(parents=True, exist_ok=True)
        (output / "ruleset.yaml").write_bytes(runtime_bytes)
        payloads = {
            row["path"]: package.read("packages/sylvia.srd-lite.bg3/" + row["path"])
            for row in primary["files"]
        }
        payloads["runtime/ruleset.yaml"] = runtime_bytes
        creation_payload = json.loads(payloads["content/character-creation-state.json"])
        character = next(item for item in record["participant_artifacts"] if item["kind"] == "player_character")
        for field in ("entity_state_schema", "initial_state_calculations", "initial_state_validation"):
            creation_payload[field] = character[field]
        payloads["content/character-creation-state.json"] = canonical_json_bytes(creation_payload)
        payloads["content/spell-catalogue.json"] = json.dumps(catalogue, indent=2).encode("utf-8")
        payloads["content/normal-roster.txt"] = (
            root / "source/rulesets/srd-lite/content/normal-roster.txt"
        ).read_bytes()
        payloads["content/roster-crosswalk.csv"] = (
            root / "source/rulesets/srd-lite/content/roster-crosswalk.csv"
        ).read_bytes()
        payloads['content/launch-roster.json'] = (root / 'source/rulesets/srd-lite/content/launch-roster.json').read_bytes()
        # Retain attribution and reference dependency; replace only variant spell declarations.
        declarations = json.loads(payloads[record["content_declarations"]])
        link_opportunity_movement_content(declarations)
        (output / "declarations-before.json").write_text(json.dumps(declarations, indent=2))
        for item in declarations["inclusions"]:
            item["links"]["schemas"] = [
                VARIANT_ID + ".player-character"
                if value == "sylvia.srd-lite.bg3.player-character"
                else value
                for value in item["links"]["schemas"]
            ]
            item["links"]["state_namespaces"] = [
                VARIANT_ID + value[len("sylvia.srd-lite.bg3") :]
                if value.startswith("sylvia.srd-lite.bg3.")
                else value
                for value in item["links"]["state_namespaces"]
            ]
            if item["family"] == "spells":
                item["disposition"] = "included_compatibility"
        adaptation = deepcopy(declarations["inclusions"][1])
        adaptation.update(
            id="srd-lite-selected-profiles",
            family="srd-lite",
            classifications=["sylvia_gm_implementation"],
            sources=[],
            disposition="included_mechanical",
        )
        adaptation["links"].update(
            artifacts=[
                "content/launch-roster.json",
                "content/normal-roster.txt",
                "content/roster-crosswalk.csv",
                "content/spell-catalogue.json",
                "content/spell-mechanics-source.json",
                "content/spell-mechanics.yaml",
            ],
            tools=sorted(CAST_ROOTS | {"end_concentration", "advance_stable_recovery"} | owned_public_operations),
        )
        adaptation['links']['ui_surfaces'] = sorted([MAP_PANEL_ID, 'dnd-character'])
        adaptation["links"]["tools"] = sorted(
            set(adaptation["links"]["tools"]) | {"stabilize_srd_creature"}
        )
        declarations["inclusions"].append(adaptation)
        declarations["inclusions"][0]["links"]["artifacts"] = sorted(
            set(
                declarations["inclusions"][0]["links"]["artifacts"]
                + [
                    "provenance/full-srd/" + name
                    for name in (
                        "spell-status-inventory.json",
                        "spell-mechanics-source.json",
                        "spell-mechanics.yaml",
                    )
                ]
            )
        )
        declarations["scope"].update(
            scope_id="srd-lite-foundation", claim_status="NOT_COMPLETE"
        )
        payloads[record["content_declarations"]] = canonical_json_bytes(declarations)
        for path in (
            "content/spell-status-inventory.json",
            "content/spell-mechanics-source.json",
            "content/spell-mechanics.yaml",
        ):
            payloads["provenance/full-srd/" + Path(path).name] = payloads[path]
        payloads["content/spell-mechanics.yaml"] = yaml.dump(
            {"spells": runtime["definitions"]["spells"]}, Dumper=_RuntimeDumper, sort_keys=True
        ).encode("utf-8")
        payloads["content/spell-mechanics-source.json"] = json.dumps(
            {
                "independently_authored_mechanics": False,
                "mechanical_rules_authority": "wotc.srd.5.2.1.pdf",
                "source_checked": "2026-10-09",
                ("baseline_runtime_sha256"): (
                    "58650516cfd6cf90b52263a357bf186430366c28d1f240cbb776b029ce0c81cf"
                ),
                "sources": {
                    row["identity"]: row.get("implementation_source_url", row["source_url"])
                    for row in catalogue["profiles"]
                    if row["identity"] in SELECTED
                },
                "adaptations": {
                    "cloud_of_daggers": "Retained approved authored seven-foot sphere at the chosen3D origin with its existing approved profile and casting header; this identity is absent from the immutable original SRD baseline.",
                    "shared_cantrip_receivers": "Reviewed numeric-distance and dead-owner Frost receiver repairs preserve original spell definitions and casting requirements.",
                    "shared_original_owners": "Reviewed original Orb fact routing, saved-hit mark observer, Shillelagh Self guard, Counterspell dispatch/context, Fire Shield receipts/qualified Pact rest and unarmed Smite continuation; native final-byte qualification remains open.",
                },
                ("profile_states"): (
                    "Selected profiles unverified until final-byte test evidence; all other"
                    " normal names pending."
                ),
            },
            indent=2,
        ).encode("utf-8")
        payloads["content/spell-status-inventory.json"] = json.dumps(
            {
                "variant_id": VARIANT_ID,
                "count": catalogue["count"],
                "foundation_only": False,
                "spells": [
                    {
                        **row,
                        "casting_requirements": runtime["definitions"]["spell_casting"].get(
                            row["identity"]
                        ),
                    }
                    for row in catalogue["profiles"]
                ],
            },
            indent=2,
        ).encode("utf-8")
        payloads["ATTRIBUTION.md"] += (
            b"\n\nSRD Lite reuses the original SRD5.2.1 rules and retained provenance."
            b" See content/spell-mechanics-source.json for the approved Cloud of Daggers"
            b" adaptation and shared implementation repairs. The96-profile source"
            b" selection remains unverified pending final-byte gameplay and UI checks.\n"
        )
        guide = "prompts/gm-operating-guide.md"
        names = [row['name'] for row in catalogue['profiles'] if row['identity'] in SELECTED]
        selected_control_notes = []
        if 'blindness_deafness' in SELECTED:
            selected_control_notes.append('For Blindness/Deafness, partition every chosen target exactly once between blinded_targets and deafened_targets; Blindness uses the blinded partition.')
        if 'irresistible_dance' in SELECTED:
            selected_control_notes.append('Irresistible Dance retains its public collect_srd_irresistible_dance action-save consumer.')
        payloads[guide] = (
            'SRD Lite selected executable profiles: ' + ', '.join(names) + '. '
            'Final-byte verification governs acceptance. Other catalogue entries remain pending. '
            'Excluded or pending requests fail through casting and selection; do not narrate them as supported. '
            'Existing full-SRD saves are not repinned. '
            'Coordinates and map movement matter only during combat. Outside combat, use current established '
            'fictional positions, distances and travel; never require move_srd_scene_creature or a map movement budget. '
            + ' '.join(selected_control_notes) + '\n\n'
        ).encode('utf-8') + payloads[guide]
        (output / "package-record.json").write_text(json.dumps(record, indent=2))
        drafts = [NormalizedPackageDraft(record, payloads, 4)]
        for reference in original:
            if reference["id"] == primary["id"]:
                continue
            reference_record = {
                k: v for k, v in reference.items() if k not in ("files", "content_sha256")
            }
            reference_payloads = {
                row["path"]: package.read("packages/" + reference["id"] + "/" + row["path"])
                for row in reference["files"]
            }
            drafts.append(NormalizedPackageDraft(reference_record, reference_payloads, 4))
    progress.phase("ordinary Format4 variant materialization started")
    with materialize_package_archive(drafts) as materialized:
        raw = materialized.archive.read()
        closure = materialized.closure_sha256
    candidate = output / "srd-lite-foundation.sylvia-gm-package"
    candidate.write_bytes(raw)
    graph = yaml.compose(runtime_bytes, Loader=yaml.CSafeLoader)
    validate_yaml_graph(
        graph,
        maximum_depth=64,
        maximum_nodes=500000,
        maximum_object_members=25000,
        count_mapping_keys=False,
    )
    decoded = yaml.load(runtime_bytes, Loader=yaml.CSafeLoader)
    counts = {
        "raw_nodes": count_nodes(decoded),
        "expanded_nodes": count_nodes(resolve_parameter_declarations(decoded)),
    }
    (output / "runtime-counts.json").write_text(json.dumps(counts, indent=2))
    if counts["expanded_nodes"] > 500000:
        raise ValueError("Unchanged 500000-node cap exceeded")
    with admit_package_archive(io.BytesIO(raw)) as admitted:
        for manifest in admitted.manifests:
            draft = next(d for d in drafts if d.record["id"] == manifest.package_id)
            validate_content_declaration_links(
                manifest,
                parse_content_declarations_bytes(
                    draft.payloads[manifest.content_declarations_path]
                ),
            )
        validate_admitted_package_runtimes(admitted)
    result = {
        "candidate": str(candidate),
        "package_bytes": len(raw),
        "package_sha256": hashlib.sha256(raw).hexdigest(),
        "runtime_bytes": len(runtime_bytes),
        "runtime_sha256": hashlib.sha256(runtime_bytes).hexdigest(),
        "closure_sha256": closure,
        "counts": counts,
        "normal_admission": True,
        "guarded_spell_owners": guarded,
        "denied_pending_spell_operations": denied,
        "identity_paths": identity_paths,
        "behavior_accepted": False,
        "test_status": "not run",
    }
    (output / "candidate-results.json").write_text(json.dumps(result, indent=2))
    progress.phase("variant admitted under unchanged graph bounds", counts=counts)
    return result
