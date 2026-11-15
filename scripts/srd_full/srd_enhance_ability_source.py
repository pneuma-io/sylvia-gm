"""Align the retained Enhance Ability target gates with its printed creature target."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_timed_actor_defenses import _RuntimeDumper
else:
    from srd_timed_actor_defenses import _RuntimeDumper


OWNER = "cast_spell_multi_target_concentration_ability_advantage"
PROFILE_ERROR = "The Enhance Ability profile is incoherent."
OLD_TARGET = (
    "caster_can_touch_all_targets == true and all_targets_willing == true "
    "and all_targets_have_clear_path == true"
)
NEW_TARGET = "caster_can_touch_all_targets == true and all_targets_have_clear_path == true"
OLD_TARGET_ERROR = (
    "Every Enhance Ability target must be willing, within Touch reach, and have a clear spell path."
)
NEW_TARGET_ERROR = (
    "Every Enhance Ability target must be within Touch reach and have a clear spell path."
)


def correct_target_guards(tool):
    """Change only the two known source gates and reject an unfamiliar retained owner."""
    changed = deepcopy(tool)
    preconditions = changed["pre_conditions"]
    profiles = [gate for gate in preconditions if gate.get("error") == PROFILE_ERROR]
    targets = [
        gate for gate in preconditions if gate.get("error") in {OLD_TARGET_ERROR, NEW_TARGET_ERROR}
    ]
    if len(profiles) != 1 or len(targets) != 1:
        raise ValueError("Enhance Ability has lost its exact retained target gates")
    profile = profiles[0]
    condition = " ".join(profile["condition"].split())
    parent = (
        "family == 'multi_target_concentration_ability_advantage' and base_level == 2 "
        "and range_kind == 'touch' and range_feet == 0 and requires_willing == true "
        "and duration_rounds == 600 and concentration == true and base_targets == 1 "
        "and targets_per_slot_level == 1 and trigger == 'ability_check' "
        "and selection == 'ability_per_target' and operation == 'grant_advantage'"
    )
    candidate = parent.replace("requires_willing == true", "requires_willing == false")
    if condition not in {parent, candidate}:
        raise ValueError("Enhance Ability has an unfamiliar retained profile gate")
    target = targets[0]
    actual_target = " ".join(target["condition"].split())
    if (actual_target, target["error"]) not in {
        (OLD_TARGET, OLD_TARGET_ERROR),
        (NEW_TARGET, NEW_TARGET_ERROR),
    }:
        raise ValueError("Enhance Ability has an unfamiliar retained touch/path gate")
    profile["condition"] = candidate
    target.update(condition=NEW_TARGET, error=NEW_TARGET_ERROR)
    return changed


def bind_runtime_consumers(text):
    """Repair the one retained family leaf before final casting and reaction captures."""
    pattern = rf"(?ms)^  {OWNER}:[^\n]*\n.*?(?=^  [A-Za-z_][^\n]*:\s*|^procedures:)"
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        raise ValueError("Enhance Ability retained family must have exactly one source block")
    tool = yaml.load(matches[0].group(), Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))[
        OWNER
    ]
    changed = correct_target_guards(tool)
    if changed == tool:
        return text
    fragment = yaml.dump({OWNER: changed}, Dumper=_RuntimeDumper, sort_keys=False, width=100)
    fragment = "".join("  " + line if line.strip() else line for line in fragment.splitlines(True))
    return re.sub(pattern, lambda _: fragment, text, count=1)
