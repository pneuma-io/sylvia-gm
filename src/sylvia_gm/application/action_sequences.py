"""Durable intentions around declared controls; native transactions remain authoritative."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from contextlib import suppress
from copy import deepcopy

from sylvia_gm.application.player_actions import (
    player_action_origins,
    player_action_request_matches,
    player_control_origin,
    validate_player_control,
)
from sylvia_gm.domain.authority import (
    AwaitingChoiceReceipt,
    CanonicalEventType,
    WriterRole,
    owned_payload,
    thaw_json_value,
    validate_authority_identity,
)
from sylvia_gm.domain.errors import AuthorityError, CommandRejected, StaleRevision
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.domain.session import SessionMessageKind, SessionStatus


def _plain(value):
    return thaw_json_value(value)


def _context(owner, session_id, campaign_id=None, *, require_active=True):
    context = (
        owner._active_dm_context(session_id)
        if require_active
        else owner._sessions.context(session_id)
    )
    if context.session.role is not WriterRole.DM or context.session.campaign_id is None:
        raise CommandRejected("Action sequence requires a campaign GM session")
    if campaign_id is not None and context.session.campaign_id != campaign_id:
        raise CommandRejected("Action sequence belongs to another campaign")
    return context


def _load(owner, session_id, sequence_id=None):
    context = _context(owner, session_id, require_active=False)
    for message in reversed(context.messages):
        content = message.content
        if (
            message.kind is SessionMessageKind.EVIDENCE
            and isinstance(content, Mapping)
            and content.get("kind") == "action_sequence"
            and (sequence_id is None or content.get("sequence_id") == sequence_id)
        ):
            return _plain(content)
    return None


def _consent(queue):
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "sequence_id": queue["sequence_id"],
                "version": queue["version"],
                "steps": [
                    {k: s.get(k) for k in ("step_id", "source", "selection", "operation_id")}
                    for s in queue["steps"]
                ],
            }
        )
    ).hexdigest()


def _project(queue):
    if queue is None:
        return None
    return owned_payload(
        {
            **queue,
            "confirmation": (
                queue.get("submission_confirmation")
                if queue["status"] == "running"
                else _consent(queue)
            ),
            "committed_action_ids": [
                s["operation_id"] for s in queue["steps"] if s["status"] == "committed"
            ],
        }
    )


def _save(owner, queue):
    queue["version"] += 1
    owner._sessions.append_message(
        session_id=queue["session_id"],
        message_id=f"sequence-{queue['sequence_id']}-{queue['version']}",
        kind=SessionMessageKind.EVIDENCE,
        content=owned_payload(queue),
    )
    return _project(queue)


def read(owner, *, session_id, sequence_id=None):
    with owner._operation():
        queue = _load(owner, session_id, sequence_id)
        if queue and queue["status"] in {"running", "paused", "failed", "stale"}:
            with owner._lane(queue["campaign_id"]):
                changed = False
                snapshot = owner._store.snapshot(queue["campaign_id"])
                session_active = (
                    owner._sessions.context(session_id).session.status is SessionStatus.ACTIVE
                )
                for step in queue["steps"]:
                    if step["status"] == "committed" or not step.get("execution"):
                        continue
                    hit = owner._store.action_hit(queue["campaign_id"], step["operation_id"])
                    if hit is None:
                        continue
                    envelope = step["execution"]
                    origin = player_control_origin(
                        envelope["source"],
                        expected_revision=envelope["expected_revision"],
                        selection=envelope.get("selection"),
                        session_id=queue["session_id"],
                    )
                    if not player_action_request_matches(
                        player_action_origins(snapshot).get(step["operation_id"]), origin
                    ):
                        raise CommandRejected("Saved sequence action identity is inconsistent")
                    receipt = hit.receipt
                    if session_active:
                        owner._append_player_action_references(queue["session_id"], receipt)
                    step["receipt"] = _receipt(receipt)
                    if str(receipt.status) == "committed":
                        step["status"], step["error"] = "committed", None
                        queue["error"] = None
                        queue["expected_revision"] = receipt.resulting_revision
                        queue["event_watermark"] = _watermark(snapshot)
                        queue["status"] = "paused"
                        queue["pause_reason"] = (
                            "Saved commitment recovered; inspect the remaining intentions"
                        )
                        if snapshot.revision != receipt.resulting_revision or _watermark(
                            snapshot
                        ) != _commit_watermark(snapshot, receipt.action_id):
                            queue["status"], queue["error"] = (
                                "stale",
                                "Campaign changed after the saved commitment",
                            )
                    else:
                        step["status"], step["error"] = "pending", "Native action did not commit"
                        queue["status"], queue["error"] = "failed", step["error"]
                    changed = True
                if changed:
                    if not session_active:
                        # Stopped history stays immutable.
                # Project the real receipt without resuming the session.
                        return _project(queue)
                    return _save(owner, queue)
        return _project(queue)


def _bound_queues(context, *, basis_message_id=None, attempt_id=None):
    latest = {}
    for message in context.messages:
        content = message.content
        if (
            message.kind is not SessionMessageKind.EVIDENCE
            or content.get("kind") != "action_sequence"
            or not content.get("narration_basis_message_id")
        ):
            continue
        if (
            basis_message_id is not None
            and content.get("narration_basis_message_id") != basis_message_id
        ):
            continue
        if attempt_id is not None and content.get("narration_attempt_id") != attempt_id:
            continue
        latest[content["sequence_id"]] = _plain(content)
    return tuple(latest.values())


def _committed_ids(owner, queue, snapshot):
    origins = player_action_origins(snapshot)
    identities = []
    for step in queue["steps"]:
        envelope = step.get("execution")
        if not envelope:
            continue
        hit = owner._store.action_hit(queue["campaign_id"], step["operation_id"])
        if hit is None or str(hit.receipt.status) != "committed":
            continue
        origin = player_control_origin(
            envelope["source"],
            expected_revision=envelope["expected_revision"],
            selection=envelope.get("selection"),
            session_id=queue["session_id"],
        )
        if not player_action_request_matches(origins.get(step["operation_id"]), origin):
            raise CommandRejected("Saved sequence action identity is inconsistent")
        identities.append(step["operation_id"])
    return identities


def response_results(owner, *, session_id, basis_message_id):
    """Recover the original input's native range even before adapter evidence was admitted."""
    with owner._operation():
        try:
            context = owner._sessions.context(session_id)
        except CommandRejected as error:
            if error.code != "session_unavailable":
                raise
            return ()  # Start preflight has a safe ephemeral, unpersisted session.
        if context.session.role is not WriterRole.DM or context.session.campaign_id is None:
            return ()
        snapshot = owner._store.snapshot(context.session.campaign_id)
        results = []
        for queue in _bound_queues(context, basis_message_id=basis_message_id):
            identities = _committed_ids(owner, queue, snapshot)
            if identities:
                results.append(
                    owned_payload(
                        {
                            **queue,
                            "committed_action_ids": identities,
                            "provider_execution_stopped": queue["status"] != "completed",
                        }
                    )
                )
        return tuple(results)


