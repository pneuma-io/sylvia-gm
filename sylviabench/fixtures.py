"""Build frozen SRD campaigns and drive scripted GM tools through the real owner."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.provider._base import FakeProvider, ProviderStreamChunk

from .contracts import ROOT, Setup, read_json, write_json
from .owner import OwnerClient

SRD = "sylvia.srd-lite.bg3"
FACT = "Use a blue lantern at the west gate."
SECRET = "copper heron seventeen"
Arguments = dict[str, Any] | Callable[[list[dict[str, Any]]], dict[str, Any]]


def receipt_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Retain outcome and public result cards, excluding large private engine contexts."""
    detail = payload.get("result", payload)
    if not isinstance(detail, dict):
        detail = {}
    return {
        "status": payload.get("status", detail.get("status")),
        "code": payload.get("code"),
        "result_card": detail.get("result_card"),
    }


class ScriptedGM(FakeProvider):  # type: ignore[misc]  # The app's provider module is untyped.
    """Emit only prescribed tool calls; never construct a network provider or fallback."""

    def __init__(self, steps: list[tuple[str, Arguments]], *, failures: int = 0) -> None:
        super().__init__()
        self.steps = steps
        self.position = 0
        self.failures = failures
        self.receipts: list[dict[str, Any]] = []
        self.tool_names: list[str] = []
        self._seen: set[str] = set()
        self._prefix = uuid.uuid4().hex

    async def generate_stream(self, messages: Any, tools: Any = None, **_kwargs: Any) -> Any:
        for message in messages:
            identity = message.get("tool_call_id")
            if identity and identity not in self._seen:
                self._seen.add(identity)
                self.receipts.append({"id": identity, "result": json.loads(message["content"])})
        self.tool_names = [t["function"]["name"] for t in tools or []]
        if self.failures:
            self.failures -= 1
            raise RuntimeError("Deliberately injected scripted interruption")
        if self.position >= len(self.steps):
            yield ProviderStreamChunk(
                content="The scripted response is complete.", finish_reason="stop"
            )
            return
        suffix, arguments = self.steps[self.position]
        names = [n for n in self.tool_names if n == suffix or n.endswith("_" + suffix)]
        if len(names) != 1:
            raise RuntimeError(f"Script requires one exposed {suffix} tool; found {names}")
        params = arguments(self.receipts) if callable(arguments) else arguments
        self.position += 1
        yield ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": f"bench-{self._prefix}-{self.position}",
                "function": {"name": names[0], "arguments": json.dumps(params)},
            },
            finish_reason="tool_calls",
        )


class ScriptedResolver:
    def __init__(self, provider: ScriptedGM) -> None:
        self.provider = provider

    def __call__(self, _session: Any) -> ScriptedGM:
        return self.provider

    def model_choices(self) -> tuple[dict[str, Any], ...]:
        return (
            {
                "provider_id": "sylviabench.scripted",
                "provider_name": "Scripted fixture",
                "model_id": "sylviabench.scripted/good",
                "model_name": "Scripted fixture",
                "context_window": 262144,
                "capability_registry_version": "sylviabench.v1",
                "capability_tier_id": None,
                "capability_evidence_sha256": None,
            },
        )


def settle(text: str, **changes: Any) -> tuple[str, Arguments]:
    return "update_campaign_file", {
        "state_change": "changed" if changes else "unchanged",
        "state_change_summary": text,
        "revised_player_facing_response": [{"kind": "narration", "text": text}],
        **changes,
    }


def observe(database: Path, campaign: str, session: str) -> dict[str, Any]:
    """Read committed state and player-visible TALK records after the owner is closed."""
    with AuthorityOwner.open(database, "sylviabench-observer") as owner:
        snapshot = owner.snapshot(campaign)
        context = owner.session_context(session)
        talks = []
        for message in context.messages:
            content = message.content
            if content.get("event_type") == "talk":
                payload = content.get("safe_payload", {})
                talks.append(str(payload.get("text", "")))
        return {
            "state": thaw_json_value(snapshot.state),
            "facts": thaw_json_value(snapshot.continuity.facts),
            "reveals": thaw_json_value(snapshot.continuity.reveals),
            "talks": talks,
            "mechanics": [
                {
                    "sequence": event.sequence,
                    "action_id": event.action_id,
                    "result": receipt_summary(thaw_json_value(event.safe_payload)),
                }
                for event in snapshot.events
                if event.event_type.value == "tool_result"
            ],
            "revision": snapshot.revision,
            "package_lock": [
                {"package_id": e.package_id, "version": e.version, "sha256": e.sha256}
                for e in snapshot.package_lock.entries
            ],
            "player_id": next(
                p.participant_id for p in snapshot.participants if p.kind == "player_character"
            ),
        }


