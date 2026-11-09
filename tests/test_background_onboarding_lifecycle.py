"""The custom interview composes with real Builder tools and prepared campaign copies."""

import asyncio
import hashlib
import json
from copy import deepcopy

from sylvia_gm.provider._base import ProviderResponse, ProviderStreamChunk, ToolCall
from tests.qa_attached_request import request_attached
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness, _ScriptedProvider
from tests.test_ready_campaigns import command


class OnboardingProvider(_ScriptedProvider):
    """Script model decisions while retaining the actual owner and tool implementations."""

    def __init__(self):
        super().__init__(())
        self.block_first = True
        self.builder_step = 0
        self.receipts = {}
        self.builder_calls = 0
        self.dm_calls = 0
        self.dm_receipts = {}
        self.reference = None
        self.draft = None
        self.advancement = None
        self.advancement_sent = False

    def call(self, tools, name, arguments):
        actual = next(t["function"]["name"] for t in tools
                      if t["function"]["name"] == name
                      or t["function"]["name"].endswith("_" + name))
        return ToolCall(id=f"onboarding-{self.builder_step}", name=actual,
                        arguments=json.dumps(arguments))

    async def generate(self, messages, tools=None, **kwargs):
        self.builder_calls += 1
        if self.block_first:
            self.block_first = False
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        for message in messages:
            identity = message.get("tool_call_id", "")
            if identity.startswith("onboarding-"):
                self.receipts[int(identity.split("-")[-1])] = json.loads(message["content"])
        step = self.builder_step
        if step:
            previous = self.receipts[step - 1]
            assert previous.get("success", True), previous
        if step == 0:
            name, args = "get_content_definition", {"name": "character_creation"}
        elif step == 1:
            name, args = "inspect_builder_references", {
                "action": "list", "query": "character-examples/", "limit": 1,
            }
        elif step == 2:
            page = self.receipts[1]
            assert page["total"] == 12 and page["next_offset"] == 1
            name, args = "inspect_builder_references", {
                "action": "list", "query": "character-examples/", "offset": 1, "limit": 100,
            }
        elif step == 3:
            assert self.receipts[2]["next_offset"] is None
            name, args = "inspect_builder_references", {
                "action": "read", "path": self.reference, "limit": 128,
            }
        elif step == 4:
            page = self.receipts[3]
            assert page["next_offset"] == 128
            name, args = "inspect_builder_references", {"action": "read_many", "pages": [
                {"path": self.reference, "offset": 128, "limit": 20000},
                {"path": "campaign-plan/plan.json", "limit": 8000},
            ]}
        elif step == 5:
            name, args = "copy_builder_reference", {
                "source_path": self.reference, "destination_path": "monk.json",
            }
        elif step == 6:
            name, args = "write_file", {
                "path": "monk.json", "expected_version": self.receipts[5]["version"],
                "content": json.dumps(self.draft),
            }
        elif step == 7:
            name, args = "check_participant_draft_file", {
                "path": "monk.json", "expected_version": self.receipts[6]["version"],
            }
        elif step == 8:
            assert self.receipts[7]["status"] == "valid_draft"
            name, args = "publish_participant_artifact_file", {
                "path": "monk.json", "expected_version": self.receipts[7]["draft_version"],
            }
        elif step == 9:
            assert self.receipts[8]["campaign_ready"] is True
            assert "complete_creation_subflow" not in [t["function"]["name"] for t in tools]
            self.builder_step += 1
            return ProviderResponse(
                content="Sori is saved. The campaign copy still needs level two."
            )
        else:
            raise AssertionError("The scripted Builder received an unexpected additional round")
        call = self.call(tools, name, args)
        self.builder_step += 1
        return ProviderResponse(tool_calls=[call])

    async def generate_stream(self, messages, tools=None, **kwargs):
        names = [t["function"]["name"] for t in tools or []]
        if "inspect_builder_references" in names:
            response = await self.generate(messages, tools, **kwargs)
            if response.tool_calls:
                call = response.tool_calls[0]
                yield ProviderStreamChunk(tool_call_delta={
                    "index": 0, "id": call.id,
                    "function": {"name": call.name, "arguments": call.arguments},
                }, finish_reason="tool_calls")
            else:
                yield ProviderStreamChunk(content=response.content, finish_reason="stop")
            return
        self.dm_calls += 1
        for message in messages:
            identity = message.get("tool_call_id", "")
            if identity.startswith("onboarding-dm-"):
                self.dm_receipts[identity] = json.loads(message["content"])
        if self.advancement is not None and not self.advancement_sent:
            suffix, args = "level_up_srd_character", self.advancement
            self.advancement_sent = True
        else:
            suffix, args = "update_campaign_file", {
                "state_change": "unchanged", "state_change_summary": "No additional state change.",
                "revised_player_facing_response": [{"kind": "ooc", "text": (
                    "Review the pending advancement before starting." if self.advancement
                    else "Nessa answers the fictional traveler's question."
                )}],
            }
        actual = next(name for name in names if name.endswith("_" + suffix))
        yield ProviderStreamChunk(tool_call_delta={
            "index": 0, "id": f"onboarding-dm-{self.dm_calls}",
            "function": {"name": actual, "arguments": json.dumps(args)},
        }, finish_reason="tool_calls")