def response_reservations(owner, campaign_id):
    snapshot = owner._store.snapshot(campaign_id)
    reserved, attempts = set(), set()
    for context in owner._store.campaign_session_contexts(campaign_id):
        queues = _bound_queues(context)
        bases = {q["narration_basis_message_id"] for q in queues}
        for queue in queues:
            reserved.update(_committed_ids(owner, queue, snapshot))
            attempts.add(queue["narration_attempt_id"])
        for message in context.messages:
            if (
                message.kind is SessionMessageKind.EVIDENCE
                and message.content.get("kind")
                in {"gm_turn_activity", "player_action_response_retry"}
                and message.content.get("basis_message_id") in bases
                and isinstance(message.content.get("attempt_id"), str)
            ):
                attempts.add(message.content["attempt_id"])
    return reserved, attempts


def last_attempt_commit(owner, campaign_id, attempt_id):
    snapshot = owner._store.snapshot(campaign_id)
    identities = set()
    for context in owner._store.campaign_session_contexts(campaign_id):
        for queue in _bound_queues(context, attempt_id=attempt_id):
            identities.update(_committed_ids(owner, queue, snapshot))
    return next(
        (a.action_id for a in reversed(snapshot.actions) if a.action_id in identities), None
    )


def _bind_execution_basis(owner, queue):
    with owner._narration_lock:
        attempts = [
            a
            for a in owner._attempts.values()
            if a.campaign_id == queue["campaign_id"]
            and not a.terminal
            and a.turn_id == queue["turn_id"]
        ]
        if not attempts:
            return
        if len(attempts) != 1:
            raise CommandRejected("Action sequence has ambiguous narration ownership")
        attempt = attempts[0]
        context = _context(owner, queue["session_id"], queue["campaign_id"])
        basis = next(
            (
                m
                for m in reversed(context.messages)
                if m.content.get("attempt_id") == attempt.attempt_id
                and (
                    m.kind is SessionMessageKind.USER
                    or (
                        m.kind is SessionMessageKind.EVIDENCE
                        and m.content.get("kind") in {"campaign_start", "player_action_response"}
                    )
                )
            ),
            None,
        )
        if basis is None:
            raise CommandRejected("Action sequence narration input is unavailable")
        if queue.get("narration_basis_message_id") not in (None, basis.message_id):
            raise CommandRejected("The sequence belongs to an earlier narration input")
        queue["narration_basis_message_id"] = basis.message_id
        queue["narration_attempt_id"] = attempt.attempt_id


