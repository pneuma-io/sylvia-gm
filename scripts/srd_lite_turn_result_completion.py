"""Keep one display row per saved repeating save and avoid repeated death-effect labels."""

from copy import deepcopy


def lists(effects):
    yield effects
    for effect in effects:
        for branch in ("on_true", "on_false"):
            if branch in effect:
                yield from lists(effect[branch])
        if "for_each" in effect:
            yield from lists(effect["for_each"].get("do", []))


def bind_turn_result_completion(runtime):
    tools = runtime["tools"]
    repeat = tools["resolve_srd_timed_condition_end_turn"]
    replaced = 0
    for effects in lists(repeat["effects"]):
        for index, effect in enumerate(effects):
            push = effect.get("list_push", {})
            row = push.get("value")
            if (
                push.get("list") == "_timed_condition_save_details"
                and isinstance(row, dict)
                and row.get("label") == "_visual_repeat_label"
            ):
                assert row["equation"]["roll"] == "_timed_condition_end_turn_roll.total"
                effects[index] = {"set": {"field": "_timed_condition_save_row", "value": row}}
                replaced += 1
    assert replaced == 1, "Keep the sole repeating-save display owner"
    # The original later append now publishes the structured row once; the native
    # recorded text and native roll/outcome lists are retained in context unchanged.
    assert (
        sum(
            effect.get("list_push")
            == {"list": "_timed_condition_save_details", "value": "_timed_condition_save_row"}
            for effects in lists(repeat["effects"])
            for effect in effects
        )
        == 1
    )

    death = tools["death_saving_throw"]
    replaced = 0
    expected = "{_death_end_turn_text}{_death_end_turn_row.label}: {_death_end_turn_row.value}. "
    for effects in list(lists(death["effects"])):
        for index in range(len(effects) - 1, -1, -1):
            original = effects[index]
            if (
                original.get("call") != "record_srd_result_text"
                or original.get("params", {}).get("text") != expected
            ):
                continue
            keep_label = deepcopy(original)
            keep_label["params"]["text"] = (
                "{_death_end_turn_text}{_death_end_turn_row.label}: {_death_end_turn_row.value} "
            )
            value_only = deepcopy(original)
            value_only["params"]["text"] = "{_death_end_turn_text}{_death_end_turn_row.value} "
            effects[index : index + 1] = [
                {
                    "call": "record_srd_result_text",
                    "params": {"text": "{_death_end_turn_row.value}"},
                    "export_context": {"_fc16_death_row_value": "_recorded_result_text"},
                },
                {
                    "call": "record_srd_result_text",
                    "params": {"text": "{_death_end_turn_row.label}: "},
                    "export_context": {"_fc16_death_row_prefix": "_recorded_result_text"},
                },
                {
                    "condition": "_fc16_death_row_prefix in _fc16_death_row_value",
                    "on_true": [value_only],
                    "on_false": [keep_label],
                },
            ]
            replaced += 1
    assert replaced == 1, "Keep the sole death end-turn text owner"
    return ("resolve_srd_timed_condition_end_turn", "death_saving_throw")
