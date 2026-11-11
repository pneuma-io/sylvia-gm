from __future__ import annotations

from typing import Any


# Executable test fixtures derived from the canonical Specialties/*.md notes.
# These rows are not rules authority and deliberately contain no magnitudes.
# Every fragment is validated against the hash-bound canonical node excerpt at
# build time. Missing rows fail closed instead of falling back to prose inference.


def prerequisite(
    field: str,
    source_fragment: str,
    *,
    expected: bool | str | int | float = True,
) -> dict[str, Any]:
    return {
        "field": field,
        "expected": expected,
        "source_fragment": source_fragment,
    }


def constraint(
    field: str,
    source_fragment: str,
    *,
    expected: bool | str | int | float = True,
    enforcement: str = "UNCLASSIFIED",
) -> dict[str, Any]:
    if enforcement not in {"UNCLASSIFIED", "GUARD", "TERMINATION", "INVARIANT"}:
        raise ValueError(f"unknown specialty boundary enforcement {enforcement}")
    return {
        "field": field,
        "expected": expected,
        "source_fragment": source_fragment,
        "enforcement": enforcement,
    }


def effect(
    field: str,
    source_fragment: str,
    *,
    value: bool | str | int | float = True,
    control: bool | str | int | float | None = None,
) -> dict[str, Any]:
    if control is None:
        if not isinstance(value, bool):
            raise ValueError(
                f"non-boolean specialty effect {field} requires an explicit control value"
            )
        control = not value
    return {
        "field": field,
        "value": value,
        "control": control,
        "source_fragment": source_fragment,
    }


def contract(
    *,
    prerequisites: tuple[dict[str, Any], ...] = (),
    constraints: tuple[dict[str, Any], ...] = (),
    effects: tuple[dict[str, Any], ...] = (),
) -> dict[str, tuple[dict[str, Any], ...]]:
    return {
        "prerequisites": prerequisites,
        "constraints": constraints,
        "effects": effects,
    }