def _needs_recovery(owner, queue):
    for step in queue["steps"]:
        if step["status"] == "committed" or not step.get("execution"):
            continue
        hit = owner._store.action_hit(queue["campaign_id"], step["operation_id"])
        if hit is not None and str(hit.receipt.status) == "committed":
            return True
    return False


def _watermark(snapshot):
    return max((e.sequence for e in snapshot.events), default=0)


def _commit_watermark(snapshot, action_id):
    return max(
        (
            e.sequence
            for e in snapshot.events
            if e.action_id == action_id and e.event_type is CanonicalEventType.ACTION_COMMITTED
        ),
        default=0,
    )


def _scope(owner, snapshot):
    value = owner._player_control_scope(snapshot)
    if value is None:
        return None
    return {
        "available": value.get("available"),
        "actors": sorted(a["id"] for a in value.get("actors", [])),
    }


def _resolve(owner, snapshot, source, selection):
    player_control_origin(
        source, expected_revision=snapshot.revision, selection=selection, session_id=None
    )
    return (
        owner._resolve_panel_control(snapshot, source, selection)
        if source["kind"] == "panel"
        else owner._resolve_card_control(snapshot, source, selection)
    )


def compose(
    owner,
    *,
    campaign_id,
    session_id,
    sequence_id,
    expected_revision,
    steps,
    expected_version=0,
    from_agent=False,
):
    validate_authority_identity(sequence_id, "Action sequence identity")
    _context(owner, session_id, campaign_id)
    if (
        not isinstance(steps, Sequence)
        or isinstance(steps, (str, bytes))
        or not 0 <= len(steps) <= 32
    ):
        raise CommandRejected("An action sequence supports at most 32 declared steps")
    with owner._operation(), owner._lane(campaign_id):
        if not from_agent and owner._campaign_work_counts.get(campaign_id):
            raise CommandRejected(
                "Finish the active campaign operation first", code="campaign_busy"
            )
        snapshot = owner._store.snapshot(campaign_id)
        if snapshot.revision != expected_revision:
            raise StaleRevision("Action sequence expected an obsolete campaign revision")
        old = _load(owner, session_id, sequence_id)
        if old and _needs_recovery(owner, old):
            read(owner, session_id=session_id, sequence_id=sequence_id)
            raise StaleRevision(
                "Saved commitments were recovered; inspect the current sequence before editing"
            )
        if (old["version"] if old else 0) != expected_version:
            raise StaleRevision("Action sequence version changed")
        pending_action = owner._store.pending_mechanics_action(campaign_id)
        if old and any(s["status"] in {"executing", "awaiting_choice"} for s in old["steps"]):
            raise CommandRejected(
                "Recover the saved sequence execution or resolve its choice before editing"
            )
        if pending_action is not None:
            raise CommandRejected("Resolve the pending mechanics choice before composing")
        old_steps = {s["step_id"]: s for s in old["steps"]} if old else {}
        committed = [s for s in old_steps.values() if s["status"] == "committed"]
        if [s.get("step_id") for s in steps[: len(committed)]] != [s["step_id"] for s in committed]:
            raise CommandRejected("Committed steps cannot be removed or reordered")
        control_scope = owner._player_control_scope(snapshot)
        actor_names = {
            a["id"]: a.get("name", a["id"]) for a in (control_scope or {}).get("actors", ())
        }
        result, ids = [], set()
        for raw in steps:
            if not isinstance(raw, Mapping) or set(raw) - {"step_id", "source", "selection"}:
                raise CommandRejected("Action sequence step is invalid")
            step_id, source, selection = raw.get("step_id"), raw.get("source"), raw.get("selection")
            validate_authority_identity(step_id, "Action sequence step identity")
            if step_id in ids:
                raise CommandRejected("Action sequence step identities must be unique")
            ids.add(step_id)
            previous = old_steps.get(step_id)
            if previous and previous["status"] == "committed":
                if previous["source"] != _plain(source) or previous["selection"] != _plain(
                    selection
                ):
                    raise CommandRejected("Committed steps cannot be edited")
                result.append(deepcopy(previous))
                continue
            tool, parameters, label, actor_parameter = _resolve(owner, snapshot, source, selection)
            if not tool.player_controls.available:
                raise CommandRejected("This declared player control is unavailable")
            validate_player_control(
                owner._player_control_scope(snapshot), parameters, actor_parameter
            )
            # Composition binds identities without simulating resources, rolls or future legality.
            warning = None
            try:
                owner._check_player_action(tool, parameters, snapshot)
            except AuthorityError as error:
                warning = str(error)
            presentation = None
            # A later intention can become legal after an earlier native commit.
            with suppress(AuthorityError):
                presentation = owner._player_action_preview(
                    tool, parameters, snapshot, source, selection
                )
            actor_id = parameters.get(actor_parameter) if actor_parameter else None
            operation = (
                "sequence-"
                + hashlib.sha256(
                    canonical_json_bytes(
                        {
                            "sequence": sequence_id,
                            "step": step_id,
                            "edit": expected_version + 1,
                            "session": session_id,
                            "source": source,
                            "selection": selection,
                        }
                    )
                ).hexdigest()
            )
            result.append(
                {
                    "step_id": step_id,
                    "source": _plain(source),
                    "selection": _plain(selection),
                    "label": label,
                    "status": "pending",
                    "operation_id": operation,
                    "receipt": None,
                    "error": None,
                    "warning": warning,
                    "package_sha256": tool.package_sha256,
                    "tool": tool.name,
                    "response_policy": tool.player_controls.response,
                    "actor": actor_id,
                    "actor_name": actor_names.get(actor_id, actor_id),
                    "preview": _plain((presentation or {}).get("preview")),
                }
            )
        queue = {
            "kind": "action_sequence",
            "campaign_id": campaign_id,
            "session_id": session_id,
            "sequence_id": sequence_id,
            "version": expected_version,
            "status": "ready",
            "expected_revision": snapshot.revision,
            "event_watermark": _watermark(snapshot),
            "scope": _scope(owner, snapshot),
            "turn_id": owner._store.current_open_turn(campaign_id),
            "steps": result,
            "error": None,
            "pause_reason": None,
        }
        return _save(owner, queue)


