"""Independent Builders publish reusable content using their retained package context."""

import json
from pathlib import Path

import pytest

from sylvia_gm.agent._builder_session import _participant_artifact_tool
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.application.reusable_participants import load_reusable_participant
from sylvia_gm.domain.authority import PackageRole
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.domain.package_library import canonical_json_bytes, participant_schema_sha256
from sylvia_gm.provider._base import FakeProvider, ProviderResponse, ProviderStreamChunk, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness


def install_character_packages(owner, *, setting_characters=()):
    fixtures = Path(__file__).parent / "fixtures"
    roots = (
        fixtures / "rulesets/signal-and-static",
        fixtures / "campaign-settings/signal-and-static",
    )
    records = [json.loads((root / "package-record.json").read_text()) for root in roots]
    records[0]["dependencies"] = [
        {
            "namespace": "campaign_setting",
            "kind": "campaign_setting",
            "id": records[1]["id"],
            "version": records[1]["version"],
        }
    ]
    schema = {
        "$id": "signal.operator.v1",
        "type": "object",
        "additionalProperties": False,
        "properties": {"name": {"type": "string"}, "callsign": {"type": "string"}},
        "required": ["name", "callsign"],
    }
    state_schema = {
        "type": "object",
        "properties": {"stress": {"type": "integer"}},
        "required": ["stress"],
        "additionalProperties": False,
    }
    creation_contract_sha256 = participant_schema_sha256(
        schema,
        entity_state_schema=state_schema,
    )
    records[0]["participant_artifacts"] = [
        {
            "kind": "operator",
            "schema_id": "signal.operator.v1",
            "schema": schema,
            "schema_sha256": creation_contract_sha256,
            "entity_state_schema": state_schema,
            "initial_state_calculations": [],
        }
    ]
    drafts = []
    for root, record in zip(roots, records, strict=True):
        payloads = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            # This Character fixture uses the declared package, not the separate Builder scenarios.
            if path.is_file() and path.name not in {"package-record.json", "builder-tests.json"}
        }
        if record["participant_artifacts"]:
            content = json.loads(payloads["content/content-declarations.json"])
            content["inclusions"][0]["links"]["schemas"] = ["signal.operator.v1"]
            payloads["content/content-declarations.json"] = canonical_json_bytes(content)
        else:
            content = json.loads(payloads["content/content-declarations.json"])
            for character in setting_characters:
                character_path = f"characters/{character['id']}.json"
                payloads[character_path] = canonical_json_bytes(
                    {
                        "format_id": "sylvia-gm-character-file",
                        "format_version": 1,
                        "schema": {
                            "id": schema["$id"],
                            "content_sha256": creation_contract_sha256,
                        },
                        **character,
                    }
                )
                content["inclusions"][0]["links"]["artifacts"].append(character_path)
            content["inclusions"][0]["links"]["artifacts"].sort()
            payloads["content/content-declarations.json"] = canonical_json_bytes(content)
        drafts.append(NormalizedPackageDraft(record=record, payloads=payloads))
    with materialize_package_archive(drafts) as archive:
        candidate = owner.install_package_archive(
            archive.archive, install_id="install-character-signal"
        )
    return candidate, records[1]["id"]


