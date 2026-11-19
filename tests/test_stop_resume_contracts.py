"""Stop and resume contracts, checked across every short order of recovery actions.

Each case starts one GM turn for a player input, optionally commits that turn's one mechanic,
and then ends it either with the player's Stop or with a provider failure. It then applies one
order of Retry, a second Retry identity, new player input (a redirection) and owner reopen to
the real owner and SQLite database: every distinct two-step order, plus every order that
resolves the interruption, reopens and asks again. After every step the observed state is
compared with a small reference model of the written promises:

- architecture contract section 7.3: reusing an action or retry identity returns the stored
  outcome without running mechanics again;
- section 8.3: Retry reuses the committed result and never reruns the action; cancellation drops
  incomplete narration; after a settled Stop, new input redirects the unfinished continuation and
  durably supersedes the Retry opportunity, so stale requests and reopening cannot revive it;
- section 9.3 and the product contract section 5: the Retry state survives reopening and Stop is
  not undo and grants no extra action.

The reference model is deliberately independent of the owner: it is written from those
sentences and never asks the production code what should happen. Every GM provider used for
recovery repeats the original tool call with the same action identity, as a model may, so the
mechanic count shows directly whether recovery ever executes that action a second time.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import pytest

from sylvia_gm.agent._session_loop import (
    CampaignSessionLoop,
    ProviderEvent,
    SessionProviderInput,
    SessionRunRequest,
)
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CatalogTool
from sylvia_gm.domain.authority import (
    MechanicContext,
    MechanicOutcome,
    NarrationStream,
    ProseChannel,
    RetryNarrationInput,
    StateMutation,
    WriterRole,
)
from sylvia_gm.domain.errors import AuthorityError
from tests.test_alpha_f4_generic_sessions import _clockwork_campaign

CAMPAIGN = "contract-campaign"
SESSION = "contract-gm"
TURN = "contract-turn"
OWNER = "contract-owner"
ACTION = "contract-action"
UNFINISHED = "UNFINISHED-PROSE-MUST-NOT-SURVIVE"
CONTINUED = "The saved outcome is narrated once."
STORED_RESULT = {"before": 0, "after": 4}
STEPS = ("retry-a", "retry-b", "redirect", "reopen")


def _distinct_orders(length: int) -> tuple[tuple[str, ...], ...]:
    """Every order of steps, without repeated reopen and with the first Retry always "a"."""

    orders = set()
    for order in product(STEPS, repeat=length):
        if any(left == right == "reopen" for left, right in zip(order, order[1:], strict=False)):
            continue
        names: dict[str, str] = {}
        for step in order:
            if step.startswith("retry") and step not in names:
                names[step] = ("retry-a", "retry-b")[len(names)]
        orders.add(tuple(names.get(step, step) for step in order))
    return tuple(sorted(orders))


# Every state is checked after every step, so two-step orders also cover their one-step prefixes.
# Three steps add the case the contract names explicitly: resolve, reopen, then ask again.
ORDERS = _distinct_orders(2) + tuple(
    order for order in _distinct_orders(3)
    if order[0] != "reopen" and order[1] == "reopen"
)


@dataclass
class Promise:
    """What the contracts say must be true now; updated only from the steps taken."""

    stopped: bool
    executed: bool
    open: bool = True
    resolved_by: str | None = None

    def expect(self, step: str) -> str:
        """Return accept, replay or reject for a step, and record its promised effect."""

        if step == "reopen":
            return "accept"
        if step == "redirect" and not self.stopped:
            return "reject"
        if self.open:
            self.open, self.resolved_by, self.executed = False, step, True
            return "accept"
        if step.startswith("retry") and step == self.resolved_by:
            return "replay"
        return "reject"


@dataclass
class _Run:
    """One owner process against one copied campaign database."""

    database: Path
    calls: list[int]
    owner: AuthorityOwner
    loop: CampaignSessionLoop

    @classmethod
    def open(cls, database: Path, calls: list[int]) -> _Run:
        owner = AuthorityOwner.open(database, OWNER, disconnect_grace_seconds=1.0)
        _register_advance(owner, calls)
        return cls(database, calls, owner, CampaignSessionLoop(owner))

    def reopen(self) -> None:
        self.owner.close()
        reopened = _Run.open(self.database, self.calls)
        self.owner, self.loop = reopened.owner, reopened.loop


def _register_advance(owner: AuthorityOwner, calls: list[int]) -> None:
    """Give a (re)opened owner the same deterministic mechanic that the template installed."""

    entry = owner.current_package_lock(CAMPAIGN).package_lock.entries[0]

    def advance(context: MechanicContext) -> MechanicOutcome:
        calls.append(int(context.parameters["steps"]))
        after = int(context.state["clock.tick"]) + int(context.parameters["steps"])
        return MechanicOutcome(
            result={"before": int(context.state["clock.tick"]), "after": after},
            mutations=(StateMutation("clock", "tick", after),),
            consequences=({"kind": "clock_advanced", "after": after},),
        )

    owner.register_tool(CatalogTool(
        package_id=entry.package_id,
        package_version=entry.version,
        package_sha256=entry.sha256,
        name="advance",
        capability="clockwork.counter",
        input_schema={
            "type": "object",
            "properties": {"steps": {"type": "integer"}},
            "required": ["steps"],
            "additionalProperties": False,
        },
        execute=advance,
    ))


def _action(owner: AuthorityOwner) -> ActionCommand:
    entry = owner.current_package_lock(CAMPAIGN).package_lock.entries[0]
    return ActionCommand(
        campaign_id=CAMPAIGN, action_id=ACTION, idempotency_key=ACTION, owner_id=OWNER,
        writer=WriterRole.DM, expected_revision=0, turn_id=TURN,
        package_id=entry.package_id, tool_name="advance", parameters={"steps": 4},
    )


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One closed database holding the campaign and its GM session, copied for each order."""

    folder = tmp_path_factory.mktemp("stop-resume-template")
    with AuthorityOwner.open(folder / "campaign.db", OWNER, disconnect_grace_seconds=1.0) as owner:
        _clockwork_campaign(owner, campaign_id=CAMPAIGN, writer=WriterRole.DM,
                            turn_id=TURN, calls=[])
        owner.create_agent_session(campaign_id=CAMPAIGN, role=WriterRole.DM,
                                   provider_id="synthetic", model_id="synthetic",
                                   session_id=SESSION)
    return folder


