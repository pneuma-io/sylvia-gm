"""Playable named-spell intent through generic model-facing D&D mechanics."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import ActionStatus, CanonicalEventType, StateValue, WriterRole
from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record
from sylvia_gm.provider._base import Provider, ProviderResponse, ProviderStreamChunk

PACKAGE_ROOT = Path(__file__).parents[1] / "rulesets" / "srd-lite"
PACKAGE_ID = "sylvia.srd-lite.bg3"


def _package_archive() -> bytes:
    """Build the current package archive without relying on generated test fixtures."""

    draft = json.loads((PACKAGE_ROOT / "package-record.json").read_text(encoding="utf-8"))
    relative_paths = tuple(
        sorted(
            path.relative_to(PACKAGE_ROOT).as_posix()
            for root in (PACKAGE_ROOT / "content", PACKAGE_ROOT / "runtime")
            for path in root.rglob("*")
            if path.is_file()
        )
    )
    files = {path: (PACKAGE_ROOT / path).read_bytes() for path in relative_paths}
    record = {
        **draft,
        "files": [
            {
                "path": path,
                "bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
            for path, value in files.items()
        ],
    }
    record["content_sha256"] = content_root_for_record(record)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            canonical_json_bytes({"format": 3, "packages": [record]}),
        )
        for path, value in files.items():
            archive.writestr(f"packages/{PACKAGE_ID}/{path}", value)
    return output.getvalue()


class _GenericSpellProvider(Provider):
    """Script one ordinary model decision while exercising the production adapter."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        raise AssertionError("The canonical DM path must use provider streaming")

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        assert tools is not None
        self.calls.append((messages.copy(), tools))
        round_index = len(self.calls) - 1
        functions = {str(tool["function"]["name"]): tool["function"] for tool in tools}

        def alias(tool_name: str) -> str:
            return next(name for name in functions if name.endswith(f"_{tool_name}"))

        if round_index == 0:
            system_text = str(messages[0]["content"])
            assert "complete current Ruleset surface exposed by the engine through MCP" in (
                system_text
            )
            assert any(
                message["role"] == "user" and "Delayed Blast Fireball" in str(message["content"])
                for message in messages
            )
            assert {name.rsplit("_", 1)[-1] for name in functions}  # aliases are nonempty
            assert any(name.endswith("_d20_test") for name in functions)
            assert any(name.endswith("_start_combat_encounter_entities") for name in functions)
            assert not any(name.endswith("_cast_spell") for name in functions)
            assert not any(name.endswith("_apply_srd_damage") for name in functions)
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "call-generic-save",
                    "function": {
                        "name": alias("d20_test"),
                        "arguments": json.dumps(
                            {
                                "actor_state": "cultist",
                                "ability": "dexterity",
                                "kind": "saving_throw",
                                "ability_modifier": 0,
                                "proficiency_bonus": 0,
                                "proficiency_factor": "none",
                                "circumstantial_modifier": 0,
                                "target_number": 15,
                                "advantage": False,
                                "disadvantage": False,
                            },
                            separators=(",", ":"),
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
            return

        tool_message = next(message for message in reversed(messages) if message["role"] == "tool")
        mechanic = json.loads(str(tool_message["content"]))
        assert mechanic["status"] == CanonicalEventType.ACTION_COMMITTED.value

        assert round_index == 1
        context = mechanic["result"]["context"]
        assert context["_outcome"] == "failure"
        yield ProviderStreamChunk(
            content=(
                "The paralyzed cultist fails the Dexterity save. Delayed Blast Fireball's "
                "damage is not encoded in the currently exposed mechanic surface, so no damage "
                "or Hit Point change is claimed."
            ),
            finish_reason="stop",
        )

    async def close(self) -> None:
        await asyncio.sleep(0)


def test_named_spell_uses_generic_mcp_mechanics_and_reopens(tmp_path: Path) -> None:
    database = tmp_path / "generic-spell-intent.db"
    campaign_id = "campaign-generic-spell-intent"
    session_id = "session-generic-spell-intent"
    turn_id = "turn-generic-spell-intent"
    owner_id = "owner-generic-spell-intent"
    provider = _GenericSpellProvider()

    with AuthorityOwner.open(database, owner_id, disconnect_grace_seconds=1.0) as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(_package_archive()),
            install_id="install-generic-spell-intent",
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id=campaign_id,
                display_name="Generic named spell intent",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id=turn_id,
                initial_state=(
                    StateValue(
                        PACKAGE_ID,
                        "entities",
                        {
                            "cultist": {
                                "conditions": ["paralyzed"],
                                "exhaustion_level": 0,
                                "dodging": False,
                                "is_alive": True,
                                "hp": 20,
                                "max_hp": 20,
                                "temp_hp": 0,
                                "creature_kind": "monster",
                                "damage_immunities": [],
                                "damage_resistances": [],
                                "damage_vulnerabilities": [],
                                "death_successes": 0,
                                "death_failures": 0,
                                "stable": False,
                                "stable_recovery_hours_remaining": 0,
                            }
                        },
                    ),
                ),
            )
        )
        owner.create_agent_session(
            campaign_id=campaign_id,
            role=WriterRole.DM,
            provider_id="scripted-openai-compatible",
            model_id="scripted-generic-spell-model",
            session_id=session_id,
        )
        result = CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id=session_id,
                turn_id=turn_id,
                attempt_id="attempt-generic-spell-intent",
                message_id="message-generic-spell-intent",
                user_text=(
                    "Mara releases Delayed Blast Fireball beside the paralyzed cultist. "
                    "Resolve the save and use 7 fire damage as the table's GM ruling for this cast."
                ),
            ),
            ProviderSessionAdapter(owner, provider, owner_id=owner_id).for_turn(turn_id),
        )

        assert len(provider.calls) == 2
        assert tuple(receipt.status for receipt in result.receipts) == (ActionStatus.COMMITTED,)
        snapshot = owner.snapshot(campaign_id)
        assert snapshot.revision == 1
        assert snapshot.state[f"{PACKAGE_ID}.entities"]["cultist"]["hp"] == 20
        event_types = tuple(event.event_type for event in result.canonical)
        assert event_types.count(CanonicalEventType.TOOL_CALL) == 1
        assert event_types.count(CanonicalEventType.ACTION_COMMITTED) == 1
        talk = next(
            event
            for event in reversed(result.canonical)
            if event.event_type is CanonicalEventType.TALK
        )
        assert "no damage or Hit Point change" in talk.safe_payload["text"]

    with AuthorityOwner.open(
        database,
        "owner-generic-spell-intent-reopened",
        disconnect_grace_seconds=1.0,
    ) as reopened:
        reopened_snapshot = reopened.snapshot(campaign_id)
        assert reopened_snapshot.revision == 1
        assert reopened_snapshot.state[f"{PACKAGE_ID}.entities"]["cultist"]["hp"] == 20
        reopened_talk = next(
            event
            for event in reversed(reopened_snapshot.events)
            if event.event_type is CanonicalEventType.TALK
        )
        assert reopened_talk.safe_payload == talk.safe_payload

    print(
        "ALPHA-04 GENERIC SPELL INTENT",
        "spell=delayed_blast_fireball specialized=false",
        "tools=d20_test,start_combat",
        "revision=0->1 hp=20 unchanged reopen=exact",
    )
