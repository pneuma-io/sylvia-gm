"""Atomic loading of an existing loose shell into a manual-action firearm's tube."""

from collections.abc import Callable
from typing import Any

from sylvia_ruleset.rifleman_entries import manipulation_price
from sylvia_ruleset.weapon_handling import break_prepared_fire


def guards() -> list[tuple[str, str]]:
    """Share the actual physical prerequisites with direct-action admission."""
    return [
        ("actor.active == true and actor.downed == false",
         "This character cannot load a shell now."),
        ("actor.weapon != null", "This character has no current firearm."),
        ("actor.weapon.feed == 'TUBE' and actor.weapon.mechanism == 'MANUAL_ACTION'",
         "Shell loading supports a manual-action tube-fed firearm."),
        ("actor.weapon.location == 'READY' and actor.weapon.controlled_by == null",
         "The firearm must be ready and free of another character's control."),
        ("actor.weapon.tube_capacity > 0 and actor.weapon.shell_load_ap > 0",
         "This firearm needs an explicitly authored tube capacity and shell-loading AP cost."),
        ("actor.weapon.spare_shells > 0", "There are no loose spare shells available."),
        ("actor.weapon.magazine.rounds < actor.weapon.tube_capacity", "The tube is full."),
        ("actor.ap >= _manipulation_ap",
         "There are not enough action points to load a shell."),
    ]


def specification(actor: dict, param: Callable[..., dict]) -> dict[str, Any]:
    """Load one real shell; preserve partial progress and the separate chamber/cycle state."""
    return {
        "capability": "trigger-discipline.equipment",
        "description": (
            "Load one existing loose spare shell into this character's manual-action firearm tube. "
            "Requires explicitly authored tube_capacity, spare_shells and shell_load_ap; no "
            "initial ammunition or cost is supplied. Does not chamber a shell or cycle the action. "
            "Each completed shell remains loaded if interrupted. Breaks maintained aim and "
            "this character's prepared fire. No automatic recoil recovery rate is selected."
        ),
        "parameters": [actor, param("prepared", "object", from_state="state.prepared")],
        "effects": [
            *manipulation_price("actor.weapon.shell_load_ap"),
            {"set": {"field": "state.last_reload", "value": {}}},
            {"set": {"field": "state.last_reload.actor", "value": "_actor_id"}},
            {"set": {"field": "state.last_reload.operation", "value": "'LOAD_ONE_SHELL'"}},
            {"set": {"field": "state.last_reload.status", "value": "'MISSING_PREREQUISITE'"}},
            {"set": {"field": "state.last_reload.ap_spent", "value": 0}},
            {
                "condition": " and ".join(condition for condition, _ in guards()),
                "on_true": [
                    {"decrement": {
                        "field": "{_actor_id}.ap", "amount": "_manipulation_ap"}},
                    {"decrement": {"field": "{_actor_id}.weapon.spare_shells", "amount": 1}},
                    {"increment": {"field": "{_actor_id}.weapon.magazine.rounds", "amount": 1}},
                    {"set": {"field": "{_actor_id}.weapon.needs_cycle", "value": True},
                     "condition": "actor.weapon.chamber != 'LIVE'"},
                    *break_prepared_fire(),
                    {"set": {
                        "field": "state.last_reload.ap_spent",
                        "value": "_manipulation_ap"}},
                    {"set": {"field": "state.last_reload.status", "value": "'COMMITTED'"}},
                ],
                "on_false": [],
            },
        ],
        "costs_action": True,
    }