class LibraryProvider(FakeProvider):
    async def generate_stream(self, messages, tools=None, **_kwargs):
        response = await self.generate(messages, tools)
        if response.tool_calls:
            for index, call in enumerate(response.tool_calls):
                yield ProviderStreamChunk(
                    tool_call_delta={
                        "index": index,
                        "id": call.id,
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                )
            yield ProviderStreamChunk(finish_reason="tool_calls")
        else:
            yield ProviderStreamChunk(content=response.content or "", finish_reason="stop")


def launch_library_builder(harness, candidate_id, setting_id, purpose):
    draft = harness.request(
        "new-library-interview",
        "create_guided_campaign_draft",
        {
            "operation_id": "library-interview",
            "display_name": "Signal campaign",
            "provider_id": "test.transport",
            "model_id": "test.transport/model0",
        },
    )
    for revision, (step, answer) in enumerate(
        (
            ("ruleset", {"candidate_id": candidate_id}),
            ("setting", {"setting_id": setting_id, "candidate_id": candidate_id}),
            ("plan", {"plan_id": None}),
            ("name", {"display_name": "Signal campaign"}),
        )
    ):
        harness.request(
            f"select-{step}",
            "advance_guided_campaign_draft",
            {
                "operation_id": f"select-{step}",
                "campaign_id": draft["campaign_id"],
                "builder_session_id": draft["builder_session_id"],
                "expected_construction_revision": revision,
                "current_step": step,
                "visible_answer": step,
                "answer": answer,
            },
        )
        if (purpose == "plan" and step == "setting") or (
            purpose == "setting" and step == "ruleset"
        ):
            break
    launched = harness.request(
        "launch-library-builder",
        "start_guided_creation_subflow",
        {
            "operation_id": "launch-library-builder",
            "campaign_id": draft["campaign_id"],
            "parent_session_id": draft["builder_session_id"],
            "expected_construction_revision": {"plan": 2, "setting": 1}.get(purpose, 4),
            "purpose": purpose,
            "visible_answer": f"New {purpose}",
        },
    )
    return draft, launched["child_session_id"]


@pytest.mark.parametrize("purpose", ["player_character", "companion"])
def test_independent_character_save_uses_original_non_dnd_context(tmp_path, purpose):
    database = tmp_path / "library.db"
    with AuthorityOwner.open(database, "owner-library-seed") as owner:
        candidate, setting_id = install_character_packages(owner)
    participant = {"name": "Vela", "callsign": "Northstar"}
    provider = LibraryProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="invalid-vela",
                        name="publish_participant_artifact",
                        arguments=json.dumps(
                            {
                                "summary": "A synthetic signal operator.",
                                "authored_profile": (
                                    "Private draft text must not enter diagnostics."
                                ),
                                "participant": {
                                    "name": "Vela",
                                    "callsign": {"item": "private value"},
                                },
                                "entity_state": json.dumps({"stress": 0}),
                            }
                        ),
                    )
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="save-vela",
                        name="publish_participant_artifact",
                        arguments=json.dumps(
                            {
                                "summary": "A synthetic signal operator.",
                                "authored_profile": "# Vela\n\nA courier from the relay station.",
                                "participant": participant,
                                "entity_state": json.dumps({"stress": 0}),
                                "schema_id": "signal.operator.v1",
                            }
                        ),
                    )
                ]
            ),
            ProviderResponse(content="Vela is saved in the local library."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        draft, session_id = launch_library_builder(
            harness, candidate.candidate_id, setting_id, purpose
        )
        if purpose == "companion":
            harness.request(
                "rename-companion",
                "rename_builder_chat",
                {
                    "session_id": session_id,
                    "display_name": "My relay companion",
                },
            )
        harness.request(
            "change-original-interview",
            "advance_guided_campaign_draft",
            {
                "operation_id": "change-original-interview",
                "campaign_id": draft["campaign_id"],
                "builder_session_id": draft["builder_session_id"],
                "expected_construction_revision": 4,
                "current_step": "ruleset",
                "visible_answer": "No Ruleset",
                "answer": {"candidate_id": None},
            },
        )
        result = harness.request(
            "save-character",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "save-character-attempt",
                "message_id": "save-character",
                "user_text": "",
                "builder_start": True,
            },
        )
        assert result["interruption"] is None
        assert len(provider.calls) == 3
        exposed = next(
            tool["function"]["parameters"]
            for tool in json.loads(json.dumps(provider.calls[0]["tools"]))
            if tool["function"]["name"] == "publish_participant_artifact"
        )
        assert exposed["properties"]["participant"]["properties"]["callsign"] == {"type": "string"}
        assert exposed["properties"]["participant"]["additionalProperties"] is False
        assert exposed["properties"]["entity_state"]["type"] == "string"
        rejected = json.loads(
            next(
                message["content"]
                for message in provider.calls[1]["messages"]
                if message.get("tool_call_id") == "invalid-vela"
            )
        )
        assert rejected["state_changed"] is False
        assert rejected["validation_issues"] == [
            {
                "parameter": "participant",
                "path": "callsign",
                "kind": "wrong_type",
                "expected_type": "string",
                "actual_type": "object",
                "parent_path": "$",
            }
        ]
        assert "private" not in json.dumps(rejected).lower()
        saved = json.loads(
            next(
                message["content"]
                for message in provider.calls[-1]["messages"]
                if message.get("tool_call_id") == "save-vela"
            )
        )
        assert saved["success"] is True, saved
        assert saved["kind"] == purpose
        assert saved["campaign_setting"]["id"] == setting_id
        assert saved["ruleset"]["id"] == "sylvia-gm.conformance.signal-static"
        chats = harness.request("saved-character-title", "builder_chat_index")
        title = next(
            chat["display_name"] for chat in chats if chat["session"]["session_id"] == session_id
        )
        assert title == ("My relay companion" if purpose == "companion" else "Vela · Character")
        assert "builder_title_preference" not in json.dumps(provider.calls[0]["messages"])
        catalog = harness.request("refresh-library", "campaign_creation_catalog")
        assert (
            next(item for item in catalog if item.get("id") == saved["id"])["campaign_ready"]
            is True
        )
        parent = harness.request(
            "parent-after-save", "session_context", {"session_id": draft["builder_session_id"]}
        )
        assert parent["messages"][-1]["content"]["construction_revision"] == 5
    finally:
        harness.close()
    with AuthorityOwner.open(database, "owner-library-reopen") as owner:
        declaration = owner.package_participant_declarations(candidate.candidate_id)[0]
        package = next(
            entry for entry in candidate.package_lock.entries if entry.role is PackageRole.PRIMARY
        )
        setting = next(
            entry for entry in candidate.package_lock.entries if entry.package_id == setting_id
        )
        loaded = load_reusable_participant(
            tmp_path,
            saved["id"],
            package=package,
            declaration=declaration,
            campaign_setting=PackageBinding(setting.package_id, setting.version, setting.sha256),
        )
        assert loaded.participant == participant
        assert loaded.authored_profile == "# Vela\n\nA courier from the relay station."
        assert owner.session_context(session_id).session.campaign_id is None
        if purpose == "companion":
            assert (
                owner.rename_builder_chat(
                    session_id, "Automatic replacement", automatic=True
                ).display_name
                == title
            )
        else:
            assert (
                next(
                    chat.display_name
                    for chat in owner.builder_chat_index()
                    if chat.session.session_id == session_id
                )
                == title
            )


