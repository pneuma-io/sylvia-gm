from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from sylvia_gm.runtime._schema_validator import validate_ruleset

if __package__:
    from .build_srd_5_2_1_mechanics import _split_spell_runtime_tool_links
    from .build_srd_beast_forms import build_index as build_beast_index
    from .build_srd_non_beast_forms import build_index as build_non_beast_index
    from .srd_actor_spell_modifiers import PUBLIC_TOOLS as ACTOR_MODIFIER_TOOLS
    from .srd_actor_spell_modifiers import extend_runtime as extend_actor_modifiers
    from .srd_actor_spell_modifiers import refresh_exploration_runtime
    from .srd_animate_objects import PUBLIC_TOOLS as ANIMATE_OBJECT_SUPPORT_TOOLS
    from .srd_animate_objects import bind_runtime_consumers as bind_animate_objects
    from .srd_animate_objects import extend_runtime as extend_animate_objects
    from .srd_animate_objects import schema as animate_object_schema
    from .srd_animate_objects import validate_profiles as validate_animate_object_profiles
    from .srd_animated_skeletons import extend_runtime as extend_animated_skeletons
    from .srd_arcane_hand import PUBLIC_TOOLS as ARCANE_HAND_SUPPORT_TOOLS
    from .srd_arcane_hand import bind_runtime_consumers as bind_arcane_hand
    from .srd_arcane_hand import schema as arcane_hand_schema
    from .srd_arcane_hand import validate_profiles as validate_arcane_hand_profiles
    from .srd_astral_projection import PUBLIC_TOOLS as ASTRAL_SUPPORT_TOOLS
    from .srd_astral_projection import bind_completed_returns as bind_astral_completed_returns
    from .srd_astral_projection import bind_departure_consumers as bind_astral_departure
    from .srd_astral_projection import bind_runtime_consumers as bind_astral_projection
    from .srd_astral_projection import schema as astral_projection_schema
    from .srd_astral_projection import validate_profiles as validate_astral_projection_profiles
    from .srd_attack_hit_riders import PUBLIC_TOOLS as AFTER_HIT_SUPPORT_TOOLS
    from .srd_attack_hit_riders import bind_runtime as bind_after_hit_riders
    from .srd_attack_hit_riders import restore_runtime as restore_after_hit_riders
    from .srd_attack_rider_d20 import bind_runtime_consumers as bind_attack_rider_d20
    from .srd_attack_rider_d20 import restore_runtime as restore_attack_rider_d20
    from .srd_aura_of_life_maximum import bind_runtime_consumers as bind_aura_life_maximum
    from .srd_awaken import PUBLIC_TOOLS as AWAKEN_SUPPORT_TOOLS
    from .srd_awaken import bind_consumers as bind_awaken
    from .srd_awaken import extend_runtime as extend_awaken
    from .srd_awaken import validate_profiles as validate_awaken_profiles
    from .srd_body_soul_spells import PUBLIC_TOOLS as BODY_SOUL_SUPPORT_TOOLS
    from .srd_body_soul_spells import extend_runtime as extend_body_soul
    from .srd_body_soul_spells import validate_profiles as validate_body_soul_profiles
    from .srd_celestial_entries import bind_runtime_consumers as bind_celestial_entries
    from .srd_celestial_entries import emit_late_qualification as emit_late_celestial_route
    from .srd_celestial_initial_sight import emit_late_initial_sight as emit_celestial_initial_sight
    from .srd_celestial_lights import PUBLIC_TOOLS as CELESTIAL_LIGHT_SUPPORT_TOOLS
    from .srd_celestial_lights import bind_runtime_consumers as bind_celestial_lights
    from .srd_celestial_shared_rolls import bind_shared_damage_rolls as bind_celestial_shared_rolls
    from .srd_chromatic_orb import PUBLIC_TOOLS as ORB_SUPPORT_TOOLS
    from .srd_chromatic_orb import bind_paid_source as bind_orb_paid_source
    from .srd_chromatic_orb import bind_runtime_consumers as bind_orb_consumers
    from .srd_cone_qualification import extend_runtime as extend_cone_qualification
    from .srd_conjured_presences import PUBLIC_TOOLS as CONJURED_SUPPORT_TOOLS
    from .srd_conjured_presences import bind_runtime_consumers as bind_conjured_presences
    from .srd_conjured_presences import schema as conjured_presence_schema
    from .srd_conjured_presences import validate_profiles as validate_conjured_profiles
    from .srd_conjured_sight import emit_late_sight as emit_late_conjured_sight
    from .srd_contingency_child_casting import bind_release as bind_contingency_child_release
    from .srd_contingency_child_casting import (
        bind_retained_choices as bind_contingency_child_choices,
    )
    from .srd_contingency_child_casting import extend_runtime as extend_contingency_child_casting
    from .srd_contingency_clone_order import bind_runtime as bind_contingency_clone_order
    from .srd_contingency_completion import bind_damage as bind_contingency_damage
    from .srd_contingency_completion import bind_runtime as bind_contingency_completion
    from .srd_contingency_inventory import bind_runtime as bind_contingency_inventory
    from .srd_contingency_inventory_mutations import (
        bind_runtime as bind_contingency_inventory_mutations,
    )
    from .srd_contingency_removal import bind_runtime as bind_contingency_removal
    from .srd_contingency_spell import extend_runtime as extend_contingency
    from .srd_contingency_spell import validate_profiles as validate_contingency_profiles
    from .srd_contingency_water_trigger import (
        bind_extended_choices as bind_contingency_water_choices,
    )
    from .srd_contingency_water_trigger import (
        bind_respiration as bind_contingency_water_respiration,
    )
    from .srd_contingency_water_trigger import extend_runtime as extend_contingency_water
    from .srd_contingency_world_fact import bind_retained_choices as bind_contingency_fact_choices
    from .srd_contingency_world_fact import extend_runtime as extend_contingency_world_fact
    from .srd_contingency_world_fact import (
        normalize_trigger_choice as normalize_contingency_fact_choice,
    )
    from .srd_control_afflictions import PUBLIC_TOOLS as AFFLICTION_SUPPORT_TOOLS
    from .srd_control_afflictions import extend_runtime as extend_control_afflictions
    from .srd_control_afflictions import validate_definition as validate_control_afflictions
    from .srd_control_behaviors import extend_runtime as extend_control_behaviors
    from .srd_control_behaviors import validate_definition as validate_control_behavior
    from .srd_control_binding import PUBLIC_TOOLS as BINDING_SUPPORT_TOOLS
    from .srd_control_binding import extend_runtime as extend_control_binding
    from .srd_control_binding import validate_profiles as validate_binding_profiles
    from .srd_control_orders import PUBLIC_TOOLS as ORDER_SUPPORT_TOOLS
    from .srd_control_orders import extend_runtime as extend_control_orders
    from .srd_control_orders import validate_definition as validate_control_orders
    from .srd_courier_body_movement import bind_runtime_consumers as bind_courier_body_movement
    from .srd_courier_chronology import bind_runtime_consumers as bind_courier_chronology
    from .srd_courier_interval_pace import bind_runtime_consumers as bind_courier_interval_pace
    from .srd_d20_normalization import normalize_runtime as normalize_d20_runtime
    from .srd_damage_type_receipts import bind_runtime as bind_damage_type_receipts
    from .srd_damage_type_receipts import restore_runtime as restore_damage_type_receipts
    from .srd_delayed_blast_completion import bind_runtime as bind_delayed_blast_completion
    from .srd_delayed_blast_contacts import PUBLIC_TOOLS as DELAYED_CONTACT_SUPPORT_TOOLS
    from .srd_delayed_blast_contacts import bind_runtime_consumers as bind_delayed_contacts
    from .srd_delayed_blast_fireball import PUBLIC_TOOLS as DELAYED_BLAST_SUPPORT_TOOLS
    from .srd_delayed_blast_fireball import bind_runtime_consumers as bind_delayed_blast
    from .srd_delayed_blast_fireball import (
        extend_cast_parameters as extend_delayed_blast_parameters,
    )
    from .srd_delayed_blast_fireball import extend_runtime as extend_delayed_blast
    from .srd_delayed_blast_fireball import schema as delayed_blast_schema
    from .srd_delayed_blast_fireball import validate_profiles as validate_delayed_blast_profiles
    from .srd_delayed_blast_ignition import bind_runtime_consumers as bind_delayed_ignition
    from .srd_detection_mask_casting import extend_runtime as extend_detection_mask_casting
    from .srd_disintegrate_bodies import bind_runtime_consumers as bind_disintegrate_bodies
    from .srd_disintegrate_sources import bind_runtime_consumers as bind_disintegrate_sources
    from .srd_divination_parameters import bind_runtime as trim_divination_parameters
    from .srd_divination_spells import PUBLIC_TOOLS as DIVINATION_SUPPORT_TOOLS
    from .srd_divination_spells import extend_runtime as extend_divination
    from .srd_divination_spells import validate_profiles as validate_divination_profiles
    from .srd_dragon_breath import PUBLIC_TOOLS as DRAGON_BREATH_SUPPORT_TOOLS
    from .srd_dragon_breath import bind_runtime_consumers as bind_dragon_breath
    from .srd_dragon_breath import schema as dragon_breath_schema
    from .srd_dragon_breath import validate_profiles as validate_dragon_breath_profiles
    from .srd_dragon_breath_magic_zones import (
        bind_runtime_consumers as bind_dragon_breath_magic_zones,
    )
    from .srd_dream_spell import PUBLIC_TOOLS as DREAM_SUPPORT_TOOLS
    from .srd_dream_spell import bind_runtime_consumers as bind_dream_consumers
    from .srd_dream_spell import extend_runtime as extend_dream
    from .srd_dream_spell import validate_profiles as validate_dream_profiles
    from .srd_earthquake import PUBLIC_TOOLS as EARTHQUAKE_SUPPORT_TOOLS
    from .srd_earthquake import bind_movement_consumers as bind_earthquake_movement
    from .srd_earthquake import extend_runtime as extend_earthquake
    from .srd_earthquake import validate_profiles as validate_earthquake_profiles
    from .srd_earthquake_fissures import (
        bind_completed_turn_consumer as bind_earthquake_fissure_turn,
    )
    from .srd_earthquake_fissures import bind_movement_consumers as bind_earthquake_fissure_movement
    from .srd_earthquake_fissures import extend_runtime as extend_earthquake_fissures
    from .srd_earthquake_sources import bind_runtime as bind_earthquake_sources
    from .srd_earthquake_structures import PUBLIC_TOOLS as EARTHQUAKE_STRUCTURE_SUPPORT_TOOLS
    from .srd_earthquake_structures import (
        bind_final_receivers as bind_earthquake_structure_receivers,
    )
    from .srd_earthquake_structures import extend_runtime as extend_earthquake_structures
    from .srd_enhance_ability_source import bind_runtime_consumers as bind_enhance_ability_source
    from .srd_environment_flaming_sphere import bind_runtime_consumers as bind_flaming_sphere_light
    from .srd_environment_wall_thorns import extend_runtime as extend_thorn_wall
    from .srd_environment_wall_thorns import validate_profiles as validate_thorn_wall_profiles
    from .srd_environment_water import bind_flood_receivers as bind_water_waves
    from .srd_environment_water import bind_runtime_consumers as bind_control_water
    from .srd_environment_weather import bind_runtime_consumers as bind_control_weather
    from .srd_environment_wind_wall import bind_runtime_consumers as bind_wind_wall
    from .srd_exploration_cast_delegate import bind_runtime as bind_exploration_cast_delegate
    from .srd_extended_casting import PUBLIC_TOOLS as EXTENDED_CASTING_TOOLS
    from .srd_extended_casting import bind_private_casting_clocks
    from .srd_extended_casting import extend_runtime as extend_casting
    from .srd_feather_fall import bind_runtime_consumers as bind_feather_fall_consumers
    from .srd_feather_fall import extend_runtime as extend_feather_fall
    from .srd_find_the_path import PROFILES as FIND_PATH_PROFILES
    from .srd_find_the_path import PUBLIC_TOOLS as FIND_PATH_SUPPORT_TOOLS
    from .srd_find_the_path import bind_runtime_consumers as bind_find_path_consumers
    from .srd_find_the_path import extend_runtime as extend_find_path
    from .srd_find_the_path import validate_profiles as validate_find_path_source
    from .srd_finger_death_zombies import PUBLIC_TOOLS as FINGER_ZOMBIE_SUPPORT_TOOLS
    from .srd_finger_death_zombies import extend_runtime as extend_finger_death_zombies
    from .srd_flame_blade import PUBLIC_TOOLS as FLAME_BLADE_SUPPORT_TOOLS
    from .srd_flame_blade import bind_runtime_consumers as bind_flame_blade
    from .srd_flame_blade_light import bind_runtime_consumers as bind_flame_blade_light
    from .srd_flame_blade_objects import PUBLIC_TOOLS as FLAME_OBJECT_SUPPORT_TOOLS
    from .srd_flame_blade_objects import bind_runtime_consumers as bind_flame_objects
    from .srd_freezing_sphere import PUBLIC_TOOLS as FREEZING_SPHERE_SUPPORT_TOOLS
    from .srd_freezing_sphere import bind_runtime_consumers as bind_freezing_sphere
    from .srd_generator_yaml import scoped_generator_yaml
    from .srd_giant_insect_actions import PUBLIC_TOOLS as INSECT_ACTION_SUPPORT_TOOLS
    from .srd_giant_insect_actions import extend_runtime as extend_insect_actions
    from .srd_guards_wards import PUBLIC_TOOLS as GUARDS_WARDS_SUPPORT_TOOLS
    from .srd_guards_wards import extend_runtime as extend_guards_wards
    from .srd_guards_wards import validate_profiles as validate_guards_wards_profiles
    from .srd_guards_wards_doors import bind_door_consumers
    from .srd_guards_wards_mouth import bind_runtime as bind_guards_mouth
    from .srd_guards_wards_obscuration import extend_runtime as extend_guards_obscuration
    from .srd_guards_wards_web import PUBLIC_TOOLS as GUARDS_WEB_SUPPORT_TOOLS
    from .srd_guards_wards_web import bind_runtime_consumers as bind_guards_web_consumers
    from .srd_guards_wards_web import extend_runtime as extend_guards_web
    from .srd_guards_wards_web_burning import bind_runtime_consumers as bind_guards_web_burning
    from .srd_gust_of_wind import PROFILES as GUST_PROFILES
    from .srd_gust_of_wind import PUBLIC_TOOLS as GUST_SUPPORT_TOOLS
    from .srd_gust_of_wind import bind_runtime_consumers as bind_gust_of_wind
    from .srd_gust_of_wind import extend_runtime as extend_gust_of_wind
    from .srd_gust_of_wind import validate_profiles as validate_gust_source
    from .srd_hellish_rebuke import bind_runtime_consumers as bind_hellish_rebuke
    from .srd_ice_storm_terrain import bind_runtime_consumers as bind_ice_storm_terrain
    from .srd_illumination_body_consumers import (
        bind_runtime_consumers as bind_illumination_body_consumers,
    )
    from .srd_illumination_object_consumers import (
        bind_runtime_consumers as bind_illumination_object_consumers,
    )
    from .srd_illusion_appearance import PUBLIC_TOOLS as APPEARANCE_SUPPORT_TOOLS
    from .srd_illusion_appearance import extend_runtime as extend_appearances
    from .srd_illusion_appearance import validate_profiles as validate_appearance_profiles
    from .srd_illusion_images import PUBLIC_TOOLS as IMAGE_SUPPORT_TOOLS
    from .srd_illusion_images import extend_runtime as extend_images
    from .srd_illusion_images import validate_profiles as validate_image_profiles
    from .srd_illusion_spells import PUBLIC_TOOLS as ILLUSION_SUPPORT_TOOLS
    from .srd_illusion_spells import extend_runtime as extend_illusions
    from .srd_illusion_spells import validate_profiles as validate_illusion_profiles
    from .srd_information_casting import bind_runtime_consumers as bind_information_casting
    from .srd_information_spells import PUBLIC_TOOLS as INFORMATION_SUPPORT_TOOLS
    from .srd_information_spells import extend_runtime as extend_information
    from .srd_information_spells import validate_profiles as validate_information_profiles
    from .srd_information_world_facts import PUBLIC_TOOLS as INFORMATION_WORLD_SUPPORT_TOOLS
    from .srd_information_world_facts import bind_runtime_consumers as bind_information_world_facts
    from .srd_information_world_facts import restore_runtime as restore_information_world_facts
    from .srd_invisibility_cast_consumers import (
        bind_runtime_consumers as bind_invisibility_cast_consumers,
    )
    from .srd_magic_jar import PUBLIC_TOOLS as MAGIC_JAR_SUPPORT_TOOLS
    from .srd_magic_jar import extend_runtime as extend_magic_jar
    from .srd_magic_jar import validate_profiles as validate_magic_jar_profiles
    from .srd_magic_jar_consumers import bind_consumers as bind_magic_jar_consumers
    from .srd_magic_jar_container import bind_container_consumers as bind_magic_jar_container
    from .srd_magic_jar_equipment import bind_equipment_consumers as bind_magic_jar_equipment
    from .srd_magic_jar_healing import bind_healing_consumers as bind_magic_jar_healing
    from .srd_magic_jar_incoming_defenses import (
        bind_incoming_defense_consumers as bind_magic_jar_incoming_defenses,
    )
    from .srd_magic_jar_materials import bind_material_consumers as bind_magic_jar_materials
    from .srd_mansion_returns import bind_runtime_consumers as bind_mansion_returns
    from .srd_meld_into_stone import PUBLIC_TOOLS as MELD_STONE_SUPPORT_TOOLS
    from .srd_meld_into_stone import extend_runtime as extend_meld_stone
    from .srd_meld_into_stone import validate_profiles as validate_meld_stone_profiles
    from .srd_meld_stone_consumers import bind_consumers as bind_meld_stone_consumers
    from .srd_meld_stone_consumers import bind_travel_consumers as bind_meld_travel_consumers
    from .srd_memory_spells import PUBLIC_TOOLS as MEMORY_SUPPORT_TOOLS
    from .srd_memory_spells import extend_runtime as extend_memory
    from .srd_memory_spells import validate_profiles as validate_memory_profiles
    from .srd_mirage_arcane import PUBLIC_TOOLS as MIRAGE_SUPPORT_TOOLS
    from .srd_mirage_arcane import bind_runtime_consumers as bind_mirage_consumers
    from .srd_mirage_arcane import extend_runtime as extend_mirage
    from .srd_mirage_arcane import validate_profiles as validate_mirage_profiles
    from .srd_mirage_geometry import extend_runtime as extend_mirage_geometry
    from .srd_mirage_support import extend_runtime as extend_mirage_support
    from .srd_mirage_support_lifecycle import (
        bind_runtime_consumers as bind_mirage_support_lifecycle,
    )
    from .srd_mirror_image_consumers import bind_runtime_consumers as bind_mirror_image_consumers
    from .srd_move_earth import PUBLIC_TOOLS as MOVE_EARTH_SUPPORT_TOOLS
    from .srd_move_earth import bind_removal_consumers as bind_move_earth_removals
    from .srd_move_earth import bind_runtime_consumers as bind_move_earth
    from .srd_move_earth import schema as move_earth_schema
    from .srd_move_earth import validate_profiles as validate_move_earth_profiles
    from .srd_move_earth_suppression import bind_runtime_consumers as bind_move_earth_suppression
    from .srd_native_condition_sources import bind_runtime as bind_native_condition_sources
    from .srd_native_mummy_actions import bind_mummy_glare_turn_consumers
    from .srd_native_mummy_actions import extend_runtime as extend_native_mummy_actions
    from .srd_native_mummy_creation import PUBLIC_TOOLS as MUMMY_SUPPORT_TOOLS
    from .srd_native_mummy_creation import extend_runtime as extend_native_mummy_creation
    from .srd_native_mummy_fist import bind_native_mummy_fist_attack_consumers
    from .srd_native_mummy_fist import extend_runtime as extend_native_mummy_fist
    from .srd_native_mummy_routine import extend_runtime as extend_native_mummy_routine
    from .srd_native_undead_curses import bind_native_mummy_maximum_zero_consumers
    from .srd_native_undead_curses import (
        bind_native_undead_rest_consumers as bind_native_undead_rest,
    )
    from .srd_native_undead_curses import extend_runtime as extend_native_undead_curses
    from .srd_native_wight_actions import bind_native_wight_life_drain_consumers
    from .srd_native_wight_actions import extend_runtime as extend_native_wight
    from .srd_native_wight_attacks import bind_native_wight_attack_consumers
    from .srd_native_wight_attacks import extend_runtime as extend_native_wight_attacks
    from .srd_native_wight_creation import PUBLIC_TOOLS as WIGHT_SUPPORT_TOOLS
    from .srd_native_wight_creation import extend_runtime as extend_native_wight_creation
    from .srd_native_wight_equipment import extend_runtime as extend_native_wight_equipment
    from .srd_native_wight_rise import PUBLIC_TOOLS as WIGHT_RISE_SUPPORT_TOOLS
    from .srd_native_wight_rise import bind_native_wight_rise_consumers
    from .srd_native_wight_rise import extend_runtime as extend_native_wight_rise
    from .srd_object_packet_types import bind_runtime_consumers as bind_object_packet_types
    from .srd_ordinary_swimming import bind_runtime_consumers as bind_ordinary_swimming
    from .srd_parameter_declarations import compact_runtime as compact_runtime_parameters
    from .srd_parameter_declarations import restore_runtime as restore_parameter_declarations
    from .srd_persistent_spell_areas import PUBLIC_TOOLS as AREA_SUPPORT_TOOLS
    from .srd_persistent_spell_areas import extend_runtime as extend_persistent_areas
    from .srd_phantom_steed import PUBLIC_TOOLS as PHANTOM_STEED_SUPPORT_TOOLS
    from .srd_phantom_steed import bind_damage_consumers as bind_phantom_steed_damage
    from .srd_phantom_steed import bind_runtime_consumers as bind_phantom_steed_consumers
    from .srd_phantom_steed import extend_runtime as extend_phantom_steed
    from .srd_phantom_steed import validate_profiles as validate_phantom_steed_profiles
    from .srd_phantom_steed_dispel import bind_target_dispel as bind_phantom_steed_dispel
    from .srd_phantom_steed_dispel import extend_runtime as extend_phantom_steed_dispel
    from .srd_planar_ally import PUBLIC_TOOLS as PLANAR_ALLY_SUPPORT_TOOLS
    from .srd_planar_ally import extend_runtime as extend_planar_ally
    from .srd_planar_ally import validate_profiles as validate_planar_ally_profiles
    from .srd_plant_growth import PUBLIC_TOOLS as PLANT_GROWTH_SUPPORT_TOOLS
    from .srd_plant_growth import extend_runtime as extend_plant_growth
    from .srd_plant_growth import validate_profiles as validate_plant_growth_profiles
    from .srd_plant_growth_consumers import bind_casting_consumers as bind_plant_growth_casting
    from .srd_plant_growth_consumers import bind_movement_consumers as bind_plant_growth_movement
    from .srd_power_words import bind_runtime_consumers as bind_power_words
    from .srd_prepared_object_storage import bind_runtime_consumers as bind_prepared_object_storage
    from .srd_prismatic_damage_packets import bind_runtime as bind_prismatic_damage_packets
    from .srd_prismatic_wall import PUBLIC_TOOLS as PRISMATIC_WALL_SUPPORT_TOOLS
    from .srd_prismatic_wall import bind_runtime_consumers as bind_prismatic_wall_consumers
    from .srd_prismatic_wall import extend_runtime as extend_prismatic_wall
    from .srd_prismatic_wall import validate_profiles as validate_prismatic_wall_profiles
    from .srd_prismatic_wall_attacks import bind_runtime as bind_prismatic_wall_attacks
    from .srd_prismatic_wall_sources import bind_runtime as bind_prismatic_wall_sources
    from .srd_produce_flame import PUBLIC_TOOLS as PRODUCE_FLAME_SUPPORT_TOOLS
    from .srd_produce_flame import extend_runtime as extend_produce_flame
    from .srd_produce_flame_reactions import PUBLIC_TOOLS as PRODUCE_FLAME_REACTION_TOOLS
    from .srd_produce_flame_reactions import bind_runtime_consumers as bind_produce_flame_reactions
    from .srd_progressive_afflictions import PUBLIC_TOOLS as PROGRESSIVE_SUPPORT_TOOLS
    from .srd_progressive_afflictions import extend_runtime as extend_progressive
    from .srd_progressive_afflictions import validate_definition as validate_progressive
    from .srd_qualified_packet_implementations import (
        bind_runtime as bind_qualified_packet_implementations,
    )
    from .srd_qualified_packet_implementations import (
        restore_runtime as restore_qualified_packet_implementations,
    )
    from .srd_reaction_spells import PUBLIC_TOOLS as REACTION_SUPPORT_TOOLS
    from .srd_reaction_spells import (
        bind_pending_support_fences,
        finalize_pending_spell_guards,
        normalize_pending_spell_guards,
    )
    from .srd_reaction_spells import extend_runtime as extend_reactions
    from .srd_reaction_spells import validate_profiles as validate_reaction_profiles
    from .srd_received_damage_lifecycle import bind_runtime as bind_received_damage_lifecycle
    from .srd_received_damage_reactions import bind_receiving_events
    from .srd_reincarnate_native_traits import extend_runtime as extend_reincarnate_native_traits
    from .srd_reincarnate_rerolls import extend_runtime as extend_reincarnate_rerolls
    from .srd_retained_nullable_captures import bind_runtime as bind_retained_nullable_captures
    from .srd_retained_nullable_captures import (
        restore_runtime as restore_retained_nullable_captures,
    )
    from .srd_retained_spell_attacks import PUBLIC_TOOLS as RETAINED_ATTACK_SUPPORT_TOOLS
    from .srd_retained_spell_attacks import bind_runtime_consumers as bind_retained_spell_attacks
    from .srd_retained_spell_attacks import schema as retained_attack_schema
    from .srd_retained_spell_attacks import validate_profiles as validate_retained_attack_profiles
    from .srd_retained_spell_parameters import bind_runtime as trim_retained_spell_parameters
    from .srd_retained_spell_reactions import bind_runtime as bind_retained_spell_reactions
    from .srd_retained_spell_reactions import restore_runtime as restore_retained_spell_reactions
    from .srd_reverse_gravity import PROFILES as GRAVITY_PROFILES
    from .srd_reverse_gravity import PUBLIC_TOOLS as GRAVITY_SUPPORT_TOOLS
    from .srd_reverse_gravity import bind_runtime_consumers as bind_reverse_gravity
    from .srd_reverse_gravity import extend_runtime as extend_reverse_gravity
    from .srd_reverse_gravity import validate_profiles as validate_reverse_gravity_source
    from .srd_rope_trick_returns import bind_runtime_consumers as bind_rope_trick_returns
    from .srd_runtime_defaults import compact_runtime_defaults
    from .srd_sanctuary_lists import bind_runtime_consumers as bind_sanctuary_lists
    from .srd_scene_hand_occupancy import PUBLIC_TOOLS as HAND_OCCUPANCY_SUPPORT_TOOLS
    from .srd_scene_hand_occupancy import extend_runtime as extend_hand_occupancy
    from .srd_scene_relations import PUBLIC_TOOLS as SCENE_SUPPORT_TOOLS
    from .srd_scene_relations import extend_runtime as extend_scene_relations
    from .srd_scene_sight_origins import PUBLIC_TOOLS as SIGHT_ORIGIN_SUPPORT_TOOLS
    from .srd_scene_sight_origins import bind_runtime_consumers as bind_scene_sight_origins
    from .srd_scene_sight_origins import extend_runtime as extend_scene_sight_origins
    from .srd_secure_area_materials import bind_runtime as bind_secure_area_materials
    from .srd_secure_area_materials import extend_runtime as extend_secure_area_materials
    from .srd_shapechange_weapons import bind_runtime_consumers as bind_shapechange_weapons
    from .srd_shatter_objects import bind_runtime_consumers as bind_shatter_objects
    from .srd_simulacrum import extend_runtime as extend_simulacrum
    from .srd_simulacrum import validate_profiles as validate_simulacrum_profiles
    from .srd_simulacrum_characters import PUBLIC_TOOLS as SIMULACRUM_CHARACTER_SUPPORT_TOOLS
    from .srd_simulacrum_characters import (
        bind_casting_consumers as bind_simulacrum_character_casting,
    )
    from .srd_simulacrum_characters import bind_check_consumer as bind_simulacrum_character_checks
    from .srd_simulacrum_characters import bind_sources as bind_simulacrum_character_sources
    from .srd_simulacrum_consumers import PUBLIC_TOOLS as SIMULACRUM_REPAIR_SUPPORT_TOOLS
    from .srd_simulacrum_consumers import bind_consumers as bind_simulacrum_consumers
    from .srd_simulacrum_equipment import bind_sources as bind_simulacrum_equipment
    from .srd_simulacrum_turns import PUBLIC_TOOLS as SIMULACRUM_TURN_SUPPORT_TOOLS
    from .srd_simulacrum_turns import bind_turn_consumers as bind_simulacrum_turns
    from .srd_solid_spell_walls import bind_runtime_consumers as bind_solid_spell_walls
    from .srd_solid_spell_walls import extend_runtime as extend_solid_spell_walls
    from .srd_solid_spell_walls import validate_profiles as validate_solid_wall_profiles
    from .srd_solid_wall_narrowing import bind_runtime_consumers as bind_solid_wall_narrowing
    from .srd_source_relocation_node import bind_runtime_consumers as bind_source_relocation_node
    from .srd_spell_barriers import PUBLIC_TOOLS as BARRIER_SUPPORT_TOOLS
    from .srd_spell_barriers import bind_runtime_consumers as bind_barrier_consumers
    from .srd_spell_barriers import extend_runtime as extend_barriers
    from .srd_spell_barriers import validate_profiles as validate_barrier_profiles
    from .srd_spell_begin_timing import bind_runtime_consumers as bind_spell_begin_timing
    from .srd_spell_damage_consumers import extend_runtime as extend_spell_damage_consumers
    from .srd_spell_damage_riders import PUBLIC_TOOLS as DAMAGE_RIDER_SUPPORT_TOOLS
    from .srd_spell_damage_riders import bind_runtime_consumers as bind_damage_riders
    from .srd_spell_damage_riders import extend_runtime as extend_damage_riders
    from .srd_spell_damage_riders import schema as damage_rider_schema
    from .srd_spell_damage_riders import validate_profiles as validate_damage_rider_profiles
    from .srd_spell_detection_masks import extend_runtime as extend_detection_masks
    from .srd_spell_detection_masks import validate_profiles as validate_detection_mask_profiles
    from .srd_spell_environment import PUBLIC_TOOLS as ENVIRONMENT_SUPPORT_TOOLS
    from .srd_spell_environment import bind_runtime_consumers as bind_environment_consumers
    from .srd_spell_environment import extend_runtime as extend_environment
    from .srd_spell_environment import validate_profiles as validate_environment_profiles
    from .srd_spell_exile_composition import bind_runtime as bind_exile_completion
    from .srd_spell_floating_disk import PUBLIC_TOOLS as DISK_SUPPORT_TOOLS
    from .srd_spell_floating_disk import bind_runtime_consumers as bind_floating_disk_consumers
    from .srd_spell_floating_disk import extend_runtime as extend_floating_disk
    from .srd_spell_grants import complete_grant_policies, definition_schema, grant_policies
    from .srd_spell_hallow import bind_runtime_consumers as bind_hallow_consumers
    from .srd_spell_hallow import extend_runtime as extend_hallow
    from .srd_spell_hallow import validate_profiles as validate_hallow_profiles
    from .srd_spell_heat_metal import PUBLIC_TOOLS as HEAT_METAL_SUPPORT_TOOLS
    from .srd_spell_heat_metal import bind_d20_runtime as bind_heat_metal_d20
    from .srd_spell_heat_metal import extend_runtime as extend_heat_metal
    from .srd_spell_heat_metal import validate_profiles as validate_heat_metal_profiles
    from .srd_spell_illumination import PUBLIC_TOOLS as ILLUMINATION_SUPPORT_TOOLS
    from .srd_spell_illumination import bind_moonbeam_runtime as bind_moonbeam_light
    from .srd_spell_illumination import extend_runtime as extend_illumination
    from .srd_spell_illumination import validate_profiles as validate_illumination_profiles
    from .srd_spell_invisibility_timing import (
        bind_runtime_consumers as bind_invisibility_completion,
    )
    from .srd_spell_light_damage_riders import bind_area_consequences
    from .srd_spell_magic_zones import bind_effect_consumers as bind_magic_zone_effects
    from .srd_spell_magic_zones import bind_runtime_consumers as bind_magic_zone_consumers
    from .srd_spell_magic_zones import extend_runtime as extend_magic_zones
    from .srd_spell_magic_zones import normalize_runtime as normalize_magic_zone_runtime
    from .srd_spell_magic_zones import validate_profiles as validate_magic_zone_profiles
    from .srd_spell_material_stock import bind_material_stock
    from .srd_spell_minor_world import PUBLIC_TOOLS as MINOR_WORLD_SUPPORT_TOOLS
    from .srd_spell_minor_world import bind_d20_runtime as bind_minor_world_d20
    from .srd_spell_minor_world import extend_runtime as extend_minor_world
    from .srd_spell_minor_world import validate_profiles as validate_minor_world_profiles
    from .srd_spell_numeric_distances import bind_runtime_consumers as bind_numeric_spell_distances
    from .srd_spell_object_content import PUBLIC_TOOLS as OBJECT_CONTENT_SUPPORT_TOOLS
    from .srd_spell_object_content import extend_runtime as extend_object_content
    from .srd_spell_object_content import validate_profiles as validate_object_content_profiles
    from .srd_spell_object_damage import bind_runtime_consumers as bind_area_object_damage
    from .srd_spell_object_forces import PUBLIC_TOOLS as OBJECT_FORCE_SUPPORT_TOOLS
    from .srd_spell_object_forces import bind_runtime_consumers as bind_object_force_consumers
    from .srd_spell_object_forces import extend_runtime as extend_object_forces
    from .srd_spell_object_targets import PUBLIC_TOOLS as OBJECT_TARGET_SUPPORT_TOOLS
    from .srd_spell_object_targets import extend_runtime as extend_object_targets
    from .srd_spell_object_utilities import PUBLIC_TOOLS as OBJECT_UTILITY_SUPPORT_TOOLS
    from .srd_spell_object_utilities import extend_runtime as extend_object_utilities
    from .srd_spell_object_utilities import validate_profiles as validate_object_utility_profiles
    from .srd_spell_object_utility_sources import (
        bind_source_consumers as bind_object_utility_sources,
    )
    from .srd_spell_recovery import PUBLIC_TOOLS as RECOVERY_SUPPORT_TOOLS
    from .srd_spell_recovery import extend_casting_runtime as extend_recovery_casting
    from .srd_spell_recovery import extend_runtime as extend_recovery
    from .srd_spell_recovery import validate_definition as validate_recovery
    from .srd_spell_removal_wakes import bind_runtime_consumers as bind_spell_removal_wakes
    from .srd_spell_removals import PUBLIC_TOOLS as SPELL_REMOVAL_SUPPORT_TOOLS
    from .srd_spell_removals import extend_runtime as extend_spell_removals
    from .srd_spell_secure_areas import PUBLIC_TOOLS as SECURE_AREA_SUPPORT_TOOLS
    from .srd_spell_secure_areas import bind_runtime_consumers as bind_secure_area_consumers
    from .srd_spell_secure_areas import extend_runtime as extend_secure_areas
    from .srd_spell_secure_areas import validate_profiles as validate_secure_area_profiles
    from .srd_spell_source_instances import extend_runtime as extend_spell_sources
    from .srd_spell_special_damage import PUBLIC_TOOLS as SPECIAL_DAMAGE_SUPPORT_TOOLS
    from .srd_spell_special_damage import extend_runtime as extend_special_damage
    from .srd_spell_summoning import ANIMATED_PUBLIC_TOOLS as ANIMATED_SUPPORT_TOOLS
    from .srd_spell_summoning import FAMILIAR_PUBLIC_TOOLS as FAMILIAR_SUPPORT_TOOLS
    from .srd_spell_summoning import HOUND_PUBLIC_TOOLS as HOUND_SUPPORT_TOOLS
    from .srd_spell_summoning import PUBLIC_TOOLS as SUMMONING_SUPPORT_TOOLS
    from .srd_spell_summoning import (
        bind_animated_undead_turn_consumers,
        bind_created_undead_cast_consumers,
        bind_hound_turn_consumers,
        bind_insect_runtime_consumers,
        bind_undead_fortitude_damage_consumers,
    )
    from .srd_spell_summoning import extend_runtime as extend_summoning
    from .srd_spell_summoning import validate_profiles as validate_summoning_profiles
    from .srd_spell_supplies import PUBLIC_TOOLS as SUPPLY_SUPPORT_TOOLS
    from .srd_spell_supplies import extend_runtime as extend_supplies
    from .srd_spell_supplies import validate_profiles as validate_supply_profiles
    from .srd_spell_travel import PUBLIC_TOOLS as TRAVEL_SUPPORT_TOOLS
    from .srd_spell_travel import bind_runtime_consumers as bind_travel_consumers
    from .srd_spell_travel import extend_runtime as extend_travel
    from .srd_spell_travel import validate_profiles as validate_travel_profiles
    from .srd_spell_travel_dwelling_lifetime import emit_runtime as emit_dwelling_lifetime
    from .srd_spell_travel_gate_transit import emit_late_gate_geometry
    from .srd_spell_travel_servants import emit_late_servant_access
    from .srd_spell_travel_telekinesis import emit_late_handoff as emit_late_telekinesis_handoff
    from .srd_spell_travel_telekinesis_fine import emit_late_access as emit_late_telekinesis_access
    from .srd_spell_travel_telekinesis_pour import emit_late_pour as emit_late_telekinesis_pour
    from .srd_spell_trigger_composition import bind_runtime as bind_trigger_consumers
    from .srd_spell_triggers import PUBLIC_TOOLS as TRIGGER_SUPPORT_TOOLS
    from .srd_spell_triggers import extend_runtime as extend_triggers
    from .srd_spell_triggers import validate_profiles as validate_trigger_profiles
    from .srd_spell_wards import PUBLIC_TOOLS as WARD_SUPPORT_TOOLS
    from .srd_spell_wards import extend_runtime as extend_wards
    from .srd_spell_wards import validate_profiles as validate_ward_profiles
    from .srd_stone_spell_wall import bind_runtime_consumers as bind_stone_spell_wall
    from .srd_stone_spell_wall import extend_runtime as extend_stone_spell_wall
    from .srd_stone_spell_wall import validate_profiles as validate_stone_wall_profiles
    from .srd_storm_conditions import bind_runtime_consumers as bind_storm_conditions
    from .srd_storm_later_turns import bind_runtime_consumers as bind_storm_later_turns
    from .srd_storm_lightning import PUBLIC_TOOLS as STORM_LIGHTNING_SUPPORT_TOOLS
    from .srd_storm_lightning import bind_runtime_consumers as bind_storm_lightning
    from .srd_storm_movement import bind_runtime_consumers as bind_storm_movement
    from .srd_storm_objects import bind_runtime_consumers as bind_storm_objects
    from .srd_storm_of_vengeance import bind_runtime_consumers as bind_storm
    from .srd_storm_of_vengeance import schema as storm_schema
    from .srd_storm_of_vengeance import validate_profiles as validate_storm_profiles
    from .srd_storm_weapon_attacks import (
        bind_runtime_consumers as bind_storm_weapon_attacks,
    )
    from .srd_storm_weapon_attacks import (
        bind_saved_attack_consumers as bind_storm_saved_weapon_attacks,
    )
    from .srd_storm_wind import bind_runtime_consumers as bind_storm_wind
    from .srd_sunbeam import PUBLIC_TOOLS as SUNBEAM_SUPPORT_TOOLS
    from .srd_sunbeam import bind_runtime_consumers as bind_sunbeam
    from .srd_sunbeam import extend_runtime as extend_sunbeam
    from .srd_sunbeam import schema as sunbeam_schema
    from .srd_sunbeam import validate_profiles as validate_sunbeam_profiles
    from .srd_thunderwave_consequences import PUBLIC_TOOLS as THUNDERWAVE_SUPPORT_TOOLS
    from .srd_thunderwave_consequences import (
        bind_runtime_consumers as bind_thunderwave_consequences,
    )
    from .srd_time_stop import PUBLIC_TOOLS as TIME_STOP_SUPPORT_TOOLS
    from .srd_time_stop import bind_runtime_consumers as bind_time_stop_consumers
    from .srd_time_stop import extend_runtime as extend_time_stop
    from .srd_time_stop import validate_profiles as validate_time_stop_profiles
    from .srd_time_stop_effects import bind_received_effects as bind_time_stop_received
    from .srd_time_stop_effects import bind_source_consumers as bind_time_stop_sources
    from .srd_time_stop_retained_changes import (
        bind_runtime_consumers as bind_time_stop_retained_changes,
    )
    from .srd_time_stop_retained_changes import (
        restore_runtime as restore_time_stop_retained_changes,
    )
    from .srd_time_stop_retained_damage import bind_runtime_consumers as bind_time_stop_retained
    from .srd_time_stop_retained_damage import restore_runtime as restore_time_stop_retained
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_actor_defenses import extend_runtime as extend_actor_defenses
    from .srd_timed_senses import PUBLIC_TOOLS as SENSE_SUPPORT_TOOLS
    from .srd_timed_senses import extend_runtime as extend_senses
    from .srd_timed_senses import validate_profiles as validate_sense_profiles
    from .srd_transformation_moonbeam import bind_runtime_consumers as bind_moonbeam_producers
    from .srd_transformation_spells import PUBLIC_TOOLS as TRANSFORMATION_SUPPORT_TOOLS
    from .srd_transformation_spells import extend_runtime as extend_transformations
    from .srd_transformation_spells import validate_profiles as validate_transformation_profiles
    from .srd_true_polymorph_objects import PUBLIC_TOOLS as TRANSFORMATION_OBJECT_SUPPORT_TOOLS
    from .srd_true_polymorph_objects import extend_runtime as extend_transformation_objects
    from .srd_true_strike import bind_runtime_consumers as bind_true_strike
    from .srd_true_strike import extend_cast_parameters as extend_true_strike_parameters
    from .srd_true_strike import restore_runtime as restore_true_strike
    from .srd_true_strike import schema as true_strike_schema
    from .srd_true_strike import validate_profiles as validate_true_strike_profiles
    from .srd_tsunami import PROFILES as TSUNAMI_PROFILES
    from .srd_tsunami import PUBLIC_TOOLS as TSUNAMI_SUPPORT_TOOLS
    from .srd_tsunami import bind_runtime_consumers as bind_tsunami
    from .srd_tsunami import extend_runtime as extend_tsunami
    from .srd_tsunami import validate_profiles as validate_tsunami_source
    from .srd_tsunami_completion import bind_runtime as bind_tsunami_completion
    from .srd_tsunami_forced_travel import bind_runtime_consumers as bind_tsunami_forced_travel
    from .srd_tsunami_source_adoption import bind_runtime as bind_tsunami_sources
    from .srd_tsunami_travel import bind_runtime_consumers as bind_tsunami_travel
    from .srd_wall_ice_relocation import bind_runtime_consumers as bind_wall_ice_relocation
    from .srd_warding_bond_damage import bind_runtime_consumers as bind_warding_bond_damage
    from .srd_whirlpool_pull import bind_runtime_consumers as bind_whirlpool_pull
    from .srd_wish import bind_runtime_consumers as bind_wish
    from .srd_wish import extend_runtime as extend_wish
    from .srd_wish import schema as wish_schema
    from .srd_wish import validate_definition as validate_wish
