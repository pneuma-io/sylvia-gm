"""Plain Builders acquire exact sources through the real owner and tool loop."""

import json

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.builder_references import launch_references, reference_reader
from sylvia_gm.application.builder_source_selection import BuilderSourceSelection
from sylvia_gm.application.bundled_campaign_plans import bundled_plan
from sylvia_gm.application.bundled_packages import bundled_rift_authority
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider, install_character_packages
from tests.test_builder_references import launch_character, open_character_interview
from tests.test_setting_character_composition import _install_setting


@pytest.mark.parametrize("ruleset_first", [False, True])
def test_plain_builder_selects_discovered_bundle_and_retains_plan_sources(tmp_path, ruleset_first):
    database = tmp_path / "bundled-plan.db"
    bundled = bundled_rift_authority()
    steps = ([('rules', 'select_builder_ruleset', {'ruleset_id': bundled.ruleset.package_id})]
             if ruleset_first else []) + [
        ('discover', 'discover_campaign_content', {'query': 'D&D SRD 5.2.1'}),
        ('setting', 'select_builder_setting', {}),
        ('skill', 'load_builder_skill', {'name': 'BUILD CAMPAIGN PLAN'}),
        ('sources', 'inspect_campaign_sources', {'action': 'list'}),
        ('draft', 'create_campaign_plan_draft', {
            'title': 'Waystation Watch', 'premise': 'Welcome a new arrival.', 'tone': 'Hopeful',
            'player_character_mode': 'compatible',
            'starting_location_path': 'locations/waystation.md',
            'opening_situation': 'An arrival needs directions.',
            'primary_objective': 'Help the arrival reach shelter.',
            'completion_conditions': [
                'The arrival reaches shelter and thanks or refuses the guide.',
            ],
            'custom_companion_minimum': 0, 'custom_companion_maximum': 0,
        }),
    ]

    class BundledProvider(LibraryProvider):
        async def generate(self, messages, tools=None, **kwargs):
            if self._index < len(steps) and steps[self._index][0] == 'setting':
                discovered = next(json.loads(message['content']) for message in messages
                                  if message.get('tool_call_id') == 'discover')
                ruleset = next(item for item in discovered['items'] if item['kind'] == 'ruleset')
                setting = next(item for item in ruleset['compatible_settings']
                               if item['id'] == bundled.setting.package_id)
                self._responses[self._index] = ProviderResponse(tool_calls=[ToolCall(
                    id='setting', name='select_builder_setting', arguments=json.dumps({
                        'setting_id': setting['id'], 'candidate_id': setting.get('candidate_id'),
                    }),
                )])
            return await super().generate(messages, tools, **kwargs)

    provider = BundledProvider(responses=[
        *(ProviderResponse(tool_calls=[
            ToolCall(id=identity, name=name, arguments=json.dumps(args)),
        ])
          for identity, name, args in steps),
        ProviderResponse(content='The unpublished draft uses the bundled Setting.'),
    ])
    harness = _OwnerHarness(database, provider)
    try:
        chat = harness.request('create', 'create_builder_chat', {
            'operation_id': 'bundled-plan', 'provider_id': 'test.transport',
            'model_id': 'test.transport/model0',
        })
        session_id = chat['session']['session_id']
        result = harness.request('build', 'submit_session_input', {
            'session_id': session_id, 'attempt_id': 'build', 'message_id': 'build',
            'user_text': 'Draft a short Plan using bundled Severed Lands. Do not publish it.',
        })
        assert result['interruption'] is None, result
        results = {message['tool_call_id']: json.loads(message['content'])
                   for message in provider.calls[-1]['messages'] if message.get('role') == 'tool'}
        for identity in ('setting', 'sources', 'draft'):
            assert results[identity]['success'] is True, results[identity]
        references = results['setting']['references']
        assert next(item for item in references['packages'] if item['prefix'] == 'setting') == {
            'prefix': 'setting', 'id': bundled.setting.package_id,
            'version': bundled.setting.version, 'content_sha256': bundled.setting.content_sha256,
        }
    finally:
        harness.close()
    with AuthorityOwner.open(database, 'reopen-bundled-plan') as owner:
        context = owner.session_context(session_id)
        assert launch_references(context) == references
        selection = BuilderSourceSelection(
            owner, tmp_path, context, lambda: _campaign_creation_catalog(tmp_path, owner),
        )
        assert selection.select_setting(references['candidate_id'], bundled.setting.package_id)[
            'references'
        ] == references
        assert 'setting/locations/waystation.md' in selection.reader().paths
        with pytest.raises(CommandRejected, match='exact Setting'):
            selection.select_setting(None, 'unadvertised.setting')
        assert selection.references == references