def test_multiple_character_schemas_require_an_explicit_valid_choice():
    calls = []

    def save(**values):
        calls.append(values)
        return {"kind": "companion", "id": "test-character"}

    tool = _participant_artifact_tool(
        role="companion",
        publisher=save,
        schemas={
            "signal.operator.v1": {
                "type": "object",
                "properties": {"callsign": {"type": "string"}},
            },
            "signal.drone.v1": {"type": "object", "properties": {"charge": {"type": "integer"}}},
        },
    )
    from sylvia_gm.tools._converter import tool_spec_to_openai

    exposed = tool_spec_to_openai(tool)["function"]["parameters"]
    assert len(exposed["properties"]["participant"]["anyOf"]) == 2
    assert "schema_id" in exposed["required"]
    params = {
        "summary": "Drone",
        "authored_profile": "Drone profile",
        "participant": {"name": "Drone"},
        "entity_state": json.dumps({}),
    }
    assert tool.handler(params, None)["success"] is False
    assert tool.handler({**params, "schema_id": "unrelated"}, None)["success"] is False
    assert calls == []
    assert tool.handler({**params, "schema_id": "signal.drone.v1"}, None)["success"] is True
    assert calls[0]["schema_id"] == "signal.drone.v1"


def test_participant_entity_state_json_text_preserves_types_on_reload(tmp_path):
    from sylvia_gm.application.reusable_participants import save_reusable_participant
    from sylvia_gm.domain.authority import PackageLockEntry, owned_payload
    from sylvia_gm.domain.package_library import (
        ParticipantArtifactDeclaration,
        participant_schema_sha256,
    )
    from sylvia_gm.tools._converter import tool_spec_to_openai

    participant_schema = {
        "$id": "synthetic.typed.participant.v1",
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }
    state_schema = {
        "type": "object",
        "properties": {
            "hp": {"type": "integer"},
            "speed": {"type": "number"},
            "conscious": {"type": "boolean"},
            "inventory": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "quantity": {"type": "integer"},
                    },
                    "required": ["id", "quantity"],
                    "additionalProperties": False,
                },
            },
            "conditions": {"type": "array", "items": {"type": "string"}},
            "second_wind": {
                "type": "object",
                "properties": {
                    "available": {"type": "boolean"},
                    "uses": {"type": "integer"},
                },
                "required": ["available", "uses"],
                "additionalProperties": False,
            },
            "resources": {
                "type": "object",
                "properties": {
                    "focus": {
                        "type": "object",
                        "properties": {
                            "current": {"type": "integer"},
                            "maximum": {"type": "integer"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["current", "maximum", "tags"],
                        "additionalProperties": False,
                    }
                },
                "required": ["focus"],
                "additionalProperties": False,
            },
        },
        "required": [
            "hp",
            "speed",
            "conscious",
            "inventory",
            "conditions",
            "second_wind",
            "resources",
        ],
        "additionalProperties": False,
    }
    package = PackageLockEntry(
        package_id="synthetic.typed-rules",
        version="1.0.0",
        sha256="8" * 64,
        role=PackageRole.PRIMARY,
        load_order=0,
        dependencies=(),
        capabilities=(),
    )
    declaration = ParticipantArtifactDeclaration(
        kind="player_character",
        schema_id="synthetic.typed.participant.v1",
        schema_sha256=participant_schema_sha256(
            participant_schema,
            entity_state_schema=state_schema,
        ),
        schema=participant_schema,
        entity_state_schema=state_schema,
    )
    setting = PackageBinding("synthetic.typed-setting", "1.0.0", "9" * 64)
    published_states = []

    def save(**values):
        published_states.append(values["entity_state"])
        return save_reusable_participant(
            tmp_path,
            operation_id="typed-entity-state",
            package=package,
            declaration=declaration,
            campaign_setting=setting,
            **values,
        ).catalog_payload()

    tool = _participant_artifact_tool(
        role="companion",
        publisher=save,
        schemas={declaration.schema_id: owned_payload(declaration.schema)},
    )
    exposed = tool_spec_to_openai(tool)["function"]["parameters"]
    assert exposed["properties"]["participant"] == declaration.schema
    assert exposed["properties"]["entity_state"]["type"] == "string"

    entity_state = {
        "hp": 21,
        "speed": 5.5,
        "conscious": True,
        "inventory": [
            {"id": "synthetic-rope", "quantity": 1},
            {"id": "synthetic-rations", "quantity": 3},
        ],
        "conditions": [],
        "second_wind": {"available": False, "uses": 1},
        "resources": {"focus": {"current": 2, "maximum": 3, "tags": ["psi", "daily"]}},
    }
    result = tool.handler(
        {
            "summary": "A synthetic companion.",
            "authored_profile": "# Sable\n\nA synthetic profile.",
            "participant": {"name": "Sable"},
            "entity_state": json.dumps(entity_state),
        },
        None,
    )

    assert result["success"] is True, result
    assert published_states == [entity_state]
    loaded = load_reusable_participant(
        tmp_path,
        result["id"],
        package=package,
        declaration=declaration,
        campaign_setting=setting,
    )
    assert loaded.entity_state == entity_state