def raw_request(harness, identity, method, params):
    harness.send(identity, {"method": method, "params": params})
    while True:
        if identity in harness._pending_replies:
            return harness._pending_replies.pop(identity)
        reply = harness._read_stream()
        if reply["id"] == identity:
            return reply
        harness._pending_replies[reply["id"]] = reply


def file_hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def test_custom_monk_stop_retry_save_advance_play_and_reopen(tmp_path):
    print("[onboarding] starting isolated actual owner", flush=True)
    provider = OnboardingProvider()
    database = tmp_path / "authority.sqlite3"
    harness = _OwnerHarness(database, provider)
    try:
        request = harness.request
        request("audit", "set_admin_full_audit", {"enabled": True})
        assert request("audit-status", "admin_full_audit_status")["enabled"]
        catalog = request("catalog", "campaign_creation_catalog")
        rules = next(r for r in catalog if r["kind"] == "ruleset"
                     and r["id"] == "sylvia.srd-lite.bg3")
        setting = next(r for r in catalog if r["id"] == "pneuma.severed-lands")
        plan = next(r for r in catalog if r["id"] == "last-cart-through")
        draft = request("draft", "create_guided_campaign_draft", {
            "operation_id": "background-monk", "display_name": "Fictional Monk Journey",
            "provider_id": "test.transport", "model_id": "test.transport/model0",
        })
        campaign, parent = draft["campaign_id"], draft["builder_session_id"]

        def answer(step, value):
            nonlocal draft
            draft = request("choose-" + step, "advance_guided_campaign_draft", {
                "operation_id": "choose-" + step, "campaign_id": campaign,
                "builder_session_id": parent,
                "expected_construction_revision": draft["construction_revision"],
                "current_step": step, "visible_answer": step, "answer": value,
            })

        for step, value in (
            ("ruleset", {"candidate_id": rules.get("selection_id") or rules["id"]}),
            ("setting", {"setting_id": setting["id"]}),
            ("plan", {"plan_id": plan["id"]}),
            ("name", {"display_name": "Fictional Monk Journey"}),
        ):
            answer(step, value)
        child = request("child", "start_guided_creation_subflow", {
            "operation_id": "monk-child", "campaign_id": campaign,
            "parent_session_id": parent,
            "expected_construction_revision": draft["construction_revision"],
            "purpose": "player_character", "visible_answer": "Create New Player Character",
        })["child_session_id"]
        launch = next(m["content"] for m in request(
            "child-context", "session_context", {"session_id": child},
        )["messages"] if m["content"].get("kind") == "builder_launch_context")
        example = next(e for e in launch["references"]["character_examples"]
                       if e["draft"]["participant"]["classes"] == [{"id": "monk", "level": 1}])
        provider.reference = example["path"]
        provider.draft = deepcopy(example["draft"])
        provider.draft["participant"]["name"] = "Sori Test"
        provider.draft["summary"] = "Sori, a fictional courier and level-one Monk."
        provider.draft["authored_profile"] = (
            "# Sori Test\n\nA patient fictional courier who counts bridge steps.\n\n"
            + example["draft"]["authored_profile"]
        )
        print("[onboarding] selected Last Cart and captured exact Monk references", flush=True)
        baseline = request("before-stop", "snapshot", {"campaign_id": campaign})
        harness.send("blocked", {"method": "submit_session_input", "params": {
            "session_id": child, "attempt_id": "blocked", "message_id": "monk-brief",
            "user_text": "Just make me a monk, choose the details for me.",
        }})
        assert provider.started.wait(30), "Builder preparation never reached the test provider"
        active = request("active", "session_context", {"session_id": child})
        assert active["activity"]["status"] == "active"
        assert active["activity_progress"]["attempt_id"] == "blocked"
        assert request("stop", "stop_agent_session", {"session_id": child})["status"] == "stopped"
        assert provider.cancelled.wait(2)
        stopped = harness.read()
        assert stopped["id"] == "blocked" and not stopped["ok"]
        assert request("after-stop", "snapshot", {"campaign_id": campaign}) == baseline
        stopped_context = request("stopped-context", "session_context", {"session_id": child})
        assert stopped_context["activity"] == {"attempt_id": "blocked", "status": "stopped"}
        assert not list((tmp_path / "participants").glob("*/payload.json"))
        print("[onboarding] Stop fenced output and left campaign and library unchanged", flush=True)
        blocked_retry = raw_request(harness, "stopped-retry", "submit_session_input", {
            "session_id": child, "attempt_id": "stopped-retry", "message_id": "stopped-retry",
            "user_text": "Continue without resuming.",
        })
        assert not blocked_retry["ok"]
        assert blocked_retry["error"]["message"] == "Agent session is not active"
        assert request(
            "resume", "resume_agent_session", {"session_id": child},
        )["status"] == "active"
        completed = request("retry", "submit_session_input", {
            "session_id": child, "attempt_id": "retry", "message_id": "monk-retry",
            "user_text": "Continue the same monk request; choose the unspecified details.",
        })
        assert completed["interruption"] is None, completed
        context = request("completed", "session_context", {"session_id": child})
        assert context["activity"]["status"] == "completed"
        assert sum(m["kind"] == "talk" and "Sori is saved" in m["content"].get("text", "")
                   for m in context["messages"]) == 1
        assert [m["sequence"] for m in context["messages"]] == sorted(
            {m["sequence"] for m in context["messages"]}
        )
        saved = provider.receipts[8]
        artifact = saved["id"]
        original_files = file_hashes(tmp_path / "participants")
        assert len(list((tmp_path / "participants").glob("*/payload.json"))) == 1
        assert provider.builder_step == 10
        print("[onboarding] retry published exactly one distinct validated Monk", flush=True)
        # Finish the ordinary interview using the exact just-published artifact.
        answer("participants", {"archetype_id": artifact, "companion_ids": []})
        answer("boundaries_preferences", {"boundaries": "", "preferences": ""})
        answer("specific_requests", {"no_additional_requests": True})
        begun = request("begin", "start_guided_campaign", {
            "operation_id": "monk-begin", "campaign_id": campaign,
            "builder_session_id": parent, "display_name": "Fictional Monk Journey",
            "candidate_id": rules.get("selection_id") or rules["id"],
            "setting_id": setting["id"], "plan_id": plan["id"],
            "player_character_id": artifact, "companion_ids": [], "premise": "",
            "construction_intent": {"boundaries": "", "preferences": "",
                                    "specific_requests": "", "contributions": {}},
        })
        assert begun["campaign_id"] == campaign
        handoff = request("handoff", "finalize_campaign_construction", {
            "campaign_id": campaign, "dm_provider_id": "test.transport",
            "dm_model_id": "test.transport/model0", "model_guidance_override": True,
        })
        session = handoff["handoff"]["dm_session_id"]
        snapshot = request("initial", "snapshot", {"campaign_id": campaign})
        pc = next(p for p in snapshot["participants"] if p["data"]["name"] == "Sori Test")
        actor_id, profile = pc["participant_id"], pc["data"]
        actor = snapshot["state"]["sylvia.srd-lite.bg3.entities"][actor_id]
        assert profile["level"] == 1 and actor["initial_advancement_queue"]
        build = {
            "abilities": profile["abilities"], "armor_class": actor["armor_class"],
            "speed": profile["speed"] + 10, "feature_hp_gain": 0,
            "mechanical_sources": "SRD Monk level two, fixed d8 HP; retained legal origin choices.",
            "spellcasting": list(profile.get("spellcasting_grants", [])),
            "prepared_spells": actor.get("prepared_spells", []),
            "resources": [{"id": "focus_point", "maximum": 2, "source": "SRD Monk level 2"}],
            "features": [{"id": "monk-2", "source": "SRD Monk level 2", "choice": (
                "Monk two with fixed HP; Focus, Unarmored Movement and Uncanny Metabolism."
            )}],
        }
        for field in ("skill_proficiencies", "tool_proficiencies", "weapon_proficiencies",
                      "weapon_masteries", "saving_throw_proficiencies"):
            build[field] = list(profile.get(field, []))
        provider.advancement = {
            "character": actor_id, "character_state": actor_id, "class_id": "monk",
            "hp_method": "fixed", "choices_summary": "Monk two; fixed HP, retained choices.",
            "build": [build],
        }
        opening = request_attached(harness, "opening", "start_dm_session", {
            "campaign_id": campaign, "session_id": session, "turn_id": handoff["dm_turn_id"],
        }, campaign_id=campaign, attempt_id=handoff["dm_attempt_id"])
        assert opening["interruption"] is None, opening
        projection = request("review", "player_projection", {"campaign_id": campaign})
        choices = [r["safe_payload"] for r in projection if r.get("kind") == "mechanics_choice"]
        assert len(choices) == 1, (projection, provider.dm_receipts)
        choice = choices[0]
        resolution = {
            "campaign_id": campaign, "session_id": session, "action_id": choice["action_id"],
            "expected_revision": choice["basis_revision"], "choice_id": choice["choice_id"],
            "choice_sequence": choice.get("choice_sequence", 0),
            "resolution_id": "apply-monk-two", "option_id": "apply",
        }
        before_reject = request("before-invalid", "snapshot", {"campaign_id": campaign})
        rejected = raw_request(harness, "invalid", "resolve_mechanics_choice", {
            **resolution, "resolution_id": "invalid", "option_id": "not-a-choice",
        })
        assert not rejected["ok"]
        assert request("after-invalid", "snapshot", {"campaign_id": campaign}) == before_reject
        request("apply", "resolve_mechanics_choice", resolution)
        advanced = request("advanced", "snapshot", {"campaign_id": campaign})
        raw_request(harness, "duplicate", "resolve_mechanics_choice", resolution)
        assert request("after-duplicate", "snapshot", {"campaign_id": campaign}) == advanced
        pc = next(p for p in advanced["participants"] if p["participant_id"] == actor_id)
        assert pc["data"]["classes"] == [{"id": "monk", "level": 2}]
        assert not advanced["state"]["sylvia.srd-lite.bg3.entities"][actor_id][
            "initial_advancement_queue"
        ]
        level_actions = [
            a for a in advanced["actions"]
            if a["tool_name"] == "level_up_srd_character" and a["status"] == "committed"
        ]
        assert len(level_actions) == 1 and level_actions[0]["parameters"]["character"] == actor_id
        provider.advancement = None
        index = next(r for r in request("index", "campaign_index") if r["campaign_id"] == campaign)
        play = request_attached(harness, "play", "submit_session_input", {
            "session_id": session, "turn_id": index["turn_id"], "attempt_id": "play",
            "message_id": "play", "user_text": "I ask Nessa about the injured teamster.",
        }, campaign_id=campaign, attempt_id="play")
        assert play["interruption"] is None, play
        saved_snapshot = request("played", "snapshot", {"campaign_id": campaign})
        saved_context = request("played-context", "session_context", {"session_id": session})
        assert file_hashes(tmp_path / "participants") == original_files
        print("[onboarding] confirmed level two and ordinary play; saved base card unchanged",
              flush=True)
    finally:
        harness.close()
        (tmp_path / "fixture-progress.json").write_text(json.dumps({
            "builder_step": provider.builder_step, "builder_calls": provider.builder_calls,
            "dm_calls": provider.dm_calls,
            "dm_receipts": provider.dm_receipts,
            "receipts": {number: {key: value for key, value in receipt.items()
                                  if key in {"success", "status", "code", "campaign_ready"}}
                         for number, receipt in provider.receipts.items()},
        }, indent=2)+"\n")

    calls = provider.builder_calls, provider.dm_calls
    reopened = _OwnerHarness(database, provider)
    try:
        assert reopened.request("reopen-snapshot", "snapshot", {
            "campaign_id": campaign,
        }) == saved_snapshot
        assert reopened.request("reopen-context", "session_context", {
            "session_id": session,
        })["messages"] == saved_context["messages"]
        assert (provider.builder_calls, provider.dm_calls) == calls
        assert file_hashes(tmp_path / "participants") == original_files
        first = reopened.request(
            "ready-first", "start_ready_campaign", command("last-cart-through"),
        )
        second = reopened.request("ready-second", "start_ready_campaign", command(
            "last-cart-through", "independent-second",
        ))
        assert first["campaign_id"] != second["campaign_id"] != campaign
        assert first["local_ready"] and second["local_ready"]
        assert (provider.builder_calls, provider.dm_calls) == calls
        assert reopened.request("custom-still-saved", "snapshot", {
            "campaign_id": campaign,
        }) == saved_snapshot
        second_snapshot = reopened.request("second-before-play", "snapshot", {
            "campaign_id": second["campaign_id"],
        })
        prepared_opening = request_attached(reopened, "ready-opening", "start_dm_session", {
            "campaign_id": first["campaign_id"], "session_id": first["dm_session_id"],
            "turn_id": first["dm_turn_id"],
        }, campaign_id=first["campaign_id"], attempt_id=first["dm_attempt_id"])
        assert prepared_opening["interruption"] is None, prepared_opening
        assert reopened.request("second-after-play", "snapshot", {
            "campaign_id": second["campaign_id"],
        }) == second_snapshot
        first_snapshot = reopened.request("first-played", "snapshot", {
            "campaign_id": first["campaign_id"],
        })
        assert {p["participant_id"] for p in first_snapshot["participants"]}.isdisjoint(
            p["participant_id"] for p in second_snapshot["participants"]
        )
        assert provider.builder_calls == calls[0]
    finally:
        reopened.close()
    calls = provider.builder_calls, provider.dm_calls
    final = _OwnerHarness(database, provider)
    try:
        for name, result, expected in (
            ("first", first, first_snapshot), ("second", second, second_snapshot),
        ):
            assert final.request("final-" + name, "snapshot", {
                "campaign_id": result["campaign_id"],
            }) == expected
        assert final.request("final-custom", "snapshot", {
            "campaign_id": campaign,
        }) == saved_snapshot
        assert (provider.builder_calls, provider.dm_calls) == calls
        assert file_hashes(tmp_path / "participants") == original_files
        (tmp_path / "qualification.json").write_text(json.dumps({
            "provider": "deterministic fixture", "native_ui": False, "live_model": False,
            "custom_create_save_advance_play_reopen": True, "stop_retry": True,
            "reference_paging_and_version_dependencies": True,
            "invalid_choice_unchanged": True, "duplicate_choice_no_additional_mutation": True,
            "saved_base_character_hashes": original_files,
            "independent_ready_copies_play_reopen": True, "no_reopen_generation": True,
            "builder_provider_calls_including_cancelled": provider.builder_calls,
            "dm_provider_calls": provider.dm_calls,
        }, indent=2)+"\n")
        print("[onboarding] custom and independent ready copies reopened without regeneration",
              flush=True)
    finally:
        final.close()