SPECIALTY_RULE_CONTRACTS: dict[
    tuple[str, str], dict[str, tuple[dict[str, Any], ...]]
] = {
    ("ANCHOR", "ESTABLISH STRONGPOINT"): contract(
        prerequisites=(
            prerequisite("suitable_position", "At a suitable position"),
            prerequisite("required_setup_time_ap_paid", "spend the required time/AP"),
        ),
        constraints=(
            constraint("creates_cover", "rather than generating cover of its own", expected=False, enforcement="INVARIANT"),
            constraint("left_abandoned_or_lost_position", "Leaving, abandoning, or losing the physical position can end it.", expected=False, enforcement="TERMINATION"),
        ),
    ),
    ("ANCHOR", "SETTLE IN"): contract(
        prerequisites=(
            prerequisite("establishing_strongpoint", "As part of ESTABLISH STRONGPOINT"),
            prerequisite("stance_and_facing_physically_valid", "one physically valid STANCE and defensive facing appropriate to the cover you occupy"),
        ),
        constraints=(
            constraint("terrain_supports_stance_and_geometry", "This cannot place you into a stance or protected geometry the terrain does not support.", enforcement="GUARD"),
        ),
    ),
    ("ANCHOR", "BRACED POSITION"): contract(
        prerequisites=(
            prerequisite("inside_strongpoint", "While inside the STRONGPOINT"),
        ),
        constraints=(
            constraint("forced_movement_overcame_brace", "must overcome your established brace", expected=False, enforcement="TERMINATION"),
            constraint("displaced_beyond_position", "If you are displaced beyond the position, the protection is lost.", expected=False, enforcement="TERMINATION"),
        ),
    ),
    ("ANCHOR", "FIRING EDGE"): contract(
        prerequisites=(
            prerequisite("establishing_strongpoint", "When establishing the STRONGPOINT"),
            prerequisite("actual_edge_opening_or_firing_position_designated", "designate one actual edge, opening, or firing position"),
            prerequisite("declared_firing_solution", "for one declared firing solution through it"),
        ),
        constraints=(
            constraint("edge_and_solution_maintained", "The AIM persists only while you maintain that edge and solution", enforcement="TERMINATION"),
            constraint("position_edge_or_target_area_changed", "changing position, edge, or target area loses it.", expected=False, enforcement="TERMINATION"),
        ),
    ),
    ("ANCHOR", "RALLY POINT"): contract(
        prerequisites=(
            prerequisite("ally_physically_reached_position", "An ally who physically reaches one"),
        ),
        constraints=(
            constraint("real_cover_reached_through_normal_movement", "The ally still has to reach real cover through normal movement.", enforcement="GUARD"),
        ),
        effects=(
            effect("protected_position_within_strongpoint", "protected positions within the STRONGPOINT", value=True, control=False),
        ),
    ),
    ("ANCHOR", "HOLD UNDER FIRE"): contract(
        prerequisites=(
            prerequisite("inside_strongpoint", "inside the STRONGPOINT"),
            prerequisite("full_protection_temporarily_removed_los", "taking full protection inside the STRONGPOINT temporarily removes LOS"),
        ),
        constraints=(
            constraint("trigger_attempt_without_los", "It cannot trigger without LOS", expected=False, enforcement="GUARD"),
            constraint("same_position_and_firing_edge_resumed_before_next_team_turn", "must resume from the same position and firing edge before your next TEAM TURN.", enforcement="TERMINATION"),
        ),
    ),
    ("ANCHOR", "CONTROLLED APPROACH"): contract(
        prerequisites=(
            prerequisite("establishing_strongpoint", "When establishing the STRONGPOINT"),
            prerequisite("approach_physically_meaningful", "one physically meaningful approach such as a doorway, corridor, stairwell, or gap between cover"),
        ),
        constraints=(constraint("unseen_or_unthreatened_paths_affected", "unseen or unthreatened paths are unaffected.", expected=False, enforcement="INVARIANT"),),
    ),
    ("ANCHOR", "FALLBACK POSITION"): contract(
        prerequisites=(
            prerequisite("primary_position_untenable", "If the primary position becomes untenable"),
            prerequisite("fallback_physically_reached", "physically reaching the fallback"),
        ),
        effects=(
            effect("nearby_suitable_fallback_designated_during_establishment", "Designate nearby suitable secondary cover when establishing the STRONGPOINT.", value=True, control=False),
        ),
    ),
    ("ANCHOR", "ROTATE POSITIONS"): contract(
        prerequisites=(
            prerequisite("allies_in_connected_cover_within_strongpoint", "Allies occupying connected cover within the STRONGPOINT"),
            prerequisite("own_movement_ap_paid", "using their own movement/AP"),
        ),
        constraints=(constraint("move_physically_possible", "Every move must remain physically possible.", enforcement="GUARD"),),
    ),
    ("ANCHOR", "PREPARED GARRISON"): contract(
        prerequisites=(
            prerequisite("allies_rotating_between_valid_strongpoint_positions", "When allies rotate between valid positions inside the STRONGPOINT"),
            prerequisite("existing_defensive_prep_action", "an existing defensive PREP ACTION"),
        ),
        constraints=(constraint("original_zone_and_physical_prerequisites_valid", "The original zone and all physical prerequisites must remain valid.", enforcement="GUARD"),),
    ),
    ("ANCHOR", "COUNTER-BREACH"): contract(
        prerequisites=(prerequisite("enemy_physically_crossing_into_strongpoint", "an enemy physically entering the STRONGPOINT"),),
        constraints=(
            constraint("trigger_is_crossing_defended_space", "The trigger is crossing into defended space", enforcement="GUARD"),
            constraint("abstract_threat_aura_used", "not an abstract threat aura.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("ANCHOR", "SECOND LINE"): contract(
        effects=(
            effect("primary_and_designated_fallback_maintained", "Maintain both the primary position and the designated FALLBACK POSITION", value=True, control=False),
            effect("loss_of_one_erases_other", "Losing one does not automatically erase the other.", value=False, control=True),
        ),
    ),
    ("ANCHOR", "REFUSE COLLAPSE"): contract(
        prerequisites=(
            prerequisite("one_cover_source_destroyed", "Destruction of one COVER source inside the STRONGPOINT"),
            prerequisite("another_physically_viable_defensive_position_occupied", "if you still occupy another physically viable defensive position within it"),
        ),
        constraints=(
            constraint("battlefield_geometry_updated_immediately", "The battlefield geometry changes immediately", enforcement="INVARIANT"),
        ),
        effects=(
            effect("strongpoint_organizational_state_survives", "even though the organizational state survives.", value=True, control=False),
        ),
    ),
    ("ANCHOR", "FORTRESS"): contract(
        prerequisites=(
            prerequisite("establishing_strongpoint", "When establishing it"),
        ),
        constraints=(
            constraint("all_components_match_real_geometry", "all of which must correspond to real battlefield geometry.", enforcement="GUARD"),
            constraint("bypassed_destroyed_abandoned_or_taken", "it ends when the position is genuinely bypassed, destroyed, abandoned, or taken.", expected=False, enforcement="TERMINATION"),
        ),
        effects=(
            effect("primary_fallback_approach_and_ally_positions_organized", "organize a primary position, a fallback, a controlled approach, and protected positions for allies", value=True, control=False),
            effect("ordinary_internal_move_or_single_cover_loss_erases_strongpoint", "Ordinary internal movement or loss of one cover element does not by itself erase the STRONGPOINT", value=False, control=True),
        ),
    ),
    ("ARSENAL", "FIGHTING LOAD"): contract(
        prerequisites=(prerequisite("additional_firearm_actually_carried", "the weapon must actually be carried"),),
        constraints=(constraint("valid_physical_carriage_and_burden_honored", "Slings, holsters, retention systems, physical placement, and burden still matter", enforcement="INVARIANT"),),
    ),
    ("ARSENAL", "FAST TRANSITION"): contract(
        prerequisites=(prerequisite("transition_between_fighting_load_firearms", "Transitioning between firearms in your FIGHTING LOAD"),),
    ),
    ("ARSENAL", "SIDEARM FIRST"): contract(
        prerequisites=(
            prerequisite("long_gun_released_to_sling", "Releasing a long gun onto its sling"),
            prerequisite("accessible_sidearm_drawn", "drawing an accessible sidearm"),
        ),
        constraints=(constraint("long_gun_remains_on_sling", "The long gun remains physically hanging from the sling.", enforcement="INVARIANT"),),
    ),
    ("ARSENAL", "PICKUP DRILL"): contract(
        prerequisites=(
            prerequisite("picked_up_conventional_firearm", "When you pick up a conventional firearm"),
            prerequisite("basic_mechanism_understood", "whose basic mechanism you understand"),
        ),
        constraints=(
            constraint("hidden_modifications_ammo_quality_internal_damage_unknown", "Hidden modifications, ammunition quality, and internal damage remain unknown unless discovered normally.", enforcement="INVARIANT"),
        ),
        effects=(
            effect("knowledge_limited_to_immediately_visible_usable_state", "its immediately visible/usable state", value=True, control=False),
        ),
    ),
    ("ARSENAL", "SECOND PRIMARY"): contract(
        constraints=(
            constraint("increased_burden_applied", "The increased burden is real", enforcement="INVARIANT"),
            constraint("mobility_reduction_rules_honored", "can still reduce mobility.", enforcement="INVARIANT"),
        ),
    ),
    ("ARSENAL", "MATCHED AMMO"): contract(
        prerequisites=(prerequisite("firearm_in_fighting_load", "Each firearm in your FIGHTING LOAD"),),
        constraints=(constraint("all_magazines_or_rounds_physically_carried", "You still carry every magazine or round physically.", enforcement="INVARIANT"),),
    ),
    ("ARSENAL", "HANDOFF"): contract(
        prerequisites=(prerequisite("accessible_firearm_exchanged_with_adjacent_ally", "Giving an accessible firearm to an adjacent ally or receiving one from them"),),
        constraints=(constraint("exact_weapon_physical_state_retained", "The weapon retains its exact magazine, chamber, ammunition, and other physical state.", enforcement="INVARIANT"),),
    ),
    ("ARSENAL", "BACKUP PLAN"): contract(
        prerequisites=(
            prerequisite("held_firearm_empty_damaged_inappropriate_or_unusable", "When the firearm in your hands becomes empty, damaged, inappropriate to the current engagement, or otherwise unusable"),
            prerequisite("another_fighting_load_firearm_available", "transition to another firearm in your FIGHTING LOAD"),
        ),
        constraints=(constraint("ability_decides_tactical_wisdom", "The ability does not decide whether switching is tactically wise.", expected=False, enforcement="INVARIANT"),),
    ),
    ("ARSENAL", "STAGED CACHE"): contract(
        prerequisites=(
            prerequisite("preparation_completed", "With preparation"),
            prerequisite("actor_remains_at_specific_cache_position", "while you remain there"),
        ),
        constraints=(
            constraint("cached_items_are_real_battlefield_objects", "They are real battlefield objects", enforcement="INVARIANT"),
            constraint("object_mutation_loss_rules_honored", "can be moved, stolen, damaged, or left behind.", enforcement="INVARIANT"),
        ),
    ),
    ("ARSENAL", "FAMILIAR CONTROLS"): contract(
        prerequisites=(
            prerequisite("brief_physical_inspection_completed", "After a brief physical inspection"),
            prerequisite("firearm_unfamiliar_but_conventional", "of an unfamiliar but conventional firearm"),
        ),
        constraints=(constraint("truly_exotic_or_unfamiliar_technology", "Truly exotic or unfamiliar technology can remain outside this ability.", expected=False, enforcement="GUARD"),),
    ),
    ("ARSENAL", "RAPID CHANGEOVER"): contract(
        prerequisites=(
            prerequisite("transitioned_to_another_fighting_load_firearm", "After transitioning to another firearm in your FIGHTING LOAD"),
        ),
        constraints=(
            constraint("required_real_manipulation_still_occurs_if_needed", "If it needs cycling, loading, or another real step, that step still occurs", enforcement="INVARIANT"),
            constraint("fictional_preparation_added_if_already_ready", "if it is already capable of firing, no fictional preparation is added.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("ARSENAL", "ROLE PACKAGE"): contract(
        prerequisites=(
            prerequisite("arranging_fighting_load_before_operation", "When arranging a FIGHTING LOAD before an operation"),
            prerequisite("firearms_have_distinct_tactical_jobs", "assign genuinely different firearms to distinct tactical jobs"),
        ),
        constraints=(
            constraint("effective_burden_below_zero", "BURDEN cannot reduce that firearm below effective BURDEN 0", expected=False, enforcement="INVARIANT"),
            constraint("physical_size_carry_interference_or_equipment_removed", "never removes its physical size, carry position, interference, or equipment requirement", expected=False, enforcement="INVARIANT"),
            constraint("redundant_copies_qualify_by_label", "Redundant copies do not qualify merely because they are given different labels.", expected=False, enforcement="GUARD"),
        ),
    ),
    ("ARSENAL", "ARMORY POSITION"): contract(
        prerequisites=(prerequisite("staged_cache_exists", "A STAGED CACHE"),),
    ),
    ("ARSENAL", "WALKING ARMORY"): contract(
        prerequisites=(prerequisite("firearm_physically_configured_in_valid_combat_carry", "Every firearm physically configured into your FIGHTING LOAD through a valid sling, holster, retention system, or equivalent combat carry position"),),
        constraints=(
            constraint("firearms_owned_and_carried", "You still must own and carry the firearms", enforcement="GUARD"),
            constraint("ammo_mounting_interference_dropped_weapon_and_burden_limits_honored", "ammunition, mounting space, long-gun interference, dropped weapons, and excessive burden remain real limits.", enforcement="INVARIANT"),
        ),
    ),
    ("AUTODRONES", "TACTICAL AUTONOMY"): contract(
        prerequisites=(
            prerequisite("assigning_standing_order", "When assigning a STANDING ORDER to an autonomous drone"),
        ),
        constraints=(
            constraint("drone_improvises_outside_order", "The drone cannot improvise outside the order", expected=False, enforcement="INVARIANT"),
            constraint("sensor_mobility_weapon_identification_limits_honored", "or exceed its sensors, mobility, weapons, or identification capability.", enforcement="INVARIANT"),
        ),
    ),
    ("AUTODRONES", "CONDITIONAL ORDER"): contract(
        prerequisites=(prerequisite("standing_order_being_configured", "A STANDING ORDER"),),
        constraints=(constraint("condition_sensor_determinable", "Conditions must be things the drone can actually sense or determine.", enforcement="GUARD"),),
    ),
    ("AUTODRONES", "AUTONOMOUS REPOSITION"): contract(
        prerequisites=(
            prerequisite("valid_standing_order_executing", "A drone executing a valid STANDING ORDER"),
            prerequisite("relocation_required_by_order", "positions required by that order"),
        ),
        constraints=(constraint("drone_uses_own_physical_movement", "The drone still uses its own physical movement.", enforcement="INVARIANT"),),
    ),
    ("AUTODRONES", "HOLD POSITION"): contract(
        prerequisites=(
            prerequisite("autonomous_drone_stationary", "A stationary autonomous drone"),
            prerequisite("prepared_response_matches_equipment", "appropriate to its equipment"),
        ),
        constraints=(constraint("trigger_part_of_standing_order", "The trigger must be part of its STANDING ORDER.", enforcement="GUARD"),),
    ),
    ("AUTODRONES", "SECOND ELEMENT"): contract(
        prerequisites=(prerequisite("additional_autonomous_drones_have_standing_orders", "additional autonomous drones"),),
        constraints=(constraint("each_drone_separate_physical_unit", "Each drone remains a separate physical unit.", enforcement="INVARIANT"),),
    ),
    ("AUTODRONES", "HANDOFF ORDER"): contract(
        prerequisites=(prerequisite("changing_existing_order_to_prepared_order", "Changing one autonomous drone from an existing STANDING ORDER to another prepared order"),),
        constraints=(constraint("communication_available", "Communication with the drone is still required.", enforcement="GUARD"),),
    ),
    ("AUTODRONES", "SECTOR NETWORK"): contract(
        prerequisites=(prerequisite("multiple_drones_observe_adjacent_or_overlapping_areas", "Multiple drones assigned to observe adjacent or overlapping areas"),),
        constraints=(constraint("signal_connectivity_available", "Signal connectivity is required.", enforcement="GUARD"),),
    ),
    ("AUTODRONES", "ESCORT ROUTINE"): contract(
        prerequisites=(
            prerequisite("drone_assigned_to_moving_ally", "An autonomous drone assigned to a moving ally"),
            prerequisite("declared_relative_role", "a declared relative role"),
        ),
        constraints=(constraint("drone_can_physically_keep_up_and_navigate", "It must physically keep up and navigate the terrain.", enforcement="GUARD"),),
    ),
    ("AUTODRONES", "REACQUIRE"): contract(
        prerequisites=(
            prerequisite("tracked_target_briefly_broke_los", "If a target being tracked briefly breaks LOS"),
            prerequisite("search_within_existing_order", "under its existing order"),
        ),
        constraints=(
            constraint("search_area_limited_and_nearby", "a limited nearby area", enforcement="GUARD"),
            constraint("drone_knows_target_destination", "It does not know where the target went.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("AUTODRONES", "LOCAL RESPONSE"): contract(
        prerequisites=(prerequisite("additional_battlefield_condition_predefined", "one additional predefined battlefield condition"),),
        constraints=(constraint("response_configured_in_advance", "The response must be configured in advance.", enforcement="GUARD"),),
    ),
    ("AUTODRONES", "COORDINATED COVERAGE"): contract(
        prerequisites=(prerequisite("autonomous_drones_share_coordinated_observation_task", "as one coordinated observation task"),),
        constraints=(constraint("walls_and_sensor_limits_create_blind_spots", "Walls and sensor limits still create real blind spots.", enforcement="INVARIANT"),),
    ),
    ("AUTODRONES", "DISTRIBUTED FIRE"): contract(
        prerequisites=(prerequisite("multiple_weapon_drones_have_valid_solutions_same_target", "If multiple weapon-capable autonomous drones have valid firing solutions against the same target"),),
        constraints=(
            constraint("each_drone_expends_own_ammunition", "Each drone still expends ammunition", enforcement="INVARIANT"),
            constraint("each_drone_uses_own_physical_line", "uses its own physical line.", enforcement="INVARIANT"),
        ),
    ),
    ("AUTODRONES", "AUTONOMOUS RECOVERY"): contract(
        prerequisites=(
            prerequisite(
                "current_order_impossible",
                "When a current order becomes impossible because of blocked route, lost target, depleted ammunition, or similar foreseeable failure",
            ),
            prerequisite(
                "predefined_secondary_order_available",
                "a predefined secondary order",
            ),
        ),
        constraints=(
            constraint(
                "baseline_stop_safety_preserved_when_no_secondary",
                "Without one, baseline stop/safety behavior applies.",
                enforcement="INVARIANT",
            ),
        ),
    ),
    ("AUTODRONES", "MACHINE SQUAD"): contract(
        constraints=(
            constraint("each_drone_has_standing_order_and_prepared_contingency", "Each drone has its own STANDING ORDER, one or more prepared contingencies", enforcement="GUARD"),
            constraint("predefined_logic_sensor_ammo_route_comms_autonomy_limits_honored", "They remain constrained by predefined logic, finite sensors/ammunition, physical routes, communications, and their actual autonomy.", enforcement="INVARIANT"),
        ),
        effects=(
            effect("coordinated_autonomous_drone_group_under_shared_plan", "a coordinated group of autonomous drones under a shared operational plan", value=True, control=False),
        ),
    ),
    ("BREAKER", "TACTICAL BREACHING"): contract(
        prerequisites=(
            prerequisite("appropriate_breaching_method_used", "Using an appropriate tool, weapon, charge, or physical method"),
            prerequisite("action_specifically_defeats_barrier_or_access_point", "specifically to defeat a barrier or access point"),
        ),
        constraints=(constraint("method_physically_capable_against_material", "The method must still be physically capable of defeating the material.", enforcement="GUARD"),),
    ),
    ("BREAKER", "READ THE BARRIER"): contract(
        prerequisites=(prerequisite("brief_inspection_completed", "After a brief inspection"),),
        constraints=(
            constraint("identification_limited_to_observable_points", "the most vulnerable observable breach points", enforcement="INVARIANT"),
            constraint("hidden_traps_reinforcement_internal_construction_unknown", "Hidden traps, concealed reinforcement, and inaccessible internal construction remain unknown.", enforcement="INVARIANT"),
        ),
    ),
    ("BREAKER", "BREACH READY"): contract(
        constraints=(constraint("bulk_and_burden_unchanged", "Bulk and burden remain unchanged.", enforcement="INVARIANT"),),
        effects=(
            effect("dedicated_breaching_tool_configured_by_specialized_carriage", "One dedicated breaching tool that would normally be inconveniently STOWED may be configured as QUICK ACCESS through specialized carriage.", value=True, control=False),
        ),
    ),
    ("BREAKER", "FOCUSED BREACH"): contract(
        prerequisites=(prerequisite("deliberately_attacking_specific_structural_component", "When deliberately attacking a specific structural component such as a lock, hinge, frame, weak panel, or mounting point"),),
        constraints=(constraint("material_rules_honored", "Material still matters.", enforcement="INVARIANT"),),
    ),
    ("BREAKER", "BREACH AND FLOW"): contract(
        prerequisites=(
            prerequisite("usable_opening_successfully_created", "After successfully creating a usable opening"),
            prerequisite("first_movement_through_that_opening", "the BREAKER's first movement through that opening"),
        ),
        constraints=(constraint("overwatch_hazard_terrain_enemy_fire_rules_honored", "OVERWATCH, hazards, difficult terrain, and enemy fire still work normally.", enforcement="INVARIANT"),),
    ),
    ("BREAKER", "ALTERNATE METHOD"): contract(
        prerequisites=(
            prerequisite("prior_breach_failed_or_insufficient", "If a breach attempt fails or proves insufficient"),
            prerequisite("different_method_accessible", "a different accessible breaching method"),
        ),
        constraints=(constraint("second_method_actually_available", "The second method must actually be available.", enforcement="GUARD"),),
    ),
    ("BREAKER", "CONTROLLED BREACH"): contract(
        prerequisites=(prerequisite("method_physically_controllable", "When the method can physically be controlled"),),
        constraints=(
            constraint("method_supports_selected_result", "The method must support the result", enforcement="GUARD"),
            constraint("both_benefits_granted", "the choice does not grant both benefits.", expected=False, enforcement="GUARD"),
        ),
    ),
    ("BREAKER", "FIRING PORT"): contract(
        prerequisites=(
            prerequisite("firing_aperture_selected_instead_of_passage", "Instead of a passage"),
            prerequisite("controlled_breach_method", "perform a controlled breach"),
        ),
        constraints=(constraint("remaining_geometry_and_material_determine_cover", "Remaining barrier geometry and material still determine COVER.", enforcement="INVARIANT"),),
    ),
    ("BREAKER", "RAPID SECOND BREACH"): contract(
        prerequisites=(
            prerequisite("structure_successfully_breached", "After successfully breaching a structure"),
            prerequisite("next_breach_same_continuous_barrier", "the next breach against the same continuous barrier"),
        ),
    ),
    ("BREAKER", "CLEAR PASSAGE"): contract(
        prerequisites=(prerequisite("breach_created_minor_movement_impeding_debris", "If the breach creates minor rubble, broken framing, glass, or similar movement-impeding debris"),),
        constraints=(constraint("major_collapse_cleared_without_work", "Major collapse still needs actual clearing work.", expected=False, enforcement="INVARIANT"),),
    ),
    ("BREAKER", "HARD TARGET"): contract(
        prerequisites=(
            prerequisite("breaching_method_appropriate", "Appropriate breaching methods"),
            prerequisite("barrier_high_resistance", "against high-resistance barriers"),
        ),
        constraints=(constraint("incapable_tool_made_capable", "This never makes an incapable tool capable.", expected=False, enforcement="INVARIANT"),),
    ),
    ("BREAKER", "WIDE BREACH"): contract(
        prerequisites=(
            prerequisite("breach_successful", "A successful breach"),
            prerequisite("adjacent_section_same_destructible_barrier", "an adjacent section of the same destructible barrier"),
        ),
        constraints=(constraint("increased_resource_tool_cost_paid", "at increased resource/tool cost", enforcement="GUARD"),),
    ),
    ("BREAKER", "WORK UNDER FIRE"): contract(
        prerequisites=(prerequisite("performing_breaching_action_under_suppression", "while performing a breaching action"),),
        constraints=(constraint("actual_hit_forced_move_incapacity_tool_loss_or_point_destruction_present", "Actual hits, forced movement, incapacitation, lost tools, or destruction of the breach point can still stop the work.", expected=False, enforcement="TERMINATION"),),
    ),
    ("BREAKER", "NO FIXED ENTRY"): contract(
        prerequisites=(
            prerequisite("barrier_assessed", "Once the BREAKER has assessed a barrier"),
            prerequisite("method_physically_capable", "has a method physically capable of defeating it"),
        ),
        constraints=(
            constraint("chosen_shape_supported_by_method", "where the method supports it", enforcement="GUARD"),
            constraint("material_impossible_for_method", "Impossible materials remain impossible.", expected=False, enforcement="GUARD"),
        ),
    ),
    ("DIPLOMAT", "NEGOTIATING POSITION"): contract(
        prerequisites=(
            prerequisite("plausible_concrete_offer_rejected", "When a plausible concrete offer is rejected"),
            prerequisite("counteroffer_materially_changed", "one materially changed counteroffer"),
        ),
        constraints=(
            constraint("counterpart_has_no_acceptable_deal", "someone who has no acceptable deal", expected=False, enforcement="GUARD"),
            constraint("counterpart_lacks_authority", "lacks authority", expected=False, enforcement="GUARD"),
            constraint("counterpart_already_committed_to_violence", "is already committed to violence", expected=False, enforcement="GUARD"),
            constraint("other_real_reason_to_leave", "or has another real reason to leave.", expected=False, enforcement="GUARD"),
        ),
    ),
    ("DIPLOMAT", "BOTTOM LINE"): contract(
        prerequisites=(prerequisite("counterpart_successfully_read", "After successfully reading a negotiating counterpart"),),
        constraints=(
            constraint("information_limited_to_observable_demand_or_priority", "which observable demand or priority", enforcement="INVARIANT"),
            constraint("undetectable_motives_revealed", "This does not reveal motives you had no way to detect.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("DIPLOMAT", "COUNTEROFFER"): contract(
        prerequisites=(
            prerequisite("offer_rejected", "After an offer is rejected"),
            prerequisite("new_offer_materially_changed", "presenting a materially changed offer"),
        ),
        constraints=(constraint("exact_same_demand_repeated", "Repeating the exact same demand louder does not qualify.", expected=False, enforcement="GUARD"),),
    ),
    ("DIPLOMAT", "FACE-SAVING EXIT"): contract(
        prerequisites=(
            prerequisite("proposal_requires_other_side_to_visibly_back_down", "When proposing withdrawal, surrender, apology, compromise, or another outcome that requires the other side to visibly back down"),
            prerequisite("terms_preserve_dignity_status_safety_or_reputation", "terms that preserve their dignity, status, safety, or reputation"),
        ),
        constraints=(constraint("plausible_reason_to_accept_exists", "They still need a plausible reason to accept.", enforcement="GUARD"),),
    ),
    ("DIPLOMAT", "CEASEFIRE"): contract(
        prerequisites=(prerequisite("ceasefire_negotiation_succeeded", "On success"),),
        constraints=(
            constraint("betrayal_magically_prevented", "This does not magically prevent betrayal", expected=False, enforcement="INVARIANT"),
        ),
        effects=(
            effect("covered_parties_prohibited_actions_and_end_conditions_recorded", "establish who is covered, what actions are prohibited, and what ends the ceasefire.", value=True, control=False),
        ),
    ),
    ("DIPLOMAT", "TERMS ON PAPER"): contract(
        prerequisites=(prerequisite("agreement_successfully_secured", "When you successfully secure an agreement"),),
        constraints=(constraint("record_or_evidence_actually_exists", "Written contracts, recordings, witnesses, or other evidence must actually exist.", enforcement="GUARD"),),
    ),
    ("DIPLOMAT", "MEDIATOR"): contract(
        prerequisites=(
            prerequisite("negotiating_between_two_other_parties", "When negotiating between two other parties with conflicting interests"),
            prerequisite("parties_have_conflicting_interests", "with conflicting interests"),
        ),
        constraints=(constraint("two_independent_decision_makers_respected", "You are still dealing with two independent decision-makers.", enforcement="INVARIANT"),),
    ),
    ("DIPLOMAT", "SURRENDER TERMS"): contract(
        prerequisites=(prerequisite("surrender_or_withdrawal_successfully_negotiated", "When successfully negotiating surrender or withdrawal"),),
        constraints=(constraint("additional_condition_plausibly_acceptable", "The extra condition still has to be something the other side could plausibly accept.", enforcement="GUARD"),),
    ),
    ("DIPLOMAT", "TRADE THE ISSUE"): contract(
        prerequisites=(
            prerequisite("several_issues_negotiated_together", "When several issues are being negotiated together"),
            prerequisite("concession_on_one_used_for_another", "a concession on one issue"),
        ),
        constraints=(constraint("concession_has_actual_value_to_counterpart", "The concession must have actual value to the counterpart.", enforcement="GUARD"),),
    ),
    ("DIPLOMAT", "PIVOT"): contract(
        prerequisites=(
            prerequisite("counterpart_clearly_rejected_one_leverage_line", "When a counterpart clearly rejects one line of LEVERAGE"),
            prerequisite("different_known_social_fact_or_interest_available", "a different known SOCIAL FACT or interest"),
        ),
        constraints=(constraint("real_second_angle_exists", "You need a real second angle.", enforcement="GUARD"),),
    ),
    ("DIPLOMAT", "MULTI-PARTY ACCORD"): contract(
        prerequisites=(prerequisite("several_parties_share_same_negotiation", "When several parties genuinely share the same negotiation"),),
        constraints=(constraint("individual_party_rejection_remains_possible", "Individual parties can still reject terms that specifically disadvantage them.", enforcement="INVARIANT"),),
    ),
    ("DIPLOMAT", "LAST OFF-RAMP"): contract(
        prerequisites=(
            prerequisite("negotiation_about_to_collapse_into_departure_violence_or_escalation", "When negotiations are about to collapse into departure, violence, or another immediate escalation"),
            prerequisite("compromise_materially_different", "a materially different compromise"),
        ),
        constraints=(
            constraint("offer_addresses_real_failure_reason", "The offer must address a real reason the talks failed", enforcement="GUARD"),
            constraint("same_demand_free_reroll", "this is not a free reroll of the same demand.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("DIPLOMAT", "GUARANTEED TERMS"): contract(
        prerequisites=(
            prerequisite(
                "credible_collateral_attached",
                "When you attach credible collateral to an agreement—money, hostages, reputation, escrow, third-party guarantee, verifiable action, or similar",
            ),
        ),
        constraints=(
            constraint(
                "guarantee_real_and_capable_of_loss",
                "The guarantee must be real and capable of being lost.",
                enforcement="GUARD",
            ),
        ),
    ),
    ("DIPLOMAT", "GRAND BARGAIN"): contract(
        prerequisites=(prerequisite("single_negotiation_has_multiple_linked_interests_and_concessions", "a single negotiation around multiple linked interests and concessions"),),
        constraints=(
            constraint("first_term_bypasses_normal_resolution", "Resolve the first term normally", expected=False, enforcement="INVARIANT"),
            constraint("affected_party_acceptance_overridden", "Every affected party still accepts or rejects the terms that apply to them", expected=False, enforcement="INVARIANT"),
            constraint("agreement_forced", "nothing forces agreement", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("ENFORCER", "WEAPON RETENTION"): contract(
        prerequisites=(prerequisite("incoming_attempt_targets_held_firearm_by_disarm_control_redirect_or_take", "attempts to DISARM, CONTROL, redirect, or otherwise physically take a firearm being held out of the fight"),),
        constraints=(constraint("maneuver_can_succeed_if_it_beats_retention", "Those maneuvers can still succeed normally if they beat the ENFORCER's retention.", enforcement="INVARIANT"),),
    ),
    ("ENFORCER", "WEAPON FRAME"): contract(
        prerequisites=(
            prerequisite("holding_suitable_long_gun", "While holding a suitable long gun"),
            prerequisite("weapon_usable_as_leverage", "using it as leverage"),
        ),
        constraints=(constraint("weapon_lost_trapped_or_unusable", "If the weapon is lost, trapped, or unusable as leverage, the benefit disappears.", expected=False, enforcement="TERMINATION"),),
    ),
    ("ENFORCER", "MUZZLE CONTROL"): contract(
        prerequisites=(
            prerequisite("successfully_shoved_or_controlled_adjacent_enemy", "After successfully SHOVING or CONTROLLING an adjacent enemy"),
            prerequisite("next_firearm_attack_same_enemy", "the next firearm attack against that same enemy"),
        ),
        constraints=(constraint("firearm_valid_firing_line_and_usable", "The firearm must still have a valid firing line and usable state.", enforcement="GUARD"),),
    ),
    ("ENFORCER", "RETENTION COUNTER"): contract(
        prerequisites=(
            prerequisite("enemy_failed_disarm_or_control_against_held_firearm", "When an enemy fails a DISARM or CONTROL attempt directed at the ENFORCER's held firearm"),
            prerequisite("next_cqc_targets_same_enemy", "against that enemy"),
        ),
    ),
    ("ENFORCER", "PEEL OFF"): contract(
        prerequisites=(
            prerequisite("adjacent_ally_physically_controlled", "When an adjacent ally is being physically CONTROLLED"),
            prerequisite("cqc_attempt_specifically_breaks_that_control", "a CQC attempt specifically to break that control"),
        ),
        constraints=(constraint("break_control_attempt_succeeds", "The attempt must still succeed.", enforcement="INVARIANT"),),
    ),
    ("ENFORCER", "DISARM AND TAKE"): contract(
        prerequisites=(
            prerequisite("enemy_successfully_disarmed", "After successfully DISARMING an enemy"),
            prerequisite("released_weapon_physically_reachable", "if it is physically reachable"),
            prerequisite("hand_available", "a hand is available"),
        ),
        constraints=(constraint("exact_ammunition_and_chamber_state_retained", "The weapon keeps its exact ammunition and chamber state.", enforcement="INVARIANT"),),
    ),
    ("ENFORCER", "HEAVY CONTACT"): contract(
        prerequisites=(prerequisite("shove_or_throw_successful", "Successful SHOVE or THROW maneuvers"),),
        constraints=(constraint("maneuver_becomes_high_pen_melee_attack", "The maneuver does not become a high-PEN melee attack.", expected=False, enforcement="INVARIANT"),),
    ),
    ("ENFORCER", "CHAIN MANEUVER"): contract(
        prerequisites=(
            prerequisite("one_cqc_maneuver_successfully_resolved", "After successfully resolving one CQC maneuver"),
            prerequisite("next_maneuver_different_and_same_target", "the next different CQC maneuver against the same target"),
        ),
        constraints=(constraint("each_maneuver_resolves_independently", "Each maneuver still resolves independently.", enforcement="INVARIANT"),),
    ),
    ("ENFORCER", "GUN-HAND TRANSITION"): contract(
        constraints=(constraint("actual_attack_and_maneuver_resolve_normally", "The actual attack and maneuver still occur normally.", enforcement="INVARIANT"),),
        effects=(
            effect("transition_is_cqc_to_firearm_or_firearm_to_cqc_against_adjacent_target", "After successfully resolving a CQC maneuver against an adjacent target, a firearm attack against that target costs less AP to bring into the sequence, and vice versa.", value=True, control=False),
        ),
    ),
    ("ENFORCER", "FOLLOW THE FALL"): contract(
        prerequisites=(
            prerequisite("throw_or_trip_put_target_prone", "When THROW or TRIP puts an enemy PRONE"),
            prerequisite("next_attempt_is_control_or_disarm_same_target", "the next CONTROL or DISARM attempt against that target"),
        ),
        constraints=(constraint("followup_maneuver_made", "The follow-up maneuver must still be made.", enforcement="INVARIANT"),),
    ),
    ("ENFORCER", "HOLD THE DOOR"): contract(
        prerequisites=(prerequisite("enemy_attempts_to_pass_immediate_contact_space", "an enemy attempting to physically pass through the ENFORCER's immediate contact space"),),
        constraints=(
            constraint("universal_opportunity_attack_rule_created", "This is not a universal opportunity-attack rule.", expected=False, enforcement="INVARIANT"),
        ),
        effects=(
            effect("triggered_response_is_normal_shove_or_control", "When triggered, make a normal SHOVE or CONTROL attempt.", value=True, control=False),
        ),
    ),
    ("ENFORCER", "COUNTER-GRAPPLE"): contract(
        prerequisites=(
            prerequisite("adjacent_enemy_failed_any_cqc_maneuver", "When an adjacent enemy fails any CQC maneuver against the ENFORCER"),
            prerequisite("next_cqc_targets_same_enemy", "against that enemy"),
        ),
    ),
    ("ENFORCER", "CONTROLLED SHOT"): contract(
        prerequisites=(prerequisite("called_shot_against_enemy_controlled_by_enforcer", "When making a CALLED SHOT against an enemy currently CONTROLLED by the ENFORCER"),),
        constraints=(constraint("selected_target_area_has_physically_valid_line", "A physically valid line to the selected target area is still required.", enforcement="GUARD"),),
    ),
    ("ENFORCER", "OWN THE CLINCH"): contract(
        prerequisites=(
            prerequisite("successful_close_action_same_adjacent_opponent", "Every successful close action against the same adjacent opponent"),
            prerequisite("next_close_action_different", "the next different close action"),
        ),
        constraints=(
            constraint("each_action_pays_ap_and_resolves_independently", "Each action still costs AP, resolves independently, can fail", enforcement="INVARIANT"),
            constraint("firearm_state_and_geometry_obeyed", "obeys firearm state and geometry", enforcement="INVARIANT"),
            constraint("target_remains_contact_range", "requires the target to remain at contact range.", enforcement="TERMINATION"),
            constraint("failure_or_successful_separation", "Failure or successful separation ends the chain.", expected=False, enforcement="TERMINATION"),
        ),
    ),
    ("EVADER", "BREAK CONTACT"): contract(
        prerequisites=(
            prerequisite("all_exact_position_observers_lost_los_due_movement_or_geometry", "After movement or geometry causes every enemy currently observing the character's exact position to lose LOS"),
            prerequisite("next_action_is_stealth_to_reenter_uncertainty", "the next STEALTH attempt to re-enter an uncertain/hidden information state"),
        ),
        constraints=(constraint("any_enemy_still_directly_sees_character", "If even one still directly sees the character, contact is not broken", expected=False, enforcement="GUARD"),),
    ),
    ("EVADER", "VANISHING MOVE"): contract(
        prerequisites=(
            prerequisite("immediately_after_breaking_los", "Immediately after breaking LOS"),
            prerequisite("first_move_toward_cover_or_concealment_supports_break_contact", "the first movement toward COVER or CONCEALMENT that supports BREAK CONTACT"),
        ),
    ),
    ("EVADER", "CUT THE CORNER"): contract(
        prerequisites=(prerequisite("solid_geometry_broke_los", "When solid geometry rather than temporary concealment breaks LOS"),),
        constraints=(constraint("visible_transition_left_to_follow", "The benefit comes from leaving no visible transition to follow.", expected=False, enforcement="GUARD"),),
    ),
    ("EVADER", "FALSE TRAIL"): contract(
        prerequisites=(prerequisite("break_contact_successful", "After a successful BREAK CONTACT"),),
        constraints=(constraint("plausible_alternative_paths_exist", "Plausible alternative paths must actually exist.", enforcement="GUARD"),),
    ),
    ("EVADER", "EXTENDED REPOSITION"): contract(
        prerequisites=(
            prerequisite("break_contact_successful", "After a successful BREAK CONTACT"),
            prerequisite("first_movement_remains_hidden", "the first movement while remaining hidden"),
        ),
        constraints=(constraint("moved_into_direct_observation", "Moving into direct observation still reveals the character normally.", expected=False, enforcement="TERMINATION"),),
    ),
    ("EVADER", "SLIP THE FIRE"): contract(
        prerequisites=(
            prerequisite("movement_triggered_suppression_attack", "When movement triggers a SUPPRESSION ATTACK"),
            prerequisite("movement_distance_sacrificed", "sacrifice part of the movement distance gained from that AP"),
        ),
        constraints=(constraint("remaining_exposed_or_other_exposed_action", "This does not help while remaining exposed or performing another exposed action.", expected=False, enforcement="GUARD"),),
    ),
    ("EVADER", "HARD TO REACQUIRE"): contract(
        prerequisites=(
            prerequisite("break_contact_successful", "after BREAK CONTACT"),
            prerequisite("enemy_attempt_specifically_locates_exact_current_position", "enemy attempts specifically to determine the exact current position"),
        ),
        constraints=(
            constraint("evader_attacked_or_directly_observed_again", "Until the EVADER attacks or is directly observed again", expected=False, enforcement="TERMINATION"),
            constraint("enemy_general_nearby_knowledge_erased", "Enemies may still know the EVADER is somewhere nearby.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("EVADER", "AMBUSH AGAIN"): contract(
        prerequisites=(
            prerequisite("first_attack_after_successful_break_contact", "The first attack after successfully using BREAK CONTACT"),
            prerequisite("new_hidden_position_reached", "reaching a new hidden position"),
        ),
        constraints=(
            constraint("automatic_critical", "It does not automatically CRITICAL", expected=False, enforcement="INVARIANT"),
            constraint("cover_ignored", "or ignore COVER.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("EVADER", "EVASIVE BREAK"): contract(
        prerequisites=(
            prerequisite("declared_enemy_enters_los_to_evader_position", "When a declared enemy enters LOS to the EVADER's position"),
        ),
        constraints=(constraint("attack_already_resolved_retroactively_interrupted", "It does not retroactively interrupt an attack already resolved.", expected=False, enforcement="INVARIANT"),),
        effects=(
            effect("prep_action_with_limited_movement_reserved", "Gain a PREP ACTION that reserves limited movement.", value=True, control=False),
        ),
    ),
    ("EVADER", "BREAK PURSUIT"): contract(
        prerequisites=(
            prerequisite("break_contact_successful", "After a successful BREAK CONTACT"),
            prerequisite("changed_direction_or_crossed_concealed_route_while_pursued", "changing direction or crossing into another concealed route while pursued"),
        ),
        constraints=(constraint("directly_reacquired", "The benefit ends once directly reacquired.", expected=False, enforcement="TERMINATION"),),
    ),
    ("EVADER", "RAPID REENTRY"): contract(
        prerequisites=(
            prerequisite("attacked_from_hidden_position", "After making an attack from a hidden position"),
            prerequisite("subsequently_broke_los_same_team_turn", "if the EVADER subsequently breaks LOS during the same TEAM TURN"),
            prerequisite("next_attempt_is_break_contact", "the next BREAK CONTACT attempt"),
        ),
        constraints=(constraint("geometry_capable_of_breaking_los", "Real geometry capable of breaking LOS is still required.", enforcement="GUARD"),),
    ),
    ("EVADER", "NOWHERE THERE"): contract(
        prerequisites=(
            prerequisite("enemy_spent_meaningful_effort_against_stale_last_known_position", "If an enemy spends an attack, SUPPRESSION action, or other meaningful effort against the EVADER's stale last-known position"),
            prerequisite("enemy_failed_to_reacquire", "fails to reacquire them"),
        ),
    ),
    ("EVADER", "GHOST WALK"): contract(
        prerequisites=(
            prerequisite("break_contact_successful", "After a successful BREAK CONTACT"),
            prerequisite("movement_covert", "while moving covertly"),
        ),
        constraints=(
            constraint("evader_attacked_or_directly_observed_again", "until the EVADER attacks or is directly observed again.", expected=False, enforcement="TERMINATION"),
            constraint("visible_open_ground_sprint_remains_unnoticed", "This does not permit visible open-ground sprinting to remain unnoticed.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("EVADER", "CAN'T PIN ME"): contract(
        prerequisites=(
            prerequisite("attacked_from_uncertain_or_hidden_position", "After attacking from an uncertain/hidden position"),
            prerequisite("movement_specifically_breaks_los", "movement specifically used to break LOS"),
            prerequisite("break_contact_successful", "a successful BREAK CONTACT"),
            prerequisite("new_position_and_firing_solution_available", "the next attack from the new position"),
        ),
        constraints=(
            constraint("geometry_concealment_ap_stealth_route_and_firing_solution_valid", "The loop still requires actual geometry/concealment, AP, successful STEALTH where needed, a route, and a firing solution.", enforcement="GUARD"),
            constraint("surrounded_maintained_los_sealed_exit_or_correct_prediction", "Surrounding, maintained LOS, sealed exits, or correct prediction stops it.", expected=False, enforcement="TERMINATION"),
        ),
    ),
    ("FIELD ENGINEER", "FIELD WORK"): contract(
        prerequisites=(
            prerequisite("engineering_action_is_repair_reconfigure_or_modify_compatible_system", "repairing, physically reconfiguring, and modifying compatible technical systems"),
            prerequisite("systems_handling_does_not_already_cover_action", "that SYSTEMS HANDLING does not already cover"),
            prerequisite("tools_materials_access_and_technical_capability_available", "Appropriate tools, materials, access, and technical capability are still required."),
        ),
        constraints=(
            constraint("ordinary_deployment_receives_entry_discount_without_other_ability", "Ordinary deployment alone is not further discounted by this Entry unless another FIELD ENGINEER ability says so.", expected=False, enforcement="INVARIANT"),
            constraint("field_work_grants_undeclared_capability", "It grants no new sensor power, range, privilege, software function, or other capability.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("FIELD ENGINEER", "FIELD REPAIR"): contract(
        prerequisites=(
            prerequisite("target_damaged_and_repairable_system", "a damaged repairable device, drone, deployable, or other mechanical/electrical system"),
            prerequisite("comparison_uses_same_repair_time_and_materials", "for the same repair time and materials"),
        ),
        constraints=(constraint("destroyed_component_replacement_requirement_bypassed", "Destroyed components may still require replacement.", expected=False, enforcement="INVARIANT"),),
    ),
    ("FIELD ENGINEER", "RAPID DEPLOYMENT"): contract(
        prerequisites=(
            prerequisite("placing_and_activating_carried_deployable", "Placing and activating a carried deployable technical device"),
            prerequisite("device_exists_in_inventory", "The device must already exist in inventory."),
        ),
        constraints=(constraint("final_cost_below_one_ap", "to a minimum final cost of 1 AP.", expected=False, enforcement="INVARIANT"),),
    ),
    ("FIELD ENGINEER", "PATCH JOB"): contract(
        prerequisites=(prerequisite("named_function_ordinarily_available", "one named function the damaged system could ordinarily perform"),),
        constraints=(
            constraint("proper_repair_replaces_patch", "Proper repair replaces the patch", enforcement="TERMINATION"),
            constraint("destroyed_or_missing_component_restored", "a patch cannot restore a destroyed or missing component", expected=False, enforcement="GUARD"),
        ),
        effects=(
            effect("repair_is_temporary_and_limited", "Make a temporary repair faster than a full repair", value=True, control=False),
        ),
    ),
    ("FIELD ENGINEER", "FIELD MODIFICATION"): contract(
        prerequisites=(
            prerequisite("tools_and_suitable_components_available", "With tools and suitable components"),
            prerequisite("target_device_compatible", "a compatible device or deployable"),
            prerequisite("one_defined_physical_characteristic_selected", "exactly one defined physical characteristic"),
            prerequisite("directly_linked_tradeoff_declared", "one directly linked tradeoff"),
        ),
        constraints=(
            constraint("change_physically_plausible", "The change must be physically plausible", enforcement="GUARD"),
            constraint("installed_component_capability_exceeded", "cannot exceed the installed components' capabilities", expected=False, enforcement="GUARD"),
            constraint("modification_removed_replaced_or_rebuilt", "ends when removed, replaced, or properly rebuilt", expected=False, enforcement="TERMINATION"),
        ),
    ),
    ("FIELD ENGINEER", "HARDEN SYSTEM"): contract(
        prerequisites=(prerequisite("deploying_or_servicing_device", "When deploying or servicing a device"),),
        constraints=(constraint("delicate_electronics_made_bulletproof", "This does not make delicate electronics bulletproof.", expected=False, enforcement="INVARIANT"),),
    ),
    ("FIELD ENGINEER", "POWER BUDGET"): contract(
        prerequisites=(
            prerequisite("devices_physically_connected", "physically connected"),
            prerequisite("devices_electrically_compatible", "electrically compatible devices or subsystems"),
        ),
        constraints=(
            constraint("power_conserved", "Power is conserved", enforcement="INVARIANT"),
            constraint("power_capable_connection_intact", "Borrowed endurance lasts only while the power-capable connection remains intact.", enforcement="TERMINATION"),
            constraint("output_sensor_reach_or_other_performance_increased", "POWER BUDGET does not increase output, sensor reach, or other performance", expected=False, enforcement="INVARIANT"),
            constraint("equipment_pair_exchange_quantity_used", "Each compatible equipment pair defines its own exchange quantity", enforcement="GUARD"),
        ),
        effects=(
            effect("donor_endurance_consumed", "extending one system's operating duration consumes the donor's available power", value=True, control=False),
        ),
    ),
    ("FIELD ENGINEER", "EMPLACE"): contract(
        prerequisites=(prerequisite("eligible_deployable_deliberately_emplaced_by_engineer", "A deployable weapon, sensor, barrier mechanism, or similar device deliberately emplaced by the engineer"),),
        constraints=(
            constraint("setup_time_paid", "Emplacement costs setup time", enforcement="GUARD"),
        ),
        effects=(
            effect("relocation_slowdown_honored", "makes relocation slower.", value=True, control=False),
        ),
    ),
    ("FIELD ENGINEER", "FIELD SENSOR"): contract(
        prerequisites=(prerequisite("carried_sensor_deployed_and_configured", "Deploy and configure a carried sensor"),),
        constraints=(constraint("detections_limited_to_actual_sensor_type", "It detects only what its actual sensor type can detect.", enforcement="INVARIANT"),),
    ),
    ("FIELD ENGINEER", "TECHNICAL OBSTACLE"): contract(
        prerequisites=(prerequisite("appropriate_materials_and_equipment_available", "Using appropriate materials and equipment"),),
        constraints=(
            constraint("area_limited", "across a limited area", enforcement="GUARD"),
            constraint("generic_hazard_damage_or_condition_created", "HAZARD creates no generic damage or condition of its own.", expected=False, enforcement="INVARIANT"),
            constraint("actual_hardware_or_material_placed", "represented by actual placed hardware or material", enforcement="GUARD"),
            constraint("hardware_removed_or_bypassed", "removing or bypassing that hardware ends the effect", expected=False, enforcement="TERMINATION"),
        ),
    ),
    ("FIELD ENGINEER", "HOT REPAIR"): contract(
        prerequisites=(
            prerequisite("target_friendly_device", "a friendly device"),
            prerequisite("device_remains_operational", "while it remains operational"),
        ),
        constraints=(
            constraint("lower_effectiveness_or_higher_risk_honored", "Repair effectiveness is lower or riskier than shutting it down for proper work", enforcement="INVARIANT"),
            constraint("physically_inaccessible_moving_components_accessed", "Physically inaccessible moving components remain inaccessible.", expected=False, enforcement="GUARD"),
        ),
    ),
    ("FIELD ENGINEER", "REDUNDANT SYSTEM"): contract(
        prerequisites=(
            prerequisite("preparing_emplaced_technical_device", "When preparing an emplaced technical device"),
            prerequisite("one_important_function_named", "name one important function"),
            prerequisite("real_backup_component_or_path_added", "add a real backup component or path"),
            prerequisite("first_failure_qualifies", "The first qualifying component failure"),
        ),
        constraints=(constraint("other_functions_or_later_failures_protected", "Other functions and later failures resolve normally.", expected=False, enforcement="INVARIANT"),),
        effects=(
            effect("backup_takes_over_named_function", "switches that named function to the backup instead of disabling it", value=True, control=False),
        ),
    ),
    ("FIELD ENGINEER", "OVERDRIVE"): contract(
        prerequisites=(
            prerequisite("device_compatible", "a compatible device"),
            prerequisite("device_pushed_beyond_safe_performance_envelope", "beyond its ordinary safe performance envelope"),
            prerequisite("one_output_selected", "improve one output"),
        ),
        effects=(
            effect("physically_appropriate_after_cost_applied", "Afterwards it accumulates a physically appropriate cost such as wear, damage, heat, or depleted power.", value=True, control=False),
        ),
    ),
    ("FIELD ENGINEER", "FIELD WORKSHOP"): contract(
        prerequisites=(
            prerequisite("suitable_tools_and_parts_available", "Given suitable tools, parts"),
            prerequisite("defensible_working_position_available", "and a defensible working position"),
        ),
        constraints=(
            constraint("matter_or_components_created_from_nothing", "Nothing is manufactured from nothing", expected=False, enforcement="INVARIANT"),
            constraint("parts_power_replacements_and_physical_access_requirements_honored", "parts, power, replacement components, and physical access remain real requirements.", enforcement="GUARD"),
        ),
    ),
    ("FIELD MEDIC", "COMBAT MEDICINE"): contract(
        prerequisites=(
            prerequisite("first_successful_battlefield_treatment_for_distinct_injury_or_condition", "The first successful battlefield treatment addressing each distinct injury or condition"),
            prerequisite("immediate_function_restoration_medically_plausible", "when restoring immediate combat function is medically plausible"),
        ),
        constraints=(
            constraint("meaningless_repeat_treatment_farms_res", "cannot be repeated to farm RES", expected=False, enforcement="GUARD"),
            constraint("hp_restored", "restores no HP", expected=False, enforcement="INVARIANT"),
            constraint("underlying_injury_erased", "does not remove the injury or destroyed anatomy", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("FIELD MEDIC", "RAPID STABILIZATION"): contract(
        prerequisites=(prerequisite("action_is_stabilize", "STABILIZE"),),
        constraints=(
            constraint("stabilization_follows_actual_injury", "Stabilization follows the actual injury", enforcement="INVARIANT"),
            constraint("hp_restored", "does not restore HP by itself.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("FIELD MEDIC", "KIT AT HAND"): contract(
        constraints=(
            constraint("kit_occupies_inventory", "The kit still occupies inventory", enforcement="INVARIANT"),
            constraint("supplies_consumed", "consumes supplies.", enforcement="INVARIANT"),
        ),
        effects=(
            effect("dedicated_medical_kit_configured_quick_access", "A dedicated medical kit may be configured as QUICK ACCESS", value=True, control=False),
        ),
    ),
    ("FIELD MEDIC", "TRIAGE"): contract(
        prerequisites=(
            prerequisite("brief_examination_completed", "A brief examination"),
            prerequisite("target_problem_observable_and_active", "observable ACTIVE injury or LIFE-THREATENING CONDITION"),
        ),
        constraints=(constraint("hidden_or_internal_problem_assessed_without_required_exam_or_equipment", "Hidden/internal problems still require appropriate examination/equipment.", expected=False, enforcement="GUARD"),),
    ),
    ("FIELD MEDIC", "PATCH AND MOVE"): contract(
        prerequisites=(
            prerequisite("injury_permits_continued_movement_after_basic_treatment", "When an injury permits continued movement after basic treatment"),
            prerequisite("battlefield_treatment_successful", "successful battlefield treatment"),
        ),
        constraints=(
            constraint("treatment_failed_or_disrupted", "until the treatment fails/is disrupted.", expected=False, enforcement="TERMINATION"),
            constraint("underlying_injury_remains", "The underlying INJURY remains.", enforcement="INVARIANT"),
        ),
    ),
    ("FIELD MEDIC", "PAIN CONTROL"): contract(
        prerequisites=(prerequisite("medication_or_treatment_appropriate", "Appropriate medication or treatment"),),
        constraints=(
            constraint("medication_resource_requirement_bypassed", "Medication remains a physical resource", expected=False, enforcement="INVARIANT"),
            constraint("duration_dosage_and_side_effect_limits_honored", "may have duration, dosage, and side-effect limits.", enforcement="GUARD"),
        ),
    ),
    ("FIELD MEDIC", "GET THEM UP"): contract(
        prerequisites=(
            prerequisite("casualty_conscious", "a conscious"),
            prerequisite("casualty_prone_impaired_or_recently_stabilized", "PRONE, impaired, or recently stabilized casualty"),
            prerequisite("target_stance_viable", "back into a viable STANCE"),
        ),
        constraints=(constraint("casualty_medically_incapable_of_standing", "It cannot make a medically incapable casualty stand.", expected=False, enforcement="GUARD"),),
    ),
    ("FIELD MEDIC", "CONTINUE CARE"): contract(
        prerequisites=(
            prerequisite("treatment_interrupted_for_move_protection_or_brief_danger_response", "If treatment is interrupted so you can move the casualty, take protection, or briefly respond to danger"),
            prerequisite("completed_setup_or_progress_exists", "completed treatment setup/progress"),
        ),
        constraints=(
            constraint("supplies_lost", "Lost supplies", expected=False, enforcement="TERMINATION"),
            constraint("access_to_casualty_lost", "lost access to the casualty", expected=False, enforcement="TERMINATION"),
            constraint("medical_condition_materially_changed", "a materially changed medical condition can still invalidate that progress.", expected=False, enforcement="TERMINATION"),
        ),
    ),
    ("FIELD MEDIC", "DRAG AND TREAT"): contract(
        prerequisites=(prerequisite("injured_casualty_moved_short_distance_to_safer_geometry", "When moving an injured casualty a short distance into safer geometry"),),
        constraints=(constraint("drag_or_carry_burden_honored", "Drag/carry burden remains physical.", enforcement="INVARIANT"),),
    ),
    ("FIELD MEDIC", "BATTLEFIELD RECOVERY"): contract(
        prerequisites=(
            prerequisite("supplies_permit_treatment", "When supplies and the injury permit it"),
            prerequisite("injury_permits_treatment", "When supplies and the injury permit it"),
        ),
        constraints=(
            constraint("serious_hp_damage_repaired", "It does not repair serious HP damage", expected=False, enforcement="INVARIANT"),
            constraint("major_injury_removed", "or remove major injuries.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("FIELD MEDIC", "MULTIPLE CASUALTIES"): contract(
        prerequisites=(
            prerequisite("immediate_treatment_completed", "After completing an immediate treatment"),
            prerequisite("next_treatment_different_and_other_casualty_nearby", "a different battlefield treatment on another nearby casualty"),
        ),
        constraints=(
            constraint("each_casualty_treated_separately", "Each casualty is still treated separately", enforcement="INVARIANT"),
            constraint("supplies_consumed_normally", "supplies are consumed normally.", enforcement="INVARIANT"),
        ),
    ),
    ("FIELD MEDIC", "FUNCTIONAL SPLINT"): contract(
        prerequisites=(
            prerequisite("injury_impairs_limb_or_movement", "an injury that impairs a limb or movement"),
            prerequisite("use_not_physically_impossible", "does not make use physically impossible"),
        ),
        constraints=(
            constraint("wound_remains", "The wound remains", enforcement="INVARIANT"),
            constraint("real_trauma_care_still_needed", "still needs real trauma care.", enforcement="INVARIANT"),
        ),
    ),
    ("FIELD MEDIC", "SECOND WIND"): contract(
        prerequisites=(
            prerequisite("treatment_successful", "After successful treatment"),
            prerequisite("casualty_conscious_and_physically_capable", "a conscious casualty who is physically capable"),
        ),
        constraints=(
            constraint("temporary_ap_used_only_for_allowed_nonattack_actions", "for movement, changing STANCE, taking COVER, or readying equipment.", enforcement="GUARD"),
            constraint("temporary_ap_used_to_attack", "It cannot be used to attack", expected=False, enforcement="GUARD"),
            constraint("medically_incapable_casualty_acts", "cannot make a medically incapable casualty act.", expected=False, enforcement="GUARD"),
            constraint("casualty_already_benefited_this_encounter", "Each casualty can benefit only once per encounter.", expected=False, enforcement="GUARD"),
        ),
    ),
    ("FIELD MEDIC", "KEEP THEM FIGHTING"): contract(
        prerequisites=(prerequisite("injury_permits_combined_sequence", "When the injury permits it"),),
        constraints=(constraint("serious_hp_loss_major_injury_destroyed_tissue_or_critical_condition_erased", "Serious HP loss, major INJURIES, destroyed tissue, and critical conditions remain.", expected=False, enforcement="INVARIANT"),),
    ),
    ("FLANKER", "ANGLE ADVANTAGE"): contract(
        prerequisites=(prerequisite("attack_direction_not_protected_by_target_current_cover", "When attacking from a direction where the target's current COVER does not protect them"),),
        constraints=(constraint("valid_firing_line", "A valid firing line is still required.", enforcement="GUARD"),),
    ),
    ("FLANKER", "FLANKING MOVE"): contract(
        prerequisites=(prerequisite("movement_toward_position_removing_selected_enemy_cover", "Movement toward a position that will cause a selected enemy's current COVER to stop protecting them"),),
        constraints=(constraint("destination_creates_qualifying_angle", "The destination must actually create the qualifying angle.", enforcement="GUARD"),),
    ),
    ("FLANKER", "SHOOT AND SCOOT"): contract(
        prerequisites=(
            prerequisite("angle_advantage_attack_resolved", "After resolving an attack with ANGLE ADVANTAGE"),
            prerequisite("first_move_toward_different_cover_or_meaningfully_different_angle", "the first movement toward different COVER or a meaningfully different firing angle"),
        ),
    ),
    ("FLANKER", "SIDE PRESSURE"): contract(
        prerequisites=(prerequisite("establishing_suppression_from_angle_not_protected_by_current_cover", "Establishing SUPPRESSION from an angle where the defenders' current COVER does not protect them"),),
        constraints=(constraint("full_cover_blocks_firing_solution", "Full COVER from that direction still blocks the firing solution.", enforcement="GUARD"),),
    ),
    ("FLANKER", "CROSSFIRE"): contract(
        prerequisites=(
            prerequisite("angle_advantage_attack_made", "After making an ANGLE ADVANTAGE attack"),
            prerequisite("ally_has_substantially_different_valid_line_same_target", "one ally with a substantially different valid firing line against the same target"),
            prerequisite("ally_next_attack_current_team_turn", "on their next attack against that target during the current TEAM TURN"),
        ),
        constraints=(constraint("both_firing_lines_exist", "Both firing lines must actually exist.", enforcement="GUARD"),),
    ),
    ("FLANKER", "CUT THE EXIT"): contract(
        prerequisites=(
            prerequisite("qualifying_flank_position_reached", "After reaching a qualifying flank position"),
            prerequisite("retreat_route_physically_valid", "a physically valid retreat route"),
        ),
        constraints=(constraint("overwatch_affects_only_declared_zone", "It watches only the declared zone.", enforcement="INVARIANT"),),
    ),
    ("FLANKER", "REORIENT PUNISH"): contract(
        prerequisites=(
            prerequisite("target_new_cover_blocks_current_firing_line", "If a target moves into new COVER that blocks the current firing line"),
            prerequisite("next_move_establishes_new_angle_against_same_target", "the FLANKER's next movement to establish another qualifying ANGLE ADVANTAGE against that target"),
        ),
    ),
    ("FLANKER", "EXPOSED TARGET"): contract(
        prerequisites=(prerequisite("angle_advantage_attack_successful", "A successful attack with ANGLE ADVANTAGE"),),
        constraints=(constraint("res_armor_and_penetration_apply_normally", "RES, armor, and PENETRATION still apply normally.", enforcement="INVARIANT"),),
    ),
    ("FLANKER", "WEAK SIDE"): contract(
        prerequisites=(prerequisite("called_shot_from_angle_advantage", "When making a CALLED SHOT from ANGLE ADVANTAGE"),),
        constraints=(constraint("selected_location_physically_visible", "The selected location must be physically visible.", enforcement="GUARD"),),
    ),
    ("FLANKER", "FLANKING OVERWATCH"): contract(
        prerequisites=(prerequisite("flanking_move_used_to_establish_qualifying_angle", "After using FLANKING MOVE to establish a qualifying angle"),),
        constraints=(constraint("movement_and_prep_both_occur", "Movement and preparation still both occur.", enforcement="INVARIANT"),),
    ),
    ("FLANKER", "DOUBLE ENVELOPMENT"): contract(
        prerequisites=(prerequisite("crossfire_active", "When CROSSFIRE is active"),),
        constraints=(
            constraint("qualifying_firing_line_broken", "The effect ends when a firing line breaks", expected=False, enforcement="TERMINATION"),
            constraint("team_turn_ended", "or the TEAM TURN ends.", expected=False, enforcement="TERMINATION"),
        ),
    ),
    ("FLANKER", "CHAINED FLANK"): contract(
        prerequisites=(
            prerequisite("angle_advantage_attack_downed_or_removed_target", "When an ANGLE ADVANTAGE attack DOWNS or otherwise removes a target"),
            prerequisite("movement_toward_flank_on_another_enemy", "movement toward a qualifying flank on another enemy"),
        ),
        constraints=(constraint("second_enemy_presents_real_new_angle", "The second enemy must present a real new angle.", enforcement="GUARD"),),
    ),
    ("FLANKER", "SHADOW THE COVER"): contract(
        prerequisites=(
            prerequisite("enemy_moves_into_geometry_blocking_current_line", "If that enemy moves specifically into new geometry that blocks the current line"),
        ),
        constraints=(constraint("movement_allowance_and_terrain_limits_honored", "The movement allowance and terrain remain physical limits.", enforcement="GUARD"),),
        effects=(
            effect("visible_enemy_designated_and_relocation_prepared", "designate a visible enemy and prepare limited relocation", value=True, control=False),
        ),
    ),
    ("FLANKER", "ENCIRCLEMENT"): contract(
        prerequisites=(prerequisite("flanker_and_ally_have_substantially_separated_valid_lines_same_target", "When the FLANKER and at least one ally maintain substantially separated valid firing lines against the same target"),),
        constraints=(
            constraint("geometry_supports_both_lines", "while the geometry supports it.", enforcement="GUARD"),
            constraint("target_relocated_or_broke_line_or_gained_dual_cover_or_shooter_removed", "The target can defeat this by relocating, breaking a firing line, using geometry that protects against both, suppressing/removing a shooter, or otherwise solving the real crossfire.", expected=False, enforcement="TERMINATION"),
        ),
    ),
    ("FPV DRONES", "MANUAL PILOT"): contract(
        prerequisites=(prerequisite("directly_controlling_fpv_drone", "When directly controlling an FPV drone"),),
        constraints=(constraint("operator_spends_own_ap_to_pilot", "The operator still spends their own AP to pilot it.", enforcement="INVARIANT"),),
    ),
    ("FPV DRONES", "NAP-OF-THE-EARTH"): contract(
        prerequisites=(
            prerequisite("nearby_terrain_already_provides_partial_masking", "When real nearby terrain already provides partial masking"),
            prerequisite("precise_close_terrain_flight", "precise close-terrain flight"),
        ),
        constraints=(
            constraint("partial_exposure_converted_to_full_cover", "It cannot convert partial exposure into full COVER", expected=False, enforcement="INVARIANT"),
            constraint("clear_los", "provides no benefit in clear LOS.", expected=False, enforcement="GUARD"),
        ),
    ),
    ("FPV DRONES", "PRECISION FLIGHT"): contract(
        prerequisites=(prerequisite("flight_path_narrow_interior_dense_or_similarly_demanding", "passing through narrow openings, interior spaces, dense obstacles, or similarly demanding flight paths"),),
        constraints=(constraint("path_physically_fits_drone", "The path must physically fit the drone.", enforcement="GUARD"),),
    ),
    ("FPV DRONES", "RAPID LAUNCH"): contract(
        prerequisites=(prerequisite("fpv_drone_combat_accessible", "a combat-accessible FPV drone"),),
        constraints=(constraint("drone_physically_deployed", "The drone still has to be physically deployed.", enforcement="GUARD"),),
    ),
    ("FPV DRONES", "FORWARD OBSERVER"): contract(
        prerequisites=(
            prerequisite("drone_has_valid_sensor_los", "While the drone has valid sensor LOS to a target or area"),
            prerequisite("nearby_allies_designated", "designated nearby allies"),
            prerequisite("drone_actively_provides_target_position", "whose position the drone is actively providing"),
        ),
        constraints=(
            constraint("information_support_shoots_through_walls", "This is information support, not shooting through walls", expected=False, enforcement="INVARIANT"),
            constraint("signal_or_observation_lost", "loss of signal or observation ends it.", expected=False, enforcement="TERMINATION"),
        ),
    ),
    ("FPV DRONES", "SNAP PEEK"): contract(
        prerequisites=(prerequisite("out_observe_return_route_is_same_and_sensor_position_valid", "moving the drone from protection to the first valid sensor position, making one observation, and returning along the same route"),),
        constraints=(
            constraint("events_resolve_at_actual_drone_positions", "OVERWATCH, signal loss, and other events still resolve at the drone's actual positions", enforcement="INVARIANT"),
            constraint("operator_awareness_exceeds_drone_sensors", "the operator gains no awareness beyond the drone's sensors.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("FPV DRONES", "MARK AND PASS"): contract(
        prerequisites=(prerequisite("target_positively_identified_through_drone", "After positively identifying a target through the drone"),),
        constraints=(
            constraint("identification_period_limited", "for a limited period after personal observation stops", enforcement="TERMINATION"),
            constraint("unseen_relocation_stales_information", "Unseen relocation makes the information stale normally.", enforcement="TERMINATION"),
        ),
    ),
    ("FPV DRONES", "PRECISION DELIVERY"): contract(
        prerequisites=(
            prerequisite("drone_places_or_releases_carried_payload", "When using a drone to place or release a carried payload"),
            prerequisite("delivery_point_specific_and_reachable", "at a specific reachable point"),
        ),
        constraints=(constraint("payload_normal_rules_apply", "The payload follows its normal rules.", enforcement="INVARIANT"),),
    ),
    ("FPV DRONES", "ATTACK RUN"): contract(
        prerequisites=(
            prerequisite("drone_carries_weapon_or_one_way_payload", "If the drone carries a weapon or one-way attack payload"),
            prerequisite("final_approach_manually_piloted", "manually piloting the final approach"),
        ),
        constraints=(constraint("cover_and_interception_use_real_attack_path", "Enemy COVER and interception depend on the real attack path.", enforcement="INVARIANT"),),
    ),
    ("FPV DRONES", "MASKED APPROACH"): contract(
        prerequisites=(
            prerequisite("drone_reaches_final_attack_or_observation_position", "If the drone reaches its final attack or observation position"),
            prerequisite("physically_masked_for_most_of_approach", "while physically masked from the target for most of the approach"),
        ),
        constraints=(constraint("normal_engagement_after_exposure", "Once exposed, it can be engaged normally.", enforcement="INVARIANT"),),
    ),
    ("FPV DRONES", "EVASIVE PILOTING"): contract(
        prerequisites=(
            prerequisite("enemy_attacks_manually_controlled_drone", "When an enemy attacks a drone currently under manual control"),
            prerequisite("additional_attention_ap_or_movement_tradeoff_paid", "by spending additional piloting attention/AP or reducing its available movement"),
        ),
        constraints=(constraint("passive_armor_granted", "This is active evasion, not passive armor.", expected=False, enforcement="INVARIANT"),),
    ),
    ("FPV DRONES", "SIGNAL DISCIPLINE"): contract(
        prerequisites=(
            prerequisite("drone_manually_controlled", "A manually controlled drone"),
            prerequisite("signal_weak_obstructed_or_contested", "weak, obstructed, or contested signal conditions"),
        ),
        constraints=(constraint("link_completely_severed", "A completely severed link still breaks control.", expected=False, enforcement="TERMINATION"),),
    ),
    ("FPV DRONES", "LAST-SECOND CORRECTION"): contract(
        prerequisites=(
            prerequisite("manual_attack_or_delivery_narrowly_failed_for_final_positioning", "After a manually controlled drone's attack or payload delivery would narrowly fail because of final positioning or placement error"),
            prerequisite("additional_piloting_ap_spent", "spend additional piloting AP"),
        ),
        constraints=(constraint("impossible_path_destroyed_drone_or_completely_lost_signal", "It cannot fix an impossible path, destroyed drone, or completely lost signal.", expected=False, enforcement="GUARD"),),
    ),
    ("FPV DRONES", "THROUGH THE EYE OF THE NEEDLE"): contract(
        prerequisites=(prerequisite("manually_piloting_one_drone", "While manually piloting one drone"),),
        constraints=(
            constraint("more_than_one_drone_directly_controlled", "Only one drone is directly controlled at a time", expected=False, enforcement="GUARD"),
            constraint("operator_spends_attention_and_ap", "the operator still spends attention/AP", enforcement="INVARIANT"),
            constraint("destruction_jamming_range_battery_and_signal_limits_honored", "the drone remains vulnerable to physical destruction, jamming, range, battery, and signal limits.", enforcement="INVARIANT"),
        ),
    ),
    ("GUARDIAN", "INTERPOSE"): contract(
        prerequisites=(
            prerequisite("protected_ally_nearby", "on a nearby ally"),
            prerequisite("valid_threat_opens_against_ally", "When a valid threat opens against that ally"),
            prerequisite("guardian_can_physically_reach_relevant_line_or_space", "you can physically place yourself into the relevant line or space"),
        ),
        constraints=(constraint("distance_wall_reach_movement_facing_and_geometry_limits_honored", "Distance, walls, reach, movement, facing, and actual geometry still matter.", enforcement="GUARD"),),
    ),
    ("GUARDIAN", "BODY COVER"): contract(
        prerequisites=(
            prerequisite("physically_screening_adjacent_protected_ally", "While physically screening an adjacent protected ally"),
            prerequisite("one_direction_declared", "from one declared direction"),
            prerequisite("guardian_body_can_add_screening", "where your body can actually provide the additional screening"),
        ),
        constraints=(
            constraint("full_cover_created", "This cannot create full COVER.", expected=False, enforcement="INVARIANT"),
            constraint("intercepted_attack_resolves_normally_against_guardian", "If an attack is intercepted by you, it resolves against you normally.", enforcement="INVARIANT"),
        ),
    ),
    ("GUARDIAN", "PULL BEHIND"): contract(
        prerequisites=(
            prerequisite("interpose_triggered", "When INTERPOSE triggers"),
            prerequisite("suitable_protection_immediately_available", "suitable protection is immediately available"),
            prerequisite("protected_ally_consents", "the consenting protected ally"),
            prerequisite("destination_adjacent_and_safer", "an adjacent safer position"),
        ),
        constraints=(constraint("movement_through_obstacle_or_beyond_permitted_distance", "This cannot move them through obstacles or beyond the physically permitted distance.", expected=False, enforcement="GUARD"),),
    ),
    ("GUARDIAN", "GUARDED STEP"): contract(
        prerequisites=(
            prerequisite("interpose_established", "While INTERPOSE is established"),
            prerequisite("guardian_spends_own_movement_ap_during_ally_movement", "spend your own movement/AP during the protected ally's movement"),
        ),
        constraints=(constraint("guardian_cannot_stay_with_ally", "If you cannot stay with them, INTERPOSE breaks normally.", expected=False, enforcement="TERMINATION"),),
    ),
    ("GUARDIAN", "DEFENSIVE EXCHANGE"): contract(
        prerequisites=(
            prerequisite("interpose_triggered", "When INTERPOSE triggers"),
            prerequisite("both_positions_physically_reachable", "when both positions are physically reachable"),
        ),
    ),
    ("GUARDIAN", "CLOSE PROTECTION"): contract(
        prerequisites=(
            prerequisite("enemy_enters_contact_range_for_physical_attack", "an enemy entering contact range to SHOVE, CONTROL, DISARM, or otherwise physically attack the protected ally"),
            prerequisite("guardian_can_get_between_them", "You still must be capable of getting between them."),
        ),
    ),
    ("GUARDIAN", "PROTECT THE OPERATOR"): contract(
        prerequisites=(
            prerequisite("protected_ally_adjacent", "an adjacent ally"),
            prerequisite("ally_commits_substantial_time_to_stationary_operational_task", "commits substantial time to a stationary operational task such as MEDICINE, hacking, ENGINEERING, AIM, or drone control"),
        ),
        constraints=(
            constraint("final_setup_cost_below_one_ap", "Minimum final setup cost remains 1 AP", expected=False, enforcement="INVARIANT"),
            constraint("ordinary_positioning_requirements_satisfied", "all ordinary positioning requirements still apply.", enforcement="GUARD"),
        ),
    ),
    ("GUARDIAN", "EMERGENCY INTERCEPT"): contract(
        prerequisites=(prerequisite("protected_ally_lost_cover_or_was_displaced_into_danger", "when the protected ally suddenly loses COVER or is forcibly displaced into danger"),),
        constraints=(constraint("reachable_physical_route", "A reachable physical route is still required.", enforcement="GUARD"),),
    ),
    ("GUARDIAN", "STAND OVER THEM"): contract(
        prerequisites=(prerequisite("protected_ally_prone_incapacitated_or_downed", "a prone, incapacitated, or DOWNED ally"),),
        constraints=(
            constraint("stabilization_or_recovery_provided", "not stabilization or recovery.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("GUARDIAN", "EXTRACT"): contract(
        prerequisites=(prerequisite("protecting_impaired_ally", "While protecting an impaired ally"),),
        constraints=(constraint("burden_reduces_movement_and_other_actions", "The physical burden still reduces movement and limits what else you can do.", enforcement="INVARIANT"),),
    ),
    ("GUARDIAN", "NO OPENING"): contract(
        prerequisites=(
            prerequisite("interpose_resolved", "After INTERPOSE resolves"),
            prerequisite("positioning_and_protection_requirements_still_satisfied", "if you still satisfy its positioning and protection requirements"),
        ),
        constraints=(
            constraint("unlimited_movement_granted", "This does not grant unlimited movement", expected=False, enforcement="INVARIANT"),
            constraint("unlimited_responses_granted", "or unlimited responses", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("GUARDIAN", "LAST GUARD"): contract(
        prerequisites=(
            prerequisite("attack_intercepted_through_interpose", "If an attack intercepted through INTERPOSE"),
            prerequisite("guardian_left_downed", "leaves you DOWNED"),
            prerequisite("ally_destination_adjacent_physically_legal_and_safer", "an adjacent physically legal safer position"),
        ),
        constraints=(constraint("overwatch_suppression_hazard_and_geometry_resolve", "This movement still resolves real OVERWATCH, SUPPRESSION, hazards, and geometry.", enforcement="INVARIANT"),),
    ),
    ("GUARDIAN", "COVERING WITHDRAWAL"): contract(
        prerequisites=(
            prerequisite("protected_ally_retreats_through_reachable_protective_area", "A protected ally may retreat through your reachable protective area"),
            prerequisite("guardian_spends_own_movement_ap", "while you spend your own movement/AP"),
        ),
        constraints=(constraint("one_dangerous_direction_screened", "keep screening one dangerous direction.", enforcement="GUARD"),),
        effects=(
            effect("interpose_established_around_nearby_withdrawal_route", "Establish INTERPOSE around a nearby withdrawal route", value=True, control=False),
        ),
    ),
    ("GUARDIAN", "NONE LEFT EXPOSED"): contract(
        prerequisites=(
            prerequisite("nearby_ally_becomes_threatened", "When a nearby ally becomes threatened"),
            prerequisite("guardian_can_reach_required_line_or_position", "if you can actually reach the necessary line or position"),
        ),
        constraints=(
            constraint("guardian_in_more_than_one_place", "You can still only be in one place at a time", expected=False, enforcement="INVARIANT"),
            constraint("simultaneous_incompatible_threats_answered_by_declaration", "cannot answer simultaneous threats from incompatible directions by declaration alone.", expected=False, enforcement="INVARIANT"),
        ),
        effects=(
            effect("protective_posture_covers_immediate_physical_reach", "a protective posture over your immediate physical reach", value=True, control=False),
        ),
    ),
    ("GUNNER", "SUSTAINED FIRE DISCIPLINE"): contract(
        prerequisites=(
            prerequisite("consecutive_firing_actions_same_firearm", "When you make consecutive firing actions with the same firearm"),
            prerequisite("recoil_not_fully_recovered", "before accumulated RECOIL has fully recovered"),
        ),
        constraints=(
            constraint("existing_recoil_still_applies", "Existing RECOIL still matters", enforcement="INVARIANT"),
            constraint("every_round_consumed", "every round is still consumed", enforcement="INVARIANT"),
            constraint("benefit_transfers_when_switching_weapons", "switching weapons does not transfer the benefit to the new firearm.", expected=False, enforcement="TERMINATION"),
        ),
    ),
    ("GUNNER", "WEAPON REST"): contract(
        prerequisites=(prerequisite("firearm_braced_against_physically_suitable_support", "When you brace or rest a firearm against physically suitable support such as a bipod, wall, barricade, vehicle edge, or ground"),),
        constraints=(constraint("moved_away_from_support", "Moving away from the support ends the benefit.", expected=False, enforcement="TERMINATION"),),
    ),
    ("GUNNER", "SUPPRESSION DISCIPLINE"): contract(
        prerequisites=(prerequisite("establishing_suppression", "Establish SUPPRESSION"),),
        constraints=(
            constraint("real_rounds_used", "by placing real rounds more efficiently into the threatened area", enforcement="INVARIANT"),
            constraint("targets_made_more_frightened_or_agency_removed", "This does not make targets more frightened or remove their agency.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("GUNNER", "RECOIL RHYTHM"): contract(
        prerequisites=(
            prerequisite("stopped_firing", "If you stop firing"),
            prerequisite("same_firing_posture_and_weapon_maintained", "remain in the same firing posture with the same weapon"),
        ),
        constraints=(constraint("moved_switched_weapon_or_broke_posture", "Moving, switching weapons, or breaking the firing posture can interrupt that recovery.", expected=False, enforcement="TERMINATION"),),
    ),
    ("GUNNER", "SHIFT FIRE"): contract(
        prerequisites=(
            prerequisite("suppression_already_established", "If SUPPRESSION is already established"),
            prerequisite("new_area_adjacent_or_overlapping", "an adjacent or overlapping area"),
            prerequisite("valid_firing_line_into_new_area", "You need a valid firing line into the new area"),
        ),
        constraints=(constraint("zone_jumps_across_blocking_geometry", "cannot jump the zone across blocking geometry.", expected=False, enforcement="GUARD"),),
    ),
    ("GUNNER", "WALKING FIRE"): contract(
        prerequisites=(prerequisite("moving_between_sustained_firing_actions", "Move between sustained firing actions"),),
        constraints=(
            constraint("movement_ap_paid", "Movement still costs AP", enforcement="INVARIANT"),
            constraint("recoil_disappears", "RECOIL does not disappear", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("GUNNER", "LONG STRING"): contract(
        prerequisites=(prerequisite("continuous_automatic_fire_action", "a continuous automatic-fire action"),),
        constraints=(
            constraint("connections_exceed_rounds_fired", "You still cannot connect more rounds than you fire", expected=False, enforcement="INVARIANT"),
            constraint("excessive_fire_control_difficulty_honored", "excessive fire remains increasingly difficult to control.", enforcement="INVARIANT"),
        ),
    ),
    ("GUNNER", "SUPPRESSIVE SWEEP"): contract(
        prerequisites=(
            prerequisite("two_areas_physically_connected_and_adjacent", "two physically connected adjacent areas"),
            prerequisite("areas_within_one_continuous_firing_arc", "within one continuous firing arc"),
            prerequisite("enough_ammunition_committed_for_both", "You must commit enough ammunition to threaten both"),
        ),
        constraints=(constraint("solid_geometry_can_split_or_block_sweep", "solid geometry can split or block the sweep.", enforcement="GUARD"),),
    ),
    ("GUNNER", "COUNTERFIRE"): contract(
        prerequisites=(
            prerequisite("establishing_suppression", "When establishing SUPPRESSION"),
            prerequisite("enemy_exposes_to_deliberately_return_fire_within_area", "an enemy who exposes themselves to deliberately return fire from within the suppressed area"),
        ),
        constraints=(
            constraint("trigger_ordering_rule_obeyed", "must obey the eventual trigger-ordering rule.", enforcement="INVARIANT"),
        ),
        effects=(
            effect("triggered_response_uses_normal_attack_roll", "If triggered, make a normal ATTACK ROLL", value=True, control=False),
            effect("required_ammunition_expended", "expend the required ammunition.", value=True, control=False),
        ),
    ),
    ("GUNNER", "KEEP THE GUN UP"): contract(
        prerequisites=(
            prerequisite("effect_would_abandon_supported_firing_posture", "When an effect would make you abandon a supported firing posture"),
            prerequisite("not_displaced_incapacitated_or_physically_prevented", "without actually displacing, incapacitating, or physically preventing you from holding the weapon"),
        ),
        constraints=(constraint("forced_movement_support_loss_weapon_loss_or_prone", "Forced movement, loss of support, losing the weapon, or being knocked PRONE can still break it.", expected=False, enforcement="TERMINATION"),),
    ),
    ("GUNNER", "DOMINATING FIRE"): contract(
        constraints=(
            constraint("ammo_commitment_scales_with_area", "Ammunition commitment scales with the threatened area", enforcement="INVARIANT"),
            constraint("walls_corners_and_geometry_limit_firing_solution", "walls, corners, and other geometry still break the firing solution normally.", enforcement="GUARD"),
        ),
        effects=(
            effect("establishing_suppression_over_larger_connected_area", "Establish SUPPRESSION across a larger connected area than normal", value=True, control=False),
        ),
    ),
    ("GUNNER", "FIRE THROUGH THE OPENING"): contract(
        prerequisites=(prerequisite("suppression_concentrated_on_one_narrow_opening", "When SUPPRESSION is concentrated on one narrow opening"),),
        constraints=(
            constraint("normal_ammunition_consumed", "The smaller zone still consumes normal ammunition", enforcement="INVARIANT"),
            constraint("wider_area_simultaneously_covered", "cannot simultaneously cover a wider area.", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("GUNNER", "RELENTLESS"): contract(
        prerequisites=(prerequisite("sustained_firing_action_resolved", "After resolving a sustained firing action"),),
        constraints=(constraint("recoil_reset_to_zero", "without resetting RECOIL to zero.", expected=False, enforcement="INVARIANT"),),
    ),
    ("GUNNER", "FIRE SUPERIORITY"): contract(
        prerequisites=(
            prerequisite("start_of_next_team_turn", "At the start of your next TEAM TURN"),
            prerequisite("existing_suppression", "continue an existing SUPPRESSION"),
            prerequisite("reduced_maintenance_ap_spent", "spend reduced maintenance AP"),
            prerequisite("fresh_ammunition_spent", "fresh ammunition"),
        ),
        constraints=(
            constraint("firing_line_weapon_state_and_ammo_commitment_valid", "The firing line, weapon state, and ammunition commitment must remain real", enforcement="GUARD"),
            constraint("recoil_continues_accumulating", "RECOIL continues accumulating", enforcement="INVARIANT"),
            constraint("blocking_geometry_limits_zone", "blocking geometry still limits the zone.", enforcement="GUARD"),
        ),
    ),
    ("HANDLER", "WORK A CONTACT"): contract(
        prerequisites=(prerequisite("established_contact_and_plausible_request", "asking an established CONTACT for plausible information, access, an introduction, or a favor"),),
        constraints=(constraint("contact_agency_and_capability_preserved", "The contact still has their own interests, limits, and things they simply cannot provide.", enforcement="INVARIANT"),),
    ),
    ("HANDLER", "VET THE SOURCE"): contract(
        prerequisites=(prerequisite("meaningful_contact_interaction", "After meaningful interaction with a CONTACT or informant"),),
        constraints=(constraint("individual_statement_truth_revealed", "It does not tell you whether every individual statement is true.", expected=False, enforcement="INVARIANT"),),
    ),
    ("HANDLER", "QUICK ASK"): contract(
        prerequisites=(prerequisite("narrow_information_already_known", "a narrow piece of information the CONTACT already knows"),),
        constraints=(constraint("unperformed_research_supplied", "Research they have not done remains research they have not done.", expected=False, enforcement="INVARIANT"),),
    ),
    ("HANDLER", "DISCREET CHANNEL"): contract(
        constraints=(constraint("actual_communication_method_still_matters", "The actual communication method still matters.", enforcement="INVARIANT"),),
    ),
    ("HANDLER", "INTRODUCTION"): contract(
        prerequisites=(prerequisite("contact_willing", "A willing CONTACT"), prerequisite("actual_one_step_access", "someone one relationship step beyond them")),
        constraints=(constraint("introduction_without_actual_access", "cannot introduce you to someone they do not actually have access to", expected=False, enforcement="GUARD"),),
    ),
    ("HANDLER", "FAVOR LEDGER"): contract(
        prerequisites=(prerequisite("meaningful_benefit_provided", "provide a meaningful service, resource, protection, introduction, or other benefit to a CONTACT"),),
        constraints=(constraint("favor_actually_matters", "The favor has to actually matter.", enforcement="GUARD"),),
    ),
    ("HANDLER", "CUTOUT"): contract(
        constraints=(constraint("time_or_distortion_may_increase", "may increase time or distortion", enforcement="INVARIANT"),),
        effects=(effect("origin_automatically_disclosed", "the final recipient does not automatically know the request originated with you", value=False, control=True),),
    ),
    ("HANDLER", "CORROBORATE"): contract(
        prerequisites=(prerequisite("two_genuinely_independent_sources_support_same_claim", "two genuinely independent human sources support the same factual claim"), prerequisite("evidence_justifies_confidence_change", "when the evidence actually justifies it")),
        constraints=(constraint("single_source_rumor_counts_as_independent", "Two people repeating the same rumor from one source are not independent corroboration.", expected=False, enforcement="GUARD"),),
    ),
    ("HANDLER", "RUMOR MAP"): contract(
        prerequisites=(prerequisite("several_relevant_sources_same_social_environment", "collecting HUMINT from several relevant people in the same social environment"),),
        constraints=(constraint("inference_bounded_by_collective_revelation", "bounded by what your sources collectively reveal", enforcement="INVARIANT"),),
    ),
    ("HANDLER", "PROTECT THE SOURCE"): contract(
        prerequisites=(prerequisite("contact_threatened_because_of_relationship", "a CONTACT is threatened specifically because of their relationship with you"), prerequisite("protective_action_succeeds", "If that action succeeds")),
        constraints=(
            constraint("existing_evidence_erased", "It does not erase existing evidence", expected=False, enforcement="INVARIANT"),
            constraint("unrelated_contact_protected", "protect an unrelated contact", expected=False, enforcement="GUARD"),
            constraint("exposure_through_surviving_chain_stopped", "stop exposure through another surviving chain", expected=False, enforcement="INVARIANT"),
        ),
        effects=(effect("additional_directly_connected_contact_avoids_chain_exposure", "one additional CONTACT directly connected through the exposed relationship avoids becoming exposed solely through that chain", value=True, control=False),),
    ),
    ("HANDLER", "PARALLEL ASK"): contract(
        prerequisites=(prerequisite("one_clearly_defined_request_through_several_relevant_contacts", "one clearly defined information request through several relevant CONTACTS"),),
        constraints=(constraint("contact_returns_only_learnable_information", "Each CONTACT still returns only what they can actually learn.", enforcement="INVARIANT"),),
    ),
    ("HANDLER", "BORROWED ACCESS"): contract(
        prerequisites=(prerequisite("contact_willing_and_has_genuine_standing", "A willing CONTACT who genuinely has standing with a person, organization, or location"),),
        constraints=(constraint("security_bypassed_beyond_contact_authority", "cannot bypass security they themselves lack authority to bypass", expected=False, enforcement="INVARIANT"),),
    ),
    ("HANDLER", "SAFE HAVEN"): contract(
        prerequisites=(prerequisite("network_plausibly_has_support_access", "a CONTACT network plausibly has access to shelter, transport, storage, medical help, documents, equipment, or similar support"),),
        constraints=(constraint("capable_provider_exists", "The network still has to contain somebody capable of providing it.", enforcement="GUARD"),),
    ),
    ("HANDLER", "HUMAN NETWORK"): contract(
        constraints=(constraint("contact_agency_and_uncertainty_preserved", "Contacts remain people who can refuse, lie, misunderstand, disappear, betray you, or simply not know anyone useful.", enforcement="INVARIANT"),),
    ),
    ("ICON", "RECOGNITION"): contract(
        prerequisites=(prerequisite("target_recognizes_icon", "someone actually recognizes you"), prerequisite("reputation_relevant", "that reputation is relevant to the interaction")),
        constraints=(constraint("benefit_without_recognition", "Someone who has never heard of you gives no benefit.", expected=False, enforcement="GUARD"),),
    ),
    ("ICON", "NAME OPENS DOORS"): contract(
        prerequisites=(prerequisite("gatekeeper_recognizes_icon", "a gatekeeper recognizes you"), prerequisite("reputation_relevant_to_organization_or_audience", "your reputation is relevant to their organization or audience")),
        constraints=(constraint("unauthorized_security_bypassed", "does not bypass physical or legal security they have no authority to waive", expected=False, enforcement="INVARIANT"),),
    ),
    ("ICON", "READ THE AUDIENCE"): contract(
        prerequisites=(prerequisite("successful_social_read_against_gathered_audience", "A successful SOCIAL READ against a gathered audience"),),
        constraints=(constraint("individuals_forced_to_match_crowd", "Individuals may still disagree with the crowd.", expected=False, enforcement="INVARIANT"),),
    ),
    ("ICON", "PUBLIC INTRODUCTION"): contract(
        prerequisites=(prerequisite("ally_openly_introduced_or_endorsed", "openly introduce or endorse an ally"), prerequisite("audience_recognizes_icon", "people who recognize you")),
        constraints=(constraint("ally_gains_permanent_reputation", "They do not gain your reputation permanently.", expected=False, enforcement="INVARIANT"),),
    ),
    ("ICON", "CROWD MOMENTUM"): contract(
        prerequisites=(prerequisite("visible_audience_successfully_influenced", "After successfully influencing a visible audience"), prerequisite("target_cares_about_audience_opinion", "demonstrably cares about that audience's opinion")),
        constraints=(constraint("uncaring_target_affected", "A person who genuinely does not care about the crowd is unaffected.", expected=False, enforcement="GUARD"),),
    ),
    ("ICON", "MAKE AN ENTRANCE"): contract(
        prerequisites=(prerequisite("arrival_publicly_recognized", "When your arrival is publicly recognized"), prerequisite("first_relevant_social_action", "your first relevant social action in that scene")),
        constraints=(constraint("benefit_when_anonymous_or_unrecognized", "If you enter anonymously or nobody knows who you are, nothing happens.", expected=False, enforcement="GUARD"),),
    ),
    ("ICON", "ENDORSEMENT"): contract(
        prerequisites=(prerequisite("support_public", "publicly support a person, product, faction, cause, or claim"), prerequisite("audience_respects_or_values_reputation", "people who already respect or value that reputation")),
        constraints=(constraint("hostile_audience_may_react_oppositely", "An audience hostile to you may react in the opposite direction.", enforcement="INVARIANT"),),
    ),
    ("ICON", "BROADCAST"): contract(
        prerequisites=(prerequisite("real_communications_channel_access", "Given access to a real communications channel"),),
        constraints=(constraint("audience_groups_forced_to_react_identically", "Different audience groups can still react differently.", expected=False, enforcement="INVARIANT"),),
    ),
    ("ICON", "MESSAGE DISCIPLINE"): contract(
        prerequisites=(prerequisite("challenged_publicly", "When challenged publicly"),),
        constraints=(constraint("accusations_disappear", "This does not make accusations disappear.", expected=False, enforcement="INVARIANT"),),
    ),
    ("ICON", "REPUTATIONAL RESERVE"): contract(
        prerequisites=(prerequisite("damaging_fact_would_create_leverage", "a public accusation or damaging SOCIAL FACT would create LEVERAGE against you"), prerequisite("contradictory_reputation_fact_well_established", "one well-established contradictory reputation fact")),
        constraints=(constraint("evidence_can_overwhelm_reputation", "Evidence can still overwhelm reputation.", enforcement="INVARIANT"),),
    ),
    ("ICON", "MAKE IT PUBLIC"): contract(
        prerequisites=(prerequisite("opponent_has_clear_reputational_stake", "an opponent has a clear reputational stake in how a dispute is perceived"), prerequisite("actual_audience_or_recording_channel", "You need an actual audience or recording channel.")),
    ),
    ("ICON", "TRANSFER PRESTIGE"): contract(
        prerequisites=(prerequisite("public_introduction_used_for_same_visible_social_effort", "PUBLIC INTRODUCTION"), prerequisite("multiple_allies_participating", "multiple allies participating in the same visible social effort")),
        constraints=(constraint("later_conduct_can_undermine_endorsement", "Their later conduct can still undermine the endorsement.", enforcement="TERMINATION"),),
        effects=(effect("temporary_reputation_leverage_granted_to_multiple_allies", "PUBLIC INTRODUCTION may grant your temporary reputation LEVERAGE to multiple allies participating in the same visible social effort", value=True, control=False),),
    ),
    ("ICON", "DEFINE THE MOMENT"): contract(
        prerequisites=(prerequisite("highly_visible_event_personally_participated", "a highly visible event in which you personally participate"), prerequisite("framed_aspect_true_and_publicly_observable", "one true and publicly observable aspect")),
        constraints=(constraint("contradictory_evidence_overwritten", "If the audience has evidence contradicting the framing, you do not overwrite reality.", expected=False, enforcement="INVARIANT"),),
    ),
    ("ICON", "LIVING SYMBOL"): contract(
        prerequisites=(prerequisite("reputation_genuinely_established_for_audience", "audiences where your reputation is genuinely established"),),
        constraints=(constraint("irrelevant_reputation_forces_care", "cannot make people care about a reputation irrelevant to them", expected=False, enforcement="GUARD"), constraint("hostile_audience_may_react_against_symbol", "a hostile audience may react against what you represent", enforcement="INVARIANT")),
    ),
    ("INFILTRATOR", "APPROACH PLAN"): contract(
        prerequisites=(prerequisite("guarded_route_observed", "After observing a guarded route"), prerequisite("following_designated_approach", "while following that APPROACH")),
        constraints=(constraint("changed_route_element_still_benefits_without_reassessment", "Material changes to the route/security remove the benefit against the changed element until reassessed.", expected=False, enforcement="TERMINATION"),),
        effects=(effect("stealth_effectiveness_against_known_threats_improved", "STEALTH checks made while following that APPROACH gain improved effectiveness against those known threats", value=True, control=False),),
    ),
    ("INFILTRATOR", "TIMED CROSSING"): contract(
        prerequisites=(prerequisite("brief_gap_against_intermittent_observed_patrol_or_sensor", "crossing a briefly exposed gap against an intermittent observed patrol or sensor"), prerequisite("real_timing_window_exists", "a real timing window must exist")),
        constraints=(constraint("continuous_observation_defeated", "Continuous observation still sees the character", expected=False, enforcement="GUARD"),),
    ),
    ("INFILTRATOR", "QUIET HANDS"): contract(
        prerequisites=(prerequisite("ordinary_object_manipulated_while_infiltrating", "manipulating ordinary physical objects while infiltrating"),),
        constraints=(constraint("required_skill_or_tool_waived", "Relevant skills/tools remain required where appropriate.", expected=False, enforcement="INVARIANT"),),
    ),
    ("INFILTRATOR", "GEAR CONTROL"): contract(
        prerequisites=(prerequisite("one_bulky_or_awkward_carried_mission_item_chosen", "Choose one bulky or awkward mission item being carried."),),
        constraints=(constraint("weight_or_occupied_hands_ignored", "Weight and occupied hands remain real.", expected=False, enforcement="INVARIANT"),),
    ),
    ("INFILTRATOR", "CLOSE PASS"): contract(
        prerequisites=(prerequisite("passing_close_to_unaware_observer_outside_direct_los", "passing close to an unaware observer while remaining outside direct LOS"),),
        constraints=(constraint("direct_sight_contact_or_impossible_concealment_remains_revealing", "Direct sight, contact, or an impossible concealment situation still reveals the infiltrator.", enforcement="GUARD"),),
    ),
    ("INFILTRATOR", "DISTRACTION WINDOW"): contract(
        prerequisites=(prerequisite("real_distraction_materially_shifts_attention", "a real distraction materially shifts an observer's attention away from the APPROACH"), prerequisite("next_stealth_movement_against_observer", "the next STEALTH movement against that observer")),
    ),
    ("INFILTRATOR", "CLEAN ENTRY"): contract(
        prerequisites=(prerequisite("quiet_non_destructive_obstacle_defeat_with_appropriate_skill_equipment", "quietly defeating a lock, access mechanism, window, fence, or similar non-destructive obstacle using appropriate skill/equipment"),),
        constraints=(constraint("underlying_check_improved", "It does not improve the underlying HACKING, ENGINEERING, or other check.", expected=False, enforcement="INVARIANT"),),
    ),
    ("INFILTRATOR", "SYNCHRONIZED PATTERNS"): contract(
        prerequisites=(prerequisite("multiple_observed_overlapping_patterns", "multiple observed patrols or sensors whose timing overlaps"),),
        constraints=(constraint("unobserved_threats_accounted_for", "Unobserved threats remain unaccounted for.", expected=False, enforcement="INVARIANT"),),
    ),
    ("INFILTRATOR", "LEAVE IT CLEAN"): contract(
        prerequisites=(prerequisite("access_point_or_object_used_or_bypassed", "After using or bypassing an access point, container, device, or similar object"),),
        constraints=(constraint("physically_obvious_damage_hidden", "physically obvious damage cannot be hidden", expected=False, enforcement="GUARD"),),
    ),
    ("INFILTRATOR", "SILENT REMOVAL"): contract(
        prerequisites=(prerequisite("quiet_successful_removal_of_unaware_isolated_target", "After successfully incapacitating or CONTROLLING an unaware and isolated target using an appropriately quiet method"),),
        constraints=(constraint("underlying_cqc_roll_improved", "The underlying CQC roll is not improved", expected=False, enforcement="INVARIANT"), constraint("loud_weapon_becomes_quiet", "loud weapons remain loud", expected=False, enforcement="INVARIANT")),
    ),
    ("INFILTRATOR", "GUIDE THROUGH"): contract(
        prerequisites=(prerequisite("nearby_ally_following_approach", "One nearby ally following the APPROACH"),),
        constraints=(constraint("ally_uses_own_stealth_and_can_compromise", "The ally still uses their own STEALTH and can compromise the infiltration.", enforcement="INVARIANT"),),
    ),
    ("INFILTRATOR", "ADAPTIVE PLAN"): contract(
        prerequisites=(prerequisite("one_approach_part_changed", "When one part of the APPROACH changes"),),
        constraints=(constraint("unknown_security_layer_bypasses_reconnaissance", "A completely unknown security layer still requires actual reconnaissance.", expected=False, enforcement="GUARD"),),
    ),
    ("INFILTRATOR", "WORK IN THE SHADOWS"): contract(
        prerequisites=(prerequisite("concealed_at_infiltration_position_performing_stationary_task", "While remaining concealed at an infiltration position"),),
        constraints=(constraint("other_task_gains_bonus", "The task itself gains no bonus.", expected=False, enforcement="INVARIANT"),),
    ),
    ("INFILTRATOR", "PHANTOM APPROACH"): contract(
        prerequisites=(prerequisite("following_prepared_approach", "While following a prepared APPROACH"), prerequisite("stealth_failed_before_direct_confirmation", "if a STEALTH check fails before an observer has directly confirmed the infiltrator's position"), prerequisite("last_concealed_position_physically_reachable", "last physically reachable concealed position")),
        constraints=(constraint("committed_ap_refunded", "The committed time/AP is still spent", expected=False, enforcement="INVARIANT"), constraint("suspicion_or_indirect_information_erased", "defenders gain any suspicion or indirect information", expected=False, enforcement="INVARIANT"), constraint("direct_confirmation_rewound", "Direct confirmation cannot be rewound.", expected=False, enforcement="GUARD")),
    ),
    ("LOCAL HACKER", "SERVICE ACCESS"): contract(
        prerequisites=(prerequisite("initial_get_in_through_genuine_direct_local_access", "The initial GET IN attempt made through genuine direct/local access"),),
        constraints=(constraint("interface_or_missing_hardware_bypassed", "It does not create an interface, bypass missing hardware", expected=False, enforcement="INVARIANT"), constraint("privilege_beyond_route_or_other_node_access", "grant privilege beyond the compromised route, or provide access to another NODE", expected=False, enforcement="INVARIANT")),
    ),
    ("LOCAL HACKER", "DIAGNOSTIC VIEW"): contract(
        prerequisites=(prerequisite("node_accessed_locally_successfully", "After successfully accessing a NODE locally"),),
        constraints=(constraint("information_absent_from_node_revealed", "Information the NODE does not possess remains unavailable.", expected=False, enforcement="INVARIANT"),),
    ),
    ("LOCAL HACKER", "LOCAL ESCALATION"): contract(
        prerequisites=(prerequisite("go_deeper_through_direct_local_access", "GO DEEPER attempts made through direct/local access"),),
        constraints=(constraint("access_to_other_nodes_granted", "does not provide access to other NODES", expected=False, enforcement="INVARIANT"),),
    ),
    ("LOCAL HACKER", "QUIET CONSOLE"): contract(
        prerequisites=(prerequisite("action_through_genuine_direct_local_access", "actions performed through genuine direct/local access"),),
        constraints=(
            constraint("action_cannot_be_detected", "The action can still be detected", expected=False, enforcement="INVARIANT"),
            constraint("physical_evidence_eliminated", "physical evidence at the interface still exists", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("LOCAL HACKER", "MULTI-FUNCTION ACCESS"): contract(
        prerequisites=(prerequisite("deep_control_successful", "After successfully gaining deep control of a NODE"), prerequisite("second_distinct_function_same_node", "a second distinct function on that same NODE"), prerequisite("both_genuine_node_functions", "both are genuinely functions of that NODE")),
    ),
    ("LOCAL HACKER", "SNAPSHOT"): contract(
        prerequisites=(prerequisite("current_node_access", "currently accessible configuration and state"),),
        constraints=(
            constraint("capture_limited_to_readable_fields", "only fields the current access can read", enforcement="GUARD"),
            constraint("restore_limited_to_supported_writable_fields", "only fields the NODE permits the current control to write", enforcement="GUARD"),
            constraint("storage_available", "storage is still required", enforcement="GUARD"),
        ),
        effects=(effect("capture_action_count", "as one HACKING action", value=1, control=0),),
    ),
    ("LOCAL HACKER", "LOCAL LOCKOUT"): contract(
        prerequisites=(prerequisite("deep_local_control_held", "After taking deep local control"),),
        constraints=(constraint("node_disconnected_without_command", "does not disconnect the NODE unless that is actually commanded", expected=False, enforcement="INVARIANT"), constraint("lockout_survives_removal_reset_reconfiguration_or_defeat", "until the lockout is removed, reset, reconfigured, or control is defeated", expected=False, enforcement="TERMINATION")),
    ),
    ("LOCAL HACKER", "PERSISTENT OVERRIDE"): contract(
        prerequisites=(prerequisite("change_made_through_deep_local_access", "configuration or control change made through deep local access"), prerequisite("node_supports_persistent_configuration", "If the NODE supports persistent configuration")),
        constraints=(constraint("override_survives_proper_remediation_or_destruction", "Proper reset, administrator intervention, replacement configuration, or destruction can still remove it.", expected=False, enforcement="TERMINATION"),),
    ),
    ("LOCAL HACKER", "INTERLOCK BYPASS"): contract(
        prerequisites=(prerequisite("node_contains_software_enforced_interlock", "When a NODE contains a software-enforced operating limit or safety interlock"), prerequisite("deep_local_control_held", "deep local control")),
        constraints=(constraint("hardware_limits_bypassed", "Hardware limits remain", expected=False, enforcement="INVARIANT"), constraint("unsafe_operation_consequences_suppressed", "unsafe operation may produce heat, wear, failure, or physical damage", expected=False, enforcement="INVARIANT")),
        effects=(effect("software_interlock_bypassed", "deep local control can temporarily bypass it", value=True, control=False),),
    ),
    ("LOCAL HACKER", "RAPID RECONFIGURE"): contract(
        prerequisites=(prerequisite("deep_control_established", "Once deep control is established"), prerequisite("both_configurations_already_established", "two configurations already established by the hacker")),
    ),
    ("LOCAL HACKER", "HARD LOCKOUT"): contract(
        prerequisites=(prerequisite("deep_control_held", "With deep control"), prerequisite("node_has_lockout_authority", "the NODE has authority to perform")),
        constraints=(
            constraint("lockout_choices_stack", "The choices do not stack", expected=False, enforcement="INVARIANT"),
            constraint("remaining_access_method_valid", "through a method that remains valid", enforcement="GUARD"),
        ),
        effects=(effect("affected_users_must_reestablish_access", "Affected users must establish access again", value=True, control=False),),
    ),
    ("LOCAL HACKER", "SCRIPTED ROUTINE"): contract(
        prerequisites=(prerequisite("simple_routine_uses_existing_node_functions", "one simple conditional routine using functions the NODE already controls"),),
        constraints=(constraint("sensors_actuators_permissions_or_capabilities_invented", "cannot invent sensors, actuators, permissions, or capabilities", expected=False, enforcement="INVARIANT"),),
    ),
    ("LOCAL HACKER", "LOCAL COUNTER-INTRUSION"): contract(
        prerequisites=(prerequisite("deep_local_control_maintained", "while maintaining deep local control"), prerequisite("specific_remote_condition_declared", "Declare a remote-control or intrusion condition affecting that NODE"), prerequisite("incoming_action_passes_through_or_affects_node", "must actually pass through or affect the controlled NODE")),
    ),
    ("LOCAL HACKER", "ROOT ON THE BOX"): contract(
        prerequisites=(prerequisite("deep_local_control_achieved", "Once deep local control is achieved"),),
        constraints=(
            constraint("replacing_one_slot_replaces_other", "replacing one does not replace the other", expected=False, enforcement="INVARIANT"),
            constraint("slots_share_remediation_limits", "Each slot retains its own ordinary trigger, capability, persistence, and remediation limits", expected=False, enforcement="INVARIANT"),
            constraint("controls_more_than_one_machine", "still controls one machine only", expected=False, enforcement="INVARIANT"),
            constraint("crosses_absent_link_or_exceeds_hardware", "cannot cross an absent LINK, exceed hardware", expected=False, enforcement="INVARIANT"),
            constraint("prevents_destruction_or_remediation", "prevent physical destruction, or make remediation impossible", expected=False, enforcement="INVARIANT"),
        ),
        effects=(
            effect("persistent_override_slot", "one PERSISTENT OVERRIDE slot", value=True, control=False),
            effect("scripted_routine_slot", "one SCRIPTED ROUTINE slot", value=True, control=False),
        ),
    ),
    ("NETWORKER", "MAP THE LINKS"): contract(
        prerequisites=(prerequisite("get_in_successful_on_networked_node", "After successfully GETTING IN to a networked NODE"),),
        constraints=(constraint("hidden_or_unseen_topology_revealed", "Hidden paths, isolated segments, undisclosed gateways, and things the NODE cannot see remain unknown.", expected=False, enforcement="INVARIANT"),),
    ),
    ("NETWORKER", "PIVOT"): contract(
        prerequisites=(
            prerequisite(
                "first_get_in_attempt",
                "The first GET IN attempt",
            ),
            prerequisite(
                "immediately_connected_discovered_node",
                "an immediately connected discovered NODE",
            ),
            prerequisite(
                "controlled_origin_node",
                "through a NODE already controlled",
            ),
            prerequisite(
                "link_exists_and_permits_traffic",
                "The LINK must actually exist and permit traffic.",
            ),
        ),
    ),
    ("NETWORKER", "QUIET ENUMERATION"): contract(
        prerequisites=(prerequisite("network_discovery_or_topology_action", "network discovery and topology-mapping actions"),),
        constraints=(constraint("undetectable", "not undetectable", expected=False, enforcement="INVARIANT"),),
    ),
    ("NETWORKER", "CREDENTIAL SWEEP"): contract(
        prerequisites=(prerequisite("compromised_node_exposes_authentication_material", "When a compromised NODE exposes credentials, tokens, session material, or equivalent access data"),),
        constraints=(
            constraint("candidate_scope_exceeds_known_directly_reachable_systems", "known directly reachable systems", expected=False, enforcement="GUARD"),
            constraint("credential_tested", "does not test the credential", expected=False, enforcement="INVARIANT"),
            constraint("credential_validity_proved", "or prove that it works", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("NETWORKER", "FOOTHOLD"): contract(
        prerequisites=(prerequisite("remote_node_compromised_through_valid_route", "After compromising a remotely reached NODE through a valid NETWORK route"),),
        constraints=(constraint("final_ap_below_one", "to a minimum final cost of 1 AP", expected=False, enforcement="INVARIANT"), constraint("route_access_monitoring_detection_persistence_and_remediation_changed", "The route, access prerequisites, monitoring, detection, persistence method, and remediation requirements are unchanged.", expected=False, enforcement="INVARIANT"), constraint("foothold_survives_proper_remediation_or_destruction", "Proper remediation, reset, credential change, reimage, discovery/removal, or destruction can defeat the foothold.", expected=False, enforcement="TERMINATION")),
    ),
    ("NETWORKER", "SEGMENT MAP"): contract(
        prerequisites=(prerequisite("one_segment_node_compromised", "After compromising one NODE in a network SEGMENT"),),
        constraints=(constraint("systems_not_discoverable_from_actual_position_revealed", "Only systems discoverable from the actual network position are revealed.", expected=False, enforcement="GUARD"),),
    ),
    ("NETWORKER", "TOKEN RELAY"): contract(
        prerequisites=(prerequisite("valid_auth_material_for_discovered_node", "authentication material genuinely valid for another discovered NODE"),),
        constraints=(constraint("invalid_credentials_become_valid", "Invalid credentials remain invalid.", expected=False, enforcement="GUARD"),),
    ),
    ("NETWORKER", "GATEWAY PIVOT"): contract(
        prerequisites=(prerequisite("compromised_gateway_connects_two_segments", "After compromising a GATEWAY that genuinely connects two SEGMENTS"),),
        constraints=(constraint("hidden_beyond_entry_topology_revealed", "Hidden paths and systems beyond that entry-facing topology remain unknown", expected=False, enforcement="INVARIANT"),),
        effects=(
            effect("far_side_links_revealed", "reveal the far-side LINKS", value=True, control=False),
            effect("immediate_neighboring_nodes_revealed", "immediate neighboring NODES that the GATEWAY itself can identify", value=True, control=False),
        ),
    ),
    ("NETWORKER", "PARALLEL PROBE"): contract(
        prerequisites=(prerequisite("multiple_targets_directly_reachable", "additional directly reachable NODES or LINKS"),),
        constraints=(constraint("automatic_detection", "the increase is not automatic DETECTION", expected=False, enforcement="INVARIANT"),),
    ),
    ("NETWORKER", "ROUTE PERSISTENCE"): contract(
        prerequisites=(
            prerequisite("one_intermediate_link_lost", "If one intermediate LINK in a current route is lost"),
            prerequisite("previously_discovered_valid_alternate_route", "a previously discovered valid alternate route"),
        ),
        constraints=(
            constraint("alternate_reaches_same_compromised_node", "the alternate route reaches the same compromised NODE", enforcement="GUARD"),
            constraint("required_session_or_foothold_valid", "required session or FOOTHOLD remains valid", enforcement="GUARD"),
        ),
    ),
    ("NETWORKER", "CASCADE ACCESS"): contract(
        prerequisites=(
            prerequisite("nodes_share_genuine_common_exploitable_basis", "multiple NODES genuinely share the same authentication authority, security flaw, configuration mistake, or other exploitable common basis"),
            prerequisite("one_node_successfully_compromised", "successfully compromising one"),
            prerequisite("shared_weakness_still_applies_to_target", "The shared weakness must actually exist and still apply to the target"),
        ),
        constraints=(constraint("cascade_benefits_stack", "the benefits do not stack", expected=False, enforcement="INVARIANT"),),
    ),
    ("NETWORKER", "NETWORK INTERCEPT"): contract(
        prerequisites=(prerequisite("controlled_node_carries_relevant_traffic", "controlling a NODE that actually carries relevant traffic"), prerequisite("specific_observable_condition_declared", "Declare a specific observable network transaction/communication condition"), prerequisite("matching_traffic_crosses_controlled_node", "when matching traffic crosses the controlled NODE"), prerequisite("access_and_protocol_security_allow_response", "where access and protocol/security allow")),
        constraints=(constraint("traffic_on_other_route_affected", "Traffic taking another route is unaffected.", expected=False, enforcement="GUARD"),),
    ),
    ("NETWORKER", "REROUTE"): contract(
        prerequisites=(prerequisite("known_link_blocked_monitored_or_unavailable", "a known LINK becomes blocked, monitored, or unavailable"), prerequisite("alternate_route_discovered", "a discovered alternate route")),
        constraints=(
            constraint("replacement_uses_active_known_links", "through active known LINKS", enforcement="GUARD"),
            constraint("replacement_reaches_same_compromised_nodes", "same compromised NODES the replacement route genuinely reaches", enforcement="GUARD"),
            constraint("unreached_sessions_retained", "Unreached sessions end normally", expected=False, enforcement="TERMINATION"),
            constraint("surviving_footholds_invalidated", "surviving FOOTHOLDS remain available under their own rules", expected=False, enforcement="INVARIANT"),
        ),
    ),
    ("NETWORKER", "OWN THE TOPOLOGY"): contract(
        prerequisites=(prerequisite("infrastructure_already_discovered", "discovered NODES, LINKS, SEGMENTS, and GATEWAYS"),),
        constraints=(
            constraint("reachability_from_surviving_control_or_foothold", "from surviving controlled NODES and valid FOOTHOLDS through active known LINKS", enforcement="GUARD"),
            constraint("topology_knowledge_cannot_become_outdated", "Knowledge can become outdated", expected=False, enforcement="INVARIANT"),
            constraint("air_gaps_boundaries_missing_links_disconnected_hardware_sessions_and_remediated_footholds_ignored", "air gaps, uncompromised boundaries, missing LINKS, disconnected hardware, expired sessions, and remediated FOOTHOLDS remain real limits", expected=False, enforcement="INVARIANT"),
        ),
        effects=(effect("topology_knowledge_persists", "persistent operational-map knowledge", value=True, control=False),),
    ),
    ("OPERATOR", "WEAPON MANIPULATION"): contract(
        prerequisites=(prerequisite("ordinary_firearm_manipulation", "ordinary firearm manipulation"),),
        constraints=(constraint("required_physical_step_skipped", "It never skips a physical step the firearm actually requires.", expected=False, enforcement="INVARIANT"),),
    ),
    ("OPERATOR", "RETENTION RELOAD"): contract(
        prerequisites=(prerequisite("partially_loaded_magazine_retained", "Retaining a partially loaded magazine during a reload"),),
        constraints=(constraint("magazine_disappears_into_inventory", "physically placed somewhere accessible rather than disappearing into inventory", expected=False, enforcement="INVARIANT"),),
    ),
    ("OPERATOR", "QUICK CYCLE"): contract(
        prerequisites=(prerequisite("manual_cycling_operation", "Manual cycling actions such as bolt, pump, lever, or charging-handle operations"),),
        constraints=(constraint("required_cycle_skipped", "you still perform the cycle", expected=False, enforcement="INVARIANT"),),
    ),
    ("OPERATOR", "TRANSITION DRILL"): contract(
        prerequisites=(prerequisite("transition_between_accessible_firearms", "Transitioning between a slung or holstered firearm and another accessible firearm"),),
        constraints=(constraint("other_weapon_carried_reachably", "The other weapon must actually be carried somewhere reachable.", enforcement="GUARD"),),
    ),
    ("OPERATOR", "ONE-HANDED MANIPULATION"): contract(
        prerequisites=(prerequisite("one_handed_procedure_physically_possible", "physically possible one-handed FIREARM MANIPULATION"),),
        constraints=(constraint("final_cost_below_normal_two_handed_cost", "The final cost cannot be lower than the ordinary two-handed cost.", expected=False, enforcement="INVARIANT"), constraint("ability_creates_baseline_permission", "This does not create permission that other characters lack", expected=False, enforcement="INVARIANT"), constraint("literal_two_hand_operation_without_workaround", "cannot perform an operation that literally requires two hands with no physical workaround", expected=False, enforcement="GUARD")),
    ),
    ("OPERATOR", "SHOULDER SWITCH"): contract(
        constraints=(constraint("character_and_firearm_position_unchanged", "The character and firearm still physically change position.", expected=False, enforcement="INVARIANT"),),
    ),
    ("OPERATOR", "SELECTIVE LOAD"): contract(
        prerequisites=(prerequisite("different_magazine_or_ammunition_type_selected", "Changing to a different magazine or ammunition type"),),
        constraints=(constraint("ammunition_carried_and_compatible", "The ammunition must already be carried and compatible.", enforcement="GUARD"),),
    ),
    ("OPERATOR", "CYCLE ON THE RETURN"): contract(
        prerequisites=(prerequisite("firearm_manually_cycled", "With a manually cycled firearm"),),
        constraints=(constraint("chamber_state_updates_normally", "The chamber state still changes normally", enforcement="INVARIANT"),),
    ),
    ("OPERATOR", "EMPTY? SWITCH"): contract(
        prerequisites=(prerequisite("current_firearm_unable_and_replacement_accessible", "If the current firearm becomes unable to fire because it is empty, damaged, or otherwise unusable, transitioning to another accessible firearm"),),
        constraints=(constraint("replacement_weapon_state_preserved", "The replacement weapon has whatever actual state it already had.", enforcement="INVARIANT"),),
    ),
    ("OPERATOR", "MALFUNCTION DRILL"): contract(
        prerequisites=(prerequisite("explicit_malfunction_exists", "When an explicit MALFUNCTION occurs"),),
        constraints=(constraint("random_jam_introduced", "This does not introduce random jams", expected=False, enforcement="INVARIANT"), constraint("malfunction_has_real_cause", "already exists because of damage, contamination, bad ammunition, poor equipment, or another explicit cause", enforcement="GUARD")),
    ),
    ("OPERATOR", "COMPOUND RELOAD"): contract(
        prerequisites=(prerequisite("complete_reload_and_physically_compatible_steps", "During a complete reload, physically compatible steps may overlap"),),
        constraints=(constraint("firearm_state_updates_stepwise", "Firearm state still updates step by step", enforcement="INVARIANT"), constraint("interruption_state_equals_actual_reached_state", "interruption leaves the gun in the state actually reached", enforcement="INVARIANT")),
    ),
    ("OPERATOR", "FIGHTING HANDS"): contract(
        prerequisites=(prerequisite("other_handheld_object_immediately_accessible", "another immediately accessible hand-held object"),),
        constraints=(constraint("other_class_specialty_granted", "does not grant another class's specialty abilities", expected=False, enforcement="INVARIANT"),),
    ),
    ("OPERATOR", "MANUAL-ACTION MASTER"): contract(
        prerequisites=(prerequisite("firearm_manually_operated", "Bolt-, pump-, lever-, and other manually operated firearms"),),
        constraints=(constraint("mechanism_or_ammunition_state_changed", "Their actual mechanisms and ammunition states remain unchanged.", expected=False, enforcement="INVARIANT"),),
    ),
    ("OPERATOR", "NO WASTED MOTION"): contract(
        prerequisites=(prerequisite("manipulation_steps_physically_simultaneous", "When firearm-manipulation steps can physically occur at the same time"),),
        constraints=(constraint("incompatible_steps_overlap", "Operations that physically cannot happen simultaneously still cannot overlap", expected=False, enforcement="GUARD"), constraint("firearm_real_state_tracked", "the firearm's real state is tracked throughout the sequence", enforcement="INVARIANT")),
    ),
    ("RESUSCITATOR", "RESUSCITATION"): contract(
        prerequisites=(prerequisite("valid_resuscitation_begun_before_window_expiry", "If you begin a valid RESUSCITATION before the condition's reversible window expires"), prerequisite("continuous_appropriate_care_maintained", "Continuous appropriate care must be maintained.")),
        constraints=(constraint("real_end_to_reversibility_can_close_window", "Interruption, impossible physiology, equipment loss, further catastrophic injury, or another real end to reversibility can still close the window.", enforcement="TERMINATION"),),
    ),
    ("RESUSCITATOR", "AIRWAY FIRST"): contract(
        prerequisites=(prerequisite("active_condition_includes_airway_or_breathing_compromise", "airway or breathing compromise is part of an ACTIVE LIFE-THREATENING CONDITION"),),
        constraints=(constraint("required_intervention_set_by_actual_obstruction_or_injury", "The actual obstruction/injury still determines the required intervention.", enforcement="GUARD"),),
    ),
    ("RESUSCITATOR", "MASSIVE BLEEDING"): contract(
        prerequisites=(prerequisite("catastrophic_hemorrhage", "catastrophic hemorrhage"),),
        constraints=(constraint("uncontrollable_source_overridden", "If the bleeding source cannot physically be controlled with available treatment, the ability cannot override that fact.", expected=False, enforcement="GUARD"),),
    ),
    ("RESUSCITATOR", "RAPID ASSESSMENT"): contract(
        prerequisites=(prerequisite("downed_casualty_on_deaths_door", "a DOWNED casualty on DEATH'S DOOR"),),
        constraints=(constraint("identified_condition_detectable", "most immediately lethal detectable LIFE-THREATENING CONDITION", enforcement="GUARD"),),
    ),
    ("RESUSCITATOR", "SUSTAIN LIFE"): contract(
        prerequisites=(prerequisite("critical_condition_successfully_stabilized", "After successfully STABILIZING a critical condition"),),
        constraints=(constraint("new_injury_or_intervention_failure_can_reactivate", "New injury, loss of the actual intervention, or failure of the treatment can still return the condition to ACTIVE where appropriate.", enforcement="TERMINATION"),),
    ),
    ("RESUSCITATOR", "CONTINUOUS CARE"): contract(
        prerequisites=(prerequisite("casualty_requires_ongoing_manual_care", "a critical casualty requires ongoing manual treatment or monitoring"),),
        constraints=(constraint("medic_remains_physically_occupied", "The medic remains physically occupied by the patient.", enforcement="INVARIANT"),),
    ),
    ("RESUSCITATOR", "RESUSCITATION KIT"): contract(
        prerequisites=(prerequisite("specialized_emergency_equipment_used", "Specialized emergency equipment"),),
        constraints=(constraint("bulk_charges_consumables_and_prerequisites_preserved", "Bulk, charges, consumables, and prerequisites remain.", enforcement="INVARIANT"),),
    ),
    ("RESUSCITATOR", "RETURN OF CIRCULATION"): contract(
        prerequisites=(prerequisite("reversible_failed_compatible_vital_condition", "a reversible FAILED LIFE-THREATENING CONDITION involving circulation or another equivalent failed vital function"), prerequisite("resuscitation_successful", "successful RESUSCITATION")),
        constraints=(constraint("incompatible_causes_affected", "Incompatible causes are unaffected.", expected=False, enforcement="GUARD"),),
    ),
    ("RESUSCITATOR", "RESTORE CONSCIOUSNESS"): contract(
        prerequisites=(prerequisite("resuscitation_successful", "After successful RESUSCITATION"), prerequisite("physiology_and_injuries_permit_consciousness", "physiology and INJURIES genuinely permit consciousness")),
        constraints=(constraint("normal_full_turn_guaranteed", "They may still remain DOWNED at 0 HP and unable to take a normal full AP turn.", expected=False, enforcement="INVARIANT"),),
    ),
    ("RESUSCITATOR", "CRASH INTERVENTION"): contract(
        prerequisites=(prerequisite("treated_casualty_known_condition_worsens", "a casualty currently being treated suddenly deteriorates because a known LIFE-THREATENING CONDITION worsens or returns to ACTIVE"), prerequisite("next_intervention_appropriate", "the next appropriate emergency intervention")),
    ),
    ("RESUSCITATOR", "MOVE THE CRITICAL"): contract(
        prerequisites=(prerequisite("stabilized_downed_transport_under_supervision", "A STABILIZED DOWNED casualty being transported under the RESUSCITATOR's supervision"),),
        constraints=(constraint("violent_movement_new_injury_or_intervention_failure_ignored", "Violent movement, new injury, loss of airway, lost hemorrhage control, or equivalent real failure still matters.", expected=False, enforcement="TERMINATION"),),
    ),
    ("RESUSCITATOR", "BRIDGE TO TRAUMA"): contract(
        prerequisites=(prerequisite("casualty_successfully_resuscitated_or_stabilized", "After successfully RESUSCITATING/STABILIZING a casualty"), prerequisite("first_definitive_treatment", "the first TRAUMA or other definitive medical treatment performed on them")),
        effects=(effect("another_medic_may_receive_benefit", "This can benefit another medic.", value=True, control=False),),
    ),
    ("RESUSCITATOR", "REFUSE DETERIORATION"): contract(
        prerequisites=(prerequisite("actively_caring_for_active_uncontrolled_lethal_condition", "While actively providing critical care to an ACTIVE LIFE-THREATENING CONDITION whose lethal cause cannot yet be fully controlled"),),
        constraints=(constraint("physically_unavoidable_failure_prevented", "Conditions that physically progress too quickly, treatment that becomes impossible, or new catastrophic injury can still force failure.", expected=False, enforcement="TERMINATION"),),
    ),
    ("RESUSCITATOR", "NOT YET"): contract(
        prerequisites=(prerequisite("downed_casualty_has_reversible_path", "a DOWNED casualty still has a physically reversible path to survival"),),
        constraints=(constraint("unsurvivable_or_irreversible_cases_included", "Unsurvivable destruction, missing anatomy required for life, conditions beyond available medical technology, and casualties whose reversible window has genuinely passed remain outside the ability.", expected=False, enforcement="GUARD"),),
    ),
    ("SENTINEL", "PREPARED SIGHTLINE"): contract(
        prerequisites=(prerequisite("stationary", "When stationary"),),
        constraints=(constraint("overwatch_zone_and_valid_los", "OVERWATCH still requires a declared zone and valid LOS.", enforcement="GUARD"),),
    ),
    ("SENTINEL", "BROAD SECTOR"): contract(
        constraints=(constraint("zone_connected", "larger connected area than normal", enforcement="GUARD"), constraint("blocking_geometry_divides_sector", "Walls and other blocking geometry still divide the sector.", enforcement="INVARIANT")),
    ),
    ("SENTINEL", "STABLE NEST"): contract(
        prerequisites=(prerequisite("overwatch_from_physically_supported_position", "firing OVERWATCH from a physically supported position"),),
        constraints=(constraint("benefit_survives_leaving_support", "Leaving the support removes the benefit.", expected=False, enforcement="TERMINATION"),),
    ),
    ("SENTINEL", "FAST SETUP"): contract(
        prerequisites=(prerequisite("stationary_and_not_moved_this_activation", "from a stationary position costs less AP when the SENTINEL has not moved since beginning the current activation"),),
    ),
    ("SENTINEL", "TRACKING SECTOR"): contract(
        prerequisites=(prerequisite("existing_zone_to_adjacent_connected_area", "an existing OVERWATCH ZONE into an adjacent connected area"),),
        constraints=(constraint("los_into_new_sector", "LOS into the new sector is still required.", enforcement="GUARD"),),
    ),
    ("SENTINEL", "CROSSING SHOT"): contract(
        prerequisites=(prerequisite("trigger_is_movement_through_watched_zone", "triggered specifically by movement through the watched zone"),),
    ),
    ("SENTINEL", "SECOND TRIGGER"): contract(
        prerequisites=(prerequisite("overwatch_shot_resolved", "After resolving an OVERWATCH shot"), prerequisite("another_trigger_valid", "another valid trigger")),
        constraints=(constraint("ammo_and_recoil_normal", "every shot still consumes ammunition and generates RECOIL normally", enforcement="INVARIANT"),),
    ),
    ("SENTINEL", "COUNTER-PREP"): contract(
        prerequisites=(prerequisite("enemy_exposes_in_zone_to_establish_prep", "an enemy in the watched area exposes themselves to establish AIM, OVERWATCH, or another firing PREP ACTION"),),
        constraints=(constraint("entirely_full_cover_action_triggers", "It does not trigger on actions performed entirely behind full COVER.", expected=False, enforcement="GUARD"),),
    ),
    ("SENTINEL", "SUPPRESSIVE WATCH"): contract(
        prerequisites=(prerequisite("overwatch_shot_resolved_and_additional_ammo_committed", "After resolving an OVERWATCH shot, commit additional ammunition"),),
        constraints=(constraint("geometry_and_firearm_support_suppression", "Geometry and firearm must support the suppression.", enforcement="GUARD"),),
    ),
    ("SENTINEL", "LINKED SECTOR"): contract(
        prerequisites=(prerequisite("sentinel_overwatch_already_established", "already has OVERWATCH established"), prerequisite("ally_zone_adjacent_or_overlapping", "an adjacent or overlapping OVERWATCH ZONE")),
        constraints=(constraint("ally_uses_own_weapon_los_ap_and_prep", "The ally still uses their own weapon, LOS, AP, and PREP ACTION.", enforcement="INVARIANT"),),
    ),
    ("SENTINEL", "HOLD UNDER FIRE"): contract(
        prerequisites=(prerequisite("same_enemy_action_triggers_overwatch_and_suppression", "the same enemy firing action both triggers your OVERWATCH and would establish or resolve SUPPRESSION against you"),),
        constraints=(constraint("suppression_cancelled_only_if_enemy_loses_ability", "If the result removes the enemy's ability to establish the fire, it never begins; otherwise SUPPRESSION resolves normally.", enforcement="INVARIANT"),),
    ),
    ("SENTINEL", "DEEP SECTOR"): contract(
        prerequisites=(prerequisite("sightline_continuous", "along a continuous sightline"),),
        constraints=(constraint("sees_around_corners", "not the ability to see around corners", expected=False, enforcement="INVARIANT"),),
    ),
    ("SENTINEL", "CONTINUOUS WATCH"): contract(
        prerequisites=(prerequisite("overwatch_retained", "Retained OVERWATCH"),),
        constraints=(constraint("ammo_magazine_recoil_and_cycling_preserved", "Every shot still deals with ammunition, magazine state, RECOIL, and actual weapon cycling.", enforcement="INVARIANT"),),
    ),
    ("SENTINEL", "KILL ZONE"): contract(
        prerequisites=(prerequisite("connected_areas_in_plausible_field_of_fire", "multiple connected watched areas within one physically plausible field of fire"),),
        constraints=(constraint("suppression_immunity", "does **not** gain a special immunity to SUPPRESSION", expected=False, enforcement="INVARIANT"), constraint("one_gun_and_position_preserved", "still has one gun, one physical position", enforcement="INVARIANT"), constraint("incompatible_threats_or_blocking_geometry_ignored", "cannot answer simultaneous incompatible threats or shoot through blocking geometry", expected=False, enforcement="INVARIANT")),
    ),
    ("SHIELD BEARER", "SHIELD TRAINING"): contract(
        prerequisites=(prerequisite("shield_deployed_for_movement_or_compatible_action", "While the shield is deployed, its effective BURDEN and HANDLING are reduced for your movement and compatible one-handed actions"),),
        constraints=(constraint("physical_shield_properties_changed", "Its real weight, size, directional geometry, durability, flankability, and MATERIAL PENETRATION interaction do not change", expected=False, enforcement="INVARIANT"),),
    ),
    ("SHIELD BEARER", "BRACE SHIELD"): contract(
        prerequisites=(prerequisite("stationary_with_shield_deployed", "While stationary with the shield deployed"),),
        constraints=(constraint("voluntary_movement_breaks_brace", "Voluntary movement breaks the brace.", enforcement="TERMINATION"),),
    ),
    ("SHIELD BEARER", "SHIELD CHECK"): contract(
        prerequisites=(prerequisite("shield_deployed", "using the deployed shield"),),
        constraints=(constraint("shield_remains_deployed_unless_position_or_facing_changes", "The shield remains deployed unless the action itself changes your position or facing.", enforcement="TERMINATION"),),
    ),
    ("SHIELD BEARER", "INDEXED RELOAD"): contract(
        prerequisites=(prerequisite("compatible_one_handed_firearm", "a compatible one-handed firearm"),),
        constraints=(constraint("reload_sequence_and_ammo_preserved", "The physical reload sequence and ammunition still matter", enforcement="INVARIANT"), constraint("protected_geometry_may_narrow", "your protected geometry may narrow while the weapon hand is occupied", enforcement="INVARIANT")),
    ),
    ("SHIELD BEARER", "CORNER GUARD"): contract(
        prerequisites=(prerequisite("adjacent_corner_paid_prep_declared_direction", "While adjacent to a solid corner, establish a shield-facing PREP ACTION against one declared direction"), prerequisite("threat_first_enters_real_line", "When a threat first enters a real line from that direction")),
        constraints=(constraint("free_awareness_or_unseen_protection", "not free awareness or protection from unseen directions", expected=False, enforcement="INVARIANT"),),
    ),
    ("SHIELD BEARER", "CONTROLLED RETREAT"): contract(
        prerequisites=(prerequisite("forced_movement_would_break_position", "an impact or forced-movement effect would break your position"), prerequisite("valid_retreat_space_exists", "if a physically valid retreat space exists")),
        constraints=(constraint("displacement_prevented", "You are still displaced", expected=False, enforcement="INVARIANT"),),
        effects=(effect("facing_and_posture_loss_automatic", "prevents displacement from automatically becoming loss of facing and posture", value=False, control=True),),
    ),
    ("SHIELD BEARER", "STEP TOGETHER"): contract(
        prerequisites=(prerequisite("adjacent_ally_and_both_spend_movement", "you and an adjacent ally each spend movement AP to advance together behind the shield"), prerequisite("stance_change_physically_valid", "one physically valid STANCE change")),
        constraints=(constraint("slower_rate_and_grid_spaces", "Both characters move at the slower applicable rate and occupy real GRID spaces.", enforcement="INVARIANT"),),
    ),
    ("SHIELD BEARER", "CROSSING SCREEN"): contract(
        prerequisites=(prerequisite("adjacent_ally_crosses_exposed_ground", "When an adjacent ally crosses exposed ground"), prerequisite("bearer_spends_movement_and_declares_direction", "spend your own movement/AP to move with them and keep the shield between them and one declared threat direction")),
        constraints=(constraint("screen_ends_if_pace_or_angle_lost", "The screen ends if you cannot physically keep pace or maintain the angle.", enforcement="TERMINATION"),),
    ),
    ("SHIELD BEARER", "SHIELDED RECOVERY"): contract(
        prerequisites=(prerequisite("ally_impaired_prone_or_downed", "an impaired, prone, or DOWNED ally"),),
        constraints=(constraint("burden_and_hands_preserved", "The burden constrains movement and occupies the relevant hands", enforcement="INVARIANT"), constraint("medical_treatment_provided", "not medical treatment", expected=False, enforcement="INVARIANT")),
    ),
    ("SHIELD BEARER", "IMPACT CONTROL"): contract(
        prerequisites=(prerequisite("impact_alters_geometry_and_fiction_permits_choice", "an impact against the shield is strong enough to alter your defensive geometry"),),
        constraints=(constraint("impact_consequences_removed", "The impact still has real consequences.", expected=False, enforcement="INVARIANT"),),
    ),
    ("SHIELD BEARER", "BREACH SCREEN"): contract(
        prerequisites=(prerequisite("transition_already_usable_and_narrow", "an already usable doorway, opening, or similarly narrow exposed transition"),),
        constraints=(constraint("obstacle_breached_or_breacher_capability_granted", "This does not breach obstacles or grant BREACHER capabilities.", expected=False, enforcement="INVARIANT"),),
    ),
    ("SHIELD BEARER", "DRAW THE BURST"): contract(
        prerequisites=(prerequisite("suppression_intercepts_deployed_shield", "a SUPPRESSION ATTACK physically intercepts the deployed shield"),),
        constraints=(constraint("shield_avoids_impact_or_cover_damage", "The shield still receives the real impact and COVER damage.", expected=False, enforcement="INVARIANT"),),
    ),
    ("SHIELD BEARER", "SEAL THE GAP"): contract(
        prerequisites=(prerequisite("cover_destroyed_or_opening_appears", "nearby COVER is destroyed or an opening suddenly appears"), prerequisite("prepared_response_and_reachable_line", "a prepared shield response may let you occupy the newly dangerous line if it is physically reachable")),
        constraints=(constraint("destroyed_object_rebuilt", "you do not rebuild the destroyed object", expected=False, enforcement="INVARIANT"),),
    ),
    ("SHIELD BEARER", "WALKING BASTION"): contract(
        prerequisites=(prerequisite("advancing", "while advancing"),),
        constraints=(constraint("allied_protection_uses_actual_geometry", "Allied protection continues to come from actual shield geometry", enforcement="INVARIANT"), constraint("coordinated_ally_upgrade_assumed", "rather than being assumed for free", expected=False, enforcement="INVARIANT"), constraint("slow_flankable_and_destructible", "remain slow, flankable, and dependent on a physical shield that can be damaged, penetrated, or destroyed", enforcement="INVARIANT")),
        effects=(effect("moving_brace_active", "Maintain a reduced form of BRACED SHIELD while advancing.", value=True, control=False), effect("controlled_facing_or_giving_ground_collapses_moving_brace", "Controlled facing changes or giving ground no longer automatically collapse that moving brace.", value=False, control=True)),
    ),
    ("SHOCK", "ASSAULT MOMENTUM"): contract(
        prerequisites=(prerequisite("specialist_caused_qualifying_disruption", "one of the SHOCK specialist's actions actually causes an enemy to leave COVER, be forcibly repositioned, or places the enemy's currently occupied position under the specialist's active SUPPRESSION"),),
        constraints=(constraint("benefit_bankable", "must be used as immediate exploitation and cannot be banked", expected=False, enforcement="TERMINATION"),),
    ),
    ("SHOCK", "CLOSE SUPPRESSION"): contract(
        prerequisites=(prerequisite("close_engagement_distance", "At close engagement distances"),),
        constraints=(constraint("suitable_firearm_and_real_line", "A suitable firearm and real firing line are still required.", enforcement="GUARD"),),
    ),
    ("SHOCK", "EXPLOIT EXPOSURE"): contract(
        prerequisites=(prerequisite("specialist_action_worsened_cover_or_angle", "the specialist's action moves an enemy from better COVER to worse COVER or exposes them from a previously protected angle"),),
        constraints=(constraint("new_geometry_applies", "New geometry still applies normally.", enforcement="INVARIANT"),),
    ),
    ("SHOCK", "SHOVE THROUGH"): contract(
        prerequisites=(prerequisite("shove_successfully_vacated_position", "After successfully SHOVING an enemy out of a position"),),
        constraints=(constraint("destination_physically_occupiable", "The destination must be physically occupiable.", enforcement="GUARD"),),
    ),
    ("SHOCK", "ASSAULT WINDOW"): contract(
        prerequisites=(prerequisite("assault_momentum_triggered", "When ASSAULT MOMENTUM triggers"), prerequisite("ally_nearby", "one nearby ally")),
        constraints=(constraint("ally_moves_toward_same_position", "toward the same disrupted position", enforcement="GUARD"),),
    ),
    ("SHOCK", "FOLLOWING FIRE"): contract(
        prerequisites=(prerequisite("enemy_moves_between_nearby_positions_within_solution", "If an enemy leaves the specialist's SUPPRESSION and moves to another nearby position that remains within the firing solution"),),
        constraints=(constraint("los_and_orientation_preserved", "LOS and weapon orientation still matter.", enforcement="GUARD"),),
    ),
    ("SHOCK", "GRENADE FOLLOW-UP"): contract(
        prerequisites=(prerequisite("grenade_caused_qualifying_disruption", "one of the specialist's grenades actually makes an enemy move, change STANCE, or lose useful COVER"),),
        constraints=(constraint("benefit_without_disruption", "A grenade that produces no qualifying disruption grants nothing.", expected=False, enforcement="GUARD"),),
    ),
    ("SHOCK", "COMBINED PRESSURE"): contract(
        prerequisites=(prerequisite("suppression_and_grenade_same_position_either_order", "After establishing SUPPRESSION against a position, a subsequent grenade action against that same defended position costs less AP, or vice versa"),),
        constraints=(constraint("normal_ammunition_and_equipment_consumed", "Both actions still consume their normal ammunition/equipment.", enforcement="INVARIANT"),),
    ),
    ("SHOCK", "MOMENTUM CHAIN"): contract(
        prerequisites=(prerequisite("momentum_used_to_advance_then_new_disruption", "If ASSAULT MOMENTUM is used to advance and the next action from the new position disrupts another enemy position"),),
        constraints=(constraint("failure_ends_chain", "Failure to create a new disruption ends the chain.", enforcement="TERMINATION"),),
    ),
    ("SHOCK", "DISLOCATE"): contract(
        prerequisites=(prerequisite("shove_or_throw_successful", "Successful SHOVE and THROW effects"),),
        constraints=(constraint("cqc_success_and_valid_destination", "The CQC action must still succeed and the destination geometry must be valid.", enforcement="GUARD"),),
    ),
    ("SHOCK", "BREAK THE RHYTHM"): contract(
        prerequisites=(prerequisite("enemy_displaced_or_under_specialist_suppression", "An enemy personally displaced by the specialist, or currently occupying a position under active SUPPRESSION established by the specialist"), prerequisite("relevant_pressure_maintained", "while the specialist continues maintaining relevant pressure")),
        constraints=(constraint("expires_after_turn_or_pressure_loss", "expires after the enemy's next TEAM TURN or when the relevant pressure/firing solution is lost", enforcement="TERMINATION"),),
    ),
    ("SHOCK", "ROLLING ASSAULT"): contract(
        constraints=(constraint("everyone_spends_own_ap", "Everyone still spends their own AP.", enforcement="INVARIANT"),),
        effects=(effect("assault_window_affects_additional_nearby_allies", "ASSAULT WINDOW may affect additional nearby allies", value=True, control=False), effect("successful_momentum_chain_refreshes_allied_movement_benefit", "a successful MOMENTUM CHAIN can refresh their movement benefit toward the new disrupted position", value=True, control=False)),
    ),
    ("SHOCK", "HARD PUSH"): contract(
        prerequisites=(prerequisite("moved_using_momentum", "After moving using ASSAULT MOMENTUM"), prerequisite("next_close_attack_against_responsible_enemy", "the specialist's next close-range firearm attack against an enemy responsible for that momentum")),
        constraints=(constraint("benefit_without_disruption", "No qualifying disruption means no benefit.", expected=False, enforcement="GUARD"),),
    ),
    ("SHOCK", "BREAK THE LINE"): contract(
        prerequisites=(prerequisite("disrupt_advance_disrupt_new_target_sequence", "creates a genuine disruption, advances into that opening, and successfully disrupts a new enemy or position"),),
        constraints=(constraint("chain_end_conditions", "The chain ends on failure, lack of AP/resources, blocked geometry, or no new position to collapse", enforcement="TERMINATION"), constraint("same_target_farming_allowed", "cannot be farmed indefinitely against one target", expected=False, enforcement="GUARD")),
    ),
    ("SNAPSHOT", "QUICK SOLUTION"): contract(
        prerequisites=(prerequisite("first_aim_target_not_attacked_this_turn", "The first AIM performed against a target the SNAPSHOT specialist has not yet attacked during the current TEAM TURN"),),
    ),
    ("SNAPSHOT", "SNAP BRACE"): contract(
        prerequisites=(prerequisite("moving_into_valid_supported_stance", "moving into a physically valid supported firing stance"),),
        constraints=(constraint("position_actually_reached", "The position must still actually be reached.", enforcement="GUARD"),),
    ),
    ("SNAPSHOT", "HASTY ZERO"): contract(
        prerequisites=(prerequisite("first_long_range_attack_after_quick_solution", "The first long-range attack after QUICK SOLUTION"),),
        constraints=(constraint("ballistic_capability_changed", "The firearm's actual ballistic capability is unchanged.", expected=False, enforcement="INVARIANT"),),
    ),
    ("SNAPSHOT", "OPENING WINDOW"): contract(
        prerequisites=(prerequisite("target_cover_changed_earlier_this_turn", "target's COVER is destroyed, reduced, or physically moved away from them earlier in the current TEAM TURN"), prerequisite("first_quick_solution_attack", "the first QUICK-SOLUTION attack against them")),
        constraints=(constraint("new_cover_state_ignored", "Their new COVER state still applies normally.", expected=False, enforcement="INVARIANT"),),
    ),
    ("SNAPSHOT", "TARGET SWITCH"): contract(
        prerequisites=(prerequisite("already_fired_then_first_aim_different_visible_long_range_target", "After firing at one target, the first AIM against a different visible long-range target"),),
    ),
    ("SNAPSHOT", "REACQUIRE"): contract(
        prerequisites=(prerequisite("target_broke_los_and_reappeared_near_previous_position", "a previously engaged target breaks LOS and then reappears near its previous position"),),
        constraints=(constraint("completely_new_position_uses_old_solution", "A completely new position requires a new solution.", expected=False, enforcement="GUARD"),),
    ),
    ("SNAPSHOT", "QUICK CALLED SHOT"): contract(
        prerequisites=(prerequisite("called_shot_after_quick_solution", "making a CALLED SHOT after QUICK SOLUTION"),),
        constraints=(constraint("selected_area_visible", "The selected area must still be visible.", enforcement="GUARD"),),
    ),
    ("SNAPSHOT", "SECOND SIGHT PICTURE"): contract(
        prerequisites=(prerequisite("quick_solution_miss_target_remains_visible", "a QUICK-SOLUTION attack misses and the target remains visible"),),
    ),
    ("SNAPSHOT", "SHOOT AND SHIFT"): contract(
        prerequisites=(prerequisite("long_range_attack_benefited_from_quick_solution", "a long-range attack that benefited from QUICK SOLUTION"),),
        constraints=(constraint("firing_while_moving", "not firing while moving", expected=False, enforcement="INVARIANT"),),
    ),
    ("SNAPSHOT", "NEXT TARGET"): contract(
        prerequisites=(prerequisite("quick_solution_target_removed", "a QUICK-SOLUTION attack DOWNS or otherwise removes the target from the immediate fight"), prerequisite("different_visible_target", "a different visible target")),
    ),
    ("SNAPSHOT", "HASTY FULL SOLUTION"): contract(
        constraints=(constraint("subsequent_shot_against_target", "The benefit applies only to the first shot against that target.", expected=False, enforcement="TERMINATION"),),
        effects=(effect("larger_ordinary_aim_attack_benefit", "QUICK SOLUTION provides a larger portion of the attack benefit normally associated with spending more time on AIM.", value=True, control=False),),
    ),
    ("SNAPSHOT", "SNAP PENETRATION"): contract(
        prerequisites=(prerequisite("first_attack_after_quick_solution_hits", "The first attack after QUICK SOLUTION gains additional **target PENETRATION** on a successful hit"),),
        constraints=(constraint("external_cover_material_penetration_improved", "does not improve MATERIAL PENETRATION through external COVER or walls", expected=False, enforcement="INVARIANT"),),
    ),
    ("SNAPSHOT", "DECISIVE WINDOW"): contract(
        prerequisites=(prerequisite("first_successful_quick_solution_attack_newly_exposed", "The first successful QUICK-SOLUTION attack against a newly exposed target"),),
        constraints=(constraint("target_exposed_all_turn_counts_as_new", "A target already sitting exposed all turn does not count as a newly opened opportunity.", expected=False, enforcement="GUARD"),),
    ),
    ("SNAPSHOT", "INSTANT SOLUTION"): contract(
        prerequisites=(prerequisite("once_per_turn_new_valid_long_range_solution", "Once per TEAM TURN, when acquiring a new valid long-range firing solution"),),
        constraints=(constraint("applies_to_one_shot", "It applies to one shot.", enforcement="TERMINATION"), constraint("continued_fire_uses_normal_aim", "Continued fire requires normal AIM", enforcement="TERMINATION"), constraint("other_specialty_territory_gained", "DEEP AIM remains SNIPER territory, sustained OVERWATCH remains SENTINEL territory", expected=False, enforcement="INVARIANT"), constraint("physical_combat_state_preserved", "movement, COVER, RANGE, ammunition, and firearm state still matter", enforcement="INVARIANT")),
    ),
    ("SNIPER", "DEEP AIM"): contract(
        prerequisites=(prerequisite("normal_aim_completed", "After performing the normal AIM process"), prerequisite("additional_ap_spent_for_same_target_position", "spend 1 additional AP to enter DEEP AIM against that target from the current firing position")),
        constraints=(constraint("movement_target_change_or_lost_solution_breaks", "material movement by the shooter or target, a target change, or loss of the firing solution breaks it.", enforcement="TERMINATION"),),
    ),
    ("SNIPER", "BRACED SOLUTION"): contract(
        prerequisites=(prerequisite("physically_supported", "physically supported by a bipod, rest, barricade, prone firing position, or similar stable support"),),
    ),
    ("SNIPER", "RANGE DOPE"): contract(
        prerequisites=(prerequisite("deep_aim_active", "DEEP AIM offsets more of the attack difficulty caused by extreme range"),),
        constraints=(constraint("projectile_extended_beyond_plausible_range", "does not extend a projectile beyond physically plausible range", expected=False, enforcement="INVARIANT"),),
    ),
    ("SNIPER", "FINE POINT"): contract(
        prerequisites=(prerequisite("called_shot_from_deep_aim", "making a CALLED SHOT from DEEP AIM"),),
        constraints=(constraint("selected_area_visible", "The selected area must still be visible.", enforcement="GUARD"),),
    ),
    ("SNIPER", "FIRST-ROUND HIT"): contract(
        prerequisites=(prerequisite("first_attack_from_deep_aim_hits", "The first attack from DEEP AIM gains improved HIT QUALITY on a successful hit"),),
        constraints=(constraint("subsequent_shots_keep_solution", "Subsequent shots require rebuilding the firing solution normally.", expected=False, enforcement="TERMINATION"),),
    ),
    ("SNIPER", "THREAD THE GAP"): contract(
        prerequisites=(prerequisite("small_exposed_silhouette_through_partial_cover", "a target presenting only a small exposed silhouette through partial COVER"),),
        constraints=(constraint("full_cover_allows_direct_shot", "Full COVER still provides no direct shot.", expected=False, enforcement="GUARD"),),
    ),
    ("SNIPER", "FOLLOW THROUGH"): contract(
        prerequisites=(prerequisite("fired_from_deep_aim_remains_and_same_visible_target", "After firing from DEEP AIM, if the shooter remains in position and continues engaging the same visible target"),),
        constraints=(constraint("benefit_survives_movement_or_lost_solution", "Movement or a lost solution removes the benefit.", expected=False, enforcement="TERMINATION"),),
    ),
    ("SNIPER", "EXTREME RANGE"): contract(
        prerequisites=(prerequisite("deep_aim_active", "DEEP AIM extends the weapon's useful RANGE PROFILE"),),
        constraints=(constraint("weapon_damage_or_projectile_behavior_changed", "Damage and projectile behavior still follow the weapon.", expected=False, enforcement="INVARIANT"),),
    ),
    ("SNIPER", "MATERIAL WINDOW"): contract(
        prerequisites=(prerequisite("target_behind_penetrable_cover_and_weak_section_identified", "a target is behind penetrable COVER and a plausible weak section can be identified"),),
        constraints=(constraint("only_specific_cover_section_affected", "against that specific section of COVER", enforcement="GUARD"), constraint("target_penetration_generically_raised", "does not generically raise target PENETRATION", expected=False, enforcement="INVARIANT"), constraint("projectile_defeats_material", "the projectile must still defeat the material", enforcement="GUARD")),
    ),
    ("SNIPER", "PATIENT REACQUIRE"): contract(
        prerequisites=(prerequisite("target_briefly_breaks_and_reappears_same_area_shooter_stationary", "a DEEP-AIMED target briefly breaks LOS and then reappears in the same local area without the shooter changing position"),),
        constraints=(constraint("genuinely_relocated_target_keeps_solution", "A genuinely relocated target must be solved again.", expected=False, enforcement="GUARD"),),
    ),
    ("SNIPER", "PENETRATING SHOT"): contract(
        prerequisites=(prerequisite("before_firing_from_deep_aim", "Before firing from DEEP AIM"), prerequisite("attack_benefit_sacrificed", "sacrifice part of its attack benefit"), prerequisite("declared_before_attack_roll", "Declare before the ATTACK ROLL")),
        constraints=(constraint("external_cover_material_penetration_improved", "does not improve MATERIAL PENETRATION through external COVER", expected=False, enforcement="INVARIANT"),),
    ),
    ("SNIPER", "TERMINAL SHOT"): contract(
        prerequisites=(prerequisite("before_firing_from_deep_aim", "Before firing from DEEP AIM"), prerequisite("attack_benefit_sacrificed", "sacrifice part of its attack benefit"), prerequisite("declared_before_attack_roll", "Declare before the ATTACK ROLL")),
    ),
    ("SNIPER", "EXACT RELEASE"): contract(
        prerequisites=(prerequisite("deep_aim_not_converted_to_penetration_or_damage", "Instead of converting DEEP AIM into target PENETRATION or DAMAGE"),),
    ),
    ("SNIPER", "PERFECT SOLUTION"): contract(
        prerequisites=(prerequisite("preparation_beyond_deep_aim_invested", "Invest one further stage of preparation beyond DEEP AIM"), prerequisite("exactly_one_solution_benefit_selected", "allocate the resulting solution to exactly one benefit")),
        constraints=(constraint("material_allocation_only_when_window_applies", "available only when MATERIAL WINDOW genuinely applies to the shot", enforcement="GUARD"), constraint("solution_choices_stack", "The choices do not stack", expected=False, enforcement="INVARIANT"), constraint("movement_lost_solution_or_abandonment_wastes", "Moving, losing the firing solution, or being forced to abandon the shot wastes the investment.", enforcement="TERMINATION")),
    ),
    ("TRAUMA", "TRAUMA CARE"): contract(
        prerequisites=(prerequisite("serious_injury_with_appropriate_equipment_conditions", "treating serious physical injury with appropriate equipment and working conditions"), prerequisite("medicine_successful", "successful MEDICINE treatment")),
        constraints=(constraint("injury_medically_treatable", "The injury must actually be medically treatable.", enforcement="GUARD"),),
    ),
    ("TRAUMA", "TRAUMA ASSESSMENT"): contract(
        prerequisites=(prerequisite("examination_thorough", "A thorough examination"),),
        constraints=(constraint("information_outside_method_detection_revealed", "The examination cannot reveal what its method cannot detect.", expected=False, enforcement="INVARIANT"),),
    ),
    ("TRAUMA", "HEMORRHAGE CONTROL"): contract(
        prerequisites=(prerequisite("treatment_targets_significant_bleeding", "Treatments specifically intended to control significant bleeding"),),
        constraints=(constraint("source_physically_controllable", "The source must be physically controllable.", enforcement="GUARD"),),
    ),
    ("TRAUMA", "WOUND MANAGEMENT"): contract(
        prerequisites=(prerequisite("treatable_wound_properly_managed", "cleaning, dressing, closing, or otherwise properly managing a treatable wound"),),
    ),
    ("TRAUMA", "FRACTURE MANAGEMENT"): contract(
        prerequisites=(prerequisite("fracture_dislocation_or_comparable_injury_treated", "Treating fractures, dislocations, and comparable structural injuries"),),
        constraints=(constraint("bone_or_joint_fully_healed", "The bone/joint remains injured until properly healed.", expected=False, enforcement="INVARIANT"),),
    ),
    ("TRAUMA", "DEBRIDEMENT"): contract(
        prerequisites=(prerequisite("proper_tools_and_compromised_tissue", "With proper tools, treating contaminated, burned, damaged, or otherwise compromised tissue"),),
        constraints=(constraint("real_treatment_time_required", "This requires real treatment time.", enforcement="GUARD"),),
    ),
    ("TRAUMA", "EFFICIENT CLOSURE"): contract(
        prerequisites=(prerequisite("bleeding_and_contamination_controlled", "Once bleeding and contamination are controlled"), prerequisite("wound_suitable_for_closure", "closing a suitable wound")),
    ),
    ("TRAUMA", "DEEP WOUND CARE"): contract(
        prerequisites=(prerequisite("damage_severe_but_survivable", "severe but survivable tissue damage"),),
        constraints=(constraint("same_wound_healed_infinitely", "This does not allow infinite repeated healing of the same wound.", expected=False, enforcement="GUARD"),),
    ),
    ("TRAUMA", "LIMB PRESERVATION"): contract(
        prerequisites=(prerequisite("badly_injured_limb_medically_salvageable", "a badly injured limb is still medically salvageable"),),
        constraints=(constraint("destroyed_or_severed_structure_restored", "Destroyed or irretrievably severed structures remain destroyed.", expected=False, enforcement="GUARD"),),
    ),
    ("TRAUMA", "SHOCK MANAGEMENT"): contract(
        prerequisites=(prerequisite("systemic_condition_worsening_from_qualifying_cause", "a casualty whose systemic condition is worsening from blood loss, trauma, or physiological shock"),),
    ),
    ("TRAUMA", "FIELD SURGERY"): contract(
        prerequisites=(prerequisite("tools_supplies_and_conditions_appropriate", "With appropriate tools, supplies, and working conditions"),),
        constraints=(constraint("environmental_conditions_still_matter", "environmental conditions still matter", enforcement="GUARD"),),
    ),
    ("TRAUMA", "SALVAGE"): contract(
        prerequisites=(prerequisite("severe_injury_with_persistent_penalty", "a severe INJURY that would normally leave a lasting functional penalty"), prerequisite("definitive_care_successful", "successful definitive care"), prerequisite("injury_has_defined_lower_severity_step", "one severity step defined by that specific INJURY")),
        constraints=(constraint("applications_at_most_once", "Apply SALVAGE at most once to the same INJURY", enforcement="GUARD"), constraint("repeated_treatment_stacks", "repeated treatment cannot stack the reduction", expected=False, enforcement="GUARD"), constraint("immediate_hp_or_combat_function_restored", "not immediate HP restoration or restored combat function", expected=False, enforcement="INVARIANT"), constraint("irreparable_destruction_reversed", "Irreparable destruction cannot be reversed", expected=False, enforcement="GUARD"), constraint("benefit_without_achievable_lower_state", "an INJURY without a medically achievable lower severity state receives no invented benefit", expected=False, enforcement="GUARD")),
    ),
    ("TRAUMA", "RECOVERY PLAN"): contract(
        prerequisites=(prerequisite("definitive_treatment_completed", "After completing definitive treatment"), prerequisite("recovery_regimen_followed", "while the regimen is followed")),
    ),
    ("TRAUMA", "DEFINITIVE CARE"): contract(
        prerequisites=(prerequisite("sufficient_equipment_time_and_viable_patient", "With sufficient equipment, time, and a medically viable patient"),),
        constraints=(constraint("anatomy_violated", "without violating anatomy", expected=False, enforcement="GUARD"),),
    ),
}