def _start(run: _Run, *, stopped: bool, committed: bool) -> str:
    """Run the first GM attempt and end it with Stop or a provider failure."""

    action = _action(run.owner)

    def first(_input: SessionProviderInput) -> Iterator[ProviderEvent]:
        if committed:
            yield ProviderEvent.action(action)
        if not stopped:
            raise RuntimeError("synthetic provider failure")
        # The desktop Stop button sends interrupt_stream, which is this owner call.
        run.owner.interrupt_narration(CAMPAIGN, "first-attempt")
        yield ProviderEvent.talk(UNFINISHED)

    request = SessionRunRequest(SESSION, TURN, "first-attempt", "first-input", "Advance four")
    if stopped:
        result = run.loop.run(request, first)
        assert result.interruption is not None
        return result.interruption.interruption_id
    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        run.loop.run(request, first)
    interruption = run.owner.retryable_interruption(CAMPAIGN, failed_attempt_id="first-attempt")
    assert interruption is not None
    return interruption.interruption_id


def _apply(
    run: _Run, step: str, number: int, interruption_id: str, seen: list[object],
) -> tuple[str, tuple[object, ...]]:
    """Apply one step to the owner; return what happened and any canonical result."""

    action = _action(run.owner)

    def provider(provider_input: SessionProviderInput) -> Iterator[ProviderEvent]:
        receipt = provider_input.retry_action_receipt
        seen.append(None if receipt is None else dict(receipt.result))
        yield ProviderEvent.action(action)
        yield ProviderEvent.talk(CONTINUED)

    def narrator(retry_input: RetryNarrationInput, stream: NarrationStream) -> None:
        receipt = retry_input.action_receipt
        seen.append(None if receipt is None else dict(receipt.result))
        stream.emit(ProseChannel.TALK, CONTINUED)

    if step == "reopen":
        run.reopen()
        return "accept", ()
    try:
        if step == "redirect":
            result = run.loop.run(SessionRunRequest(
                SESSION, TURN, f"redirect-{number}", f"redirect-input-{number}", "Talk instead",
                redirect_interruption_id=interruption_id,
            ), provider)
            return "accept", result.canonical
        retried = run.loop.retry(
            SESSION, retry_id=step, interruption_id=interruption_id, narrator=narrator,
            provider_factory=lambda _turn: provider,
        )
    except AuthorityError:
        return "reject", ()
    return ("replay" if retried.replayed else "accept"), retried.canonical