@pytest.mark.parametrize("source_kind", ["bundled", "saved-setting"])
def test_plain_builder_continues_from_saved_setting_to_plan_in_same_turn(tmp_path, source_kind):
    database = tmp_path / "transition.db"
    ruleset_id = bundled_rift_authority().ruleset.package_id
    if source_kind == "saved-setting":
        database, candidate_id = _install_setting(tmp_path)
        ruleset_id = candidate_id
    setting = {"package_id": "pes.plan-harbor", "version": "1.0.0", "title": "Plan Harbor"}
    steps = [
        ("rules", "select_builder_ruleset", {
            "ruleset_id": ruleset_id,
        }),
        ("world", "write_file", {
            "path": "setting-draft/files/world.md", "content": "# Plan Harbor\nA village.",
        }),
        ("place", "write_file", {
            "path": "setting-draft/files/locations/pier.md", "content": "# Pier\nA quiet pier.",
        }),
        ("setting", "publish_campaign_setting", setting),
        ("skill", "load_builder_skill", {"name": "BUILD CAMPAIGN PLAN"}),
        ("source", "select_builder_setting", {}),
        ("sources", "inspect_campaign_sources", {"action": "list"}),
        ("draft", "create_campaign_plan_draft", {
            "title": "Harbor Watch", "premise": "Watch the pier.", "tone": "Quiet mystery",
            "player_character_mode": "compatible", "starting_location_path": "locations/pier.md",
            "opening_situation": "A stranger arrives.", "primary_objective": "Learn their purpose.",
            "completion_conditions": [
                "The stranger has stated their request and responded to its acceptance or refusal.",
            ],
            "custom_companion_minimum": 0, "custom_companion_maximum": 0,
        }),
        ("check", "check_campaign_plan_draft", {}),
        ("plan", "publish_campaign_plan", {}),
    ]
    if source_kind == "bundled":
        check_index = next(i for i, step in enumerate(steps) if step[0] == "check")
        steps[check_index:check_index] = [
            ("incomplete", "check_campaign_plan_draft", {}),
            ("read-plan", "read_file", {"path": "campaign-plan-draft/plan.json"}),
            ("state", "edit_file", {}),
        ]

    class TransitionProvider(LibraryProvider):
        async def generate(self, messages, tools=None, **kwargs):
            if self._index == 5:
                saved = next(json.loads(message["content"]) for message in messages
                             if message.get("tool_call_id") == "setting")
                self._responses[5] = ProviderResponse(tool_calls=[ToolCall(
                    id="source", name="select_builder_setting", arguments=json.dumps({
                        "candidate_id": saved["candidate_id"], "setting_id": saved["id"],
                    }),
                )])
            if self._index < len(steps) and steps[self._index][0] == "state":
                read = next(json.loads(message["content"]) for message in messages
                            if message.get("tool_call_id") == "read-plan")
                assert read["lines"] == read["total_lines"]
                content = "".join(
                    line.split("|", 1)[1] for line in read["content"].splitlines(keepends=True)
                )
                plan = json.loads(content)
                plan["initial_state"]["mechanics"] = [{
                    "namespace": ruleset_id, "key": "state", "value": {"advancement": {
                        "method": "fixed", "starting_level": 1, "maximum_level": 1,
                        "recipients": "whole_party", "timing": "Remain at level one.",
                        "allocation": "The whole party stays at the starting level.",
                    }},
                }]
                self._responses[self._index] = ProviderResponse(tool_calls=[ToolCall(
                    id="state", name="edit_file", arguments=json.dumps({
                        "path": read["path"], "old_text": content,
                        "new_text": json.dumps(plan, sort_keys=True, separators=(",", ":")),
                        "expected_version": read["version"],
                    }),
                )])
            return await super().generate(messages, tools, **kwargs)

    provider = TransitionProvider(responses=[
        *(ProviderResponse(tool_calls=[
            ToolCall(id=identity, name=name, arguments=json.dumps(args)),
        ])
          for identity, name, args in steps),
        ProviderResponse(content="The Setting and Plan are saved; select a Player Character next."),
    ])
    harness = _OwnerHarness(database, provider)
    try:
        chat = harness.request("create", "create_builder_chat", {
            "operation_id": "transition", "provider_id": "test.transport",
            "model_id": "test.transport/model0",
        })
        session_id = chat["session"]["session_id"]
        result = harness.request("build", "submit_session_input", {
            "session_id": session_id, "attempt_id": "build", "message_id": "build",
            "user_text": "Create Plan Harbor and then Harbor Watch using it in this chat.",
        })
        assert result["interruption"] is None, result
        results = {message["tool_call_id"]: json.loads(message["content"])
                   for message in provider.calls[-1]["messages"] if message.get("role") == "tool"}
        for identity, _name, _args in steps:
            if identity == "incomplete":
                assert results[identity]["success"] is False
                assert results[identity]["state_changed"] is False
                assert results[identity]["validation_errors"][0]["code"] == (
                    "plan_starting_state_invalid"
                )
                continue
            assert results[identity]["success"] is True, (identity, results[identity])
        references = results["source"]["references"]
        assert results["plan"]["campaign_setting"]["id"] == setting["package_id"]
    finally:
        harness.close()
    with AuthorityOwner.open(database, "reopen-transition") as owner:
        context = owner.session_context(session_id)
        assert launch_references(context) == references
        selection = BuilderSourceSelection(
            owner, tmp_path, context, lambda: _campaign_creation_catalog(tmp_path, owner),
        )
        assert selection.select_setting(references["candidate_id"], setting["package_id"])[
            "references"
        ] == references
        with pytest.raises(CommandRejected, match="exact Setting"):
            selection.select_setting(references["candidate_id"], "not-a-saved-setting")
        with pytest.raises(CommandRejected, match="different source snapshot"):
            selection.select(bundled_rift_authority().ruleset.package_id)
        if source_kind == "saved-setting":
            with pytest.raises(CommandRejected, match="different Setting snapshot"):
                selection.select_setting(candidate_id, "example.glass-harbor")
        assert selection.references == references
    reopened_provider = LibraryProvider(responses=[
        ProviderResponse(tool_calls=[ToolCall(
            id="recheck", name="check_campaign_plan_draft", arguments="{}",
        )]),
        ProviderResponse(content="The saved Plan draft still validates."),
    ])
    reopened = _OwnerHarness(database, reopened_provider)
    try:
        resumed = reopened.request("resume", "submit_session_input", {
            "session_id": session_id, "attempt_id": "resume", "message_id": "resume",
            "user_text": "Check the retained Plan draft against the same Setting.",
        })
        assert resumed["interruption"] is None
        checked = next(json.loads(message["content"])
                       for message in reopened_provider.calls[-1]["messages"]
                       if message.get("tool_call_id") == "recheck")
        assert checked["success"] is True, checked
    finally:
        reopened.close()