def _receipt(receipt):
    if isinstance(receipt, AwaitingChoiceReceipt):
        return {
            "action_id": receipt.action_id,
            "status": "awaiting_choice",
            "basis_revision": receipt.basis_revision,
            "choice_id": receipt.choice.choice_id,
        }
    return {
        "action_id": receipt.action_id,
        "status": str(receipt.status),
        "result": _plain(receipt.result),
        "resulting_revision": receipt.resulting_revision,
    }


def _rebind_map(owner, snapshot, step):
    selection = deepcopy(step["selection"])
    if (
        isinstance(selection, dict)
        and "map_revision" in selection
        and step["source"]["kind"] == "panel"
    ):
        panels = owner.campaign_panels(snapshot.campaign_id)
        surface = next(
            (s for s in panels["surfaces"] if s["id"] == step["source"]["surface_id"]), None
        )
        if surface is None:
            raise CommandRejected("The queued map control is no longer available")
        selection["map_revision"] = surface["data"]["map_revision"]
    return selection


def execute(
    owner,
    *,
    campaign_id,
    session_id,
    sequence_id,
    expected_version,
    confirmation=None,
    from_agent=False,
):
    _context(owner, session_id, campaign_id)
    if from_agent:
        # Private adapter seam: it already owns the GM campaign-work lease.
        if not owner._campaign_work_counts.get(campaign_id):
            raise CommandRejected("Agent sequence execution requires active campaign work")
        return _execute(owner, campaign_id, session_id, sequence_id, expected_version, confirmation)
    with owner._operation(), owner._direct_action_work(campaign_id):
        return _execute(owner, campaign_id, session_id, sequence_id, expected_version, confirmation)