def _broken_promises(run: _Run, promise: Promise) -> list[str]:
    """Compare the owner's durable state with what the contracts promise right now."""

    snapshot = run.owner.snapshot(CAMPAIGN)
    projection = run.owner.player_projection(CAMPAIGN)
    results = [event for event in snapshot.events if event.event_type.value == "tool_result"]
    narrated = [event for event in snapshot.events
                if event.event_type.value == "talk" and event.safe_payload.get("text") == CONTINUED]
    retries = [item.safe_payload for item in projection if item.kind.value == "retry"]
    broken = []
    if run.calls != [4] * promise.executed or len(results) != int(promise.executed):
        broken.append(f"the action ran {len(run.calls)} times with {len(results)} saved results")
    if snapshot.revision != int(promise.executed):
        broken.append(f"campaign revision is {snapshot.revision}")
    if UNFINISHED in repr(projection) or UNFINISHED in repr(snapshot.events):
        broken.append("unfinished prose from the stopped attempt was kept")
    if len(narrated) != int(promise.resolved_by is not None):
        broken.append(f"the recovered narration is saved {len(narrated)} times")
    if len(retries) != int(promise.open):
        broken.append(f"{len(retries)} Retry offers are shown")
    elif retries and retries[0].get("can_redirect") is not promise.stopped:
        broken.append(f"Retry offer has can_redirect={retries[0].get('can_redirect')}")
    return broken


def _check_order(
    template: Path, folder: Path, order: tuple[str, ...], *, stopped: bool, committed: bool,
) -> list[str]:
    """Run one order from a fresh copy; return a readable line for each broken promise."""

    shutil.copytree(template, folder, ignore=shutil.ignore_patterns("*.lock"))
    run = _Run.open(folder / "campaign.db", [])
    try:
        interruption_id = _start(run, stopped=stopped, committed=committed)
        promise = Promise(stopped=stopped, executed=committed)
        failures = [f"after the first attempt: {item}" for item in _broken_promises(run, promise)]
        first_resolution: tuple[object, ...] = ()
        for number, step in enumerate(order, start=1):
            seen: list[object] = []
            receipt = STORED_RESULT if promise.executed else None
            expected = promise.expect(step)
            outcome, canonical = _apply(run, step, number, interruption_id, seen)
            label = f"step {number} ({step})"
            if outcome != expected:
                failures.append(f"{label}: expected {expected}, owner gave {outcome}")
            if expected == "accept" and step != "reopen":
                if seen != [receipt]:
                    failures.append(f"{label}: recovery saw {seen}, expected [{receipt}]")
                first_resolution = canonical
            elif seen:
                failures.append(f"{label}: a provider ran for a {expected}ed step")
            if expected == "replay" and canonical != first_resolution:
                failures.append(f"{label}: the replayed Retry differs from the original")
            failures.extend(f"{label}: {item}" for item in _broken_promises(run, promise))
        return failures
    finally:
        run.owner.close()


@pytest.mark.parametrize("order", ORDERS, ids=["-".join(order) for order in ORDERS])
@pytest.mark.parametrize("committed", [True, False], ids=["committed", "uncommitted"])
@pytest.mark.parametrize("stopped", [True, False], ids=["stop", "failure"])
def test_recovery_order_keeps_the_stop_and_resume_promises(
    template: Path, tmp_path: Path, stopped: bool, committed: bool, order: tuple[str, ...],
) -> None:
    """Retry, redirection and reopen in this order never repeat or lose the saved action."""

    failures = _check_order(template, tmp_path / "campaign", order,
                            stopped=stopped, committed=committed)
    assert not failures, "\n".join(failures)


def test_the_checked_orders_are_the_documented_set() -> None:
    """Guard the enumeration itself, so a refactor cannot silently shrink the checked orders."""

    assert len(_distinct_orders(2)) == 9
    assert ORDERS[9:] == (
        ("redirect", "reopen", "redirect"),
        ("redirect", "reopen", "retry-a"),
        ("retry-a", "reopen", "redirect"),
        ("retry-a", "reopen", "retry-a"),
        ("retry-a", "reopen", "retry-b"),
    )
    assert ("reopen", "redirect") in ORDERS and ("retry-a", "retry-b") in ORDERS
    assert not any(order[0] == "retry-b" for order in ORDERS)