def prepare(database: Path) -> dict[str, str]:
    """Use the supported campaign interview and Character Builder to admit the starter."""
    starter = next(
        row
        for row in read_json(ROOT / "src/sylvia_gm/campaign/srd-starters.json")
        if row["id"] == "cleric"
    )
    state = dict(starter["entity_state"])
    provider = ScriptedGM(
        [
            (
                "publish_participant_artifact",
                {
                    "summary": "A synthetic benchmark copy of the bundled Cleric starter.",
                    "authored_profile": starter["authored_profile"],
                    "participant": starter["participant"],
                    "entity_state": json.dumps(state),
                    "setting_data": "{}",
                },
            )
        ]
    )
    with OwnerClient(database, ScriptedResolver(provider), timeout=300) as client:
        client.request("set_admin_full_audit", {"enabled": True})
        draft = client.request(
            "create_guided_campaign_draft",
            {
                "operation_id": "bench-new",
                "display_name": "Synthetic benchmark",
                "provider_id": "sylviabench.scripted",
                "model_id": "sylviabench.scripted/good",
            },
        )
        campaign, interview = draft["campaign_id"], draft["builder_session_id"]

        def answer(revision: int, step: str, value: dict[str, Any]) -> None:
            client.request(
                "advance_guided_campaign_draft",
                {
                    "operation_id": "bench-" + step,
                    "campaign_id": campaign,
                    "builder_session_id": interview,
                    "expected_construction_revision": revision,
                    "current_step": step,
                    "visible_answer": step,
                    "answer": value,
                },
            )

        for index, (step, value) in enumerate(
            [
                ("ruleset", {"candidate_id": SRD}),
                ("setting", {"setting_id": "pneuma.severed-lands"}),
                ("plan", {"plan_id": "the-rift-awakening"}),
                ("name", {"display_name": "Synthetic benchmark"}),
            ]
        ):
            answer(index, step, value)
        child = client.request(
            "start_guided_creation_subflow",
            {
                "operation_id": "bench-character",
                "campaign_id": campaign,
                "parent_session_id": interview,
                "expected_construction_revision": 4,
                "purpose": "player_character",
                "visible_answer": "New Character",
            },
        )
        built = client.request(
            "submit_session_input",
            {
                "session_id": child["child_session_id"],
                "attempt_id": "bench-build",
                "message_id": "bench-build",
                "user_text": "Save the synthetic Cleric starter.",
            },
        )
        if built.get("interruption"):
            raise RuntimeError(f"Fixture character was rejected: {provider.receipts}")
        saved = next(
            (row["result"] for row in provider.receipts if row["result"].get("campaign_ready")),
            None,
        )
        if saved is None:
            raise RuntimeError(f"Fixture character validation failed: {provider.receipts}")
        answer(4, "participants", {"archetype_id": saved["id"], "companion_ids": []})
        answer(5, "boundaries_preferences", {"boundaries": "", "preferences": ""})
        answer(6, "specific_requests", {"no_additional_requests": True, "requests": ""})
        client.request(
            "start_guided_campaign",
            {
                "operation_id": "bench-ready",
                "campaign_id": campaign,
                "builder_session_id": interview,
                "display_name": "Synthetic benchmark",
                "candidate_id": SRD,
                "setting_id": "pneuma.severed-lands",
                "plan_id": "the-rift-awakening",
                "player_character_id": saved["id"],
                "companion_ids": [],
                "premise": "",
                "construction_intent": {
                    "boundaries": "",
                    "preferences": "",
                    "specific_requests": "",
                    "contributions": {},
                },
            },
        )
        final = client.request(
            "finalize_campaign_construction",
            {
                "campaign_id": campaign,
                "dm_provider_id": "sylviabench.scripted",
                "dm_model_id": "sylviabench.scripted/good",
                "model_guidance_override": True,
            },
        )
    return {"campaign": campaign, "session": final["handoff"]["dm_session_id"]}