else:
    from build_srd_5_2_1_mechanics import _split_spell_runtime_tool_links
    from build_srd_beast_forms import build_index as build_beast_index
    from build_srd_non_beast_forms import build_index as build_non_beast_index
    from scripts.srd_full.srd_celestial_lights import PUBLIC_TOOLS as CELESTIAL_LIGHT_SUPPORT_TOOLS
    from scripts.srd_full.srd_celestial_lights import bind_runtime_consumers as bind_celestial_lights
    from scripts.srd_full.srd_celestial_shared_rolls import (
        bind_shared_damage_rolls as bind_celestial_shared_rolls,
    )
    from scripts.srd_full.srd_conjured_presences import PUBLIC_TOOLS as CONJURED_SUPPORT_TOOLS
    from scripts.srd_full.srd_conjured_presences import bind_runtime_consumers as bind_conjured_presences
    from scripts.srd_full.srd_conjured_presences import schema as conjured_presence_schema
    from scripts.srd_full.srd_conjured_presences import validate_profiles as validate_conjured_profiles
    from scripts.srd_full.srd_conjured_sight import emit_late_sight as emit_late_conjured_sight
    from srd_actor_spell_modifiers import PUBLIC_TOOLS as ACTOR_MODIFIER_TOOLS
    from srd_actor_spell_modifiers import extend_runtime as extend_actor_modifiers
    from srd_actor_spell_modifiers import refresh_exploration_runtime
    from srd_animate_objects import PUBLIC_TOOLS as ANIMATE_OBJECT_SUPPORT_TOOLS
    from srd_animate_objects import bind_runtime_consumers as bind_animate_objects
    from srd_animate_objects import extend_runtime as extend_animate_objects
    from srd_animate_objects import schema as animate_object_schema
    from srd_animate_objects import validate_profiles as validate_animate_object_profiles
    from srd_animated_skeletons import extend_runtime as extend_animated_skeletons
    from srd_arcane_hand import PUBLIC_TOOLS as ARCANE_HAND_SUPPORT_TOOLS
    from srd_arcane_hand import bind_runtime_consumers as bind_arcane_hand
    from srd_arcane_hand import schema as arcane_hand_schema
    from srd_arcane_hand import validate_profiles as validate_arcane_hand_profiles
    from srd_astral_projection import PUBLIC_TOOLS as ASTRAL_SUPPORT_TOOLS
    from srd_astral_projection import bind_completed_returns as bind_astral_completed_returns
    from srd_astral_projection import bind_departure_consumers as bind_astral_departure
    from srd_astral_projection import bind_runtime_consumers as bind_astral_projection
    from srd_astral_projection import schema as astral_projection_schema
    from srd_astral_projection import validate_profiles as validate_astral_projection_profiles
    from srd_attack_hit_riders import PUBLIC_TOOLS as AFTER_HIT_SUPPORT_TOOLS
    from srd_attack_hit_riders import bind_runtime as bind_after_hit_riders
    from srd_attack_hit_riders import restore_runtime as restore_after_hit_riders
    from srd_attack_rider_d20 import bind_runtime_consumers as bind_attack_rider_d20
    from srd_attack_rider_d20 import restore_runtime as restore_attack_rider_d20
    from srd_aura_of_life_maximum import bind_runtime_consumers as bind_aura_life_maximum
    from srd_awaken import PUBLIC_TOOLS as AWAKEN_SUPPORT_TOOLS
    from srd_awaken import bind_consumers as bind_awaken
    from srd_awaken import extend_runtime as extend_awaken
    from srd_awaken import validate_profiles as validate_awaken_profiles
    from srd_body_soul_spells import PUBLIC_TOOLS as BODY_SOUL_SUPPORT_TOOLS
    from srd_body_soul_spells import extend_runtime as extend_body_soul
    from srd_body_soul_spells import validate_profiles as validate_body_soul_profiles
    from srd_celestial_entries import bind_runtime_consumers as bind_celestial_entries
    from srd_celestial_entries import emit_late_qualification as emit_late_celestial_route
    from srd_celestial_initial_sight import emit_late_initial_sight as emit_celestial_initial_sight
    from srd_chromatic_orb import PUBLIC_TOOLS as ORB_SUPPORT_TOOLS
    from srd_chromatic_orb import bind_paid_source as bind_orb_paid_source
    from srd_chromatic_orb import bind_runtime_consumers as bind_orb_consumers
    from srd_cone_qualification import extend_runtime as extend_cone_qualification
    from srd_contingency_child_casting import bind_release as bind_contingency_child_release
    from srd_contingency_child_casting import (
        bind_retained_choices as bind_contingency_child_choices,
    )
    from srd_contingency_child_casting import extend_runtime as extend_contingency_child_casting
    from srd_contingency_clone_order import bind_runtime as bind_contingency_clone_order
    from srd_contingency_completion import bind_damage as bind_contingency_damage
    from srd_contingency_completion import bind_runtime as bind_contingency_completion
    from srd_contingency_inventory import bind_runtime as bind_contingency_inventory
    from srd_contingency_inventory_mutations import (
        bind_runtime as bind_contingency_inventory_mutations,
    )
    from srd_contingency_removal import bind_runtime as bind_contingency_removal
    from srd_contingency_spell import extend_runtime as extend_contingency
    from srd_contingency_spell import validate_profiles as validate_contingency_profiles
    from srd_contingency_water_trigger import (
        bind_extended_choices as bind_contingency_water_choices,
    )
    from srd_contingency_water_trigger import bind_respiration as bind_contingency_water_respiration
    from srd_contingency_water_trigger import extend_runtime as extend_contingency_water
    from srd_contingency_world_fact import bind_retained_choices as bind_contingency_fact_choices
    from srd_contingency_world_fact import extend_runtime as extend_contingency_world_fact
    from srd_contingency_world_fact import (
        normalize_trigger_choice as normalize_contingency_fact_choice,
    )
    from srd_control_afflictions import PUBLIC_TOOLS as AFFLICTION_SUPPORT_TOOLS
    from srd_control_afflictions import extend_runtime as extend_control_afflictions
    from srd_control_afflictions import validate_definition as validate_control_afflictions
    from srd_control_behaviors import extend_runtime as extend_control_behaviors
    from srd_control_behaviors import validate_definition as validate_control_behavior
    from srd_control_binding import PUBLIC_TOOLS as BINDING_SUPPORT_TOOLS
    from srd_control_binding import extend_runtime as extend_control_binding
    from srd_control_binding import validate_profiles as validate_binding_profiles
    from srd_control_orders import PUBLIC_TOOLS as ORDER_SUPPORT_TOOLS
    from srd_control_orders import extend_runtime as extend_control_orders
    from srd_control_orders import validate_definition as validate_control_orders
    from srd_courier_body_movement import bind_runtime_consumers as bind_courier_body_movement
    from srd_courier_chronology import bind_runtime_consumers as bind_courier_chronology
    from srd_courier_interval_pace import bind_runtime_consumers as bind_courier_interval_pace
    from srd_d20_normalization import normalize_runtime as normalize_d20_runtime
    from srd_damage_type_receipts import bind_runtime as bind_damage_type_receipts
    from srd_damage_type_receipts import restore_runtime as restore_damage_type_receipts
    from srd_delayed_blast_completion import bind_runtime as bind_delayed_blast_completion
    from srd_delayed_blast_contacts import PUBLIC_TOOLS as DELAYED_CONTACT_SUPPORT_TOOLS
    from srd_delayed_blast_contacts import bind_runtime_consumers as bind_delayed_contacts
    from srd_delayed_blast_fireball import PUBLIC_TOOLS as DELAYED_BLAST_SUPPORT_TOOLS
    from srd_delayed_blast_fireball import bind_runtime_consumers as bind_delayed_blast
    from srd_delayed_blast_fireball import extend_cast_parameters as extend_delayed_blast_parameters
    from srd_delayed_blast_fireball import extend_runtime as extend_delayed_blast
    from srd_delayed_blast_fireball import schema as delayed_blast_schema
    from srd_delayed_blast_fireball import validate_profiles as validate_delayed_blast_profiles
    from srd_delayed_blast_ignition import bind_runtime_consumers as bind_delayed_ignition
    from srd_detection_mask_casting import extend_runtime as extend_detection_mask_casting
    from srd_disintegrate_bodies import bind_runtime_consumers as bind_disintegrate_bodies
    from srd_disintegrate_sources import bind_runtime_consumers as bind_disintegrate_sources
    from srd_divination_parameters import bind_runtime as trim_divination_parameters
    from srd_divination_spells import PUBLIC_TOOLS as DIVINATION_SUPPORT_TOOLS
    from srd_divination_spells import extend_runtime as extend_divination
    from srd_divination_spells import validate_profiles as validate_divination_profiles
    from srd_dragon_breath import PUBLIC_TOOLS as DRAGON_BREATH_SUPPORT_TOOLS
    from srd_dragon_breath import bind_runtime_consumers as bind_dragon_breath
    from srd_dragon_breath import schema as dragon_breath_schema
    from srd_dragon_breath import validate_profiles as validate_dragon_breath_profiles
    from srd_dragon_breath_magic_zones import (
        bind_runtime_consumers as bind_dragon_breath_magic_zones,
    )
    from srd_dream_spell import PUBLIC_TOOLS as DREAM_SUPPORT_TOOLS
    from srd_dream_spell import bind_runtime_consumers as bind_dream_consumers
    from srd_dream_spell import extend_runtime as extend_dream
    from srd_dream_spell import validate_profiles as validate_dream_profiles
    from srd_earthquake import PUBLIC_TOOLS as EARTHQUAKE_SUPPORT_TOOLS
    from srd_earthquake import bind_movement_consumers as bind_earthquake_movement
    from srd_earthquake import extend_runtime as extend_earthquake
    from srd_earthquake import validate_profiles as validate_earthquake_profiles
    from srd_earthquake_fissures import bind_completed_turn_consumer as bind_earthquake_fissure_turn
    from srd_earthquake_fissures import bind_movement_consumers as bind_earthquake_fissure_movement
    from srd_earthquake_fissures import extend_runtime as extend_earthquake_fissures
    from srd_earthquake_sources import bind_runtime as bind_earthquake_sources
    from srd_earthquake_structures import PUBLIC_TOOLS as EARTHQUAKE_STRUCTURE_SUPPORT_TOOLS
    from srd_earthquake_structures import (
        bind_final_receivers as bind_earthquake_structure_receivers,
    )
    from srd_earthquake_structures import extend_runtime as extend_earthquake_structures
    from srd_enhance_ability_source import bind_runtime_consumers as bind_enhance_ability_source
    from srd_environment_flaming_sphere import bind_runtime_consumers as bind_flaming_sphere_light
    from srd_environment_wall_thorns import extend_runtime as extend_thorn_wall
    from srd_environment_wall_thorns import validate_profiles as validate_thorn_wall_profiles
    from srd_environment_water import bind_flood_receivers as bind_water_waves
    from srd_environment_water import bind_runtime_consumers as bind_control_water
    from srd_environment_weather import bind_runtime_consumers as bind_control_weather
    from srd_environment_wind_wall import bind_runtime_consumers as bind_wind_wall
    from srd_exploration_cast_delegate import bind_runtime as bind_exploration_cast_delegate
    from srd_extended_casting import PUBLIC_TOOLS as EXTENDED_CASTING_TOOLS
    from srd_extended_casting import bind_private_casting_clocks
    from srd_extended_casting import extend_runtime as extend_casting
    from srd_feather_fall import bind_runtime_consumers as bind_feather_fall_consumers
    from srd_feather_fall import extend_runtime as extend_feather_fall
    from srd_find_the_path import PROFILES as FIND_PATH_PROFILES
    from srd_find_the_path import PUBLIC_TOOLS as FIND_PATH_SUPPORT_TOOLS
    from srd_find_the_path import bind_runtime_consumers as bind_find_path_consumers
    from srd_find_the_path import extend_runtime as extend_find_path
    from srd_find_the_path import validate_profiles as validate_find_path_source
    from srd_finger_death_zombies import PUBLIC_TOOLS as FINGER_ZOMBIE_SUPPORT_TOOLS
    from srd_finger_death_zombies import extend_runtime as extend_finger_death_zombies
    from srd_flame_blade import PUBLIC_TOOLS as FLAME_BLADE_SUPPORT_TOOLS
    from srd_flame_blade import bind_runtime_consumers as bind_flame_blade
    from srd_flame_blade_light import bind_runtime_consumers as bind_flame_blade_light
    from srd_flame_blade_objects import PUBLIC_TOOLS as FLAME_OBJECT_SUPPORT_TOOLS
    from srd_flame_blade_objects import bind_runtime_consumers as bind_flame_objects
    from srd_freezing_sphere import PUBLIC_TOOLS as FREEZING_SPHERE_SUPPORT_TOOLS
    from srd_freezing_sphere import bind_runtime_consumers as bind_freezing_sphere
    from srd_generator_yaml import scoped_generator_yaml
    from srd_giant_insect_actions import PUBLIC_TOOLS as INSECT_ACTION_SUPPORT_TOOLS
    from srd_giant_insect_actions import extend_runtime as extend_insect_actions
    from srd_guards_wards import PUBLIC_TOOLS as GUARDS_WARDS_SUPPORT_TOOLS
    from srd_guards_wards import extend_runtime as extend_guards_wards
    from srd_guards_wards import validate_profiles as validate_guards_wards_profiles
    from srd_guards_wards_doors import bind_door_consumers
    from srd_guards_wards_mouth import bind_runtime as bind_guards_mouth
    from srd_guards_wards_obscuration import extend_runtime as extend_guards_obscuration
    from srd_guards_wards_web import PUBLIC_TOOLS as GUARDS_WEB_SUPPORT_TOOLS
    from srd_guards_wards_web import bind_runtime_consumers as bind_guards_web_consumers
    from srd_guards_wards_web import extend_runtime as extend_guards_web
    from srd_guards_wards_web_burning import bind_runtime_consumers as bind_guards_web_burning
    from srd_gust_of_wind import PROFILES as GUST_PROFILES
    from srd_gust_of_wind import PUBLIC_TOOLS as GUST_SUPPORT_TOOLS
    from srd_gust_of_wind import bind_runtime_consumers as bind_gust_of_wind
    from srd_gust_of_wind import extend_runtime as extend_gust_of_wind
    from srd_gust_of_wind import validate_profiles as validate_gust_source
    from srd_hellish_rebuke import bind_runtime_consumers as bind_hellish_rebuke
    from srd_ice_storm_terrain import bind_runtime_consumers as bind_ice_storm_terrain
    from srd_illumination_body_consumers import (
        bind_runtime_consumers as bind_illumination_body_consumers,
    )
    from srd_illumination_object_consumers import (
        bind_runtime_consumers as bind_illumination_object_consumers,
    )
    from srd_illusion_appearance import PUBLIC_TOOLS as APPEARANCE_SUPPORT_TOOLS
    from srd_illusion_appearance import extend_runtime as extend_appearances
    from srd_illusion_appearance import validate_profiles as validate_appearance_profiles
    from srd_illusion_images import PUBLIC_TOOLS as IMAGE_SUPPORT_TOOLS
    from srd_illusion_images import extend_runtime as extend_images
    from srd_illusion_images import validate_profiles as validate_image_profiles
    from srd_illusion_spells import PUBLIC_TOOLS as ILLUSION_SUPPORT_TOOLS
    from srd_illusion_spells import extend_runtime as extend_illusions
    from srd_illusion_spells import validate_profiles as validate_illusion_profiles
    from srd_information_casting import bind_runtime_consumers as bind_information_casting
    from srd_information_spells import PUBLIC_TOOLS as INFORMATION_SUPPORT_TOOLS
    from srd_information_spells import extend_runtime as extend_information
    from srd_information_spells import validate_profiles as validate_information_profiles
    from srd_information_world_facts import PUBLIC_TOOLS as INFORMATION_WORLD_SUPPORT_TOOLS
    from srd_information_world_facts import bind_runtime_consumers as bind_information_world_facts
    from srd_information_world_facts import restore_runtime as restore_information_world_facts
    from srd_invisibility_cast_consumers import (
        bind_runtime_consumers as bind_invisibility_cast_consumers,
    )
    from srd_magic_jar import PUBLIC_TOOLS as MAGIC_JAR_SUPPORT_TOOLS
    from srd_magic_jar import extend_runtime as extend_magic_jar
    from srd_magic_jar import validate_profiles as validate_magic_jar_profiles
    from srd_magic_jar_consumers import bind_consumers as bind_magic_jar_consumers
    from srd_magic_jar_container import bind_container_consumers as bind_magic_jar_container
    from srd_magic_jar_equipment import bind_equipment_consumers as bind_magic_jar_equipment
    from srd_magic_jar_healing import bind_healing_consumers as bind_magic_jar_healing
    from srd_magic_jar_incoming_defenses import (
        bind_incoming_defense_consumers as bind_magic_jar_incoming_defenses,
    )
    from srd_magic_jar_materials import bind_material_consumers as bind_magic_jar_materials
    from srd_mansion_returns import bind_runtime_consumers as bind_mansion_returns
    from srd_meld_into_stone import PUBLIC_TOOLS as MELD_STONE_SUPPORT_TOOLS
    from srd_meld_into_stone import extend_runtime as extend_meld_stone
    from srd_meld_into_stone import validate_profiles as validate_meld_stone_profiles
    from srd_meld_stone_consumers import bind_consumers as bind_meld_stone_consumers
    from srd_meld_stone_consumers import bind_travel_consumers as bind_meld_travel_consumers
    from srd_memory_spells import PUBLIC_TOOLS as MEMORY_SUPPORT_TOOLS
    from srd_memory_spells import extend_runtime as extend_memory
    from srd_memory_spells import validate_profiles as validate_memory_profiles
    from srd_mirage_arcane import PUBLIC_TOOLS as MIRAGE_SUPPORT_TOOLS
    from srd_mirage_arcane import bind_runtime_consumers as bind_mirage_consumers
    from srd_mirage_arcane import extend_runtime as extend_mirage
    from srd_mirage_arcane import validate_profiles as validate_mirage_profiles
    from srd_mirage_geometry import extend_runtime as extend_mirage_geometry
    from srd_mirage_support import extend_runtime as extend_mirage_support
    from srd_mirage_support_lifecycle import bind_runtime_consumers as bind_mirage_support_lifecycle
    from srd_mirror_image_consumers import bind_runtime_consumers as bind_mirror_image_consumers
    from srd_move_earth import PUBLIC_TOOLS as MOVE_EARTH_SUPPORT_TOOLS
    from srd_move_earth import bind_removal_consumers as bind_move_earth_removals
    from srd_move_earth import bind_runtime_consumers as bind_move_earth
    from srd_move_earth import schema as move_earth_schema
    from srd_move_earth import validate_profiles as validate_move_earth_profiles
    from srd_move_earth_suppression import bind_runtime_consumers as bind_move_earth_suppression
    from srd_native_condition_sources import bind_runtime as bind_native_condition_sources
    from srd_native_mummy_actions import bind_mummy_glare_turn_consumers
    from srd_native_mummy_actions import extend_runtime as extend_native_mummy_actions
    from srd_native_mummy_creation import PUBLIC_TOOLS as MUMMY_SUPPORT_TOOLS
    from srd_native_mummy_creation import extend_runtime as extend_native_mummy_creation
    from srd_native_mummy_fist import bind_native_mummy_fist_attack_consumers
    from srd_native_mummy_fist import extend_runtime as extend_native_mummy_fist
    from srd_native_mummy_routine import extend_runtime as extend_native_mummy_routine
    from srd_native_undead_curses import bind_native_mummy_maximum_zero_consumers
    from srd_native_undead_curses import (
        bind_native_undead_rest_consumers as bind_native_undead_rest,
    )
    from srd_native_undead_curses import extend_runtime as extend_native_undead_curses
    from srd_native_wight_actions import bind_native_wight_life_drain_consumers
    from srd_native_wight_actions import extend_runtime as extend_native_wight
    from srd_native_wight_attacks import bind_native_wight_attack_consumers
    from srd_native_wight_attacks import extend_runtime as extend_native_wight_attacks
    from srd_native_wight_creation import PUBLIC_TOOLS as WIGHT_SUPPORT_TOOLS
    from srd_native_wight_creation import extend_runtime as extend_native_wight_creation
    from srd_native_wight_equipment import extend_runtime as extend_native_wight_equipment
    from srd_native_wight_rise import PUBLIC_TOOLS as WIGHT_RISE_SUPPORT_TOOLS
    from srd_native_wight_rise import bind_native_wight_rise_consumers
    from srd_native_wight_rise import extend_runtime as extend_native_wight_rise
    from srd_object_packet_types import bind_runtime_consumers as bind_object_packet_types
    from srd_ordinary_swimming import bind_runtime_consumers as bind_ordinary_swimming
    from srd_parameter_declarations import compact_runtime as compact_runtime_parameters
    from srd_parameter_declarations import restore_runtime as restore_parameter_declarations
    from srd_persistent_spell_areas import PUBLIC_TOOLS as AREA_SUPPORT_TOOLS
    from srd_persistent_spell_areas import extend_runtime as extend_persistent_areas
    from srd_phantom_steed import PUBLIC_TOOLS as PHANTOM_STEED_SUPPORT_TOOLS
    from srd_phantom_steed import bind_damage_consumers as bind_phantom_steed_damage
    from srd_phantom_steed import bind_runtime_consumers as bind_phantom_steed_consumers
    from srd_phantom_steed import extend_runtime as extend_phantom_steed
    from srd_phantom_steed import validate_profiles as validate_phantom_steed_profiles
    from srd_phantom_steed_dispel import bind_target_dispel as bind_phantom_steed_dispel
    from srd_phantom_steed_dispel import extend_runtime as extend_phantom_steed_dispel
    from srd_planar_ally import PUBLIC_TOOLS as PLANAR_ALLY_SUPPORT_TOOLS
    from srd_planar_ally import extend_runtime as extend_planar_ally
    from srd_planar_ally import validate_profiles as validate_planar_ally_profiles
    from srd_plant_growth import PUBLIC_TOOLS as PLANT_GROWTH_SUPPORT_TOOLS
    from srd_plant_growth import extend_runtime as extend_plant_growth
    from srd_plant_growth import validate_profiles as validate_plant_growth_profiles
    from srd_plant_growth_consumers import bind_casting_consumers as bind_plant_growth_casting
    from srd_plant_growth_consumers import bind_movement_consumers as bind_plant_growth_movement
    from srd_power_words import bind_runtime_consumers as bind_power_words
    from srd_prepared_object_storage import bind_runtime_consumers as bind_prepared_object_storage
    from srd_prismatic_damage_packets import bind_runtime as bind_prismatic_damage_packets
    from srd_prismatic_wall import PUBLIC_TOOLS as PRISMATIC_WALL_SUPPORT_TOOLS
    from srd_prismatic_wall import bind_runtime_consumers as bind_prismatic_wall_consumers
    from srd_prismatic_wall import extend_runtime as extend_prismatic_wall
    from srd_prismatic_wall import validate_profiles as validate_prismatic_wall_profiles
    from srd_prismatic_wall_attacks import bind_runtime as bind_prismatic_wall_attacks
    from srd_prismatic_wall_sources import bind_runtime as bind_prismatic_wall_sources
    from srd_produce_flame import PUBLIC_TOOLS as PRODUCE_FLAME_SUPPORT_TOOLS
    from srd_produce_flame import extend_runtime as extend_produce_flame
    from srd_produce_flame_reactions import PUBLIC_TOOLS as PRODUCE_FLAME_REACTION_TOOLS
    from srd_produce_flame_reactions import bind_runtime_consumers as bind_produce_flame_reactions
    from srd_progressive_afflictions import PUBLIC_TOOLS as PROGRESSIVE_SUPPORT_TOOLS
    from srd_progressive_afflictions import extend_runtime as extend_progressive
    from srd_progressive_afflictions import validate_definition as validate_progressive
    from srd_qualified_packet_implementations import (
        bind_runtime as bind_qualified_packet_implementations,
    )
    from srd_qualified_packet_implementations import (
        restore_runtime as restore_qualified_packet_implementations,
    )
    from srd_reaction_spells import PUBLIC_TOOLS as REACTION_SUPPORT_TOOLS
    from srd_reaction_spells import (
        bind_pending_support_fences,
        finalize_pending_spell_guards,
        normalize_pending_spell_guards,
    )
    from srd_reaction_spells import extend_runtime as extend_reactions
    from srd_reaction_spells import validate_profiles as validate_reaction_profiles
    from srd_received_damage_lifecycle import bind_runtime as bind_received_damage_lifecycle
    from srd_received_damage_reactions import bind_receiving_events
    from srd_reincarnate_native_traits import extend_runtime as extend_reincarnate_native_traits
    from srd_reincarnate_rerolls import extend_runtime as extend_reincarnate_rerolls
    from srd_retained_nullable_captures import bind_runtime as bind_retained_nullable_captures
    from srd_retained_nullable_captures import restore_runtime as restore_retained_nullable_captures
    from srd_retained_spell_attacks import PUBLIC_TOOLS as RETAINED_ATTACK_SUPPORT_TOOLS
    from srd_retained_spell_attacks import bind_runtime_consumers as bind_retained_spell_attacks
    from srd_retained_spell_attacks import schema as retained_attack_schema
    from srd_retained_spell_attacks import validate_profiles as validate_retained_attack_profiles
    from srd_retained_spell_parameters import bind_runtime as trim_retained_spell_parameters
    from srd_retained_spell_reactions import bind_runtime as bind_retained_spell_reactions
    from srd_retained_spell_reactions import restore_runtime as restore_retained_spell_reactions
    from srd_reverse_gravity import PROFILES as GRAVITY_PROFILES
    from srd_reverse_gravity import PUBLIC_TOOLS as GRAVITY_SUPPORT_TOOLS
    from srd_reverse_gravity import bind_runtime_consumers as bind_reverse_gravity
    from srd_reverse_gravity import extend_runtime as extend_reverse_gravity
    from srd_reverse_gravity import validate_profiles as validate_reverse_gravity_source
    from srd_rope_trick_returns import bind_runtime_consumers as bind_rope_trick_returns
    from srd_runtime_defaults import compact_runtime_defaults
    from srd_sanctuary_lists import bind_runtime_consumers as bind_sanctuary_lists
    from srd_scene_hand_occupancy import PUBLIC_TOOLS as HAND_OCCUPANCY_SUPPORT_TOOLS
    from srd_scene_hand_occupancy import extend_runtime as extend_hand_occupancy
    from srd_scene_relations import PUBLIC_TOOLS as SCENE_SUPPORT_TOOLS
    from srd_scene_relations import extend_runtime as extend_scene_relations
    from srd_scene_sight_origins import PUBLIC_TOOLS as SIGHT_ORIGIN_SUPPORT_TOOLS
    from srd_scene_sight_origins import bind_runtime_consumers as bind_scene_sight_origins
    from srd_scene_sight_origins import extend_runtime as extend_scene_sight_origins
    from srd_secure_area_materials import bind_runtime as bind_secure_area_materials
    from srd_secure_area_materials import extend_runtime as extend_secure_area_materials
    from srd_shapechange_weapons import bind_runtime_consumers as bind_shapechange_weapons
    from srd_shatter_objects import bind_runtime_consumers as bind_shatter_objects
    from srd_simulacrum import extend_runtime as extend_simulacrum
    from srd_simulacrum import validate_profiles as validate_simulacrum_profiles
    from srd_simulacrum_characters import PUBLIC_TOOLS as SIMULACRUM_CHARACTER_SUPPORT_TOOLS
    from srd_simulacrum_characters import (
        bind_casting_consumers as bind_simulacrum_character_casting,
    )
    from srd_simulacrum_characters import bind_check_consumer as bind_simulacrum_character_checks
    from srd_simulacrum_characters import bind_sources as bind_simulacrum_character_sources
    from srd_simulacrum_consumers import PUBLIC_TOOLS as SIMULACRUM_REPAIR_SUPPORT_TOOLS
    from srd_simulacrum_consumers import bind_consumers as bind_simulacrum_consumers
    from srd_simulacrum_equipment import bind_sources as bind_simulacrum_equipment
    from srd_simulacrum_turns import PUBLIC_TOOLS as SIMULACRUM_TURN_SUPPORT_TOOLS
    from srd_simulacrum_turns import bind_turn_consumers as bind_simulacrum_turns
    from srd_solid_spell_walls import bind_runtime_consumers as bind_solid_spell_walls
    from srd_solid_spell_walls import extend_runtime as extend_solid_spell_walls
    from srd_solid_spell_walls import validate_profiles as validate_solid_wall_profiles
    from srd_solid_wall_narrowing import bind_runtime_consumers as bind_solid_wall_narrowing
    from srd_source_relocation_node import bind_runtime_consumers as bind_source_relocation_node
    from srd_spell_barriers import PUBLIC_TOOLS as BARRIER_SUPPORT_TOOLS
    from srd_spell_barriers import bind_runtime_consumers as bind_barrier_consumers
    from srd_spell_barriers import extend_runtime as extend_barriers
    from srd_spell_barriers import validate_profiles as validate_barrier_profiles
    from srd_spell_begin_timing import bind_runtime_consumers as bind_spell_begin_timing
    from srd_spell_damage_consumers import extend_runtime as extend_spell_damage_consumers
    from srd_spell_damage_riders import PUBLIC_TOOLS as DAMAGE_RIDER_SUPPORT_TOOLS
    from srd_spell_damage_riders import bind_runtime_consumers as bind_damage_riders
    from srd_spell_damage_riders import extend_runtime as extend_damage_riders
    from srd_spell_damage_riders import schema as damage_rider_schema
    from srd_spell_damage_riders import validate_profiles as validate_damage_rider_profiles
    from srd_spell_detection_masks import extend_runtime as extend_detection_masks
    from srd_spell_detection_masks import validate_profiles as validate_detection_mask_profiles
    from srd_spell_environment import PUBLIC_TOOLS as ENVIRONMENT_SUPPORT_TOOLS
    from srd_spell_environment import bind_runtime_consumers as bind_environment_consumers
    from srd_spell_environment import extend_runtime as extend_environment
    from srd_spell_environment import validate_profiles as validate_environment_profiles
    from srd_spell_exile_composition import bind_runtime as bind_exile_completion
    from srd_spell_floating_disk import PUBLIC_TOOLS as DISK_SUPPORT_TOOLS
    from srd_spell_floating_disk import bind_runtime_consumers as bind_floating_disk_consumers
    from srd_spell_floating_disk import extend_runtime as extend_floating_disk
    from srd_spell_grants import complete_grant_policies, definition_schema, grant_policies
    from srd_spell_hallow import bind_runtime_consumers as bind_hallow_consumers
    from srd_spell_hallow import extend_runtime as extend_hallow
    from srd_spell_hallow import validate_profiles as validate_hallow_profiles
    from srd_spell_heat_metal import PUBLIC_TOOLS as HEAT_METAL_SUPPORT_TOOLS
    from srd_spell_heat_metal import bind_d20_runtime as bind_heat_metal_d20
    from srd_spell_heat_metal import extend_runtime as extend_heat_metal
    from srd_spell_heat_metal import validate_profiles as validate_heat_metal_profiles
    from srd_spell_illumination import PUBLIC_TOOLS as ILLUMINATION_SUPPORT_TOOLS
    from srd_spell_illumination import bind_moonbeam_runtime as bind_moonbeam_light
    from srd_spell_illumination import extend_runtime as extend_illumination
    from srd_spell_illumination import validate_profiles as validate_illumination_profiles
    from srd_spell_invisibility_timing import bind_runtime_consumers as bind_invisibility_completion
    from srd_spell_light_damage_riders import bind_area_consequences
    from srd_spell_magic_zones import bind_effect_consumers as bind_magic_zone_effects
    from srd_spell_magic_zones import bind_runtime_consumers as bind_magic_zone_consumers
    from srd_spell_magic_zones import extend_runtime as extend_magic_zones
    from srd_spell_magic_zones import normalize_runtime as normalize_magic_zone_runtime
    from srd_spell_magic_zones import validate_profiles as validate_magic_zone_profiles
    from srd_spell_material_stock import bind_material_stock
    from srd_spell_minor_world import PUBLIC_TOOLS as MINOR_WORLD_SUPPORT_TOOLS
    from srd_spell_minor_world import bind_d20_runtime as bind_minor_world_d20
    from srd_spell_minor_world import extend_runtime as extend_minor_world
    from srd_spell_minor_world import validate_profiles as validate_minor_world_profiles
    from srd_spell_numeric_distances import bind_runtime_consumers as bind_numeric_spell_distances
    from srd_spell_object_content import PUBLIC_TOOLS as OBJECT_CONTENT_SUPPORT_TOOLS
    from srd_spell_object_content import extend_runtime as extend_object_content
    from srd_spell_object_content import validate_profiles as validate_object_content_profiles
    from srd_spell_object_damage import bind_runtime_consumers as bind_area_object_damage
    from srd_spell_object_forces import PUBLIC_TOOLS as OBJECT_FORCE_SUPPORT_TOOLS
    from srd_spell_object_forces import bind_runtime_consumers as bind_object_force_consumers
    from srd_spell_object_forces import extend_runtime as extend_object_forces
    from srd_spell_object_targets import PUBLIC_TOOLS as OBJECT_TARGET_SUPPORT_TOOLS
    from srd_spell_object_targets import extend_runtime as extend_object_targets
    from srd_spell_object_utilities import PUBLIC_TOOLS as OBJECT_UTILITY_SUPPORT_TOOLS
    from srd_spell_object_utilities import extend_runtime as extend_object_utilities
    from srd_spell_object_utilities import validate_profiles as validate_object_utility_profiles
    from srd_spell_object_utility_sources import (
        bind_source_consumers as bind_object_utility_sources,
    )
    from srd_spell_recovery import PUBLIC_TOOLS as RECOVERY_SUPPORT_TOOLS
    from srd_spell_recovery import extend_casting_runtime as extend_recovery_casting
    from srd_spell_recovery import extend_runtime as extend_recovery
    from srd_spell_recovery import validate_definition as validate_recovery
    from srd_spell_removal_wakes import bind_runtime_consumers as bind_spell_removal_wakes
    from srd_spell_removals import PUBLIC_TOOLS as SPELL_REMOVAL_SUPPORT_TOOLS
    from srd_spell_removals import extend_runtime as extend_spell_removals
    from srd_spell_secure_areas import PUBLIC_TOOLS as SECURE_AREA_SUPPORT_TOOLS
    from srd_spell_secure_areas import bind_runtime_consumers as bind_secure_area_consumers
    from srd_spell_secure_areas import extend_runtime as extend_secure_areas
    from srd_spell_secure_areas import validate_profiles as validate_secure_area_profiles
    from srd_spell_source_instances import extend_runtime as extend_spell_sources
    from srd_spell_special_damage import PUBLIC_TOOLS as SPECIAL_DAMAGE_SUPPORT_TOOLS
    from srd_spell_special_damage import extend_runtime as extend_special_damage
    from srd_spell_summoning import ANIMATED_PUBLIC_TOOLS as ANIMATED_SUPPORT_TOOLS
    from srd_spell_summoning import FAMILIAR_PUBLIC_TOOLS as FAMILIAR_SUPPORT_TOOLS
    from srd_spell_summoning import HOUND_PUBLIC_TOOLS as HOUND_SUPPORT_TOOLS
    from srd_spell_summoning import PUBLIC_TOOLS as SUMMONING_SUPPORT_TOOLS
    from srd_spell_summoning import (
        bind_animated_undead_turn_consumers,
        bind_created_undead_cast_consumers,
        bind_hound_turn_consumers,
        bind_insect_runtime_consumers,
        bind_undead_fortitude_damage_consumers,
    )
    from srd_spell_summoning import extend_runtime as extend_summoning
    from srd_spell_summoning import validate_profiles as validate_summoning_profiles
    from srd_spell_supplies import PUBLIC_TOOLS as SUPPLY_SUPPORT_TOOLS
    from srd_spell_supplies import extend_runtime as extend_supplies
    from srd_spell_supplies import validate_profiles as validate_supply_profiles
    from srd_spell_travel import PUBLIC_TOOLS as TRAVEL_SUPPORT_TOOLS
    from srd_spell_travel import bind_runtime_consumers as bind_travel_consumers
    from srd_spell_travel import extend_runtime as extend_travel
    from srd_spell_travel import validate_profiles as validate_travel_profiles
    from srd_spell_travel_dwelling_lifetime import emit_runtime as emit_dwelling_lifetime
    from srd_spell_travel_gate_transit import emit_late_gate_geometry
    from srd_spell_travel_servants import emit_late_servant_access
    from srd_spell_travel_telekinesis import emit_late_handoff as emit_late_telekinesis_handoff
    from srd_spell_travel_telekinesis_fine import emit_late_access as emit_late_telekinesis_access
    from srd_spell_travel_telekinesis_pour import emit_late_pour as emit_late_telekinesis_pour
    from srd_spell_trigger_composition import bind_runtime as bind_trigger_consumers
    from srd_spell_triggers import PUBLIC_TOOLS as TRIGGER_SUPPORT_TOOLS
    from srd_spell_triggers import extend_runtime as extend_triggers
    from srd_spell_triggers import validate_profiles as validate_trigger_profiles
    from srd_spell_wards import PUBLIC_TOOLS as WARD_SUPPORT_TOOLS
    from srd_spell_wards import extend_runtime as extend_wards
    from srd_spell_wards import validate_profiles as validate_ward_profiles
    from srd_stone_spell_wall import bind_runtime_consumers as bind_stone_spell_wall
    from srd_stone_spell_wall import extend_runtime as extend_stone_spell_wall
    from srd_stone_spell_wall import validate_profiles as validate_stone_wall_profiles
    from srd_storm_conditions import bind_runtime_consumers as bind_storm_conditions
    from srd_storm_later_turns import bind_runtime_consumers as bind_storm_later_turns
    from srd_storm_lightning import PUBLIC_TOOLS as STORM_LIGHTNING_SUPPORT_TOOLS
    from srd_storm_lightning import bind_runtime_consumers as bind_storm_lightning
    from srd_storm_movement import bind_runtime_consumers as bind_storm_movement
    from srd_storm_objects import bind_runtime_consumers as bind_storm_objects
    from srd_storm_of_vengeance import bind_runtime_consumers as bind_storm
    from srd_storm_of_vengeance import schema as storm_schema
    from srd_storm_of_vengeance import validate_profiles as validate_storm_profiles
    from srd_storm_weapon_attacks import (
        bind_runtime_consumers as bind_storm_weapon_attacks,
    )
    from srd_storm_weapon_attacks import (
        bind_saved_attack_consumers as bind_storm_saved_weapon_attacks,
    )
    from srd_storm_wind import bind_runtime_consumers as bind_storm_wind
    from srd_sunbeam import PUBLIC_TOOLS as SUNBEAM_SUPPORT_TOOLS
    from srd_sunbeam import bind_runtime_consumers as bind_sunbeam
    from srd_sunbeam import extend_runtime as extend_sunbeam
    from srd_sunbeam import schema as sunbeam_schema
    from srd_sunbeam import validate_profiles as validate_sunbeam_profiles
    from srd_thunderwave_consequences import PUBLIC_TOOLS as THUNDERWAVE_SUPPORT_TOOLS
    from srd_thunderwave_consequences import bind_runtime_consumers as bind_thunderwave_consequences
    from srd_time_stop import PUBLIC_TOOLS as TIME_STOP_SUPPORT_TOOLS
    from srd_time_stop import bind_runtime_consumers as bind_time_stop_consumers
    from srd_time_stop import extend_runtime as extend_time_stop
    from srd_time_stop import validate_profiles as validate_time_stop_profiles
    from srd_time_stop_effects import bind_received_effects as bind_time_stop_received
    from srd_time_stop_effects import bind_source_consumers as bind_time_stop_sources
    from srd_time_stop_retained_changes import (
        bind_runtime_consumers as bind_time_stop_retained_changes,
    )
    from srd_time_stop_retained_changes import restore_runtime as restore_time_stop_retained_changes
    from srd_time_stop_retained_damage import bind_runtime_consumers as bind_time_stop_retained
    from srd_time_stop_retained_damage import restore_runtime as restore_time_stop_retained
    from srd_timed_actor_defenses import _load_runtime
    from srd_timed_actor_defenses import extend_runtime as extend_actor_defenses
    from srd_timed_senses import PUBLIC_TOOLS as SENSE_SUPPORT_TOOLS
    from srd_timed_senses import extend_runtime as extend_senses
    from srd_timed_senses import validate_profiles as validate_sense_profiles
    from srd_transformation_moonbeam import bind_runtime_consumers as bind_moonbeam_producers
    from srd_transformation_spells import PUBLIC_TOOLS as TRANSFORMATION_SUPPORT_TOOLS
    from srd_transformation_spells import extend_runtime as extend_transformations
    from srd_transformation_spells import validate_profiles as validate_transformation_profiles
    from srd_true_polymorph_objects import PUBLIC_TOOLS as TRANSFORMATION_OBJECT_SUPPORT_TOOLS
    from srd_true_polymorph_objects import extend_runtime as extend_transformation_objects
    from srd_true_strike import bind_runtime_consumers as bind_true_strike
    from srd_true_strike import extend_cast_parameters as extend_true_strike_parameters
    from srd_true_strike import restore_runtime as restore_true_strike
    from srd_true_strike import schema as true_strike_schema
    from srd_true_strike import validate_profiles as validate_true_strike_profiles
    from srd_tsunami import PROFILES as TSUNAMI_PROFILES
    from srd_tsunami import PUBLIC_TOOLS as TSUNAMI_SUPPORT_TOOLS
    from srd_tsunami import bind_runtime_consumers as bind_tsunami
    from srd_tsunami import extend_runtime as extend_tsunami
    from srd_tsunami import validate_profiles as validate_tsunami_source
    from srd_tsunami_completion import bind_runtime as bind_tsunami_completion
    from srd_tsunami_forced_travel import bind_runtime_consumers as bind_tsunami_forced_travel
    from srd_tsunami_source_adoption import bind_runtime as bind_tsunami_sources
    from srd_tsunami_travel import bind_runtime_consumers as bind_tsunami_travel
    from srd_wall_ice_relocation import bind_runtime_consumers as bind_wall_ice_relocation
    from srd_warding_bond_damage import bind_runtime_consumers as bind_warding_bond_damage
    from srd_whirlpool_pull import bind_runtime_consumers as bind_whirlpool_pull
    from srd_wish import bind_runtime_consumers as bind_wish
    from srd_wish import extend_runtime as extend_wish
    from srd_wish import schema as wish_schema
    from srd_wish import validate_definition as validate_wish

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "rulesets" / "dnd-srd-5.2.1"
CONTENT_ROOT = PACKAGE_ROOT / "content"
CORPUS_PATH = CONTENT_ROOT / "corpus" / "spells.json"
RULES_GLOSSARY_CORPUS_PATH = CONTENT_ROOT / "corpus" / "rules_glossary.json"
PLAYING_THE_GAME_CORPUS_PATH = CONTENT_ROOT / "corpus" / "playing_the_game.json"
RECORD_PATH = PACKAGE_ROOT / "package-record.json"
DECLARATIONS_PATH = CONTENT_ROOT / "content-declarations.json"
REFERENCE_RECORD_PATH = PACKAGE_ROOT / "reference-package-record.json"
IDENTITY_PATH = PACKAGE_ROOT / "package-identity.json"
REFERENCE_DECLARATIONS_PATH = CONTENT_ROOT / "reference-content-declarations.json"
INVENTORY_PATH = CONTENT_ROOT / "spell-status-inventory.json"
SUPPORT_PATH = PACKAGE_ROOT / "support" / "gameplay-resolution.json"
MAGIC_MISSILE_SOURCE_PATH = CONTENT_ROOT / "magic-missile.source.json"
SPELL_MECHANICS_PATH = CONTENT_ROOT / "spell-mechanics.yaml"
SPELL_MECHANICS_SOURCE_PATH = CONTENT_ROOT / "spell-mechanics-source.json"
BEAST_FORMS_PATH = CONTENT_ROOT / "beast-form-index.json"
NON_BEAST_FORMS_PATH = CONTENT_ROOT / "non-beast-form-index.json"
RUNTIME_PATH = PACKAGE_ROOT / "runtime" / "ruleset.yaml"
SOURCE_SHA256 = "8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87"
SPELL_COUNT = 339
SPELL_CAPABILITY = "dnd.srd.5.2.1.spells"
SPELL_TOOL = "cast_spell"
EXPLORATION_SPELL_TOOL = "cast_spell_outside_combat"
SPELL_SUPPORT_TOOLS = {
    "record_srd_contingency_world_fact",
    "advance_srd_lasting_time",
    "end_concentration",
    "dash_srd_mobility_bonus_action",
    "transition_srd_water_walk",
    "dismiss_srd_mobility_spell",
    "shake_srd_creature_from_stupor",
    "shake_srd_sleeping_creature",
    "collect_srd_irresistible_dance",
    "resolve_srd_confused_turn",
    "resolve_srd_fear_turn",
    "read_srd_passive_perception",
    "record_srd_control_held_items",
} | (
    SENSE_SUPPORT_TOOLS
    | MIRAGE_SUPPORT_TOOLS
    | RETAINED_ATTACK_SUPPORT_TOOLS
    | DRAGON_BREATH_SUPPORT_TOOLS
    | DAMAGE_RIDER_SUPPORT_TOOLS
    | AFTER_HIT_SUPPORT_TOOLS
    | INSECT_ACTION_SUPPORT_TOOLS
    | THUNDERWAVE_SUPPORT_TOOLS
    | FLAME_BLADE_SUPPORT_TOOLS
    | FLAME_OBJECT_SUPPORT_TOOLS
    | GUARDS_WEB_SUPPORT_TOOLS
    | HAND_OCCUPANCY_SUPPORT_TOOLS
    | DREAM_SUPPORT_TOOLS
    | EXTENDED_CASTING_TOOLS
    | REACTION_SUPPORT_TOOLS
    | SCENE_SUPPORT_TOOLS
    | ACTOR_MODIFIER_TOOLS
    | ORDER_SUPPORT_TOOLS
    | INFORMATION_SUPPORT_TOOLS
    | INFORMATION_WORLD_SUPPORT_TOOLS
    | AFFLICTION_SUPPORT_TOOLS
    | AREA_SUPPORT_TOOLS
    | DIVINATION_SUPPORT_TOOLS
    | TRANSFORMATION_SUPPORT_TOOLS
    | TRANSFORMATION_OBJECT_SUPPORT_TOOLS
    | RECOVERY_SUPPORT_TOOLS
    | TRAVEL_SUPPORT_TOOLS
    | PROGRESSIVE_SUPPORT_TOOLS
    | ENVIRONMENT_SUPPORT_TOOLS
    | FREEZING_SPHERE_SUPPORT_TOOLS
    | SUMMONING_SUPPORT_TOOLS
    | PLANAR_ALLY_SUPPORT_TOOLS
    | SUPPLY_SUPPORT_TOOLS
    | SIGHT_ORIGIN_SUPPORT_TOOLS
    | ILLUMINATION_SUPPORT_TOOLS
    | PRODUCE_FLAME_REACTION_TOOLS
    | PRODUCE_FLAME_SUPPORT_TOOLS
    | OBJECT_TARGET_SUPPORT_TOOLS
    | OBJECT_CONTENT_SUPPORT_TOOLS
    | DISK_SUPPORT_TOOLS
    | OBJECT_FORCE_SUPPORT_TOOLS
    | OBJECT_UTILITY_SUPPORT_TOOLS
    | HEAT_METAL_SUPPORT_TOOLS
    | SPELL_REMOVAL_SUPPORT_TOOLS
    | HOUND_SUPPORT_TOOLS
    | FAMILIAR_SUPPORT_TOOLS
    | ANIMATED_SUPPORT_TOOLS
    | MINOR_WORLD_SUPPORT_TOOLS
    | MEMORY_SUPPORT_TOOLS
    | BINDING_SUPPORT_TOOLS
    | BODY_SOUL_SUPPORT_TOOLS
    | MAGIC_JAR_SUPPORT_TOOLS
    | MELD_STONE_SUPPORT_TOOLS
    | PHANTOM_STEED_SUPPORT_TOOLS
    | PLANT_GROWTH_SUPPORT_TOOLS
    | SUNBEAM_SUPPORT_TOOLS
    | GUST_SUPPORT_TOOLS
    | CONJURED_SUPPORT_TOOLS
    | CELESTIAL_LIGHT_SUPPORT_TOOLS
    | STORM_LIGHTNING_SUPPORT_TOOLS
    | AWAKEN_SUPPORT_TOOLS
    | EARTHQUAKE_SUPPORT_TOOLS
    | EARTHQUAKE_STRUCTURE_SUPPORT_TOOLS
    | ORB_SUPPORT_TOOLS
    | FINGER_ZOMBIE_SUPPORT_TOOLS
    | ANIMATE_OBJECT_SUPPORT_TOOLS
    | GRAVITY_SUPPORT_TOOLS
    | SIMULACRUM_REPAIR_SUPPORT_TOOLS
    | SIMULACRUM_CHARACTER_SUPPORT_TOOLS
    | WIGHT_SUPPORT_TOOLS
    | WIGHT_RISE_SUPPORT_TOOLS
    | MUMMY_SUPPORT_TOOLS
    | SIMULACRUM_TURN_SUPPORT_TOOLS
    | ASTRAL_SUPPORT_TOOLS
    | MOVE_EARTH_SUPPORT_TOOLS
    | TSUNAMI_SUPPORT_TOOLS
    | ARCANE_HAND_SUPPORT_TOOLS
    | DELAYED_BLAST_SUPPORT_TOOLS
    | DELAYED_CONTACT_SUPPORT_TOOLS
    | FIND_PATH_SUPPORT_TOOLS
    | ILLUSION_SUPPORT_TOOLS
    | APPEARANCE_SUPPORT_TOOLS
    | IMAGE_SUPPORT_TOOLS
    | TIME_STOP_SUPPORT_TOOLS
    | SPECIAL_DAMAGE_SUPPORT_TOOLS
    | PRISMATIC_WALL_SUPPORT_TOOLS
    | BARRIER_SUPPORT_TOOLS
    | WARD_SUPPORT_TOOLS
    | TRIGGER_SUPPORT_TOOLS
    | GUARDS_WARDS_SUPPORT_TOOLS
    | SECURE_AREA_SUPPORT_TOOLS
)
RETIRED_SPELL_TOOLS = {"cast_magic_missile"}
SPELL_SELECTOR_START = "        # BEGIN GENERATED SPELL SELECTOR"
SPELL_SELECTOR_END = "        # END GENERATED SPELL SELECTOR"
SPELL_DEFINITIONS_START = "    # BEGIN GENERATED SPELL DEFINITIONS"
SPELL_DEFINITIONS_END = "    # END GENERATED SPELL DEFINITIONS"
BEAST_FORMS_START = "    # BEGIN GENERATED BEAST FORM CORES"
BEAST_FORMS_END = "    # END GENERATED BEAST FORM CORES"
CASTING_REQUIREMENTS_START = "    # BEGIN GENERATED CASTING REQUIREMENTS"
CASTING_REQUIREMENTS_END = "    # END GENERATED CASTING REQUIREMENTS"
DAMAGE_TYPES = (
    "acid",
    "bludgeoning",
    "cold",
    "fire",
    "force",
    "lightning",
    "necrotic",
    "piercing",
    "poison",
    "psychic",
    "radiant",
    "slashing",
    "thunder",
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _replace_bytes(path: Path, value: bytes) -> None:
    """Publish one fully written generated output through same-directory replace."""

    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class _UniqueKeyLoader(yaml.SafeLoader):  # type: ignore[misc]
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _replace_generated_block(text: str, start: str, end: str, body: list[str]) -> str:
    lines = text.splitlines(keepends=True)
    start_lines = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == start]
    end_lines = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == end]
    if len(start_lines) != 1 or len(end_lines) != 1:
        raise SystemExit(f"Runtime must contain exactly one generated block: {start}")
    start_index = start_lines[0]
    end_index = end_lines[0]
    if end_index <= start_index:
        raise SystemExit(f"Generated runtime markers are reversed: {start}")
    generated_lines = [f"{line}\n" for line in body]
    return "".join(lines[: start_index + 1] + generated_lines + lines[end_index:])


