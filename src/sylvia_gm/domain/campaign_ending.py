"""Authored campaign conclusions and their saved, player-selected continuation."""

from collections.abc import Mapping, Sequence


def intended_endings(plan: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """Return finite ending preparation; older and explicitly ongoing Plans have none."""
    ending = plan.get("ending")
    if not isinstance(ending, Mapping) or ending.get("mode") != "finite":
        return ()
    items = ending.get("outcomes", ())
    return tuple(item for item in items if isinstance(item, Mapping)) if isinstance(
        items, Sequence
    ) else ()


def ending_progress_id(ending: Mapping[str, object]) -> str:
    """Namespace ending progress so it cannot accidentally resolve an act or objective."""
    return f"ending:{ending['id']}"


def required_story_items(plan: Mapping[str, object]) -> set[str]:
    """Identify finite acts and their linked objectives without judging narrative truth."""
    items: set[str] = set()
    structure = plan.get("structure", ())
    if not isinstance(structure, Sequence):
        return items
    for act in structure:
        if isinstance(act, Mapping):
            items.add(str(act["id"]))
            items.update(str(item) for item in act.get("objectives", ()))
    return items


def resolved_progress(progress: Sequence[Mapping[str, object]]) -> set[str]:
    """Use the latest recorded status, including a later correction to a resolved item."""
    latest = {str(item["plan_item_id"]): item.get("status") for item in progress}
    return {key for key, status in latest.items() if status == "resolved"}


def required_ending_items(
    plan: Mapping[str, object], ending: Mapping[str, object],
) -> set[str]:
    """Keep ordinary completion gates; an explicit early outcome does not finish unplayed acts."""
    return set() if ending.get("requires_all_acts") is False else required_story_items(plan)


def check_ending_progress(
    plan: Mapping[str, object], progress: Sequence[Mapping[str, object]],
    updates: Sequence[Mapping[str, object]],
) -> None:
    """Reject a premature or ambiguous ending; narrative facts remain the GM's judgement."""
    endings = {ending_progress_id(item): item for item in intended_endings(plan)}
    for update in updates:
        identity = str(update.get("plan_item_id", ""))
        if identity.startswith("ending:") and identity not in endings:
            raise ValueError("Ending is not declared by this Campaign Plan")
    ending = plan.get("ending")
    structure = plan.get("structure")
    if isinstance(ending, Mapping) and ending.get("mode") == "ongoing" and isinstance(
        structure, Sequence
    ) and structure and any(item.get("plan_item_id") == structure[-1]["id"]
                            and item.get("status") == "resolved" for item in updates):
        raise ValueError("The final act is explicitly ongoing and has no completion")
    if not any(item.get("plan_item_id") in endings and item.get("status") == "resolved"
               for item in updates):
        return
    resolved = resolved_progress((*progress, *updates))
    for identity in endings.keys() & resolved:
        if not required_ending_items(plan, endings[identity]) <= resolved:
            raise ValueError("Resolve the required acts and objectives before recording the ending")
    if len(endings.keys() & resolved) > 1:
        raise ValueError("Only one intended ending can be resolved; retire a superseded outcome")


def validate_ending(plan: Mapping[str, object]) -> None:
    """Check outcome identity and ongoing-act shape beyond the closed JSON definition."""
    if "ending" not in plan:
        return  # Retained pre-ending-format snapshots remain readable without migration.
    ending = plan["ending"]
    if not isinstance(ending, Mapping):
        raise ValueError("Campaign ending must be an object")
    structure = plan["structure"]
    if not isinstance(structure, Sequence) or not structure:
        raise ValueError("Campaign ending requires a final act")
    final_act = structure[-1]
    if not isinstance(final_act, Mapping):
        raise ValueError("Campaign final act must be an object")
    if ending.get("mode") == "ongoing":
        if final_act.get("objectives"):
            raise ValueError("An ongoing final act has no required completion objectives")
        return
    outcomes = intended_endings(plan)
    identities = [item.get("id") for item in outcomes]
    if len(set(identities)) != len(identities):
        raise ValueError("Campaign ending identities must be unique")
    collisions = required_story_items(plan) & {ending_progress_id(item) for item in outcomes}
    if collisions:
        raise ValueError("Campaign ending progress identity overlaps an act or objective")
