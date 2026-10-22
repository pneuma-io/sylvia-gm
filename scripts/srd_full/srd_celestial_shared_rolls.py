"""Apply the actual caster's raw modifier once to each shared Celestial damage roll."""

from copy import deepcopy

if __package__:
    from .srd_conjured_presences import _lists
    from .srd_timed_actor_defenses import _load_runtime
    from .srd_timed_senses import _replace_tools
else:
    from scripts.srd_full.srd_conjured_presences import _lists
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
    from scripts.srd_full.srd_timed_senses import _replace_tools

LIGHT_TOOLS = (
    "resolve_srd_celestial_initial_lights",
    "resolve_srd_celestial_event_lights",
)


def _shared_modifier(original):
    """Move the existing modifier pair beside its roll; keep recipient packets unchanged."""
    tool = deepcopy(original)
    sequences = list(_lists(tool["effects"]))
    branches = [
        effect["on_true"]
        for sequence in sequences
        for effect in sequence
        if effect.get("condition") == "len(searing_targets) > 0"
    ]
    if len(branches) != 1:
        raise ValueError("Retain the one shared Celestial Searing roll branch.")
    branch = branches[0]
    if not branch or branch[0] != {
        "dice": {"expression": "{_presence_searing_dice}d12"},
        "store_as": "_presence_searing_roll",
    }:
        raise ValueError("Retain the native shared Celestial damage dice and raw evidence.")
    modifiers = [
        (sequence, index)
        for sequence in sequences
        for index, effect in enumerate(sequence)
        if effect.get("call") == "modify_srd_actor_raw_damage_roll"
    ]
    if len(modifiers) != 1:
        raise ValueError("Retain exactly one genuine raw modifier for this shared roll.")
    sequence, index = modifiers[0]
    modifier = sequence[index]
    if index == 0 or sequence[index - 1].get("call") != "resolve_srd_actor_spell_modifiers":
        raise ValueError("Retain the actual caster refresh immediately before its modifier.")
    refresh = sequence[index - 1]
    if refresh["params"] != {
        "target_state": "{_presence.caster}",
        "lasting_state": "state",
    } or modifier["params"] != {
        "actor_state": "{_presence.caster}",
        "lasting_state": "state",
        "amount": "{_presence_searing_roll.total}",
    }:
        raise ValueError("Qualify only the actual source caster and this shared raw roll.")
    if sequence is branch:
        if index != 2 or len(branch) != 3:
            raise ValueError("The shared modifier must immediately follow its native roll.")
        return tool
    if len(branch) != 1:
        raise ValueError("Do not replace an unknown shared-roll producer continuation.")
    del sequence[index - 1 : index + 1]
    branch.extend([refresh, modifier])
    return tool


def bind_shared_damage_rolls(text):
    """Bind both initial and later real light batches after their owning emitters."""
    tools = _load_runtime(text)["tools"]
    return _replace_tools(text, {name: _shared_modifier(tools[name]) for name in LIGHT_TOOLS})
