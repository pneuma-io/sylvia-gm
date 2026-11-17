"""Observe existing action-sequence edges in tests without production enforcement."""

import pytest

from sylvia_gm.application import action_sequences as sequences


@pytest.fixture(autouse=True)
def assert_action_sequence_moves(monkeypatch):
    queue_writer, step_writer = sequences._move_queue, sequences._move_step
    queue_versions = {}
    violations = []

    def check(kind, old, new, moves, initial):
        allowed = {initial} if old is None else moves.get(old, ())
        if new not in allowed:
            violations.append((kind, old, new))

    def queue_move(row, new):
        identity = (row.get("campaign_id"), row.get("session_id"), row.get("sequence_id"))
        old = row.get("status", queue_versions.get(identity))
        queue_writer(row, new)
        check("queue", old, new, sequences.QUEUE_MOVES, sequences.QueueStatus.READY)
        if type(row["status"]) is not str:
            violations.append(("queue", "non-string wire value", type(row["status"]).__name__))
        queue_versions[identity] = row["status"]

    def step_move(row, new):
        old = row.get("status")
        step_writer(row, new)
        check("step", old, new, sequences.STEP_MOVES, sequences.StepStatus.PENDING)
        if type(row["status"]) is not str:
            violations.append(("step", "non-string wire value", type(row["status"]).__name__))

    monkeypatch.setattr(sequences, "_move_queue", queue_move)
    monkeypatch.setattr(sequences, "_move_step", step_move)
    yield
    assert not violations, violations
