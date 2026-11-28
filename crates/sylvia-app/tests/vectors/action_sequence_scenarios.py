"""Action-sequence scenarios for `generate_action_sequence_runs.py` to record.

Python's own tests reach a sequence's refusal and recovery paths mostly with Trigger
Discipline, whose content is private and is not recorded. These scenarios drive the same paths
on the bundled SRD Lite fixture through the real owner, so the recorder keeps Python's answers.
They assert nothing: each call's result or exception is what the recording keeps.

An owner crash is the recorder's injected fault (`FAULT`): the chosen owner call raises
`RuntimeError` before or after its real work, as a crash there would, and the trace records it.
"""

from __future__ import annotations

import generate_action_sequence_runs as recorder
from sylvia_gm.domain.authority import WriterRole
from tests.test_action_sequences import GM, compose, execute, lite, lite_step, read_queue  # noqa: F401 - fixture

OTHER = "scenario-builder"


def attempt(call):
    try:
        return call()
    except Exception as error:  # noqa: BLE001 - the recording keeps every outcome
        return error


def fault(**settings):
    recorder.FAULT.clear()
    recorder.FAULT.update(settings)


def walk(value, identity, x):
    return lite_step(value, identity, "move-walk", destination=[0, x, 0])


def test_compose_refusals(lite):
    step = walk(lite, "walk", 5)
    revision = lite.snapshot().revision
    owner = lite.owner
    attempt(lambda: owner.compose_action_sequence(
        campaign_id="elsewhere", session_id=GM, sequence_id="other-campaign", expected_revision=revision,
        steps=[step]))
    attempt(lambda: compose(lite, [step], sequence_id="version-five", expected_version=5))
    attempt(lambda: compose(lite, [step], sequence_id="version-false", expected_version=False))
    attempt(lambda: compose(lite, [step], sequence_id="version-float", expected_version=0.0))
    attempt(lambda: compose(lite, [step], sequence_id="stale", expected_revision=revision + 1))
    attempt(lambda: compose(lite, [{**step, "step_id": f"walk-{n}"} for n in range(33)], sequence_id="thirty-three"))
    attempt(lambda: compose(lite, [{**step, "step_id": f"walk-{n}"} for n in range(32)], sequence_id="thirty-two"))
    attempt(lambda: compose(lite, [], sequence_id="empty"))
    attempt(lambda: compose(lite, "steps", sequence_id="text-steps"))
    attempt(lambda: compose(lite, [1], sequence_id="number-step"))
    attempt(lambda: compose(lite, [{**step, "extra": 1}], sequence_id="extra-key"))
    attempt(lambda: compose(lite, [step, step], sequence_id="duplicate"))
    attempt(lambda: compose(lite, [{**step, "step_id": " "}], sequence_id="blank-step"))
    attempt(lambda: compose(lite, [step], sequence_id=" padded "))
    attempt(lambda: compose(lite, [{**step, "source": {"kind": "tool", "tool_name": "dash"}}], sequence_id="raw-tool"))
    first = lite.snapshot().actions[0].action_id if lite.snapshot().actions else "none"
    attempt(lambda: compose(lite, [{"step_id": "card", "selection": None,
                                    "source": {"kind": "result_card", "action_id": first, "button_index": 0}}],
                            sequence_id="card"))
    attempt(lambda: compose(lite, [lite_step(lite, "npc", "move-walk", actor="guard", destination=[10, 0, 0])],
                            sequence_id="npc"))


def test_builder_session_cannot_read(lite):
    attempt(lambda: lite.owner.create_agent_session(
        campaign_id=lite.campaign_id, session_id=OTHER, role=WriterRole.BUILDER, provider_id="synthetic",
        model_id="synthetic", selected_skill="BUILD RULESET"))
    attempt(lambda: lite.owner.action_sequence(session_id=OTHER, sequence_id="none"))


def test_committed_prefix_and_execution_refusals(lite):
    steps = [
        lite_step(lite, "cast-once", "cast-magic_missile"),
        lite_step(lite, "cast-again", "cast-magic_missile"),
        walk(lite, "later", 5),
    ]
    queue = compose(lite, steps)
    attempt(lambda: lite.owner.execute_action_sequence(
        campaign_id=lite.campaign_id, session_id=GM, sequence_id=queue["sequence_id"],
        expected_version=queue["version"] + 1, confirmation=queue["confirmation"]))
    attempt(lambda: lite.owner.execute_action_sequence(
        campaign_id=lite.campaign_id, session_id=GM, sequence_id=queue["sequence_id"],
        expected_version=queue["version"], confirmation="0" * 64))
    attempt(lambda: lite.owner.execute_action_sequence(
        campaign_id=lite.campaign_id, session_id=GM, sequence_id="missing-sequence",
        expected_version=0, confirmation=None))
    result = execute(lite, queue)
    attempt(lambda: execute(lite, result))
    version = result["version"]
    attempt(lambda: compose(lite, [steps[1], steps[0], steps[2]], expected_version=version))
    edited = {**steps[0], "selection": {**steps[0]["selection"], "target": "hero"}}
    attempt(lambda: compose(lite, [edited, steps[1]], expected_version=version))
    attempt(lambda: compose(lite, [steps[0], walk(lite, "after", 10)], expected_version=version))
    attempt(lambda: read_queue(lite))