def _register_spell_family_schema(text: str, family: str, schema: dict) -> str:
    """Register one source-checked family without rewriting executable owners."""
    lines = text.splitlines(keepends=True)
    owners = [i for i, line in enumerate(lines) if line.rstrip() == "  definitions.spells:"]
    if len(owners) != 1:
        raise ValueError("Spell family schemas need one owning definitions.spells section.")
    start = owners[0]
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].strip() and (not lines[i].startswith(" ") or re.match(r"^  \S", lines[i]))
        ),
        len(lines),
    )
    section = "".join(lines[start:end])
    row = "      " + family + ": " + _canonical(schema).decode("utf-8")
    pattern = r"(?m)^      " + re.escape(family) + r": .*$"
    if re.search(pattern, section):
        section, count = re.subn(pattern, lambda _: row, section)
        if count != 1:
            raise ValueError(f"The {family} schema must have one owning variant.")
    else:
        variants = _load_runtime(text)["definition_row_schemas"]["definitions.spells"]["variants"]
        anchor = r"(?m)^      " + re.escape(next(iter(variants))) + r":"
        section, count = re.subn(anchor, lambda match: row + "\n" + match[0], section)
        if count != 1:
            raise ValueError(f"The {family} schema needs one existing variant anchor.")
    return "".join(lines[:start]) + section + "".join(lines[end:])


