"""Compose the completed workers' selected SRD repairs onto Main's retained owners."""
from copy import deepcopy
from .srd_launch_restorative import bind_restorative_launch_repairs
from .srd_lite_mass_cure_geometry import bind_mass_cure_scene_geometry
from .srd_lite_fire_storm_geometry import bind_fire_storm_scene_geometry, expose_fire_storm_cube_intent
from .srd_lite_steed_life_bond import bind_steed_life_bond
from .srd_lite_steed_healing_touch import bind_steed_healing_touch
from .srd_lite_steed_casting_statistics import bind_steed_casting_statistics
from .srd_lite_steed_slam import bind_steed_slam, bind_steed_slam_widgets
from .srd_lite_steed_slam_cover import bind_steed_slam_cover
from .srd_lite_steed_fell_glare import bind_steed_fell_glare
from .srd_lite_steed_fey_step import bind_steed_fey_step
from .srd_lite_steed_mount import bind_steed_mount
from .srd_lite_steed_control import bind_steed_control
from .srd_lite_launch_area_concentration_receipts import bind_area_damage_concentration_receipts
from .srd_lite_concentration_cards import bind_area_concentration_cards
from .srd_lite_concentration_totals import bind_concentration_roll_totals
from .srd_lite_dragon_expiry import bind_dragon_natural_expiry_cleanup
from .srd_scope65_owned_four import bind_owned_srd_sunburst_cover_ids
from .srd_sunburst_geometry import bind_sunburst_origin_interior
from .srd_scope96_guardian_entry import bind_guardian_movement_entry
from .srd_revised_casting_rank import bind_revised_casting_rank
from .srd_revised_misty_step import bind_revised_misty_step
from .srd_revised_darkvision import capture_srd_darkvision, bind_revised_srd_darkvision
from .srd_revised_longstrider import capture_srd_longstrider, bind_revised_srd_longstrider
from .srd_revised_information import capture_srd_information, bind_revised_srd_information
from .srd_revised_fly_alterself import bind_revised_fly_alterself
from .srd65_dispel_magic import bind_srd65_dispel_magic
from .srd_revised_ethereal_tree import bind_revised_ethereal_tree
from .srd_revised_foresight import preserve_foresight
from .srd_revised_contact_patron import repair_contact_patron
from .srd_revised_long_cast_rank import bind_revised_long_cast_rank
from .srd_revised_foresight_touch import bind_revised_srd_foresight_touch
from .srd_revised_fly_touch import bind_revised_srd_fly_touch
from .srd_revised_longstrider_touch import bind_revised_longstrider_touch
from .srd_revised_information_expiry import bind_revised_srd_information_expiry
from .srd_revised_information_group_expiry import bind_revised_srd_information_group_expiry
from .srd_material_card_formatting import bind_material_card_formatting
from .srd_retained_zone_support import bind_environment_attack_visibility
from .srd_retained_spell_attack_sight import bind_environment_spell_attack_sight
from .srd_retained_web_lifecycle_v1 import bind_web_support_and_collapse
from .srd_retained_web_fire_v2 import bind_web_fire_cells
from .srd_retained_web_flames_v3 import bind_web_flame_sections
from .srd_retained_ice_storm_geometry_v3 import stage_runtime as bind_ice_storm_geometry


def bind_worker_repairs(runtime, record, selected, native_pool):
    """Keep original spell contracts while repairing their demonstrated shared seams."""
    selected = frozenset(selected)
    if 'ice_storm' in selected:
        bind_ice_storm_geometry(runtime)
    bind_revised_casting_rank(runtime)
    bind_revised_long_cast_rank(runtime)
    if 'misty_step' in selected:
        bind_revised_misty_step(runtime)
    if 'darkvision' in selected:
        bind_revised_srd_darkvision(runtime, capture_srd_darkvision(runtime))
    if 'longstrider' in selected:
        bind_revised_srd_longstrider(runtime, capture_srd_longstrider(runtime))
    if {'arcane_eye', 'speak_with_animals'} & selected:
        bind_revised_srd_information(runtime, capture_srd_information(runtime))
    if {'fly', 'alter_self'} & selected:
        bind_revised_fly_alterself(runtime, record)
    if 'dispel_magic' in selected:
        bind_srd65_dispel_magic(runtime)
    if {'etherealness', 'tree_stride'} & selected:
        bind_revised_ethereal_tree(runtime)
    if 'foresight' in selected:
        preserve_foresight(runtime)
        bind_revised_srd_foresight_touch(runtime)
    if 'fly' in selected:
        bind_revised_srd_fly_touch(runtime)
    if 'longstrider' in selected:
        bind_revised_longstrider_touch(runtime)
    if 'arcane_eye' in selected:
        bind_revised_srd_information_expiry(runtime)
        bind_revised_srd_information_group_expiry(runtime)
    if 'contact_other_plane' in selected:
        repair_contact_patron(runtime, record)
    bind_restorative_launch_repairs(runtime)
    if 'find_steed' in selected:
        bind_steed_life_bond(runtime)
        bind_steed_healing_touch(runtime)
        bind_steed_casting_statistics(runtime)
        bind_steed_slam(runtime)
        bind_steed_slam_widgets(runtime)
        bind_steed_slam_cover(runtime)
        bind_steed_fell_glare(runtime)
        bind_steed_fey_step(runtime)
        bind_steed_mount(runtime, record)
        bind_steed_control(runtime)
    if 'mass_cure_wounds' in selected:
        bind_mass_cure_scene_geometry(runtime)
    bind_area_damage_concentration_receipts(runtime)
    bind_concentration_roll_totals(runtime)
    bind_area_concentration_cards(runtime)
    if 'dragons_breath' in selected:
        bind_dragon_natural_expiry_cleanup(runtime)
    if 'sunburst' in selected:
        bind_sunburst_origin_interior(runtime)
        if 'parameter_declarations' in runtime:
            raise ValueError('Main composes worker repairs at its resolved parameter boundary')
        runtime['parameter_declarations'] = {}
        bind_owned_srd_sunburst_cover_ids(runtime)
        pool = runtime.pop('parameter_declarations')
        for tool in runtime['tools'].values():
            tool['parameters'] = [deepcopy(pool[row]) if isinstance(row, str) and row in pool else row for row in tool['parameters']]
    if 'guardian_of_faith' in selected:
        bind_guardian_movement_entry(runtime)
        owner = runtime['tools']['resolve_srd_scope96_guardian_move']
        owner['parameters'] = [deepcopy(native_pool[row]) if isinstance(row, str) else row for row in owner['parameters']]
    bind_material_card_formatting(runtime)
    if 'fire_storm' in selected:
        bind_fire_storm_scene_geometry(runtime)
        expose_fire_storm_cube_intent(runtime, record)
    bind_environment_attack_visibility(runtime, selected)
    if 'web' in selected:
        bind_web_support_and_collapse(runtime, selected)
        bind_web_fire_cells(runtime, selected)
        bind_web_flame_sections(runtime, selected)


def bind_worker_attack_sight(runtime, selected):
    """Apply environmental sight after Main's final independent Orb producer."""
    return bind_environment_spell_attack_sight(runtime, selected)