def test_crash_after_native_commit_then_read(lite):
    queue = compose(lite, [walk(lite, "first", 5), walk(lite, "second", 10)])
    # The second save of the run is the one after the first native commit.
    fault(name="append_session_message", when="before", nth=2)
    attempt(lambda: execute(lite, queue))
    fault()
    attempt(lambda: read_queue(lite))
    attempt(lambda: read_queue(lite))


def test_crash_after_native_commit_then_compose_and_execute(lite):
    queue = compose(lite, [walk(lite, "first", 5), walk(lite, "second", 10)])
    fault(name="append_session_message", when="before", nth=2)
    attempt(lambda: execute(lite, queue))
    fault()
    attempt(lambda: compose(lite, [walk(lite, "again", 5)], expected_version=queue["version"] + 1))
    attempt(lambda: execute(lite, {**queue, "version": queue["version"] + 1}))


def test_crash_after_native_commit_then_campaign_changes(lite):
    queue = compose(lite, [walk(lite, "first", 5), walk(lite, "second", 10)])
    fault(name="append_session_message", when="before", nth=2)
    attempt(lambda: execute(lite, queue))
    fault()
    other = compose(lite, [walk(lite, "elsewhere", 15)], sequence_id="other-sequence")
    attempt(lambda: execute(lite, other))
    attempt(lambda: read_queue(lite))


def test_crash_after_native_commit_then_session_stops(lite):
    queue = compose(lite, [walk(lite, "first", 5), walk(lite, "second", 10)])
    fault(name="append_session_message", when="before", nth=2)
    attempt(lambda: execute(lite, queue))
    fault()
    attempt(lambda: lite.owner.stop_agent_session(GM))
    attempt(lambda: read_queue(lite))


def test_crash_before_native_commit(lite):
    queue = compose(lite, [walk(lite, "first", 5), walk(lite, "second", 10)])
    fault(name="player_action_in_owned_work", when="before", nth=1,
          where=lambda args, kwargs: not kwargs.get("preview_only", False))
    attempt(lambda: execute(lite, queue))
    fault()
    attempt(lambda: read_queue(lite))
    attempt(lambda: compose(lite, [walk(lite, "again", 5)], expected_version=queue["version"] + 1))
    attempt(lambda: execute(lite, queue))


def test_campaign_changes_between_compose_and_execute(lite):
    queue = compose(lite, [walk(lite, "first", 5)])
    other = compose(lite, [walk(lite, "elsewhere", 15)], sequence_id="other-sequence")
    attempt(lambda: execute(lite, other))
    attempt(lambda: execute(lite, queue))


def synthetic_case(tmp_path, name, **options):
    from tests.test_action_sequences import NativeCase, create_session
    from tests.test_player_actions import _create

    value = NativeCase(tmp_path / f"{name}.sqlite3", "campaign", "owner")
    _create(value.owner, **options)
    create_session(value.owner, value.campaign_id)
    return value


def synthetic_step(identity, destination):
    return {"step_id": identity, "source": {"kind": "panel", "surface_id": "operations.map", "action_id": "move"},
            "selection": {"actor": "operator", "destination": destination, "map_revision": 7}}


def test_waiting_choice_blocks_editing_and_other_sequences(tmp_path):
    value = synthetic_case(tmp_path, "choice", choice=True)
    try:
        queue = compose(value, [synthetic_step("choice-step", [4, 4]), synthetic_step("later-step", [3, 3])])
        attempt(lambda: execute(value, queue))
        saved = read_queue(value)
        version = saved["version"]
        attempt(lambda: compose(value, [synthetic_step("choice-step", [4, 4])], expected_version=version))
        attempt(lambda: compose(value, [synthetic_step("other-step", [2, 2])], sequence_id="other-sequence"))
        attempt(lambda: read_queue(value))
    finally:
        value.close()


def test_unavailable_declared_control_is_refused(tmp_path):
    value = synthetic_case(tmp_path, "unavailable", controls={"confirmation": "sometimes"})
    try:
        attempt(lambda: compose(value, [synthetic_step("step", [4, 4])], sequence_id="unavailable"))
    finally:
        value.close()