def _execute(owner, campaign_id, session_id, sequence_id, expected_version, confirmation):
    with owner._lane(campaign_id):
        queue = _load(owner, session_id, sequence_id)
        if queue and _needs_recovery(owner, queue):
            read(owner, session_id=session_id, sequence_id=sequence_id)
            queue = _load(owner, session_id, sequence_id)
        if queue is None:
            raise CommandRejected("Action sequence does not exist")
        if queue["campaign_id"] != campaign_id:
            raise CommandRejected("Action sequence belongs to another campaign")
        # Duplicate/uncertain submissions return the durable settled result, never re-execute it.
        if queue["status"] in {"completed", "failed", "stale", "paused"}:
            return _project(queue)
        if queue["version"] != expected_version and not (
            queue["status"] == "running" and queue.get("submission_version") == expected_version
        ):
            raise StaleRevision("Action sequence version changed")
        if confirmation != (
            _consent(queue)
            if queue["status"] != "running"
            else queue.get("submission_confirmation")
        ):
            raise CommandRejected(
                "Inspect and confirm the ordered actions first", code="action_confirmation_required"
            )
        if queue["status"] != "running":
            queue["submission_version"] = expected_version
            queue["submission_confirmation"] = confirmation
        _bind_execution_basis(owner, queue)
        queue["status"] = "running"
        for step in queue["steps"]:
            if step["status"] == "committed":
                continue
            snapshot = owner._store.snapshot(campaign_id)
            envelope = step.get("execution")
            hit = owner._store.action_hit(campaign_id, step["operation_id"])
            # Recover an uncertain native commit before comparing its advanced revision.
            if hit is not None and envelope is not None:
                receipt = owner._player_action_in_owned_work(**envelope)
                if snapshot.revision != receipt.resulting_revision or _watermark(
                    snapshot
                ) != _commit_watermark(snapshot, receipt.action_id):
                    step["receipt"], step["status"] = _receipt(receipt), "committed"
                    queue["status"], queue["error"] = (
                        "stale",
                        "Campaign changed after the saved commitment",
                    )
                    return _save(owner, queue)
            else:
                if (
                    snapshot.revision != queue["expected_revision"]
                    or _watermark(snapshot) != queue["event_watermark"]
                ):
                    queue["status"], queue["error"] = (
                        "stale",
                        "Campaign changed outside this sequence",
                    )
                    return _save(owner, queue)
                if (
                    _scope(owner, snapshot) != queue["scope"]
                    or owner._store.current_open_turn(campaign_id) != queue["turn_id"]
                ):
                    queue["status"], queue["pause_reason"] = "paused", "Control or turn changed"
                    return _save(owner, queue)
                try:
                    if envelope is None:
                        selection = _rebind_map(owner, snapshot, step)
                        tool, _, _, _ = _resolve(owner, snapshot, step["source"], selection)
                        if (
                            tool.package_sha256 != step["package_sha256"]
                            or tool.name != step["tool"]
                        ):
                            raise CommandRejected("Queued package or declared mechanic changed")
                        envelope = {
                            "campaign_id": campaign_id,
                            "source": step["source"],
                            "operation_id": step["operation_id"],
                            "expected_revision": snapshot.revision,
                            "selection": selection,
                            "session_id": session_id,
                        }
                        preview = owner._player_action_in_owned_work(**envelope, preview_only=True)
                        envelope["confirmation"] = preview["confirmation"]
                        step["execution"] = envelope
                        step["status"] = "executing"
                        _save(owner, queue)  # Persist exact envelope before any native transaction.
                    receipt = owner._player_action_in_owned_work(**envelope)
                except AuthorityError as error:
                    step["status"], step["error"] = "pending", str(error)
                    queue["status"], queue["error"] = "failed", str(error)
                    return _save(owner, queue)
            step["receipt"] = _receipt(receipt)
            if isinstance(receipt, AwaitingChoiceReceipt):
                step["status"] = "awaiting_choice"
                queue["status"], queue["pause_reason"] = (
                    "paused",
                    "Resolve the saved mechanics choice",
                )
                return _save(owner, queue)
            if str(receipt.status) != "committed":
                step["status"], step["error"] = "pending", "Native action did not commit"
                queue["status"], queue["error"] = "failed", step["error"]
                return _save(owner, queue)
            step["status"] = "committed"
            with owner._narration_lock:
                attempts = [
                    a
                    for a in owner._attempts.values()
                    if a.campaign_id == campaign_id
                    and not a.terminal
                    and a.turn_id == queue["turn_id"]
                ]
                if len(attempts) == 1:
                    owner.bind_narration_action(
                        campaign_id, attempts[0].attempt_id, receipt.action_id
                    )
            after = owner._store.snapshot(campaign_id)
            queue["expected_revision"], queue["event_watermark"] = after.revision, _watermark(after)
            if (
                _scope(owner, after) != queue["scope"]
                or owner._store.current_open_turn(campaign_id) != queue["turn_id"]
            ):
                queue["status"], queue["pause_reason"] = (
                    "paused",
                    "Native action reached a control or turn boundary",
                )
                return _save(owner, queue)
            # The package's authoritative fresh catalog declares required engine transitions.
            # Required reactions and lifecycle stop the queue through the native catalog.
            required = [
                t
                for t in owner._catalog(campaign_id)
                if str(t.model_behavior) in {
                    "required_engine_transition", "required_ruleset_decision",
                }
            ]
            if required:
                queue["status"], queue["pause_reason"] = (
                    "paused",
                    "Required Ruleset decision: " + ", ".join(t.name for t in required),
                )
                titles = list(dict.fromkeys(t.decision_title for t in required if t.decision_title))
                queue["pause_summary"] = (
                    "Resolve " + "; ".join(titles) + " before continuing queued actions."
                    if titles else None
                )
                return _save(owner, queue)
            _save(owner, queue)
        queue["status"] = "completed"
        return _save(owner, queue)