def _tsunami_spell_schema() -> dict:
    """Use the owning bounded wall profile with the section-scoped schema registrar."""
    properties = {
        name: {
            "type": "boolean"
            if type(value) is bool
            else "integer"
            if type(value) is int
            else "string"
        }
        for name, value in TSUNAMI_PROFILES["tsunami"].items()
        if name != "family"
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _register_retained_attack_schema(text: str) -> str:
    """Register the retained attack family through the same closed variant boundary."""
    return _register_spell_family_schema(text, "retained_spell_attack", retained_attack_schema())


def _spell_declaration(text: str) -> dict[str, Any]:
    try:
        declaration = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise SystemExit("Spell mechanics declaration is invalid YAML") from error
    if not isinstance(declaration, dict):
        raise SystemExit("Spell mechanics declaration must be a mapping")
    if set(declaration) != {
        "format_id",
        "format_version",
        "source_sha256",
        "general_rules",
        "spells",
    }:
        raise SystemExit("Spell mechanics declaration has missing or unknown top-level fields")
    if (
        declaration.get("format_id") != "sylvia-gm-srd-spell-mechanics-declaration"
        or declaration.get("format_version") != 29
        or declaration.get("source_sha256") != SOURCE_SHA256
    ):
        raise SystemExit("Spell mechanics declaration does not bind the admitted SRD bytes")
    general_rules = declaration.get("general_rules")
    if not isinstance(general_rules, dict) or not general_rules:
        raise SystemExit("Spell mechanics declaration must contain its general casting rules")
    if list(general_rules) != sorted(general_rules):
        raise SystemExit("General spell rules must be sorted by stable rule id")
    for rule_id, source in general_rules.items():
        if (
            not isinstance(rule_id, str)
            or not isinstance(source, dict)
            or set(source) != {"corpus", "page", "source_heading", "next_heading", "not_yet_mapped"}
            or source.get("corpus") not in {"spells", "playing_the_game", "rules_glossary"}
            or _spell_id(str(source.get("source_heading", ""))) != rule_id
            or not isinstance(source.get("not_yet_mapped"), list)
            or any(
                not isinstance(item, str) or not item for item in source.get("not_yet_mapped", [])
            )
        ):
            raise SystemExit(f"General spell rule source is invalid: {rule_id}")
        next_heading = source.get("next_heading")
        terminal_rule = (
            rule_id == "combining_spell_effects"
            and source.get("corpus") == "spells"
            and source.get("page") == 106
            and source.get("source_heading") == "Combining Spell Effects"
        ) or (
            rule_id == "dead"
            and source.get("corpus") == "rules_glossary"
            and source.get("page") == 180
            and source.get("source_heading") == "Dead"
        )
        if next_heading is None and not terminal_rule:
            raise SystemExit("Only the exact terminal general rule may use page end")
        if next_heading is not None and not isinstance(next_heading, str):
            raise SystemExit(f"General spell rule source is invalid: {rule_id}")
    spells = declaration.get("spells")
    if not isinstance(spells, dict) or not spells:
        raise SystemExit("Spell mechanics declaration must contain executable spells")
    if list(spells) != sorted(spells):
        raise SystemExit("Spell mechanics declarations must be sorted by stable spell id")
    for spell_id, spell in spells.items():
        if not isinstance(spell_id, str) or not isinstance(spell, dict):
            raise SystemExit("Spell mechanics declarations must be mappings")
        if set(spell) != {"source", "definition"}:
            raise SystemExit(f"Spell declaration has missing or unknown fields: {spell_id}")
        source = spell.get("source")
        if not isinstance(source, dict) or set(source) not in (
            {"page", "source_heading", "next_heading", "not_yet_mapped"},
            {
                "page",
                "source_heading",
                "next_heading",
                "not_yet_mapped",
                "continuation_start",
                "continuation_next_heading",
            },
            {"pages", "source_heading", "next_heading", "not_yet_mapped"},
        ):
            raise SystemExit(f"Spell source has missing or unknown fields: {spell_id}")
        if _spell_id(str(source.get("source_heading", ""))) != spell_id:
            raise SystemExit(f"Spell source heading differs from its stable id: {spell_id}")
        source_pages = source.get("pages")
        if source_pages is not None and (
            not isinstance(source_pages, list)
            or len(source_pages) < 2
            or any(not isinstance(page, int) or isinstance(page, bool) for page in source_pages)
            or source_pages != list(range(source_pages[0], source_pages[-1] + 1))
        ):
            raise SystemExit(
                f"Spell source pages must be a consecutive increasing span: {spell_id}"
            )
        definition = spell.get("definition")
        if not isinstance(definition, dict) or not isinstance(definition.get("family"), str):
            raise SystemExit(f"Spell declaration lacks a family definition: {spell_id}")
    return declaration


def _project_final_spell_selector_copies(text, definitions):
    """Refresh actual exact-selector copies after the public declaration projection."""
    from copy import deepcopy

    if __package__:
        from .srd_timed_senses import _replace_tools
    else:
        from srd_timed_senses import _replace_tools

    text = _replace_generated_block(
        text,
        SPELL_SELECTOR_START,
        SPELL_SELECTOR_END,
        [f"          - {spell_id}" for spell_id in definitions],
    )
    runtime = _load_runtime(text)
    public = next(p for p in runtime["tools"][SPELL_TOOL]["parameters"] if p["name"] == "spell")
    if public["enum_values"] != list(definitions):
        raise ValueError("Public spell selector differs from its exact declaration keys")
    changed = {}
    for name in ("begin_srd_spell_action", "prepare_srd_contingent_child"):
        tool = deepcopy(runtime["tools"][name])
        parameters = [p for p in tool["parameters"] if p["name"] == "spell"]
        if len(parameters) != 1 or parameters[0].get("type") != public.get("type"):
            raise ValueError("Exact spell selector copy has an unsupported declaration")
        parameters[0]["enum_values"] = list(public["enum_values"])
        changed[name] = tool
    # Keep exact family predicates on one line for the unchanged composition check.
    changed[SPELL_TOOL] = deepcopy(runtime["tools"][SPELL_TOOL])
    return _replace_tools(text, changed, width=1000)


def _compile_runtime(
    declaration: dict[str, Any],
    runtime_source: str,
    requirements: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    definitions = {
        spell_id: declaration["spells"][spell_id]["definition"]
        for spell_id in sorted(declaration["spells"])
    }
    _validate_definition_profiles(definitions)
    for spell_id, definition in definitions.items():
        if requirements[spell_id]["level"] != definition["base_level"]:
            raise SystemExit(f"Executable spell level differs from its SRD header: {spell_id}")
    beast_index = json.loads(BEAST_FORMS_PATH.read_text(encoding="utf-8"))
    if beast_index != build_beast_index():
        raise SystemExit("Generated Beast form cores differ from the admitted SRD corpus")
    beast_forms = beast_index["forms"]
    runtime_source = restore_parameter_declarations(runtime_source)
    runtime_source = restore_information_world_facts(runtime_source)
    runtime_source = restore_time_stop_retained(runtime_source)
    runtime_source = restore_time_stop_retained_changes(runtime_source)
    runtime_source = restore_true_strike(runtime_source)
    runtime_source = restore_after_hit_riders(runtime_source)
    runtime_source = restore_attack_rider_d20(runtime_source)
    runtime_source = restore_qualified_packet_implementations(runtime_source)
    runtime_source = restore_damage_type_receipts(runtime_source)
    runtime_source = restore_retained_nullable_captures(runtime_source)
    runtime_source = restore_retained_spell_reactions(runtime_source)
    runtime_source = bind_enhance_ability_source(runtime_source)
    runtime_source = normalize_magic_zone_runtime(runtime_source)
    runtime_source = normalize_pending_spell_guards(runtime_source)
    runtime_text = extend_actor_defenses(_retire_manifest_owned_runtime_fields(runtime_source))
    runtime_text = extend_actor_modifiers(runtime_text)
    runtime_text = extend_senses(runtime_text)
    runtime_text = extend_scene_relations(runtime_text)
    runtime_text = extend_control_behaviors(runtime_text)
    runtime_text = extend_information(runtime_text)
    runtime_text = extend_control_orders(runtime_text)
    runtime_text = extend_control_afflictions(runtime_text)
    runtime_text = extend_persistent_areas(runtime_text)
    runtime_text = extend_transformations(runtime_text)
    runtime_text = extend_recovery(runtime_text)
    runtime_text = extend_progressive(runtime_text)
    runtime_text = extend_secure_areas(runtime_text)
    runtime_text = extend_hallow(runtime_text)
    runtime_text = extend_guards_wards(runtime_text)
    runtime_text = extend_guards_web(runtime_text)
    runtime_text = extend_guards_obscuration(runtime_text)
    runtime_text = extend_secure_area_materials(runtime_text)
    runtime_text = extend_awaken(runtime_text)
    runtime_text = extend_plant_growth(runtime_text)
    runtime_text = extend_gust_of_wind(runtime_text)
    runtime_text = extend_contingency(runtime_text)
    runtime_text = normalize_contingency_fact_choice(runtime_text)
    runtime_text = extend_contingency_water(runtime_text)
    runtime_text = extend_contingency_child_casting(runtime_text)
    runtime_text = extend_contingency_world_fact(runtime_text)
    runtime_text = extend_casting(runtime_text, spell_ids=sorted(definitions))
    runtime_text = bind_plant_growth_casting(runtime_text)
    runtime_text = extend_divination(runtime_text)
    runtime_text = extend_find_path(runtime_text)
    runtime_text = extend_recovery_casting(runtime_text)
    runtime_text = extend_travel(runtime_text)
    runtime_text = extend_environment(runtime_text)
    runtime_text = extend_thorn_wall(runtime_text)
    runtime_text = extend_barriers(runtime_text)
    runtime_text = extend_wards(runtime_text)
    runtime_text = extend_magic_zones(runtime_text)
    runtime_text = extend_cone_qualification(runtime_text)
    runtime_text = extend_summoning(runtime_text)
    runtime_text = extend_planar_ally(runtime_text)
    runtime_text = extend_phantom_steed(runtime_text)
    runtime_text = extend_phantom_steed_dispel(runtime_text)
    runtime_text = extend_illumination(runtime_text)
    runtime_text = extend_illusions(runtime_text)
    runtime_text = extend_appearances(runtime_text)
    runtime_text = extend_images(runtime_text)
    runtime_text = extend_mirage(runtime_text)
    runtime_text = extend_mirage_geometry(runtime_text)
    runtime_text = extend_dream(runtime_text)
    runtime_text = extend_time_stop(runtime_text)
    runtime_text = extend_object_targets(runtime_text)
    runtime_text = extend_object_content(runtime_text)
    runtime_text = extend_minor_world(runtime_text)
    runtime_text = extend_memory(runtime_text)
    runtime_text = extend_control_binding(runtime_text)
    runtime_text = extend_body_soul(runtime_text)
    runtime_text = extend_magic_jar(runtime_text)
    runtime_text = extend_supplies(runtime_text)
    runtime_text = extend_object_utilities(runtime_text)
    runtime_text = extend_object_forces(runtime_text)
    runtime_text = extend_floating_disk(runtime_text)
    runtime_text = extend_heat_metal(runtime_text)
    runtime_text = extend_damage_riders(runtime_text)
    runtime_text = extend_feather_fall(runtime_text)
    runtime_text = extend_triggers(runtime_text)
    runtime_text = extend_produce_flame(runtime_text)
    runtime_text = extend_detection_masks(runtime_text)
    runtime_text = extend_detection_mask_casting(runtime_text)
    runtime_text = bind_travel_consumers(runtime_text)
    runtime_text = bind_object_force_consumers(runtime_text)
    runtime_text = bind_mirage_consumers(runtime_text)
    runtime_text = extend_scene_sight_origins(runtime_text)
    runtime_text = bind_environment_consumers(runtime_text)
    runtime_text = bind_barrier_consumers(runtime_text)
    runtime_text = bind_magic_zone_consumers(runtime_text)
    runtime_text = extend_special_damage(runtime_text)
    runtime_text = extend_meld_stone(runtime_text)
    runtime_text = extend_prismatic_wall(runtime_text)
    runtime_text = extend_solid_spell_walls(runtime_text)
    runtime_text = extend_stone_spell_wall(runtime_text)
    runtime_text = bind_solid_wall_narrowing(runtime_text)
    runtime_text = extend_sunbeam(runtime_text)
    runtime_text = _register_spell_family_schema(runtime_text, "sunbeam", sunbeam_schema())
    runtime_text = _register_spell_family_schema(runtime_text, "storm_of_vengeance", storm_schema())
    runtime_text = _register_spell_family_schema(
        runtime_text, "astral_projection", astral_projection_schema()
    )
    runtime_text = extend_reverse_gravity(runtime_text)
    runtime_text = _register_spell_family_schema(runtime_text, "tsunami", _tsunami_spell_schema())
    runtime_text = extend_tsunami(runtime_text)
    runtime_text = extend_animate_objects(runtime_text)
    runtime_text = _register_spell_family_schema(
        runtime_text, "animate_objects", animate_object_schema()
    )
    runtime_text = bind_arcane_hand(runtime_text)
    runtime_text = _register_spell_family_schema(runtime_text, "arcane_hand", arcane_hand_schema())
    runtime_text = extend_delayed_blast_parameters(runtime_text)
    runtime_text = extend_delayed_blast(runtime_text)
    runtime_text = bind_delayed_contacts(runtime_text)
    runtime_text = _register_spell_family_schema(
        runtime_text, "delayed_blast_fireball", delayed_blast_schema()
    )
    runtime_text = refresh_exploration_runtime(runtime_text)
    runtime_text = bind_conjured_presences(runtime_text)
    runtime_text = bind_celestial_lights(runtime_text)
    runtime_text = bind_celestial_shared_rolls(runtime_text)
    runtime_text = bind_celestial_entries(runtime_text)
    runtime_text = _register_spell_family_schema(
        runtime_text, "conjured_presence", conjured_presence_schema()
    )
    runtime_text = emit_dwelling_lifetime(runtime_text)
    runtime_text = extend_spell_sources(
        runtime_text,
        requirements=requirements,
        definitions=definitions,
    )
    runtime_text = normalize_contingency_fact_choice(runtime_text)
    runtime_text = bind_contingency_water_choices(runtime_text)
    runtime_text = bind_contingency_child_choices(runtime_text)
    runtime_text = bind_contingency_fact_choices(runtime_text)
    runtime_text = extend_wish(runtime_text)
    runtime_text = bind_move_earth(runtime_text)
    runtime_text = _register_spell_family_schema(runtime_text, "wish", wish_schema())
    runtime_text = _register_spell_family_schema(runtime_text, "move_earth", move_earth_schema())
    runtime_text = extend_earthquake(runtime_text)
    runtime_text = extend_earthquake_structures(runtime_text)
    runtime_text = extend_earthquake_fissures(runtime_text)
    runtime_text = refresh_exploration_runtime(runtime_text)
    runtime_text = extend_simulacrum(runtime_text)
    runtime_text = bind_object_utility_sources(runtime_text)
    runtime_text = extend_spell_removals(runtime_text)
    runtime_text = bind_contingency_removal(runtime_text)
    runtime_text = bind_contingency_inventory(runtime_text)
    runtime_text = bind_contingency_inventory_mutations(runtime_text)
    runtime_text = bind_move_earth_removals(runtime_text)
    runtime_text = bind_move_earth_suppression(runtime_text)
    runtime_text = bind_tsunami_sources(runtime_text)
    runtime_text = bind_phantom_steed_dispel(runtime_text)
    runtime_text = extend_spell_damage_consumers(runtime_text)
    runtime_text = bind_time_stop_consumers(runtime_text)
    runtime_text = bind_minor_world_d20(runtime_text)
    runtime_text = bind_heat_metal_d20(runtime_text)
    runtime_text = bind_awaken(runtime_text)
    runtime_text = normalize_d20_runtime(runtime_text)
    runtime_text = bind_trigger_consumers(runtime_text)
    runtime_text = bind_meld_travel_consumers(runtime_text)
    runtime_text = bind_source_relocation_node(runtime_text)
    runtime_text = bind_door_consumers(runtime_text)
    runtime_text = emit_late_telekinesis_access(runtime_text)
    runtime_text = emit_late_telekinesis_pour(runtime_text)
    runtime_text = emit_late_servant_access(runtime_text)
    runtime_text = bind_floating_disk_consumers(runtime_text)
    runtime_text = bind_hound_turn_consumers(runtime_text)
    runtime_text = bind_animated_undead_turn_consumers(runtime_text)
    runtime_text = bind_undead_fortitude_damage_consumers(runtime_text)
    runtime_text = bind_native_condition_sources(runtime_text)
    runtime_text = extend_native_undead_curses(runtime_text)
    runtime_text = bind_contingency_child_release(runtime_text)
    runtime_text = extend_transformation_objects(runtime_text)
    runtime_text = bind_moonbeam_light(runtime_text)
    runtime_text = bind_moonbeam_producers(runtime_text)
    runtime_text = bind_feather_fall_consumers(runtime_text)
    runtime_text = bind_illumination_object_consumers(runtime_text)
    runtime_text = bind_invisibility_cast_consumers(runtime_text)
    runtime_text = bind_contingency_damage(runtime_text)
    runtime_text = bind_contingency_clone_order(runtime_text)
    runtime_text = bind_magic_jar_consumers(runtime_text)
    runtime_text = bind_magic_jar_container(runtime_text)
    runtime_text = bind_magic_jar_healing(runtime_text)
    runtime_text = bind_received_damage_lifecycle(runtime_text)
    runtime_text = bind_damage_type_receipts(runtime_text)
    runtime_text = bind_simulacrum_consumers(runtime_text)
    runtime_text = bind_simulacrum_character_sources(runtime_text)
    runtime_text = bind_simulacrum_equipment(runtime_text)
    runtime_text = bind_simulacrum_character_casting(runtime_text)
    runtime_text = bind_astral_projection(runtime_text)
    runtime_text = bind_storm(runtime_text)
    runtime_text = bind_storm_later_turns(runtime_text)
    runtime_text = bind_storm_objects(runtime_text)
    runtime_text = bind_storm_lightning(runtime_text)
    runtime_text = bind_animate_objects(runtime_text)
    runtime_text = bind_delayed_blast(runtime_text)
    runtime_text = bind_delayed_contacts(runtime_text)
    runtime_text = bind_delayed_ignition(runtime_text)
    runtime_text = bind_phantom_steed_damage(runtime_text)
    runtime_text = bind_dream_consumers(runtime_text)
    runtime_text = bind_mirror_image_consumers(runtime_text)
    runtime_text = bind_warding_bond_damage(runtime_text)
    runtime_text = bind_prismatic_damage_packets(runtime_text)
    runtime_text = bind_secure_area_materials(runtime_text)
    runtime_text = bind_material_stock(runtime_text)
    runtime_text = bind_magic_jar_materials(runtime_text)
    runtime_text = bind_information_casting(runtime_text)
    runtime_text = bind_spell_removal_wakes(runtime_text)
    runtime_text = bind_insect_runtime_consumers(runtime_text)
    runtime_text = extend_insect_actions(runtime_text)
    runtime_text = bind_sunbeam(runtime_text)
    runtime_text = bind_solid_spell_walls(runtime_text)
    runtime_text = bind_stone_spell_wall(runtime_text)
    runtime_text = bind_wind_wall(runtime_text)
    runtime_text = bind_gust_of_wind(runtime_text)
    runtime_text = bind_courier_body_movement(runtime_text)
    runtime_text = bind_phantom_steed_consumers(runtime_text)
    runtime_text = bind_courier_interval_pace(runtime_text)
    runtime_text = bind_receiving_events(runtime_text)
    runtime_text = bind_qualified_packet_implementations(runtime_text)
    runtime_text = bind_retained_spell_attacks(runtime_text)
    runtime_text = extend_hand_occupancy(runtime_text)
    runtime_text = bind_flame_blade(runtime_text)
    runtime_text = bind_flame_blade_light(runtime_text)
    runtime_text = bind_flame_objects(runtime_text)
    runtime_text = bind_magic_jar_equipment(runtime_text)
    runtime_text = bind_magic_jar_incoming_defenses(runtime_text)
    runtime_text = _register_retained_attack_schema(runtime_text)
    runtime_text = _register_spell_family_schema(
        runtime_text, "attack_rider_spell", damage_rider_schema()
    )
    runtime_text = _register_spell_family_schema(
        runtime_text, "dragon_breath", dragon_breath_schema()
    )
    runtime_text = _register_spell_family_schema(runtime_text, "true_strike", true_strike_schema())
    runtime_text = bind_guards_mouth(runtime_text)
    runtime_text = extend_true_strike_parameters(runtime_text)
    runtime_text = bind_dragon_breath(runtime_text)
    runtime_text = bind_damage_riders(runtime_text)
    runtime_text = extend_native_mummy_actions(runtime_text)
    runtime_text = bind_native_mummy_maximum_zero_consumers(runtime_text)
    runtime_text = extend_native_mummy_fist(runtime_text)
    runtime_text = bind_native_mummy_fist_attack_consumers(runtime_text)
    runtime_text = bind_meld_stone_consumers(runtime_text)
    runtime_text = bind_prismatic_wall_sources(runtime_text)
    runtime_text = bind_earthquake_sources(runtime_text)
    runtime_text = bind_prismatic_wall_attacks(runtime_text)
    runtime_text = extend_native_wight(runtime_text)
    runtime_text = bind_native_wight_life_drain_consumers(runtime_text)
    runtime_text = extend_native_wight_attacks(runtime_text)
    runtime_text = bind_native_wight_attack_consumers(runtime_text)
    runtime_text = extend_native_wight_creation(runtime_text)
    runtime_text = extend_native_wight_equipment(runtime_text)
    runtime_text = extend_native_wight_rise(runtime_text)
    runtime_text = bind_native_wight_rise_consumers(runtime_text)
    runtime_text = extend_native_mummy_routine(runtime_text)
    runtime_text = extend_native_mummy_creation(runtime_text)
    runtime_text = extend_animated_skeletons(runtime_text)
    runtime_text = bind_reverse_gravity(runtime_text)
    runtime_text = bind_control_weather(runtime_text)
    runtime_text = bind_control_water(runtime_text)
    runtime_text = bind_simulacrum_turns(runtime_text)
    runtime_text = bind_storm_conditions(runtime_text)
    runtime_text = bind_storm_movement(runtime_text)
    runtime_text = bind_storm_wind(runtime_text)
    runtime_text = bind_storm_weapon_attacks(runtime_text)
    runtime_text = bind_tsunami(runtime_text)
    runtime_text = bind_ordinary_swimming(runtime_text)
    runtime_text = bind_wish(runtime_text)
    runtime_text = bind_storm_objects(runtime_text)
    runtime_text = bind_storm_lightning(runtime_text)
    runtime_text = bind_contingency_water_respiration(runtime_text)
    runtime_text = bind_find_path_consumers(runtime_text)
    runtime_text = bind_thunderwave_consequences(runtime_text)
    runtime_text = bind_ice_storm_terrain(runtime_text)
    runtime_text = bind_area_object_damage(runtime_text)
    runtime_text = bind_area_consequences(runtime_text)
    runtime_text = bind_shatter_objects(runtime_text)
    runtime_text = bind_prepared_object_storage(runtime_text)
    runtime_text = bind_object_packet_types(runtime_text)
    runtime_text = bind_power_words(runtime_text)
    runtime_text = bind_attack_rider_d20(runtime_text)
    runtime_text = bind_shapechange_weapons(runtime_text)
    runtime_text = extend_finger_death_zombies(runtime_text)
    runtime_text = bind_aura_life_maximum(runtime_text)
    runtime_text = bind_freezing_sphere(runtime_text)
    runtime_text = bind_disintegrate_sources(runtime_text)
    runtime_text = bind_time_stop_sources(runtime_text)
    runtime_text = bind_time_stop_received(runtime_text)
    runtime_text = bind_disintegrate_bodies(runtime_text)
    runtime_text = bind_time_stop_retained(runtime_text)
    runtime_text = bind_time_stop_retained_changes(runtime_text)
    runtime_text = bind_information_world_facts(runtime_text)
    runtime_text = bind_orb_paid_source(runtime_text)
    runtime_text = bind_guards_web_consumers(runtime_text)
    runtime_text = bind_astral_departure(runtime_text)
    runtime_text = extend_reactions(runtime_text)
    runtime_text = bind_sanctuary_lists(runtime_text)
    runtime_text = bind_created_undead_cast_consumers(runtime_text)
    runtime_text = finalize_pending_spell_guards(runtime_text)
    runtime_text = bind_prismatic_wall_consumers(runtime_text)
    runtime_text = bind_guards_web_burning(runtime_text)
    runtime_text = bind_magic_zone_effects(runtime_text)
    runtime_text = bind_dragon_breath_magic_zones(runtime_text)
    runtime_text = bind_produce_flame_reactions(runtime_text)
    runtime_text = bind_secure_area_consumers(runtime_text)
    runtime_text = bind_hallow_consumers(runtime_text)
    runtime_text = bind_illumination_body_consumers(runtime_text)
    runtime_text = bind_storm_conditions(runtime_text)
    runtime_text = bind_storm_movement(runtime_text)
    runtime_text = bind_storm_wind(runtime_text)
    runtime_text = bind_flaming_sphere_light(runtime_text)
    runtime_text = bind_scene_sight_origins(runtime_text)
    runtime_text = emit_late_conjured_sight(runtime_text)
    runtime_text = emit_celestial_initial_sight(runtime_text)
    runtime_text = bind_tsunami(runtime_text)
    runtime_text = bind_after_hit_riders(runtime_text)
    runtime_text = bind_hellish_rebuke(runtime_text)
    runtime_text = bind_true_strike(runtime_text)
    runtime_text = bind_orb_consumers(runtime_text)
    runtime_text = bind_pending_support_fences(runtime_text)
    runtime_text = bind_spell_begin_timing(runtime_text)
    runtime_text = bind_invisibility_completion(runtime_text)
    runtime_text = bind_storm_saved_weapon_attacks(runtime_text)
    runtime_text = extend_mirage_support(runtime_text)
    runtime_text = bind_mirage_support_lifecycle(runtime_text)
    runtime_text = bind_plant_growth_movement(runtime_text)
    runtime_text = bind_earthquake_movement(runtime_text)
    runtime_text = bind_earthquake_fissure_movement(runtime_text)
    runtime_text = bind_storm_movement(runtime_text)
    runtime_text = bind_ordinary_swimming(runtime_text)
    runtime_text = bind_earthquake_structure_receivers(runtime_text)
    runtime_text = emit_late_celestial_route(runtime_text)
    runtime_text = emit_late_gate_geometry(runtime_text)
    runtime_text = emit_late_telekinesis_handoff(runtime_text)
    runtime_text = bind_exile_completion(runtime_text, SPELL_SUPPORT_TOOLS)
    runtime_text = bind_wall_ice_relocation(runtime_text)
    runtime_text = bind_mansion_returns(runtime_text)
    runtime_text = bind_native_undead_rest(runtime_text)
    runtime_text = extend_reincarnate_native_traits(runtime_text)
    runtime_text = extend_reincarnate_rerolls(runtime_text)
    runtime_text = bind_simulacrum_character_checks(runtime_text)
    runtime_text = bind_mummy_glare_turn_consumers(runtime_text)
    runtime_text = bind_rope_trick_returns(runtime_text)
    runtime_text = bind_courier_chronology(runtime_text)
    runtime_text = bind_simulacrum_turns(runtime_text)
    runtime_text = bind_whirlpool_pull(runtime_text)
    runtime_text = bind_tsunami_travel(runtime_text)
    runtime_text = bind_tsunami_forced_travel(runtime_text)
    runtime_text = bind_water_waves(runtime_text)
    runtime_text = bind_earthquake_fissure_turn(runtime_text)
    runtime_text = bind_retained_spell_reactions(runtime_text)
    runtime_text = bind_delayed_blast_completion(runtime_text, SPELL_SUPPORT_TOOLS)
    runtime_text = bind_contingency_completion(runtime_text, SPELL_SUPPORT_TOOLS)
    runtime_text = bind_astral_completed_returns(
        runtime_text,
        SPELL_SUPPORT_TOOLS
        | {SPELL_TOOL, EXPLORATION_SPELL_TOOL}
        | {tool["name"] for tool in json.loads(RECORD_PATH.read_text(encoding="utf-8"))["tools"]},
    )
    runtime_text = trim_retained_spell_parameters(runtime_text)
    runtime_text = trim_divination_parameters(runtime_text)
    runtime_text = bind_exploration_cast_delegate(runtime_text)
    runtime_text = bind_tsunami_completion(runtime_text, SPELL_SUPPORT_TOOLS)
    runtime_text = bind_numeric_spell_distances(runtime_text)
    runtime_text = _project_final_spell_selector_copies(runtime_text, definitions)
    definition_lines = [
        "    "
        + spell_id
        + ": "
        + json.dumps(
            definition,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        for spell_id, definition in definitions.items()
    ]
    runtime_text = _replace_generated_block(
        runtime_text,
        SPELL_DEFINITIONS_START,
        SPELL_DEFINITIONS_END,
        definition_lines,
    )
    runtime_text = _replace_generated_block(
        runtime_text,
        BEAST_FORMS_START,
        BEAST_FORMS_END,
        [
            "    " + form_id + ": " + _canonical(row).decode("utf-8")
            for form_id, row in beast_forms.items()
        ],
    )
    runtime_text = _replace_generated_block(
        runtime_text,
        CASTING_REQUIREMENTS_START,
        CASTING_REQUIREMENTS_END,
        [
            "    " + spell + ": " + _canonical(row).decode("utf-8")
            for spell, row in sorted(requirements.items())
        ],
    )
    grants = complete_grant_policies(grant_policies(), requirements)
    for source, policy in grants.items():
        if set(policy["fixed"]) - set(requirements):
            raise SystemExit(f"Unknown SRD spell in grant source: {source}")
    runtime_text, schema_count = re.subn(
        r"(?m)^  definitions\.spell_grants: .*$",
        "  definitions.spell_grants: " + _canonical(definition_schema()).decode("utf-8"),
        runtime_text,
    )
    if schema_count != 1:
        raise SystemExit("The runtime needs exactly one spell grant definition schema")
    runtime_text = _replace_generated_block(
        runtime_text,
        "    # BEGIN GENERATED SPELL GRANTS",
        "    # END GENERATED SPELL GRANTS",
        [
            "    " + source + ": " + _canonical(policy).decode("utf-8")
            for source, policy in sorted(grants.items())
        ],
    )
    runtime_text = bind_private_casting_clocks(runtime_text)
    runtime = _load_runtime(runtime_text)
    _validate_spell_composition(definitions, requirements, runtime, runtime_text)
    if runtime.get("definitions", {}).get("spell_grants") != grants:
        raise SystemExit("Generated spell grant policies differ from their source")
    if runtime.get("definitions", {}).get("beast_forms") != beast_forms:
        raise SystemExit("Generated Beast form cores differ from their admitted source")
    non_beasts = {
        name: row
        for name, row in build_non_beast_index()["forms"].items()
        if row["execution_supported"]
    }
    if runtime.get("definitions", {}).get("non_beast_forms") != non_beasts:
        raise SystemExit("Generated non-Beast bodies differ from their admitted source")
    if runtime.get("definitions", {}).get("spell_casting") != requirements:
        raise SystemExit("Generated casting requirements differ from their SRD headers")
    projected_definitions = runtime.get("definitions", {}).get("spells")
    if projected_definitions != definitions:
        raise SystemExit("Generated runtime spell definitions differ from their declarations")
    spell_tool = runtime.get("tools", {}).get(SPELL_TOOL)
    if not isinstance(spell_tool, dict):
        raise SystemExit(f"Runtime is missing {SPELL_TOOL}")
    spell_parameter = next(
        (
            parameter
            for parameter in spell_tool.get("parameters", [])
            if parameter.get("name") == "spell"
        ),
        None,
    )
    if spell_parameter is None or spell_parameter.get("enum_values") != list(definitions):
        raise SystemExit("Generated public spell selector differs from the declarations")
    runtime_text = bind_retained_nullable_captures(runtime_text)
    runtime_text = compact_runtime_defaults(runtime_text)
    runtime_text = compact_runtime_parameters(runtime_text)
    errors = validate_ruleset(runtime_text)
    if errors:
        raise SystemExit("Generated spell runtime failed admission: " + "; ".join(errors))
    return runtime_text, runtime, definitions


def _validate_spell_composition(
    definitions: dict[str, Any],
    requirements: dict[str, Any],
    runtime: dict[str, Any],
    runtime_text: str,
) -> None:
    """Reject declarations whose headers, timing, effects or resolver path disagree."""

    variants = runtime["definition_row_schemas"]["definitions.spells"]["variants"]
    branches = set(re.findall(r"_spell_definition\.family == '([^']+)'", runtime_text))
    for spell_id, definition in definitions.items():
        header = requirements[spell_id]
        family = definition["family"]
        if family not in variants or family not in branches:
            raise SystemExit(f"Spell has no admitted executable family: {spell_id}")
        ordinary_casting_time = header["casting_time"].split(" or Ritual", 1)[0]
        if (
            spell_id == "plant_growth"
            and family == "plant_growth_spell"
            and ordinary_casting_time == "Action (Overgrowth) or 8 hours (Enrichment)"
        ):
            ordinary_casting_time = "Action"
        conditional_bonus_headers = {
            "divine_smite": (
                "Bonus Action, which you take immediately after hitting a target with "
                "a Melee weapon or an Unarmed Strike"
            ),
            "ensnaring_strike": (
                "Bonus Action, which you take immediately after hitting a creature with a weapon"
            ),
            "searing_smite": (
                "Bonus Action, which you take immediately after hitting a target with "
                "a Melee weapon or an Unarmed Strike"
            ),
            "shining_smite": (
                "Bonus Action, which you take immediately after hitting a creature "
                "with a Melee weapon or an Unarmed Strike"
            ),
        }
        if (
            family == "attack_rider_spell"
            and definition["casting_time"] == "bonus_action"
            and ordinary_casting_time == conditional_bonus_headers.get(spell_id)
        ):
            ordinary_casting_time = "Bonus Action"
        if definition["casting_time"] == "reaction":
            ordinary_casting_time = ordinary_casting_time.split(",", 1)[0]
        if definition["casting_time"] != ordinary_casting_time.lower().replace(" ", "_"):
            raise SystemExit(f"Spell casting time disagrees with its SRD header: {spell_id}")
        duration = header["duration"]
        if ("Concentration" in duration) != (definition.get("concentration") is True):
            raise SystemExit(f"Spell concentration disagrees with its SRD header: {spell_id}")
        retained_zero_durations = {
            ("simulacrum", "simulacrum_spell"): "Until dispelled",
        }
        if "duration_rounds" in definition and not (
            (
                (spell_id, family) in retained_zero_durations
                and duration == retained_zero_durations[(spell_id, family)]
                and definition["duration_rounds"] == 0
            )
            or (spell_id, family)
            in {
                ("command", "timed_control_orders"),
                ("plant_growth", "plant_growth_spell"),
                ("awaken", "awaken_spell"),
                ("divine_smite", "attack_rider_spell"),
            }
            and duration == "Instantaneous"
            and definition["duration_rounds"] == 0
        ):
            match = re.fullmatch(
                r"(?:Concentration,? up to |Up to )?(\d+) "
                r"(round|rounds|minute|minutes|hour|hours|day|days)",
                duration,
            )
            if match is None:
                raise SystemExit(f"Spell duration needs an exact mechanical mapping: {spell_id}")
            rounds_per_unit = {
                "round": 1,
                "rounds": 1,
                "minute": 10,
                "minutes": 10,
                "hour": 600,
                "hours": 600,
                "day": 14400,
                "days": 14400,
            }
            if definition["duration_rounds"] != int(match[1]) * rounds_per_unit[match[2]]:
                raise SystemExit(f"Spell duration disagrees with its SRD header: {spell_id}")
        header_range = header["range"]
        feet = re.fullmatch(r"(\d+) feet", header_range)
        if feet and "range_feet" in definition and int(feet[1]) != definition["range_feet"]:
            raise SystemExit(f"Spell range disagrees with its SRD header: {spell_id}")
        if header_range == "Touch" and definition.get("range_kind") not in (None, "touch"):
            raise SystemExit(f"Touch spell has a different range kind: {spell_id}")
        if header_range == "Self" and definition.get("range_kind") not in (None, "self"):
            raise SystemExit(f"Self spell has a different range kind: {spell_id}")
        turn_effect = definition.get("turn_effect")
        if turn_effect is not None:
            allowed = {
                "spell_attack_damage": {
                    "speed_reduction",
                    "next_attack_advantage",
                    "delayed_damage",
                    "healing_block",
                    "poisoned",
                },
                "single_target_save_damage": {"next_attack_disadvantage"},
            }
            if turn_effect["kind"] not in allowed.get(family, set()):
                raise SystemExit(f"Spell turn effect has no matching outcome consumer: {spell_id}")
        if definition.get("reduce_hit_point_maximum_on_failed_save") is True and not (
            family == "single_target_save_damage"
            and definition["damage_type"] == "necrotic"
            and definition["success_damage"] == "half"
        ):
            raise SystemExit(f"Spell Hit Point maximum rider is incoherent: {spell_id}")


def _retire_manifest_owned_runtime_fields(runtime_source: str) -> str:
    """Remove legacy identity and public-tool declarations without reformatting generated YAML."""

    retired = {"meta", "model_tool_availability"}
    lines = runtime_source.splitlines(keepends=True)
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        key = line.split(":", 1)[0] if line and not line[0].isspace() and ":" in line else None
        if key in retired:
            index += 1
            while index < len(lines) and (not lines[index].strip() or lines[index][0].isspace()):
                index += 1
            continue
        if line.startswith("  package_id:"):
            index += 1
            continue
        output.append(line)
        index += 1
    return "".join(output)


def _validate_definition_profiles(definitions: dict[str, Any]) -> None:
    validate_sense_profiles(definitions)
    validate_reaction_profiles(definitions)
    validate_information_profiles(definitions)
    validate_divination_profiles(definitions)
    validate_transformation_profiles(definitions)
    validate_travel_profiles(definitions)
    validate_environment_profiles({k: v for k, v in definitions.items() if k != "wall_of_thorns"})
    validate_thorn_wall_profiles(definitions)
    validate_barrier_profiles(definitions)
    validate_ward_profiles(definitions)
    validate_magic_zone_profiles(definitions)
    validate_secure_area_profiles(definitions)
    validate_hallow_profiles(definitions)
    validate_guards_wards_profiles(definitions)
    validate_prismatic_wall_profiles(definitions)
    validate_summoning_profiles(definitions)
    validate_planar_ally_profiles(definitions)
    validate_object_content_profiles(definitions)
    validate_illumination_profiles(definitions)
    validate_illusion_profiles(definitions)
    validate_appearance_profiles(definitions)
    validate_image_profiles(definitions)
    validate_detection_mask_profiles(definitions)
    validate_time_stop_profiles(definitions)
    validate_minor_world_profiles(definitions)
    validate_memory_profiles(definitions)
    validate_binding_profiles(definitions)
    validate_mirage_profiles(definitions)
    validate_retained_attack_profiles(definitions)
    validate_dream_profiles(definitions)
    validate_contingency_profiles(definitions)
    validate_dragon_breath_profiles(definitions)
    validate_true_strike_profiles(definitions)
    validate_damage_rider_profiles(definitions)
    validate_body_soul_profiles(definitions)
    validate_magic_jar_profiles(definitions)
    validate_meld_stone_profiles(definitions)
    validate_phantom_steed_profiles(definitions)
    validate_plant_growth_profiles(definitions)
    validate_awaken_profiles(definitions)
    validate_earthquake_profiles(definitions)
    validate_solid_wall_profiles({k: v for k, v in definitions.items() if k != "wall_of_stone"})
    validate_sunbeam_profiles(definitions)
    validate_storm_profiles(definitions)
    validate_stone_wall_profiles(definitions)
    validate_reverse_gravity_source()
    validate_simulacrum_profiles(definitions)
    validate_astral_projection_profiles(definitions)
    validate_move_earth_profiles(definitions)
    validate_tsunami_source()
    validate_animate_object_profiles(definitions)
    validate_arcane_hand_profiles(definitions)
    validate_delayed_blast_profiles(definitions)
    validate_gust_source()
    validate_find_path_source()
    validate_conjured_profiles(definitions)
    validate_supply_profiles(definitions)
    validate_object_utility_profiles(definitions)
    validate_heat_metal_profiles(definitions)
    validate_trigger_profiles(definitions)
    for spell_id, definition in definitions.items():
        family = definition.get("family")
        if family == "planar_ally_service":
            continue
        if family == "tsunami":
            if spell_id != "tsunami" or definition != TSUNAMI_PROFILES["tsunami"]:
                raise SystemExit("Tsunami must retain its exact source-owned bounded profile")
            continue
        if family == "wish":
            validate_wish(spell_id, definition)
            continue
        if family == "move_earth":
            continue
        if family == "reverse_gravity":
            if definition != GRAVITY_PROFILES.get(spell_id):
                raise SystemExit(f"Gravity profile differs from its pinned source: {spell_id}")
            continue
        if family == "find_path":
            if definition != FIND_PATH_PROFILES.get(spell_id):
                raise SystemExit(f"Find Path profile differs from its pinned source: {spell_id}")
            continue
        if family == "gust_of_wind":
            if definition != GUST_PROFILES.get(spell_id):
                raise SystemExit(f"Gust profile differs from its pinned source: {spell_id}")
            continue
        if family == "magic_jar_spell":
            continue
        if family == "mirage_spell":
            continue
        if family in {
            "contingency_spell",
            "dragon_breath",
            "true_strike",
            "attack_rider_spell",
            "meld_stone_spell",
            "phantom_steed",
            "plant_growth_spell",
            "awaken_spell",
            "earthquake_spell",
            "solid_spell_wall",
            "sunbeam",
            "storm_of_vengeance",
            "simulacrum_spell",
            "astral_projection",
            "animate_objects",
            "arcane_hand",
            "delayed_blast_fireball",
        }:
            continue
        if family == "conjured_presence":
            continue
        if family == "retained_spell_attack":
            continue
        if family == "dream_spell":
            continue
        if family == "body_soul_spell":
            continue
        if family == "control_binding_spell":
            continue
        if family == "detection_mask_spell":
            continue
        if family == "memory_dialogue_spell":
            continue
        if family == "trigger_spell":
            continue
        if family == "heat_metal_spell":
            continue
        if family == "object_utility_spell":
            continue
        if family == "supply_spell":
            continue
        if family == "minor_world_spell":
            continue
        if family in {"illusion_spell", "illusion_appearance", "illusion_image", "time_stop"}:
            continue
        if family in {
            "transformation_spell",
            "spell_barrier",
            "actor_spell_ward",
            "spell_magic_zone",
            "guarded_area",
            "prismatic_wall",
            "hallowed_area",
            "spell_secure_area",
        }:
            continue
        if family == "instantaneous_healing_pool" and not (
            definition.get("casting_time") == "action"
            and type(definition.get("base_level")) is int
            and 1 <= definition["base_level"] <= 9
            and type(definition.get("range_feet")) is int
            and definition["range_feet"] > 0
            and type(definition.get("healing_pool")) is int
            and definition["healing_pool"] > 0
            and type(definition.get("max_targets")) is int
            and 1 <= definition["max_targets"] <= 64
            and definition.get("requires_each_target_sight") is True
            and all(
                type(definition.get(key)) is bool
                for key in (
                    "ends_blinded",
                    "ends_deafened",
                    "ends_poisoned",
                )
            )
        ):
            raise SystemExit(f"Healing-pool definition is incoherent: {spell_id}")
        if family == "threshold_timed_condition" and not (
            definition.get("casting_time") == "action"
            and type(definition.get("base_level")) is int
            and 1 <= definition["base_level"] <= 9
            and definition.get("range_kind") == "distance"
            and type(definition.get("range_feet")) is int
            and definition["range_feet"] > 0
            and definition.get("requires_sight") is True
            and type(definition.get("hit_point_threshold")) is int
            and definition["hit_point_threshold"] > 0
            and definition.get("condition") == "stunned"
            and definition.get("save_ability") == "constitution"
            and definition.get("repeat_save_timing") == "end_of_target_turn"
            and definition.get("above_threshold_effect") == "speed_zero_until_caster_next_turn"
        ):
            raise SystemExit(f"Threshold-condition definition is incoherent: {spell_id}")
        if family == "summoned_creature":
            saves = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
            if not (
                definition.get("casting_time") == "action"
                and type(definition.get("base_level")) is int
                and 1 <= definition["base_level"] <= 9
                and definition.get("range_kind") == "distance"
                and type(definition.get("range_feet")) is int
                and definition["range_feet"] > 0
                and definition.get("requires_sight") is True
                and type(definition.get("duration_rounds")) is int
                and definition["duration_rounds"] > 0
                and definition.get("concentration") is True
                and isinstance(definition.get("creature_name"), str)
                and definition["creature_name"].strip()
                and isinstance(definition.get("creature_type"), str)
                and definition["creature_type"].strip()
                and isinstance(definition.get("alignment"), str)
                and definition["alignment"].strip()
                and all(
                    type(definition.get(key)) is int
                    for key in (
                        "base_hit_points",
                        "hit_points_per_level",
                        "armor_class_base",
                        "speed",
                        "flying_speed",
                        "swimming_speed",
                        "size_rank",
                        "melee_reach",
                        "rend_base_damage_modifier",
                        "attacks_per_level_divisor",
                        "breath_dice_count",
                        "breath_cone_feet",
                        "blindsight_feet",
                        "darkvision_feet",
                        "passive_perception",
                        *(f"{ability}_score" for ability in saves),
                        *(f"{ability}_save" for ability in saves),
                    )
                )
                and all(1 <= definition[f"{ability}_score"] <= 30 for ability in saves)
                and definition["base_hit_points"] > 0
                and definition["attacks_per_level_divisor"] > 0
                and 1 <= definition["breath_dice_count"] <= 32
                and definition["breath_cone_feet"] > 0
                and isinstance(definition.get("damage_resistances"), list)
                and isinstance(definition.get("condition_immunities"), list)
                and isinstance(definition.get("languages"), list)
                and definition.get("understands_caster_languages") is True
                and isinstance(definition.get("selectable_damage_types"), list)
                and definition["selectable_damage_types"]
                and set(definition["selectable_damage_types"]).issubset(
                    definition["damage_resistances"]
                )
                and definition.get("rend_damage_die") in {"d4", "d6", "d8", "d10", "d12"}
                and definition.get("breath_damage_die") in {"d4", "d6", "d8", "d10", "d12"}
                and definition.get("breath_save_ability") in saves
            ):
                raise SystemExit(
                    f"Summoned creature definition has an incoherent profile: {spell_id}"
                )
            continue
        if family == "multi_target_timed_actor_mobility":
            expected = {
                "fly": (3, "action", "touch", 0, 100, True, 1, 1, "flight", 60, 0, 0),
                "jump": (1, "bonus_action", "touch", 0, 10, False, 1, 1, "jump", 0, 30, 10),
                "spider_climb": (2, "action", "touch", 0, 600, True, 1, 1, "spider_climb", 0, 0, 0),
                "water_walk": (
                    3,
                    "action",
                    "distance",
                    30,
                    600,
                    False,
                    10,
                    0,
                    "water_walk",
                    0,
                    0,
                    0,
                ),
                "expeditious_retreat": (
                    1,
                    "bonus_action",
                    "self",
                    0,
                    100,
                    True,
                    0,
                    0,
                    "bonus_dash",
                    0,
                    0,
                    0,
                ),
            }
            keys = (
                "base_level",
                "casting_time",
                "range_kind",
                "range_feet",
                "duration_rounds",
                "concentration",
                "base_targets",
                "targets_per_slot_level",
                "mobility",
                "flying_speed",
                "jump_distance",
                "jump_cost",
            )
            if (
                spell_id not in expected
                or tuple(definition.get(key) for key in keys) != expected[spell_id]
            ):
                raise SystemExit(
                    f"Timed actor mobility differs from its source profile: {spell_id}"
                )
            continue
        if family == "multi_target_timed_speed_bonus":
            if not (
                definition.get("casting_time") == "action"
                and type(definition.get("base_level")) is int
                and 1 <= definition["base_level"] <= 9
                and definition.get("range_kind") == "touch"
                and definition.get("range_feet") == 0
                and type(definition.get("duration_rounds")) is int
                and 1 <= definition["duration_rounds"] <= 100000
                and definition.get("concentration") is False
                and type(definition.get("base_targets")) is int
                and 1 <= definition["base_targets"] <= 64
                and type(definition.get("targets_per_slot_level")) is int
                and 0 <= definition["targets_per_slot_level"] <= 64
                and definition["base_targets"]
                + (9 - definition["base_level"]) * definition["targets_per_slot_level"]
                <= 64
                and type(definition.get("speed_bonus")) is int
                and 1 <= definition["speed_bonus"] <= 100
            ):
                raise SystemExit(f"Timed speed definition has an incoherent profile: {spell_id}")
            continue
        if family == "multi_target_timed_hit_point_bonus":
            if not (
                definition.get("casting_time") == "action"
                and type(definition.get("base_level")) is int
                and 1 <= definition["base_level"] <= 9
                and definition.get("range_kind") == "distance"
                and type(definition.get("range_feet")) is int
                and 1 <= definition["range_feet"] <= 1000
                and type(definition.get("duration_rounds")) is int
                and 1 <= definition["duration_rounds"] <= 100000
                and definition.get("concentration") is False
                and type(definition.get("max_targets")) is int
                and 1 <= definition["max_targets"] <= 64
                and type(definition.get("base_hit_point_bonus")) is int
                and 1 <= definition["base_hit_point_bonus"] <= 1000
                and type(definition.get("hit_point_bonus_per_slot_level")) is int
                and 0 <= definition["hit_point_bonus_per_slot_level"] <= 1000
            ):
                raise SystemExit(f"Timed Hit Point bonus has an incoherent profile: {spell_id}")
            continue
        if family == "single_target_timed_armor_class_floor":
            if not (
                definition.get("casting_time") in {"action", "bonus_action"}
                and type(definition.get("base_level")) is int
                and 1 <= definition["base_level"] <= 9
                and definition.get("range_kind") == "touch"
                and definition.get("range_feet") == 0
                and definition.get("requires_willing") is True
                and type(definition.get("duration_rounds")) is int
                and 1 <= definition["duration_rounds"] <= 100000
                and definition.get("concentration") is False
                and type(definition.get("armor_class_floor")) is int
                and 1 <= definition["armor_class_floor"] <= 40
            ):
                raise SystemExit(f"Timed Armor Class floor has an incoherent profile: {spell_id}")
            continue
        if family == "single_target_timed_condition_defense":
            if not (
                definition.get("casting_time") in {"action", "bonus_action"}
                and type(definition.get("base_level")) is int
                and 1 <= definition["base_level"] <= 9
                and definition.get("range_kind") == "touch"
                and definition.get("range_feet") == 0
                and type(definition.get("duration_rounds")) is int
                and 1 <= definition["duration_rounds"] <= 100000
                and definition.get("ends_condition") == "poisoned"
                and definition.get("save_advantage_condition") == "poisoned"
                and definition.get("resistance_damage_type") == "poison"
            ):
                raise SystemExit(f"Timed condition defense has an incoherent profile: {spell_id}")
            continue
        if family == "single_target_timed_death_prevention":
            if not (
                definition.get("casting_time") == "action"
                and definition.get("base_level") == 4
                and definition.get("range_kind") == "touch"
                and definition.get("range_feet") == 0
                and definition.get("duration_rounds") == 4800
                and definition.get("concentration") is False
                and definition.get("replacement_hit_points") == 1
                and definition.get("prevents_instant_death") is True
            ):
                raise SystemExit(
                    f"Timed death-prevention definition has an incoherent profile: {spell_id}"
                )
            continue
        if family == "self_temporary_hit_points":
            if not (
                definition.get("casting_time") == "action"
                and definition.get("base_level") == 1
                and definition.get("base_dice") == 2
                and definition.get("temporary_hit_point_die") == "d4"
                and definition.get("flat_bonus") == 4
                and definition.get("temporary_hit_points_per_slot_level") == 5
                and definition.get("range_kind") == "self"
                and definition.get("range_feet") == 0
            ):
                raise SystemExit(
                    f"Self Temporary Hit Point definition has an incoherent profile: {spell_id}"
                )
            continue
        if family == "single_target_concentration_armor_class_bonus":
            if not (
                definition.get("casting_time") == "bonus_action"
                and definition.get("base_level") == 1
                and definition.get("range_kind") == "distance"
                and definition.get("range_feet") == 60
                and definition.get("requires_sight") is False
                and definition.get("duration_rounds") == 100
                and definition.get("concentration") is True
                and definition.get("operation") == "add"
                and definition.get("armor_class_bonus") == 2
            ):
                raise SystemExit(
                    f"Concentration armor-class definition has an incoherent profile: {spell_id}"
                )
            continue
        if family == "multi_target_concentration_ability_advantage":
            if not (
                definition.get("casting_time") == "action"
                and definition.get("base_level") == 2
                and definition.get("range_kind") == "touch"
                and definition.get("range_feet") == 0
                and definition.get("requires_willing") is False
                and definition.get("duration_rounds") == 600
                and definition.get("concentration") is True
                and definition.get("base_targets") == 1
                and definition.get("targets_per_slot_level") == 1
                and definition.get("trigger") == "ability_check"
                and definition.get("selection") == "ability_per_target"
                and definition.get("operation") == "grant_advantage"
            ):
                raise SystemExit(
                    "Multi-target concentration ability-advantage definition has an "
                    f"incoherent profile: {spell_id}"
                )
            continue
        if family == "multi_target_concentration_d20_modifier":
            spell_specific = (
                definition.get("initial_save") == "charisma"
                and definition.get("operation") == "subtract_roll"
                and definition.get("requires_sight") is True
            ) or (
                definition.get("initial_save") == "none"
                and definition.get("operation") == "add_roll"
                and definition.get("requires_sight") is False
            )
            if not (
                definition.get("casting_time") == "action"
                and definition.get("base_level") == 1
                and definition.get("range_kind") == "distance"
                and definition.get("range_feet") == 30
                and definition.get("duration_rounds") == 10
                and definition.get("concentration") is True
                and definition.get("base_targets") == 3
                and definition.get("targets_per_slot_level") == 1
                and definition.get("trigger") == "attack_roll_or_saving_throw"
                and definition.get("modifier_die") == "d4"
                and spell_specific
            ):
                raise SystemExit(
                    "Multi-target concentration D20 definition has an incoherent profile: "
                    f"{spell_id}"
                )
            continue
        if family == "lasting_reactive_concentration":
            if not (
                definition.get("casting_time") == "action"
                and definition.get("base_level") == 0
                and definition.get("range_kind") == "touch"
                and definition.get("range_feet") == 0
                and definition.get("requires_willing") is True
                and definition.get("duration_rounds") == 10
                and definition.get("concentration") is True
                and definition.get("trigger") == "skill_ability_check"
                and definition.get("selection") == "skill"
                and definition.get("operation") == "add_roll"
                and definition.get("modifier_die") == "d4"
                and definition.get("uses_per_turn") == 0
            ):
                raise SystemExit(
                    f"Lasting-concentration definition has an incoherent profile: {spell_id}"
                )
            continue
        if family == "single_target_concentration_damage_resistance":
            selection = definition.get("selection")
            resistance_flags = [
                definition.get(f"resists_{damage_type}")
                for damage_type in (
                    "acid",
                    "bludgeoning",
                    "cold",
                    "fire",
                    "force",
                    "lightning",
                    "necrotic",
                    "piercing",
                    "poison",
                    "psychic",
                    "radiant",
                    "slashing",
                    "thunder",
                )
            ]
            valid_common = (
                definition.get("casting_time") == "action"
                and definition.get("range_kind") == "touch"
                and definition.get("range_feet") == 0
                and definition.get("requires_willing") is True
                and definition.get("concentration") is True
            )
            valid_operation = (
                definition.get("operation") == "grant_resistance"
                and type(definition.get("base_level")) is int
                and 1 <= definition["base_level"] <= 9
                and definition.get("duration_rounds") == 600
                and "reduction_die" not in definition
                and "uses_per_turn" not in definition
            ) or (
                definition.get("operation") == "reduce_damage_roll"
                and type(definition.get("base_level")) is int
                and definition["base_level"] == 0
                and definition.get("duration_rounds") == 10
                and definition.get("selection") == "damage_type"
                and definition.get("reduction_die") == "d4"
                and definition.get("uses_per_turn") == 1
            )
            valid_selection = (
                selection in {"damage_type", "none"}
                and all(isinstance(flag, bool) for flag in resistance_flags)
                and any(resistance_flags)
            )
            if not (valid_common and valid_operation and valid_selection):
                raise SystemExit(
                    f"Concentration-resistance definition has an incoherent profile: {spell_id}"
                )
            continue
        if family == "timed_control_behavior":
            validate_control_behavior(spell_id, definition)
            continue
        if family == "timed_control_orders":
            validate_control_orders(spell_id, definition)
            continue
        if family == "recovery":
            validate_recovery(spell_id, definition)
            continue
        if family == "progressive_affliction":
            validate_progressive(spell_id, definition)
            continue
        if family == "timed_control_affliction":
            validate_control_afflictions(spell_id, definition)
            continue
        if family == "multi_target_timed_control":
            # The admitted control records share one lifecycle; these fixed profiles bind every
            # mechanically distinctive clause to its SRD spell rather than caller choice.
            control_profiles = {
                "animal_friendship": (
                    1,
                    30,
                    14400,
                    False,
                    1,
                    1,
                    "charmed",
                    "none",
                    "beast",
                    "animal_charm",
                ),
                "charm_person": (
                    1,
                    30,
                    600,
                    False,
                    1,
                    1,
                    "charmed",
                    "none",
                    "humanoid",
                    "friendly_charm",
                ),
                "charm_monster": (
                    4,
                    30,
                    600,
                    False,
                    1,
                    1,
                    "charmed",
                    "none",
                    "any",
                    "friendly_charm",
                ),
                "hideous_laughter": (
                    1,
                    30,
                    10,
                    True,
                    1,
                    1,
                    "incapacitated",
                    "end_of_target_turn",
                    "any",
                    "laughter",
                ),
                "hypnotic_pattern": (3, 120, 10, True, 8, 0, "charmed", "none", "any", "hypnotic"),
            }
            keys = (
                "base_level",
                "range_feet",
                "duration_rounds",
                "concentration",
                "base_targets",
                "targets_per_slot_level",
                "condition_choice",
                "repeat_save_timing",
                "required_creature_type",
                "control_profile",
            )
            if (
                spell_id not in control_profiles
                or tuple(definition.get(key) for key in keys) != control_profiles[spell_id]
                or definition.get("casting_time") != "action"
                or definition.get("range_kind") != "distance"
                or definition.get("targeting_sense")
                != ("none" if spell_id == "hypnotic_pattern" else "sight")
                or definition.get("save_ability") != "wisdom"
                or definition.get("repeat_save_success") != "ends_source"
            ):
                raise SystemExit(f"Timed control definition is incoherent: {spell_id}")
            continue
        if family == "multi_target_timed_repeating_save_condition":
            if not (
                definition.get("casting_time") == "action"
                and type(definition.get("base_level")) is int
                and 1 <= definition["base_level"] <= 9
                and definition.get("range_kind") == "distance"
                and type(definition.get("range_feet")) is int
                and 1 <= definition["range_feet"] <= 5280
                and definition.get("targeting_sense") == "sight"
                and type(definition.get("duration_rounds")) is int
                and 1 <= definition["duration_rounds"] <= 1000
                and type(definition.get("concentration")) is bool
                and definition.get("base_targets") == 1
                and definition.get("targets_per_slot_level") == 1
                and definition.get("save_ability")
                in {
                    "strength",
                    "dexterity",
                    "constitution",
                    "intelligence",
                    "wisdom",
                    "charisma",
                }
                and definition.get("condition_choice") in {"blinded_or_deafened", "paralyzed"}
                and definition.get("required_creature_type") in {"any", "humanoid"}
                and definition.get("repeat_save_timing") == "end_of_target_turn"
                and definition.get("repeat_save_success") == "ends_source"
            ):
                raise SystemExit(
                    "Timed repeating-save condition definition has an incoherent profile: "
                    f"{spell_id}"
                )
            continue
        if family == "spell_attack_damage":
            scaling_mode = definition.get("scaling_mode")
            base_level = definition.get("base_level")
            dice_per_slot_level = definition.get("dice_per_slot_level")
            cantrip_upgrade_dice = definition.get("cantrip_upgrade_dice")
            attack_kind = definition.get("attack_kind")
            range_kind = definition.get("range_kind")
            range_feet = definition.get("range_feet")
            upgrade_levels = (
                definition.get("upgrade_level_1"),
                definition.get("upgrade_level_2"),
                definition.get("upgrade_level_3"),
            )
            valid_upgrade_levels = all(
                isinstance(level, int) and not isinstance(level, bool) for level in upgrade_levels
            ) and (upgrade_levels[0] < upgrade_levels[1] < upgrade_levels[2])
            valid_scaling = (
                scaling_mode == "caster_level"
                and base_level == 0
                and dice_per_slot_level == 0
                and isinstance(cantrip_upgrade_dice, int)
                and not isinstance(cantrip_upgrade_dice, bool)
                and cantrip_upgrade_dice >= 1
                and valid_upgrade_levels
            ) or (
                scaling_mode == "slot_level"
                and isinstance(base_level, int)
                and not isinstance(base_level, bool)
                and base_level >= 1
                and isinstance(dice_per_slot_level, int)
                and not isinstance(dice_per_slot_level, bool)
                and dice_per_slot_level >= 0
                and cantrip_upgrade_dice == 0
                and valid_upgrade_levels
            )
            valid_attack_range = (
                attack_kind == "melee" and range_kind == "touch" and range_feet == 0
            ) or (
                attack_kind == "ranged"
                and range_kind == "distance"
                and isinstance(range_feet, int)
                and not isinstance(range_feet, bool)
                and range_feet > 0
            )
            damage_type_mode = definition.get("damage_type_mode")
            damage_type_flags = [definition.get(f"allows_{kind}") for kind in DAMAGE_TYPES]
            valid_damage_type = (
                damage_type_mode in {"fixed", "selected"}
                and all(isinstance(flag, bool) for flag in damage_type_flags)
                and (
                    (damage_type_mode == "fixed" and sum(damage_type_flags) == 1)
                    or (damage_type_mode == "selected" and sum(damage_type_flags) >= 1)
                )
                and definition.get("miss_damage") in {"none", "half"}
            )
            if not (valid_scaling and valid_attack_range and valid_damage_type):
                raise SystemExit(f"Spell-attack definition has an incoherent profile: {spell_id}")
            effect = definition.get("turn_effect")
            if effect is not None:
                valid_effect = isinstance(effect, dict) and set(effect) == {
                    "label",
                    "kind",
                    "expiry_owner",
                    "expiry_phase",
                    "magnitude",
                    "dice_count",
                    "damage_type",
                    "description",
                }
                if valid_effect:
                    valid_effect = (
                        effect["kind"]
                        in {
                            "speed_reduction",
                            "next_attack_advantage",
                            "delayed_damage",
                            "healing_block",
                            "poisoned",
                        }
                        and effect["expiry_owner"] in {"caster", "target"}
                        and effect["expiry_phase"] in {"start", "end"}
                        and all(
                            isinstance(effect[key], str) and effect[key].strip()
                            for key in ("label", "description")
                        )
                        and type(effect["magnitude"]) is int
                        and 0 <= effect["magnitude"] <= 100
                        and type(effect["dice_count"]) is int
                        and 0 <= effect["dice_count"] <= 32
                        and effect["damage_type"] in DAMAGE_TYPES
                    )
                if valid_effect and effect["kind"] == "delayed_damage":
                    valid_effect = (
                        scaling_mode == "slot_level"
                        and effect["dice_count"] >= 1
                        and effect["dice_count"] + (9 - base_level) * dice_per_slot_level <= 32
                    )
                if not valid_effect:
                    raise SystemExit(f"Spell-attack turn effect is incoherent: {spell_id}")
        if family == "instantaneous_attack_area_save_damage":
            base_level = definition.get("base_level")
            attack_base_dice = definition.get("attack_base_dice")
            area_base_dice = definition.get("area_base_dice")
            area_dice_per_slot_level = definition.get("area_dice_per_slot_level")
            valid_dice = all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in (
                    base_level,
                    attack_base_dice,
                    area_base_dice,
                    area_dice_per_slot_level,
                )
            ) and (
                1 <= base_level <= 9
                and 1 <= attack_base_dice <= 32
                and 1 <= area_base_dice <= 32
                and 0 <= area_dice_per_slot_level <= 32
                and area_base_dice + (9 - base_level) * area_dice_per_slot_level <= 32
            )
            range_feet = definition.get("range_feet")
            area_size_feet = definition.get("area_size_feet")
            valid_targeting = (
                definition.get("attack_kind") == "ranged"
                and isinstance(range_feet, int)
                and not isinstance(range_feet, bool)
                and 1 <= range_feet <= 1000
                and isinstance(area_size_feet, int)
                and not isinstance(area_size_feet, bool)
                and 1 <= area_size_feet <= 1000
                and definition.get("area_shape") == "sphere"
                and definition.get("primary_must_be_affected") is True
            )
            valid_resolution = (
                definition.get("casting_time") == "action"
                and definition.get("save_ability") == "dexterity"
                and definition.get("success_damage") == "none"
                and isinstance(definition.get("requires_sight"), bool)
                and definition.get("construct_save_disadvantage") is False
                and definition.get("attack_damage_type") != definition.get("area_damage_type")
            )
            if not (valid_dice and valid_targeting and valid_resolution):
                raise SystemExit(f"Attack-area definition has an incoherent profile: {spell_id}")
            continue
        if family == "single_target_save_damage":
            scaling_mode = definition.get("scaling_mode")
            base_level = definition.get("base_level")
            dice_per_slot_level = definition.get("dice_per_slot_level")
            cantrip_upgrade_dice = definition.get("cantrip_upgrade_dice")
            flat_damage = definition.get("flat_damage")
            range_kind = definition.get("range_kind")
            range_feet = definition.get("range_feet")
            targeting_sense = definition.get("targeting_sense")
            upgrade_levels = (
                definition.get("upgrade_level_1"),
                definition.get("upgrade_level_2"),
                definition.get("upgrade_level_3"),
            )
            valid_upgrade_levels = all(
                isinstance(level, int) and not isinstance(level, bool) for level in upgrade_levels
            ) and (upgrade_levels[0] < upgrade_levels[1] < upgrade_levels[2])
            valid_scaling = (
                scaling_mode == "caster_level"
                and base_level == 0
                and dice_per_slot_level == 0
                and isinstance(cantrip_upgrade_dice, int)
                and not isinstance(cantrip_upgrade_dice, bool)
                and cantrip_upgrade_dice >= 1
                and valid_upgrade_levels
            ) or (
                scaling_mode == "slot_level"
                and isinstance(base_level, int)
                and not isinstance(base_level, bool)
                and base_level >= 1
                and isinstance(dice_per_slot_level, int)
                and not isinstance(dice_per_slot_level, bool)
                and dice_per_slot_level >= 0
                and cantrip_upgrade_dice == 0
                and valid_upgrade_levels
            )
            valid_flat_damage = (
                isinstance(flat_damage, int)
                and not isinstance(flat_damage, bool)
                and 0 <= flat_damage <= 100
            )
            valid_range = (range_kind == "touch" and range_feet == 0) or (
                range_kind == "distance"
                and isinstance(range_feet, int)
                and not isinstance(range_feet, bool)
                and range_feet >= 1
            )
            if not (
                valid_scaling
                and valid_flat_damage
                and valid_range
                and targeting_sense in {"sight", "sight_or_hearing", "none"}
            ):
                raise SystemExit(f"Save-damage definition has an incoherent profile: {spell_id}")
        if family == "multi_spell_attack_damage":
            scaling_mode = definition.get("scaling_mode")
            base_level = definition.get("base_level")
            base_attacks = definition.get("base_attacks")
            attacks_per_slot_level = definition.get("attacks_per_slot_level")
            cantrip_upgrade_attacks = definition.get("cantrip_upgrade_attacks")
            dice_per_attack = definition.get("dice_per_attack")
            upgrade_levels = (
                definition.get("upgrade_level_1"),
                definition.get("upgrade_level_2"),
                definition.get("upgrade_level_3"),
            )
            valid_upgrade_levels = all(
                isinstance(level, int) and not isinstance(level, bool) for level in upgrade_levels
            ) and (upgrade_levels[0] < upgrade_levels[1] < upgrade_levels[2])
            valid_base = (
                isinstance(base_attacks, int)
                and not isinstance(base_attacks, bool)
                and 1 <= base_attacks <= 10
                and isinstance(dice_per_attack, int)
                and not isinstance(dice_per_attack, bool)
                and 1 <= dice_per_attack <= 32
            )
            valid_scaling = (
                scaling_mode == "caster_level"
                and base_level == 0
                and attacks_per_slot_level == 0
                and isinstance(cantrip_upgrade_attacks, int)
                and not isinstance(cantrip_upgrade_attacks, bool)
                and cantrip_upgrade_attacks >= 1
                and valid_upgrade_levels
                and valid_base
                and base_attacks + 3 * cantrip_upgrade_attacks <= 10
            ) or (
                scaling_mode == "slot_level"
                and isinstance(base_level, int)
                and not isinstance(base_level, bool)
                and 1 <= base_level <= 9
                and isinstance(attacks_per_slot_level, int)
                and not isinstance(attacks_per_slot_level, bool)
                and attacks_per_slot_level >= 0
                and cantrip_upgrade_attacks == 0
                and valid_upgrade_levels
                and valid_base
                and base_attacks + (9 - base_level) * attacks_per_slot_level <= 10
            )
            if not valid_scaling:
                raise SystemExit(
                    f"Multi-spell-attack definition has an incoherent profile: {spell_id}"
                )
        if family == "instantaneous_area_save_composite_damage":
            base_level = definition.get("base_level")
            component_profiles = (
                (
                    definition.get("component_1_base_dice"),
                    definition.get("component_1_dice_per_slot_level"),
                ),
                (
                    definition.get("component_2_base_dice"),
                    definition.get("component_2_dice_per_slot_level"),
                ),
            )
            valid_base_level = (
                definition.get("scaling_mode") == "slot_level"
                and isinstance(base_level, int)
                and not isinstance(base_level, bool)
                and 1 <= base_level <= 9
            )
            valid_components = valid_base_level and all(
                isinstance(base_dice, int)
                and not isinstance(base_dice, bool)
                and 1 <= base_dice <= 32
                and isinstance(dice_per_slot_level, int)
                and not isinstance(dice_per_slot_level, bool)
                and 0 <= dice_per_slot_level <= 32
                and base_dice + (9 - base_level) * dice_per_slot_level <= 32
                for base_dice, dice_per_slot_level in component_profiles
            )
            valid_types = (
                isinstance(definition.get("component_1_damage_type"), str)
                and isinstance(definition.get("component_2_damage_type"), str)
                and definition.get("component_1_damage_type")
                != definition.get("component_2_damage_type")
            )
            valid_dimensions = (
                isinstance(definition.get("range_feet"), int)
                and not isinstance(definition.get("range_feet"), bool)
                and 0 <= definition.get("range_feet") <= 5280
                and isinstance(definition.get("area_radius_feet"), int)
                and not isinstance(definition.get("area_radius_feet"), bool)
                and 1 <= definition.get("area_radius_feet") <= 1000
                and isinstance(definition.get("area_height_feet"), int)
                and not isinstance(definition.get("area_height_feet"), bool)
                and 0 <= definition.get("area_height_feet") <= 1000
                and isinstance(definition.get("area_point_count"), int)
                and not isinstance(definition.get("area_point_count"), bool)
            )
            valid_geometry = (
                definition.get("origin_kind") == "point"
                and valid_dimensions
                and (
                    (
                        definition.get("area_shape") == "cylinder"
                        and definition.get("area_height_feet") >= 1
                        and definition.get("area_point_count") == 1
                    )
                    or (
                        definition.get("area_shape") == "multi_sphere"
                        and definition.get("range_feet") == 5280
                        and definition.get("area_radius_feet") == 40
                        and definition.get("area_height_feet") == 0
                        and definition.get("area_point_count") == 4
                    )
                )
            )
            valid_save = (
                definition.get("save_ability") == "dexterity"
                and definition.get("success_damage") == "half"
                and definition.get("construct_save_disadvantage") is False
            )
            if not (valid_components and valid_types and valid_geometry and valid_save):
                raise SystemExit(
                    f"Composite area spell definition has an incoherent profile: {spell_id}"
                )
            continue
        if family == "instantaneous_stabilization":
            upgrade_levels = (
                definition.get("upgrade_level_1"),
                definition.get("upgrade_level_2"),
                definition.get("upgrade_level_3"),
            )
            upgrade_ranges = (
                definition.get("upgrade_range_feet_1"),
                definition.get("upgrade_range_feet_2"),
                definition.get("upgrade_range_feet_3"),
            )
            base_range = definition.get("base_range_feet")
            valid_levels = all(
                isinstance(level, int) and not isinstance(level, bool) for level in upgrade_levels
            ) and (upgrade_levels[0] < upgrade_levels[1] < upgrade_levels[2])
            valid_ranges = (
                isinstance(base_range, int)
                and not isinstance(base_range, bool)
                and 1 <= base_range <= 1000
                and all(
                    isinstance(distance, int)
                    and not isinstance(distance, bool)
                    and 1 <= distance <= 1000
                    for distance in upgrade_ranges
                )
                and base_range < upgrade_ranges[0] < upgrade_ranges[1] < upgrade_ranges[2]
            )
            if not (definition.get("base_level") == 0 and valid_levels and valid_ranges):
                raise SystemExit(
                    f"Stabilization spell definition has an incoherent profile: {spell_id}"
                )
            continue
        if family == "instantaneous_threshold_death_damage":
            if not (
                definition.get("casting_time") == "action"
                and definition.get("base_level") == 9
                and definition.get("range_feet") == 60
                and definition.get("requires_sight") is True
                and definition.get("hit_point_threshold") == 100
                and definition.get("above_threshold_dice") == 12
                and definition.get("damage_die") == "d12"
                and definition.get("damage_type") == "psychic"
            ):
                raise SystemExit(
                    f"Threshold-death definition has an incoherent profile: {spell_id}"
                )
            continue
        if family != "instantaneous_area_save_damage":
            continue
        delayed_dice = definition.get("failed_save_delayed_damage_dice", 0)
        if (
            type(delayed_dice) is not int
            or not 0 <= delayed_dice <= 32
            or (
                delayed_dice > 0
                and (
                    definition.get("targeting_profile") != "area"
                    or definition.get("save_ability") != "dexterity"
                    or definition.get("damage_type") != "acid"
                )
            )
        ):
            raise SystemExit(f"Area spell delayed damage is incoherent: {spell_id}")
        origin_kind = definition.get("origin_kind")
        range_feet = definition.get("range_feet")
        area_shape = definition.get("area_shape")
        area_width_feet = definition.get("area_width_feet")
        scaling_mode = definition.get("scaling_mode")
        base_level = definition.get("base_level")
        dice_per_slot_level = definition.get("dice_per_slot_level")
        cantrip_upgrade_dice = definition.get("cantrip_upgrade_dice")
        upgrade_levels = (
            definition.get("upgrade_level_1"),
            definition.get("upgrade_level_2"),
            definition.get("upgrade_level_3"),
        )
        targeting_profile = definition.get("targeting_profile")
        base_targets = definition.get("base_targets")
        targets_per_slot_level = definition.get("targets_per_slot_level")
        secondary_range_feet = definition.get("secondary_range_feet")
        valid_targeting_profile = (
            targeting_profile == "area"
            and base_targets == 0
            and targets_per_slot_level == 0
            and secondary_range_feet == 0
        ) or (
            targeting_profile == "chain"
            and scaling_mode == "slot_level"
            and base_level == 6
            and base_targets == 4
            and targets_per_slot_level == 1
            and secondary_range_feet == 30
            and origin_kind == "primary_target"
            and range_feet == 150
            and area_shape == "chain"
            and definition.get("area_size_feet") == 0
            and area_width_feet == 0
        )
        if not valid_targeting_profile:
            raise SystemExit(
                f"Area spell definition has an incoherent targeting profile: {spell_id}"
            )
        valid_profile = (
            targeting_profile == "chain"
            or (
                origin_kind == "self"
                and range_feet == 0
                and area_shape == "cone"
                and area_width_feet == 0
            )
            or (
                origin_kind == "self"
                and range_feet == 0
                and area_shape == "line"
                and isinstance(area_width_feet, int)
                and not isinstance(area_width_feet, bool)
                and area_width_feet >= 1
            )
            or (
                origin_kind == "self"
                and range_feet == 0
                and area_shape == "cube"
                and area_width_feet == 0
            )
            or (
                origin_kind == "point"
                and isinstance(range_feet, int)
                and not isinstance(range_feet, bool)
                and range_feet >= 1
                and area_shape in {"cube", "sphere"}
                and area_width_feet == 0
            )
        )
        if not valid_profile:
            raise SystemExit(
                f"Area spell definition has an incoherent geometry profile: {spell_id}"
            )
        valid_scaling = (
            scaling_mode == "caster_level"
            and base_level == 0
            and dice_per_slot_level == 0
            and isinstance(cantrip_upgrade_dice, int)
            and not isinstance(cantrip_upgrade_dice, bool)
            and cantrip_upgrade_dice >= 1
            and all(
                isinstance(level, int) and not isinstance(level, bool) for level in upgrade_levels
            )
            and upgrade_levels[0] < upgrade_levels[1] < upgrade_levels[2]
        ) or (
            scaling_mode == "slot_level"
            and isinstance(base_level, int)
            and not isinstance(base_level, bool)
            and base_level >= 1
            and cantrip_upgrade_dice == 0
        )
        if not valid_scaling:
            raise SystemExit(f"Area spell definition has an incoherent scaling profile: {spell_id}")
        failed_condition = definition.get("failed_save_condition")
        failed_rounds = definition.get("failed_save_condition_rounds")
        failed_expiry = definition.get("failed_save_condition_expiry", "rounds_repeat_save")
        timed_repeat = (failed_condition is None and failed_rounds is None) or (
            failed_condition == "blinded"
            and type(failed_rounds) is int
            and 1 <= failed_rounds <= 1000
        )
        next_turn = (
            failed_condition == "blinded"
            and failed_rounds is None
            and failed_expiry == "caster_next_turn_end"
        )
        if not (timed_repeat and failed_expiry == "rounds_repeat_save" or next_turn):
            raise SystemExit(f"Area spell failed-save condition is incoherent: {spell_id}")
        zero_damage = definition.get("base_dice") == 0
        if (
            zero_damage != (failed_expiry == "caster_next_turn_end")
            or (
                zero_damage
                and not (
                    definition.get("dice_per_slot_level") == 0
                    and definition.get("damage_die") == "none"
                    and definition.get("damage_type") == "none"
                    and definition.get("success_damage") == "none"
                )
            )
            or (
                not zero_damage
                and (
                    definition.get("damage_die") == "none"
                    or definition.get("damage_type") == "none"
                )
            )
        ):
            raise SystemExit(f"Area spell damage/condition composition is incoherent: {spell_id}")


def _spell_id(heading: str) -> str:
    normalized = unicodedata.normalize("NFKD", heading).encode("ascii", "ignore").decode()
    result = re.sub(r"[^a-z0-9]+", "_", normalized.casefold()).strip("_")
    if not result:
        raise ValueError(f"Spell heading has no stable identifier: {heading!r}")
    return result


def _spell_rows(corpus: dict[str, Any], executable_spell_ids: set[str]) -> list[dict[str, Any]]:
    page_records = corpus["page_records"]
    pages = {record["page"]: record for record in page_records}
    lines = [
        (record["page"], line) for record in page_records for line in record["text"].splitlines()
    ]
    spells: list[dict[str, Any]] = []
    for index, (page, line) in enumerate(lines):
        classification = line.strip()
        is_level_spell = classification.startswith("Level ")
        is_cantrip = " Cantrip " in classification and "Cantrip Upgrade" not in classification
        if not (is_level_spell or is_cantrip):
            continue
        if index == 0:
            raise ValueError("Spell classification has no preceding heading")
        heading = lines[index - 1][1].strip()
        spell_id = _spell_id(heading)
        source_page = pages[page]
        requirements = _casting_requirements(lines, index, spell_id)
        requirements["name"] = heading
        spells.append(
            {
                "id": spell_id,
                "source_heading": heading,
                "casting_requirements": requirements,
                "mechanics_status": (
                    "implemented" if spell_id in executable_spell_ids else "pending"
                ),
                "source": {
                    "artifact": "content/corpus/spells.json",
                    "page": page,
                    "page_text_sha256": source_page["text_sha256"],
                    "classification": classification,
                },
            }
        )
    if len(spells) != SPELL_COUNT:
        raise SystemExit(f"Expected {SPELL_COUNT} official spell descriptions, found {len(spells)}")
    ids = [spell["id"] for spell in spells]
    if len(set(ids)) != SPELL_COUNT:
        raise SystemExit("Official spell inventory contains colliding stable identifiers")
    return spells


def _casting_requirements(
    lines: list[tuple[int, str]],
    start: int,
    spell_id: str,
) -> dict[str, Any]:
    """Read bounded SRD headers, retaining complex material clauses without inventing costs."""
    header: list[str] = []
    for _, raw in lines[start : start + 35]:
        line = raw.strip()
        if line == "System Reference Document 5.2.1" or line.isdigit():
            continue
        header.append(line)
        if line.startswith("Duration:"):
            break
    text = _normalized_text(" ".join(header)).replace(" - ", "")
    text = text.replace("Component:", "Components:")
    fields = ("Casting Time", "Range", "Components", "Duration")
    for field in fields:
        if text.count(field + ":") != 1:
            raise ValueError(f"{spell_id}: expected exactly one {field} header")
    pattern = (
        r"(?:(?:Level (?P<level>[1-9]) (?P<school>\w+))|"
        r"(?P<cantrip_school>\w+) Cantrip) \((?P<classes>[^)]+)\) "
        r"Casting Time: (?P<time>.+?) Range: (?P<range>.+?) "
        r"Components: (?P<components>.+?) Duration: (?P<duration>.+)"
    )
    match = re.fullmatch(pattern, text)
    if match is None:
        raise ValueError(f"{spell_id}: malformed spell casting header")
    classes = [value.strip().casefold() for value in match["classes"].split(",")]
    allowed = {"bard", "cleric", "druid", "paladin", "ranger", "sorcerer", "warlock", "wizard"}
    if not classes or len(set(classes)) != len(classes) or not set(classes) <= allowed:
        raise ValueError(f"{spell_id}: unrecognized class spell list")
    component_match = re.fullmatch(r"([VSM](?:, [VSM])*)(?: \((.+)\))?", match["components"])
    if component_match is None:
        raise ValueError(f"{spell_id}: malformed Components header")
    letters = component_match[1].split(", ")
    material = component_match[2] or ""
    if len(set(letters)) != len(letters) or bool(material) != ("M" in letters):
        raise ValueError(f"{spell_id}: inconsistent material component description")
    priced = bool(re.search(r"\b(?:[GCSEP]P|Copper Pieces|Silver Pieces|Gold Pieces)\b", material))
    consumed = "consumes" in material
    return {
        "level": int(match["level"] or 0),
        "school": (match["school"] or match["cantrip_school"]).casefold(),
        "classes": classes,
        "casting_time": match["time"],
        "ritual": "Ritual" in match["time"],
        "range": match["range"],
        "components": [{"V": "verbal", "S": "somatic", "M": "material"}[c] for c in letters],
        "material": material,
        "material_has_cost": priced,
        "material_consumed": consumed,
        "material_parts": _material_parts(spell_id, material),
        "focus_substitution": bool(material) and not priced and not consumed,
        "duration": match["duration"],
    }


def _material_parts(spell: str, material: str) -> list[dict[str, Any]]:
    """Compile material units; keep mixed consumption and per-target costs separate."""
    if not material:
        return []
    prices = re.findall(r"([\d,]+)\+? (GP|CP|Copper Pieces)", material)
    costs = [int(amount.replace(",", "")) * (100 if unit == "GP" else 1) for amount, unit in prices]
    # These headers contain more than one separately priced physical component.
    mixed = {
        "astral_projection": [(1, "target", 100000, True), (1, "target", 10000, True)],
        "clone": [(1, "fixed", 100000, True), (1, "fixed", 200000, False)],
        "legend_lore": [(1, "fixed", 25000, True), (4, "fixed", 5000, False)],
        "secret_chest": [(1, "fixed", 500000, False), (1, "fixed", 5000, False)],
    }
    if spell in mixed:
        parts = mixed[spell]
        if costs != [part[2] for part in parts]:
            raise ValueError(f"{spell}: multi-part material costs differ from their source")
    else:
        if len(costs) > 1:
            raise ValueError(f"{spell}: material needs an explicit multi-part profile")
        quantity = 2 if spell in {"gentle_repose", "warding_bond", "telepathic_bond"} else 1
        cost = (costs[0] if costs else 0) // (2 if spell == "gentle_repose" else 1)
        scale = "target" if spell == "create_undead" else "fixed"
        parts = [(quantity, scale, cost, "consumes" in material)]
    return [
        {
            "id": f"{spell}.{index}",
            "quantity": quantity,
            "scale": scale,
            "minimum_value_cp": cost,
            "consumed": consumed,
        }
        for index, (quantity, scale, cost, consumed) in enumerate(parts, 1)
    ]


def _tool_schema(tool: dict[str, Any]) -> dict[str, object]:
    properties: dict[str, object] = {}
    required: list[str] = []
    for parameter in tool["parameters"]:
        if parameter.get("from_state") is not None:
            continue
        parameter_type = parameter["type"]
        if parameter_type == "enum":
            child: dict[str, object] = {"type": "string", "enum": parameter["enum_values"]}
        elif parameter_type in {"string", "entity_id"}:
            child = {"type": "string"}
        elif parameter_type == "entity_id_list":
            child = {"type": "array", "items": {"type": "string"}}
        else:
            child = {"type": parameter_type}
        if "items" in parameter:
            child["items"] = parameter["items"]
        if parameter.get("resolve") == "participant":
            child["x-sylvia-gm-reference"] = "participant"
        elif parameter.get("resolve") == "entity":
            child["x-sylvia-gm-reference"] = "entity"
        elif parameter.get("resolve") == "entity_list" and isinstance(child.get("items"), dict):
            child["items"]["x-sylvia-gm-reference"] = "entity"
        for constraint in ("minimum", "maximum"):
            if constraint in parameter:
                child[constraint] = parameter[constraint]
        for runtime_name, schema_name in (
            ("min_items", "minItems"),
            ("max_items", "maxItems"),
            ("unique_items", "uniqueItems"),
        ):
            if runtime_name in parameter:
                child[schema_name] = parameter[runtime_name]
        properties[parameter["name"]] = child
        if parameter.get("required", True):
            required.append(parameter["name"])
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _spell_mechanics_source(
    corpora: dict[str, dict[str, Any]], declaration: dict[str, Any]
) -> dict[str, Any]:
    source_rules = declaration.get("general_rules")
    if not isinstance(source_rules, dict) or not source_rules:
        raise SystemExit("Spell mechanics source map must declare general casting rules")
    source_spells = declaration.get("spells")
    if not isinstance(source_spells, dict) or not source_spells:
        raise SystemExit("Spell mechanics source map must declare executable spells")
    derived_rules: dict[str, dict[str, Any]] = {}
    for rule_id, source in source_rules.items():
        corpus_id = source.get("corpus")
        corpus = corpora.get(corpus_id)
        if not isinstance(corpus, dict):
            raise SystemExit(f"General spell rule corpus is invalid: {rule_id}")
        pages = {record["page"]: record for record in corpus["page_records"]}
        page_number = source.get("page")
        heading = source.get("source_heading")
        next_heading = source.get("next_heading")
        not_yet_mapped = source.get("not_yet_mapped")
        if (
            not isinstance(rule_id, str)
            or corpus_id not in {"spells", "playing_the_game", "rules_glossary"}
            or not isinstance(page_number, int)
            or page_number not in pages
            or not isinstance(heading, str)
            or _spell_id(heading) != rule_id
            or (next_heading is not None and not isinstance(next_heading, str))
            or not isinstance(not_yet_mapped, list)
            or any(not isinstance(item, str) or not item for item in not_yet_mapped)
        ):
            raise SystemExit(f"General spell rule source entry is invalid: {rule_id}")
        page = pages[page_number]
        text = page["text"]
        try:
            start = text.index(f"{heading}\n")
            end = len(text) if next_heading is None else text.index(f"{next_heading}\n", start)
        except ValueError as error:
            raise SystemExit(f"General spell rule boundaries are absent: {rule_id}") from error
        derived_text = text[start:end].rstrip()
        source_namespace = corpus_id.replace("_", "-")
        if corpus_id == "spells":
            section_path = ["Spells", "Casting Spells", heading]
        elif corpus_id == "playing_the_game":
            section_path = ["Playing the Game", "Combat", heading]
        else:
            section_path = ["Rules Glossary", heading]
        derived_rules[rule_id] = {
            "authority_id": "wotc.srd.5.2.1.pdf",
            "claim_status": (
                "COMPLETE_MECHANICAL_MAPPING"
                if not not_yet_mapped
                else "PARTIAL_MECHANICAL_MAPPING"
            ),
            "derivation": (
                "Exact excerpt from the immutable admitted official corpus page; "
                "no normalization or paraphrase is applied."
            ),
            "derived_text": derived_text,
            "derived_text_sha256": hashlib.sha256(derived_text.encode("utf-8")).hexdigest(),
            "page": page_number,
            "page_text_sha256": page["text_sha256"],
            "section_path": section_path,
            "source_artifact": f"content/corpus/{corpus_id}.json",
            "source_unit_id": f"srd.5.2.1.{source_namespace}.{rule_id.replace('_', '-')}",
            "rule_id": rule_id,
            "not_yet_mapped": not_yet_mapped,
        }
    derived: dict[str, dict[str, Any]] = {}
    pages = {record["page"]: record for record in corpora["spells"]["page_records"]}
    for spell_id, spell in source_spells.items():
        if not isinstance(spell_id, str) or not isinstance(spell, dict):
            raise SystemExit("Spell mechanics source entries must be mappings")
        source = spell["source"]
        page_number = source.get("page")
        page_span = source.get("pages")
        heading = source.get("source_heading")
        next_heading = source.get("next_heading")
        not_yet_mapped = source.get("not_yet_mapped")
        if (
            (page_number is None) == (page_span is None)
            or not isinstance(heading, str)
            or _spell_id(heading) != spell_id
            or (next_heading is not None and not isinstance(next_heading, str))
            or not isinstance(not_yet_mapped, list)
            or any(not isinstance(item, str) or not item for item in not_yet_mapped)
        ):
            raise SystemExit(f"Spell mechanics source entry is invalid: {spell_id}")
        base_source = {
            "authority_id": "wotc.srd.5.2.1.pdf",
            "claim_status": (
                "PARTIAL_MECHANICAL_MAPPING" if not_yet_mapped else "COMPLETE_MECHANICAL_MAPPING"
            ),
            "derivation": (
                "Exact excerpt from the immutable admitted official corpus page bytes; "
                "no normalization or paraphrase is applied."
            ),
            "section_path": ["Spells", "Spell Descriptions", heading],
            "source_unit_id": f"srd.5.2.1.spells.{spell_id.replace('_', '-')}",
            "spell_id": spell_id,
            "not_yet_mapped": not_yet_mapped,
        }
        if page_number is not None:
            if not isinstance(page_number, int) or page_number not in pages:
                raise SystemExit(f"Spell mechanics source entry is invalid: {spell_id}")
            text = pages[page_number]["text"]
            try:
                start = text.index(f"{heading}\n")
                end = len(text) if next_heading is None else text.index(f"{next_heading}\n", start)
            except ValueError as error:
                raise SystemExit(f"Spell source boundaries are absent: {spell_id}") from error
            derived_text = text[start:end].rstrip()
            continuation = None
            if "continuation_start" in source:
                expected_continuations = {
                    ("antilife_shell", 109): (
                        "or make attacks with Ranged or Reach weapons \nthrough the barrier.\n",
                        "Antimagic Field",
                    ),
                    ("find_the_path", 131): (
                        "face a choice of paths along the way there, you know \n"
                        "which path is the most direct.\n",
                        "Find Traps",
                    ),
                }
                if (source["continuation_start"], source["continuation_next_heading"]) != (
                    expected_continuations.get((spell_id, page_number))
                ):
                    raise SystemExit("Reordered spell continuation must match its pinned page")
                try:
                    continuation_start = text.index(source["continuation_start"])
                    continuation_end = text.index(
                        source["continuation_next_heading"] + "\n",
                        continuation_start,
                    )
                except ValueError as error:
                    raise SystemExit("Antilife's source continuation is absent") from error
                continuation_text = text[continuation_start:continuation_end].rstrip()
                continuation = {
                    "page": page_number,
                    "page_text_sha256": pages[page_number]["text_sha256"],
                    "derived_text": continuation_text,
                    "derived_text_sha256": hashlib.sha256(
                        continuation_text.encode("utf-8")
                    ).hexdigest(),
                }
                derived_text += "\n" + continuation_text
            derived[spell_id] = {
                **base_source,
                "derived_text": derived_text,
                "derived_text_sha256": hashlib.sha256(derived_text.encode("utf-8")).hexdigest(),
                "page": page_number,
                "page_text_sha256": pages[page_number]["text_sha256"],
                **({"continuation": continuation} if continuation is not None else {}),
            }
            continue
        if (
            not isinstance(page_span, list)
            or len(page_span) < 2
            or any(page not in pages for page in page_span)
        ):
            raise SystemExit(f"Spell mechanics source entry is invalid: {spell_id}")
        first_text = pages[page_span[0]]["text"]
        last_text = pages[page_span[-1]]["text"]
        try:
            first_start = first_text.index(f"{heading}\n")
            last_end = (
                len(last_text) if next_heading is None else last_text.index(f"{next_heading}\n")
            )
        except ValueError as error:
            raise SystemExit(f"Spell source boundaries are absent: {spell_id}") from error
        if any(f"{heading}\n" in pages[page]["text"] for page in page_span[1:]) or (
            next_heading is not None
            and any(f"{next_heading}\n" in pages[page]["text"] for page in page_span[:-1])
        ):
            raise SystemExit(f"Spell source boundaries are not confined to span ends: {spell_id}")
        fragments: list[dict[str, Any]] = []
        for index, page in enumerate(page_span):
            text = pages[page]["text"]
            start = first_start if index == 0 else 0
            end = last_end if index == len(page_span) - 1 else len(text)
            fragment = text[start:end].rstrip()
            fragments.append(
                {
                    "page": page,
                    "page_text_sha256": pages[page]["text_sha256"],
                    "derived_text": fragment,
                    "derived_text_sha256": hashlib.sha256(fragment.encode("utf-8")).hexdigest(),
                }
            )
        joined_text = "\n".join(fragment["derived_text"] for fragment in fragments)
        derived[spell_id] = {
            **base_source,
            "pages": fragments,
            "joined_text_sha256": hashlib.sha256(joined_text.encode("utf-8")).hexdigest(),
        }
    return {
        "format_id": "sylvia-gm-srd-spell-mechanics-source",
        "format_version": 4,
        "source_sha256": SOURCE_SHA256,
        "general_rules": derived_rules,
        "spells": derived,
    }


def _build_spell_outputs(
    *,
    corpus_bytes: bytes,
    playing_the_game_bytes: bytes,
    rules_glossary_bytes: bytes,
    declaration_text: str,
    runtime_source: str,
    record_bytes: bytes,
    declarations_bytes: bytes,
    identity_bytes: bytes,
) -> tuple[dict[str, bytes], dict[str, int]]:
    """Derive and validate every generated byte without mutating the filesystem."""
    corpus = json.loads(corpus_bytes)
    playing_the_game_corpus = json.loads(playing_the_game_bytes)
    rules_glossary_corpus = json.loads(rules_glossary_bytes)
    if (
        corpus["source_sha256"] != SOURCE_SHA256
        or playing_the_game_corpus["source_sha256"] != SOURCE_SHA256
        or rules_glossary_corpus["source_sha256"] != SOURCE_SHA256
    ):
        raise SystemExit("Refusing to build spell inventory from unrecognized SRD bytes")
    declaration = _spell_declaration(declaration_text)
    spells = _spell_rows(corpus, set(declaration["spells"]))
    spell_source = _spell_mechanics_source(
        {
            "spells": corpus,
            "playing_the_game": playing_the_game_corpus,
            "rules_glossary": rules_glossary_corpus,
        },
        declaration,
    )
    runtime_text, runtime, spell_definitions = _compile_runtime(
        declaration,
        runtime_source,
        {row["id"]: row["casting_requirements"] for row in spells},
    )
    spell_tool = runtime["tools"].get(SPELL_TOOL)
    if not isinstance(spell_tool, dict):
        raise SystemExit(f"Runtime is missing {SPELL_TOOL}")
    executable_spell_ids = set(spell_definitions)
    if set(spell_source["spells"]) != executable_spell_ids:
        raise SystemExit("Executable spell definitions must exactly match their source map")
    inventory = {
        "format_id": "sylvia-gm-srd-spell-status-inventory",
        "format_version": 1,
        "source_sha256": SOURCE_SHA256,
        "spell_count": SPELL_COUNT,
        "status_definitions": {
            "implemented": (
                "A source-linked spell has executable mechanics. Its spell-mechanics-source.json "
                "not_yet_mapped list records any remaining known rules."
            ),
            "pending": (
                "The exact official spell description is admitted in the immutable corpus "
                "but has no executable mechanic claim."
            ),
        },
        "spells": spells,
    }
    executable_spell_ids = {
        spell["id"] for spell in spells if spell["mechanics_status"] == "implemented"
    }
    if set(spell_definitions) != executable_spell_ids:
        raise SystemExit(
            "Executable spell selector and package-owned definitions must exactly match"
        )
    support_records = [
        {
            "id": f"spell.{spell['id']}",
            "reference_availability": "included",
            "gameplay_resolution": (
                "engine_specialized"
                if spell["mechanics_status"] == "implemented"
                else "unsupported"
            ),
            "mechanic_family": None,
            "deterministic_part": None,
            "gm_part": None,
        }
        for spell in spells
    ]
    support_records.extend(
        [
            {
                "id": "catalog.srd-reference",
                "reference_availability": "included",
                "gameplay_resolution": "not_applicable",
                "mechanic_family": None,
                "deterministic_part": None,
                "gm_part": None,
            },
            {
                "id": "mechanic.adjudicated-creature-attack",
                "reference_availability": "included",
                "gameplay_resolution": "hybrid",
                "mechanic_family": "bounded-creature-attack-adjudication",
                "deterministic_part": "d20 roll, damage roll, defenses, and committed mutation",
                "gm_part": "attack profile adapted from the creature's admitted rules",
            },
            {
                "id": "mechanic.d20-test",
                "reference_availability": "included",
                "gameplay_resolution": "engine_family",
                "mechanic_family": "d20-test",
                "deterministic_part": None,
                "gm_part": None,
            },
            {
                "id": "mechanic.opportunity-reaction",
                "reference_availability": "included",
                "gameplay_resolution": "hybrid",
                "mechanic_family": "trigger-bound-reaction",
                "deterministic_part": (
                    "trigger identity, Reaction spend, d20 roll, damage, and committed mutation"
                ),
                "gm_part": "non-spatial visibility and the reactor's admitted attack profile",
            },
            {
                "id": "mechanic.group-initiative",
                "reference_availability": "included",
                "gameplay_resolution": "engine_family",
                "mechanic_family": "ordered-group-initiative",
                "deterministic_part": None,
                "gm_part": None,
            },
            {
                "id": "mechanic.short-rest-hit-die",
                "reference_availability": "included",
                "gameplay_resolution": "engine_family",
                "mechanic_family": "short-rest-hit-die-recovery",
                "deterministic_part": None,
                "gm_part": None,
            },
            {
                "id": "mechanic.weapon-loading-extra-attacks",
                "reference_availability": "included",
                "gameplay_resolution": "unsupported",
                "mechanic_family": None,
                "deterministic_part": None,
                "gm_part": None,
            },
        ]
    )
    support = {
        "format_id": "sylvia-gm-gameplay-support",
        "format_version": 1,
        "records": sorted(support_records, key=lambda item: item["id"]),
    }
    record = json.loads(record_bytes)
    identity_source = json.loads(identity_bytes)
    if (
        not isinstance(identity_source, dict)
        or identity_source.get("format_id") != "sylvia-gm-package-identity-source"
        or identity_source.get("format_version") != 1
        or not isinstance(identity_source.get("primary"), dict)
        or not isinstance(identity_source.get("reference"), dict)
    ):
        raise SystemExit("SRD package identity source is invalid")
    primary_identity = identity_source["primary"]
    reference_identity = identity_source["reference"]
    expected_primary_identity = {
        "id": primary_identity["id"],
        "version": primary_identity["version"],
        "role": primary_identity["role"],
        "content_kind": primary_identity["content_kind"],
        "metadata": {"label": primary_identity["label"]},
        "provenance": {
            "publisher": primary_identity["publisher"],
            "source_sha256": primary_identity["source_sha256"],
        },
        "compatibility": primary_identity["compatibility"],
        "minimum_model_capability_tier": primary_identity["minimum_model_capability_tier"],
        "content_declarations": primary_identity["content_declarations"],
        "dependencies": primary_identity["dependencies"],
    }
    if any(record.get(key) != value for key, value in expected_primary_identity.items()):
        raise SystemExit("Primary SRD record differs from its canonical identity source")
    public_predicates = {
        declaration["name"]: declaration.get("state_predicates", [])
        for declaration in record["tools"]
    }
    # Prepared creatures and admitted characters share the public casting path.
    public_predicates[SPELL_TOOL] = [
        predicate
        for predicate in public_predicates[SPELL_TOOL]
        if predicate.get("key") != "combat.active_actor_kind"
    ]
    public_predicates[EXPLORATION_SPELL_TOOL] = [
        {
            "namespace": "wotc.srd.5.2.1",
            "key": "combat.encounter_active",
            "equals": True,
            "negated": True,
        }
    ]
    public_predicates["advance_srd_lasting_time"] = public_predicates[EXPLORATION_SPELL_TOOL]
    public_predicates["end_concentration"] = []
    public_predicates["dash_srd_mobility_bonus_action"] = [
        predicate
        for predicate in public_predicates[SPELL_TOOL]
        if predicate["key"] in {"combat.phase", "combat.active_actor_can_act"}
    ]
    public_predicates["transition_srd_water_walk"] = []
    public_predicates["dismiss_srd_mobility_spell"] = []
    for tool_name in SPELL_SUPPORT_TOOLS:
        public_predicates.setdefault(tool_name, [])
    generated_tool_names = {SPELL_TOOL, EXPLORATION_SPELL_TOOL} | SPELL_SUPPORT_TOOLS
    tool_declarations = []
    for tool_name in sorted(generated_tool_names):
        runtime_tool = runtime["tools"].get(tool_name)
        if not isinstance(runtime_tool, dict):
            raise SystemExit(f"Runtime is missing {tool_name}")
        tool_declarations.append(
            {
                "name": tool_name,
                "capability": SPELL_CAPABILITY,
                "writers": ["dm"],
                "turn_statuses": ["open"],
                "state_predicates": [],
                "input_schema": _tool_schema(runtime_tool),
            }
        )
    all_declarations = {
        tool["name"]: tool
        for tool in [
            tool
            for tool in record["tools"]
            if tool["name"] not in RETIRED_SPELL_TOOLS | generated_tool_names
        ]
        + tool_declarations
    }
    missing_model_tools = sorted(set(public_predicates) - set(all_declarations))
    if missing_model_tools:
        raise SystemExit(f"Model availability references undeclared tools: {missing_model_tools}")
    record["tools"] = []
    for name in sorted(public_predicates):
        declaration = dict(all_declarations[name])
        if name in {
            "apply_srd_condition",
            "resolve_srd_condition_saving_throw",
            "spend_combat_movement",
        }:
            declaration["input_schema"] = _tool_schema(runtime["tools"][name])
        declaration["state_predicates"] = public_predicates[name]
        record["tools"].append(declaration)
    record["capabilities"] = sorted(set(record["capabilities"]) | {SPELL_CAPABILITY})
    declarations = json.loads(declarations_bytes)

    def spell_source_locator(source: dict[str, Any]) -> dict[str, Any]:
        if "pages" not in source:
            return {
                "kind": "pdf_page_section",
                "page": source["page"],
                "section_path": source["section_path"],
                "derived_text_sha256": source["derived_text_sha256"],
            }
        return {
            "kind": "pdf_page_range",
            "pages": [source["pages"][0]["page"], source["pages"][-1]["page"]],
            "content_sha256": source["joined_text_sha256"],
        }

    inclusion = {
        "id": "alpha04-spells-executable",
        "family": "spells",
        "kind": "mechanic",
        "disposition": "included_mechanical",
        "classifications": ["official_licensed_content", "sylvia_gm_implementation"],
        "sources": sorted(
            [
                {
                    "authority_id": "wotc.srd.5.2.1.pdf",
                    "source_unit_id": source["source_unit_id"],
                    "locator": {
                        "kind": "pdf_page_section",
                        "page": source["page"],
                        "section_path": source["section_path"],
                        "derived_text_sha256": source["derived_text_sha256"],
                    },
                }
                for _, source in sorted(spell_source["general_rules"].items())
            ]
            + [
                {
                    "authority_id": "wotc.srd.5.2.1.pdf",
                    "source_unit_id": source["source_unit_id"],
                    "locator": spell_source_locator(source),
                }
                for _, source in sorted(spell_source["spells"].items())
            ],
            key=lambda item: item["source_unit_id"],
        ),
        "links": {
            "artifacts": sorted(
                [
                    "content/corpus/spells.json",
                    "content/corpus/playing_the_game.json",
                    "content/corpus/rules_glossary.json",
                    "content/magic-missile.source.json",
                    "content/spell-mechanics.yaml",
                    "content/spell-mechanics-source.json",
                    "content/spell-status-inventory.json",
                    "runtime/ruleset.yaml",
                ]
            ),
            "capabilities": [SPELL_CAPABILITY],
            "mechanics": [
                "automatic_force_projectiles",
                "instantaneous_area_save_composite_damage",
                "instantaneous_area_save_damage",
                "instantaneous_attack_area_save_damage",
                "instantaneous_multi_target_healing",
                "instantaneous_restoration",
                "instantaneous_threshold_death_damage",
                "lasting_reactive_concentration",
                "multi_spell_attack_damage",
                "multi_target_concentration_ability_advantage",
                "multi_target_concentration_d20_modifier",
                "multi_target_timed_control",
                "timed_control_behavior",
                "timed_control_orders",
                "timed_control_affliction",
                "multi_target_timed_repeating_save_condition",
                "package_defined_spell_dispatch",
                "single_target_concentration_armor_class_bonus",
                "single_target_concentration_damage_resistance",
                "single_target_save_damage",
                "spell_attack_damage",
                "spell_casting_budget",
                "spell_healing",
                "spell_lasting_effect_clock",
                "spell_slot_spend",
            ],
            "procedures": [],
            "prompt_fragments": [],
            "schemas": [],
            "state_namespaces": [
                "wotc.srd.5.2.1.combat",
                "wotc.srd.5.2.1.entities",
                "wotc.srd.5.2.1.state",
            ],
            "tools": sorted(generated_tool_names),
            "ui_surfaces": [],
        },
        "evidence": {
            "tests": sorted(
                [
                    "alpha04-srd-spell-magic-missile",
                    "alpha04-srd-spell-attack-family",
                    "alpha04-srd-spell-multi-attack-family",
                    "alpha04-srd-spell-healing-family",
                    "alpha04-srd-spell-lasting-concentration-family",
                    "alpha04-srd-spell-concentration-armor-class-family",
                    "alpha04-srd-spell-area-save-damage-family",
                    "alpha04-srd-spell-area-save-composite-damage-family",
                    "alpha04-srd-spell-multi-target-healing-family",
                    "alpha04-srd-spell-restoration-family",
                    "alpha04-srd-spell-casting-budgets",
                    "alpha04-srd-spell-save-damage-family",
                ]
            )
        },
    }
    declarations["inclusions"] = sorted(
        [
            item
            for item in declarations["inclusions"]
            if item["id"] not in {"alpha04-spell-magic-missile", inclusion["id"]}
        ]
        + [inclusion],
        key=lambda item: item["id"],
    )
    model_tool_names = {tool["name"] for tool in record["tools"]}
    for declared_content in declarations["inclusions"]:
        links = declared_content.get("links")
        if not isinstance(links, dict) or not isinstance(links.get("tools"), list):
            continue
        links["tools"] = sorted(
            tool_name for tool_name in links["tools"] if tool_name in model_tool_names
        )

    magic_missile_source = spell_source["spells"].get("magic_missile")
    if not isinstance(magic_missile_source, dict):
        raise SystemExit(
            "Spell mechanics declaration must retain magic_missile while its compatibility "
            "source artifact is shipped"
        )
    source_authority = {
        "id": "wotc.srd.5.2.1.pdf",
        "document_id": "dnd-srd-5.2.1-en",
        "version": "5.2.1",
        "media_type": "application-pdf",
        "content_sha256": SOURCE_SHA256,
        "license_id": "CC-BY-4.0",
    }
    complete_source = {
        "authority_id": "wotc.srd.5.2.1.pdf",
        "source_unit_id": "srd.5.2.1.complete-corpus",
        "locator": {
            "kind": "pdf_page_range",
            "pages": [1, 364],
            "content_sha256": SOURCE_SHA256,
        },
    }
    boundary = [
        {
            "artifact_id": "srd-5.2.1-source-catalog",
            "sha256": "34b0b5587f6e610dbf325c9cae634ae7bf57fd242bc6ecdfa6f6fc59dce4fc55",
        }
    ]
    existing_runtime = next(
        (item for item in declarations["inclusions"] if item["id"] == "srd-5.2.1-gameplay-runtime"),
        None,
    )
    if existing_runtime is None:
        raise SystemExit("The SRD runtime content inclusion is missing")
    existing_artifacts = existing_runtime["links"]["artifacts"]
    declarations = {
        "format_id": "sylvia-gm-package-content-declarations",
        "format_version": 1,
        "scope": {
            "scope_id": "srd-5.2.1-gameplay",
            "scope_kind": "bounded_package",
            "claim_status": "NOT_COMPLETE",
            "source_boundary_refs": boundary,
        },
        "source_authorities": [source_authority],
        "inclusions": [
            {
                "id": "srd-5.2.1-gameplay-runtime",
                "family": "srd-gameplay",
                "kind": "ruleset_runtime",
                "disposition": "included_mechanical",
                "classifications": [
                    "official_licensed_content",
                    "sylvia_gm_implementation",
                ],
                "sources": [complete_source],
                "links": {
                    "artifacts": sorted(
                        set(existing_artifacts)
                        | {
                            "ATTRIBUTION.md",
                            "prompts/gm-operating-guide.md",
                            "runtime/ruleset.yaml",
                            "support/gameplay-resolution.json",
                        }
                    ),
                    "capabilities": list(record["capabilities"]),
                    "mechanics": [],
                    "procedures": [],
                    "prompt_fragments": ["gm-operating-guide"],
                    "schemas": [item["schema_id"] for item in record["participant_artifacts"]],
                    "state_namespaces": [
                        "wotc.srd.5.2.1.combat",
                        "wotc.srd.5.2.1.entities",
                        "wotc.srd.5.2.1.state",
                    ],
                    "tools": sorted(item["name"] for item in record["tools"]),
                    "ui_surfaces": sorted(item["id"] for item in record["ui_surfaces"]),
                },
                "evidence": {"tests": []},
            }
        ],
        "exclusions": [],
    }
    _split_spell_runtime_tool_links(declarations, record["tools"])
    reference_artifacts = [
        "ATTRIBUTION.md",
        "content/corpus-index.json",
        *[
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in sorted((CONTENT_ROOT / "corpus").glob("*.json"))
        ],
    ]
    reference_declarations = {
        "format_id": "sylvia-gm-package-content-declarations",
        "format_version": 1,
        "scope": {
            "scope_id": "srd-5.2.1-reference",
            "scope_kind": "complete_ruleset",
            "claim_status": "COMPLETE",
            "source_boundary_refs": boundary,
        },
        "source_authorities": [source_authority],
        "inclusions": [
            {
                "id": "official-srd-5.2.1-corpus",
                "family": "official-srd-corpus",
                "kind": "reference_corpus",
                "disposition": "included_nonmechanical",
                "classifications": ["official_licensed_content"],
                "sources": [complete_source],
                "links": {
                    "artifacts": reference_artifacts,
                    "capabilities": ["dnd.srd.5.2.1.corpus"],
                    "mechanics": [],
                    "procedures": [],
                    "prompt_fragments": [],
                    "schemas": [],
                    "state_namespaces": [],
                    "tools": [],
                    "ui_surfaces": [],
                },
                "evidence": {"tests": []},
            }
        ],
        "exclusions": [],
    }
    reference_record = {
        "id": reference_identity["id"],
        "version": reference_identity["version"],
        "role": reference_identity["role"],
        "content_kind": reference_identity["content_kind"],
        "metadata": {"label": reference_identity["label"]},
        "provenance": {
            "publisher": reference_identity["publisher"],
            "source_sha256": reference_identity["source_sha256"],
        },
        "compatibility": reference_identity["compatibility"],
        "minimum_model_capability_tier": reference_identity["minimum_model_capability_tier"],
        "dependencies": reference_identity["dependencies"],
        "capabilities": ["dnd.srd.5.2.1.corpus"],
        "tools": [],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "participant_artifacts": [],
        "content_declarations": reference_identity["content_declarations"],
    }
    outputs = {
        "runtime": runtime_text.encode("utf-8"),
        "inventory": _canonical(inventory),
        "source_map": _canonical(spell_source),
        "non_beast_forms": _canonical(build_non_beast_index()),
        "magic_missile_source": _canonical(magic_missile_source),
        "record": _canonical(record),
        "declarations": _canonical(declarations),
        "support": _canonical(support),
        "reference_record": _canonical(reference_record),
        "reference_declarations": _canonical(reference_declarations),
    }
    counts = {
        "total": len(spells),
        "implemented": sum(spell["mechanics_status"] == "implemented" for spell in spells),
        "pending": sum(spell["mechanics_status"] == "pending" for spell in spells),
    }
    return outputs, counts


@scoped_generator_yaml
def main() -> None:
    outputs, counts = _build_spell_outputs(
        corpus_bytes=CORPUS_PATH.read_bytes(),
        playing_the_game_bytes=PLAYING_THE_GAME_CORPUS_PATH.read_bytes(),
        rules_glossary_bytes=RULES_GLOSSARY_CORPUS_PATH.read_bytes(),
        declaration_text=SPELL_MECHANICS_PATH.read_text(encoding="utf-8"),
        runtime_source=RUNTIME_PATH.read_text(encoding="utf-8"),
        record_bytes=RECORD_PATH.read_bytes(),
        declarations_bytes=DECLARATIONS_PATH.read_bytes(),
        identity_bytes=IDENTITY_PATH.read_bytes(),
    )

    # All candidate bytes are derived and validated before any tracked output is replaced.
    for path, output_name in (
        (RUNTIME_PATH, "runtime"),
        (INVENTORY_PATH, "inventory"),
        (SPELL_MECHANICS_SOURCE_PATH, "source_map"),
        (NON_BEAST_FORMS_PATH, "non_beast_forms"),
        (MAGIC_MISSILE_SOURCE_PATH, "magic_missile_source"),
        (RECORD_PATH, "record"),
        (DECLARATIONS_PATH, "declarations"),
        (SUPPORT_PATH, "support"),
        (REFERENCE_RECORD_PATH, "reference_record"),
        (REFERENCE_DECLARATIONS_PATH, "reference_declarations"),
    ):
        _replace_bytes(path, outputs[output_name])
    print(
        "Built SRD 5.2.1 spell inventory:",
        counts["total"],
        "spells; implemented",
        counts["implemented"],
        "pending",
        counts["pending"],
    )


if __name__ == "__main__":
    main()