@pytest.mark.parametrize(
    "entity_state",
    [
        {"hp": 12},
        '{"hp": 12',
        '[{"id": "synthetic-rope"}]',
        '{"hp": 12, "hp": 13}',
        '{"hp": NaN}',
        json.dumps({"synthetic_blob": "x" * (256 * 1024)}),
        '{"nested":' * 34 + "0" + "}" * 34,
    ],
    ids=[
        "nonstring",
        "malformed",
        "nonobject",
        "duplicate-key",
        "nonfinite",
        "oversized",
        "over-nested",
    ],
)
def test_participant_entity_state_rejects_invalid_model_input_without_publication(
    entity_state,
):
    published = []

    def save(**values):
        published.append(values)
        return {"kind": "companion", "id": "must-not-publish"}

    tool = _participant_artifact_tool(role="companion", publisher=save)
    result = tool.handler(
        {
            "summary": "A synthetic companion.",
            "authored_profile": "# Sable\n\nA synthetic profile.",
            "participant": {"name": "Sable"},
            "entity_state": entity_state,
        },
        None,
    )

    assert result["success"] is False
    assert result["status"] == "rejected"
    assert result["code"] == "participant_artifact_invalid"
    assert result["state_changed"] is False
    message = result.get("message")
    assert isinstance(message, str)
    assert "entity_state" in message
    assert len(message) <= 500
    assert published == []