def test_plain_builder_selects_reads_checks_and_publishes_in_one_turn_then_reopens(tmp_path):
    ruleset_id = bundled_rift_authority().ruleset.package_id
    setting = {"package_id": "pes.plain-harbor", "version": "1.0.0", "title": "Plain Harbor"}
    calls = [
        ("unbound", "inspect_builder_references", {"action": "list"}),
        ("select", "select_builder_ruleset", {"ruleset_id": ruleset_id}),
        ("read", "inspect_builder_references", {"action": "list", "query": "ruleset/"}),
        ("write", "write_file", {
            "path": "setting-draft/files/world.md", "content": "# Plain Harbor\nA fishing village.",
        }),
        ("check", "check_campaign_setting_draft", setting),
        ("publish", "publish_campaign_setting", setting),
    ]
    provider = LibraryProvider(responses=[
        *(ProviderResponse(tool_calls=[
            ToolCall(id=identity, name=name, arguments=json.dumps(args)),
        ])
          for identity, name, args in calls),
        ProviderResponse(content="Plain Harbor is saved."),
    ])
    database = tmp_path / "plain-builder.db"
    harness = _OwnerHarness(database, provider)
    try:
        chat = harness.request("create", "create_builder_chat", {
            "operation_id": "plain-builder", "provider_id": "test.transport",
            "model_id": "test.transport/model0",
        })
        session_id = chat["session"]["session_id"]
        result = harness.request("build", "submit_session_input", {
            "session_id": session_id, "attempt_id": "build-attempt", "message_id": "build",
            "user_text": "Create Plain Harbor using the installed SRD Ruleset and save it.",
        })
        assert result["interruption"] is None, result
        results = {
            message["tool_call_id"]: json.loads(message["content"])
            for message in provider.calls[-1]["messages"] if message.get("role") == "tool"
        }
        assert results["unbound"]["code"] == "builder_ruleset_selection_required"
        for identity in ("select", "read", "write", "check", "publish"):
            assert results[identity]["success"] is True, results[identity]
        assert results["read"]["paths"]
        assert results["check"]["published"] is False
        assert results["publish"]["id"] == setting["package_id"]
        catalog = harness.request("catalog", "campaign_creation_catalog")
        assert any(item.get("id") == setting["package_id"] for item in catalog)
        selected_references = results["select"]["references"]
    finally:
        harness.close()
    with AuthorityOwner.open(database, "reopen-plain-builder") as owner:
        context = owner.session_context(session_id)
        assert launch_references(context) == selected_references
        assert reference_reader(owner, context).paths
        selection = BuilderSourceSelection(
            owner, tmp_path, context, lambda: _campaign_creation_catalog(tmp_path, owner),
        )
        assert selection.candidate().candidate_id == selected_references["candidate_id"]
        other, _ = install_character_packages(owner)
        with pytest.raises(CommandRejected, match="different source snapshot"):
            selection.select(other.candidate_id)
        assert selection.references == selected_references


def test_contextual_builder_cannot_replace_its_original_sources(tmp_path):
    bindings = bundled_rift_authority()
    with AuthorityOwner.open(tmp_path / "contextual.db", "contextual-builder") as owner:
        draft = open_character_interview(
            owner, bindings.ruleset.package_id, bindings.setting.package_id,
            bundled_plan("rift-awakening").plan.payload["id"],
        )
        context = launch_character(owner, tmp_path, draft)
        selection = BuilderSourceSelection(
            owner, tmp_path, context, lambda: _campaign_creation_catalog(tmp_path, owner),
        )
        assert selection.selectable is False
        with pytest.raises(CommandRejected, match="original content selection"):
            selection.select(bindings.ruleset.package_id)
        with pytest.raises(CommandRejected, match="original content selection"):
            selection.select_setting("other-candidate", bindings.setting.package_id)
        assert selection.references == launch_references(context)