def submit(
    database: Path, ids: dict[str, str], provider: ScriptedGM, text: str, identity: str
) -> dict[str, Any]:
    with AuthorityOwner.open(database, "bench-submit-basis") as owner:
        revision = owner.snapshot(ids["campaign"]).revision
        turn = owner.current_open_turn(ids["campaign"]) or identity + "-turn"
    with OwnerClient(database, ScriptedResolver(provider)) as client:
        client.request("set_admin_full_audit", {"enabled": True})
        reply = client.request(
            "submit_session_input",
            {
                "session_id": ids["session"],
                "attempt_id": identity,
                "message_id": identity,
                "turn_id": turn,
                "expected_revision": revision,
                "user_text": text,
            },
            campaign=ids["campaign"],
            attempt=identity,
        )
        if not isinstance(reply, dict):
            raise ValueError("The owner returned an invalid session result")
        return reply


def copy_save(source: Path, target: Path) -> None:
    """Use SQLite backup after the owner closes; include the campaign's ordinary local files."""
    if target.exists():
        raise ValueError("Refusing to replace an existing scenario save")
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as saved, closing(sqlite3.connect(target)) as copied:
        saved.backup(copied)
    for name in ("campaigns", "participants"):
        authored = source.parent / name
        if authored.exists():
            shutil.copytree(authored, target.parent / name)


def seed(directory: Path) -> tuple[Path, dict[str, str]]:
    """Establish facts with the scripted GM, then close and freeze its saved campaign."""
    database = directory / "seed" / "campaign.sqlite3"
    ids = prepare(database)
    memories = [
        {
            "fact_id": "bench-signal",
            "category": "promise",
            "text": FACT,
            "visibility": "player_known",
            "known_by": [],
            "subjects": ["west-gate"],
        },
        {
            "fact_id": "bench-password",
            "category": "continuity",
            "text": SECRET,
            "visibility": "gm_private",
            "known_by": [],
            "subjects": ["courier"],
        },
    ]
    actor = observe(database, **ids)["player_id"]
    provider = ScriptedGM(
        [
            (
                "resolve_srd_fall",
                {"target_state": actor, "distance": 10, "landing": "ground", "reaction": "none"},
            ),
            settle("After a short fall, we agreed: " + FACT, facts=memories),
        ]
    )
    reply = submit(
        database, ids, provider, "Establish the benchmark's synthetic starting facts.", "bench-seed"
    )
    if reply.get("interruption"):
        raise RuntimeError(f"Fixture seed failed: {reply}; {provider.receipts}")
    observation = observe(database, **ids)
    write_json(
        directory / "seed.json",
        {
            **ids,
            "observation": observation,
            "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        },
    )
    return database, ids


def script(scenario: str, setup: Setup, before: dict[str, Any]) -> ScriptedGM:
    """The bad driver changes narration only, making each checker failure attributable."""
    bad = setup.behavior == "bad"
    if scenario == "T1":
        actor = before["player_id"]

        def closing(receipts: list[dict[str, Any]]) -> dict[str, Any]:
            rendered = json.dumps(receipts)
            matches = re.findall(r"HP restored (\d+)", rendered)
            if not matches:
                raise RuntimeError("Healing tool returned no recorded HP-restored amount")
            amount = int(matches[-1]) + (1 if bad else 0)
            arguments = settle(f"Cure Wounds restores {amount} hit points.")[1]
            assert isinstance(arguments, dict)
            return arguments

        steps: list[tuple[str, Arguments]] = [
            (
                "cast_spell_outside_combat",
                {
                    "spell": "cure_wounds",
                    "caster": actor,
                    "caster_state": actor,
                    "target": actor,
                    "slot_resource": "spell_slot_1",
                    "target_distance": 0,
                    "caster_can_see_target": True,
                    "caster_can_hear_target": True,
                    "caster_can_touch_target": True,
                    "target_willing": True,
                    "verbal_component_available": True,
                    "somatic_component_available": True,
                    "material_component_available": True,
                    "material_item_ids": [],
                },
            ),
            ("update_campaign_file", closing),
        ]
    elif scenario == "T2":
        steps = [
            settle("The password is " + SECRET if bad else "The courier keeps the note private.")
        ]
    elif scenario == "T3":
        steps = [settle("Use a red flag at the east gate." if bad else FACT)]
    else:
        raise ValueError("Unknown scenario")
    return ScriptedGM(steps, failures={"recover": 1, "stuck": 3}.get(setup.behavior, 0))