def test_srd_character_type_errors_are_correctable_without_partial_publication(tmp_path):
    from sylvia_gm.application.bundled_packages import bundled_rift_authority
    from sylvia_gm.application.reusable_participants import save_reusable_participant
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.tools._converter import tool_spec_to_openai
    from tests.test_reusable_participants import (
        _participant,
        _participant_authority,
        _starting_state,
    )

    package, declaration = _participant_authority()
    setting = bundled_rift_authority().setting

    def save(**values):
        return save_reusable_participant(
            tmp_path,
            operation_id="corrected-character",
            package=package,
            declaration=declaration,
            campaign_setting=setting,
            **values,
        ).catalog_payload()

    tool = _participant_artifact_tool(
        role="companion",
        publisher=save,
        schemas={declaration.schema_id: owned_payload(declaration.schema)},
    )
    exposed = json.loads(json.dumps(tool_spec_to_openai(tool)))["function"]["parameters"]
    assert exposed["properties"]["participant"] == declaration.schema
    exposed["properties"]["participant"]["properties"]["level"]["type"] = "string"
    unchanged = tool_spec_to_openai(tool)["function"]["parameters"]["properties"]["participant"]
    assert unchanged == declaration.schema

    participant = _participant("Synthetic guard")
    params = {
        "summary": "A synthetic companion.",
        "authored_profile": "Private character narrative.",
        "participant": {
            **participant,
            "level": "1",
            "classes": {"item": {"id": "fighter", "level": "1"}},
        },
        "entity_state": json.dumps(_starting_state(max_hp=12)),
    }
    rejected = tool.handler(params, None)
    assert rejected["success"] is False
    assert rejected["state_changed"] is False
    assert {
        issue["path"]: (issue["expected_type"], issue["actual_type"])
        for issue in rejected["validation_issues"]
    } == {
        "classes": ("array", "object"),
        "level": ("integer", "string"),
    }
    assert "Private character narrative" not in json.dumps(rejected)
    assert not (tmp_path / "participants").exists()
    saved = tool.handler({**params, "participant": participant}, None)
    assert saved["success"] is True, saved
    loaded = load_reusable_participant(
        tmp_path,
        saved["id"],
        package=package,
        declaration=declaration,
        campaign_setting=setting,
    )
    assert loaded.participant["level"] == 1
    assert loaded.participant["classes"] == [{"id": "fighter", "level": 1}]
    assert loaded.entity_state["hp"] == 12


def test_library_refresh_cannot_see_a_partially_saved_character(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sylvia_gm.application import reusable_participants as library
    from sylvia_gm.application.bundled_packages import bundled_rift_authority
    from tests.test_reusable_participants import (
        _participant,
        _participant_authority,
        _starting_state,
    )

    written = Event()
    finish = Event()
    actual_write = library._atomic_write

    def hold_profile_write(path, value):
        actual_write(path, value)
        if path.name == "profile.md":
            written.set()
            assert finish.wait(10)

    monkeypatch.setattr(library, "_atomic_write", hold_profile_write)
    package, declaration = _participant_authority()
    setting = bundled_rift_authority().setting
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            library.save_reusable_participant,
            tmp_path,
            operation_id="concurrent-save",
            role="player_character",
            summary="Mira",
            authored_profile="Mira watches the harbor.",
            participant=_participant("Mira"),
            entity_state=_starting_state(),
            package=package,
            declaration=declaration,
            campaign_setting=setting,
        )
        try:
            assert written.wait(10)
            assert (
                library.compatible_reusable_participants(
                    tmp_path, package=package, declaration=declaration, campaign_setting=setting
                )
                == ()
            )
        finally:
            finish.set()
        saved = future.result(timeout=10)
    assert library.compatible_reusable_participants(
        tmp_path, package=package, declaration=declaration, campaign_setting=setting
    ) == (saved,)


def test_independent_plan_saves_and_can_be_selected_as_a_later_builder_reference(tmp_path):
    from sylvia_gm.application.builder_references import reference_reader
    from tests.test_builder_references import launch_character, open_character_interview

    database = tmp_path / "plan-library.db"
    with AuthorityOwner.open(database, "owner-plan-library-seed") as owner:
        candidate, setting_id = install_character_packages(owner)
    provider = LibraryProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="create-relay-plan",
                        name="create_campaign_plan_draft",
                        arguments=json.dumps(
                            {
                                "title": "Relay Intrusion",
                                "premise": "An operator follows an impossible signal.",
                                "tone": "Tense science fiction",
                                "player_character_mode": "compatible",
                                "custom_companion_minimum": 0,
                                "custom_companion_maximum": 3,
                                "starting_location_path": "locations/relay.md",
                                "opening_situation": "The relay broadcasts an impossible response.",
                                "primary_objective": "Secure the relay.",
                                "completion_conditions": ["The signal is disabled or the relay abandoned, and the keeper has responded to that outcome."],
                            }
                        ),
                    )
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(id="save-relay-plan", name="publish_campaign_plan", arguments="{}")
                ]
            ),
            ProviderResponse(content="Relay Intrusion is saved to the local library."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        draft, session_id = launch_library_builder(
            harness, candidate.candidate_id, setting_id, "plan"
        )
        result = harness.request(
            "save-plan",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "save-plan-attempt",
                "message_id": "save-plan",
                "user_text": "",
                "builder_start": True,
            },
        )
        assert result["interruption"] is None
        assert len(provider.calls) == 3
        result = json.loads(
            next(
                message["content"]
                for message in provider.calls[-1]["messages"]
                if message.get("tool_call_id") == "save-relay-plan"
            )
        )
        assert result["success"] is True, result
        saved = result["library_artifact"]
        assert saved["title"] == "Relay Intrusion"
        assert saved.get("setup_id") != "the-rift-awakening"
        catalog = harness.request("refresh-plans", "campaign_creation_catalog")
        assert next(item for item in catalog if item.get("id") == saved["id"]) == saved
        parent = harness.request(
            "parent-plan-after-save", "session_context", {"session_id": draft["builder_session_id"]}
        )
        assert parent["messages"][-1]["content"]["construction_revision"] == 2
    finally:
        harness.close()
    with AuthorityOwner.open(database, "owner-plan-library-reopen") as owner:
        selected = open_character_interview(owner, candidate.candidate_id, setting_id, saved["id"])
        context = launch_character(owner, tmp_path, selected)
        reader = reference_reader(owner, context)
        assert "Relay Intrusion" in reader.read("campaign-plan/plan.md")
        payload = json.loads(reader.read("campaign-plan/plan.json"))
        assert payload["bindings"]["package_closure_sha256"] == candidate.closure_sha256
        assert payload["starting_party"]["player_character"]["mode"] == "compatible"


def test_explicit_builder_title_can_change_while_stopped_and_remains_protected(tmp_path):
    database = tmp_path / "titles.db"
    with AuthorityOwner.open(database, "owner-titles") as owner:
        chat = owner.create_builder_chat(
            session_id="builder-titles", provider_id="test", model_id="test"
        )
        owner.stop_agent_session(chat.session.session_id)
        owner.rename_builder_chat(chat.session.session_id, "My chat")
        renamed = owner.rename_builder_chat(chat.session.session_id, "My renamed chat")
        assert renamed.display_name == "My renamed chat"
    with AuthorityOwner.open(database, "owner-titles-reopen") as owner:
        assert (
            owner.rename_builder_chat(
                "builder-titles", "An automatic name", automatic=True
            ).display_name
            == "My renamed chat"
        )


def test_ruleset_publication_refines_the_saved_chat_title_and_preserves_manual_renames(tmp_path):
    from sylvia_gm.builder._package_materializer import materialize_ruleset_draft
    from sylvia_gm.interfaces.owner_process import _publish_builder_ruleset_candidate
    from tests.test_alpha_f6g_builder_package_journey import _stage_ruleset_draft

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _stage_ruleset_draft(workspace)
    database = tmp_path / "ruleset-title.db"
    with AuthorityOwner.open(database, "titles") as owner:
        owner.create_builder_chat(session_id="rules", provider_id="test", model_id="test")
        with materialize_ruleset_draft(workspace) as materialized:

            def publish():
                materialized.archive.seek(0)
                return _publish_builder_ruleset_candidate(
                    owner,
                    "rules",
                    materialized.archive,
                    install_id="ruleset-title",
                    expected_closure_sha256=materialized.closure_sha256,
                )

            first = publish()
            primary = next(
                item
                for item in owner.inspect_package_candidate(first["candidate_id"])["packages"]
                if item["role"] == "primary"
            )
            assert owner.builder_chat_index()[0].display_name == f"{primary['label']} · Ruleset"
            owner.rename_builder_chat("rules", "My rules research")
            assert publish() == first
            assert owner.builder_chat_index()[0].display_name == "My rules research"
    with AuthorityOwner.open(database, "titles-reopen") as owner:
        assert owner.builder_chat_index()[0].display_name == "My rules research"
