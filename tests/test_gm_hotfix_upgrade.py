"""Upgrade the actual published Alpha4 Lite closure through explicit review and adoption.

The retained fixture is byte-for-byte cb8bd3f:campaign-plans/a-debt-in-iron/
setting.sylvia-gm-package. It preserves the published 0.3.15 negative baseline.
The integration fixture preserves main6526904's exact primary record and normalized
declared payloads, with the same immutable reference package. Each variant must retain
its panel contracts; the other predecessor remains blocked rather than bypassing them.
"""

import io
import json
import zipfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_packages import bundled_srd_archive, bundled_srd_version
from sylvia_gm.application.campaign_rules_update import _tool_input_change
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.application.package_runtime import PACKAGE_RUNTIME_PATH
from sylvia_gm.domain.authority import StateValue, WriterRole, owned_payload
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import ToolDeclaration, content_root_for_record
from sylvia_gm.domain.session import SessionMessageKind
from tests.test_alpha_f3_package_execution import _archive, _runtime_yaml
from tests.test_alpha_f3_package_publication import _file, _install
from tests.test_campaign_rules_update import adopt
from tests.test_campaign_rules_update import prepared as prepared
from tests.test_gm_memory_casting_hotfix import HealingGM, create_cleric_save, play

SRD = "sylvia.srd-lite.bg3"


def test_reviewed_input_changes_keep_authority_and_executable_schema_fences():
    schema = {
        "type": "object",
        "properties": {"choice": {"type": "string"}},
        "required": ["choice"],
        "additionalProperties": False,
    }
    prior = ToolDeclaration(
        name="choose",
        capability="choice",
        input_schema=owned_payload(schema),
        state_predicates=(),
        turn_statuses=("open",),
        writers=("dm",),
    )
    choices = deepcopy(schema)
    choices["properties"]["choice"].update(
        enum=["red", "blue"], **{"x-sylvia-gm-model-required": True}
    )
    incoming = replace(prior, input_schema=owned_payload(choices))
    assert _tool_input_change(prior, incoming) == "changed_input_choices"
    assert _tool_input_change(incoming, prior) == "changed_input_choices"
    guidance = deepcopy(schema)
    guidance["properties"]["choice"]["description"] = "Choose the actual scene fact."
    described = replace(prior, input_schema=owned_payload(guidance))
    assert _tool_input_change(prior, described) == "changed_model_guidance"
    assert _tool_input_change(described, prior) == "changed_model_guidance"
    guidance["properties"]["choice"]["description"] = False
    assert _tool_input_change(prior, replace(prior, input_schema=owned_payload(guidance))) is None
    assert _tool_input_change(prior, replace(incoming, writers=("player",))) is None
    assert _tool_input_change(prior, replace(incoming, capability="another.capability")) is None
    for key, value in [
        ("type", "integer"),
        ("x-sylvia-gm-reference", "participant"),
        ("maxLength", 2),
        ("default", True),
        ("x-sylvia-gm-model-required", "invalid"),
    ]:
        changed = deepcopy(choices)
        changed["properties"]["choice"][key] = value
        assert (
            _tool_input_change(prior, replace(incoming, input_schema=owned_payload(changed)))
            is None
        )
    changed = deepcopy(choices)
    changed["required"] = []
    assert _tool_input_change(prior, replace(incoming, input_schema=owned_payload(changed))) is None
    changed = deepcopy(choices)
    changed["properties"]["choice"]["enum"] = ["green"]
    assert (
        _tool_input_change(incoming, replace(incoming, input_schema=owned_payload(changed))) is None
    )


@pytest.mark.parametrize("refusal", ["literal", "conditional", "later"])
def test_only_an_initial_literal_false_can_retire_an_admitted_contract(tmp_path, refusal):
    runtime = yaml.safe_load(_runtime_yaml())
    original = runtime["tools"]["advance"]["effects"]
    check = {
        "require": {
            "condition": "false" if refusal != "conditional" else "steps == 2",
            "error": "This action is unavailable.",
        }
    }
    runtime["tools"]["advance"]["effects"] = (
        [original[0], check] if refusal == "later" else [check, *original]
    )
    raw = _archive(runtime_bytes=yaml.safe_dump(runtime).encode())
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        record = json.loads(archive.read("manifest.json"))["packages"][0]
        files = {
            f["path"]: archive.read(f"packages/{record['id']}/{f['path']}") for f in record["files"]
        }
    runtime["tools"].pop("advance")
    files[PACKAGE_RUNTIME_PATH] = yaml.safe_dump(runtime).encode()
    record.update(version="1.0.1", tools=[], files=[_file(p, v) for p, v in sorted(files.items())])
    record["content_sha256"] = content_root_for_record(record)
    with AuthorityOwner.open(tmp_path / "retirement.db", "retirement") as owner:
        old = owner.install_package_archive(io.BytesIO(raw), install_id="before")
        new = _install(owner, ((record, files),), "after")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="retained",
                display_name="Retained counter",
                writer=WriterRole.DM,
                package_lock=old.package_lock,
                turn_id="checkpoint",
                initial_state=(
                    StateValue("synthetic.clockwork", "state", {"counter": 9, "bonus": 2}),
                ),
            )
        )
        owner.complete_turn("retained", "checkpoint")
        before = owner.snapshot("retained")
        pin = owner.current_package_lock("retained")
        preview = owner.preview_campaign_rules_update("retained", new.candidate_id)
        assert preview["compatible"] is (refusal == "literal"), preview
        if refusal == "literal":
            assert preview["tool_changes"][0]["retired_unavailable_actions"] == 1
            adopt(owner, preview)
            assert owner.snapshot("retained").state == before.state
            assert owner.package_lock_lineage("retained")[0] == pin
        else:
            with pytest.raises(CommandRejected, match="conflicts"):
                adopt(owner, preview)
            assert owner.snapshot("retained") == before
            assert owner.current_package_lock("retained") == pin


def test_saved_pending_queue_invalidates_review_without_rewriting_it(prepared):
    owner, _old, new = prepared
    owner.create_agent_session(
        campaign_id="running",
        session_id="queue-gm",
        role=WriterRole.DM,
        provider_id="fixture",
        model_id="fixture",
    )
    reviewed = owner.preview_campaign_rules_update("running", new.candidate_id)
    assert reviewed["compatible"]
    queue = {
        "kind": "action_sequence",
        "sequence_id": "retained-queue",
        "steps": [{"step_id": "future", "status": "pending"}],
    }
    owner.append_agent_message(
        session_id="queue-gm",
        message_id="queue-before-update",
        kind=SessionMessageKind.EVIDENCE,
        content=queue,
    )
    before = owner.snapshot("running")
    pin = owner.current_package_lock("running")
    blocked = owner.preview_campaign_rules_update("running", new.candidate_id)
    assert not blocked["compatible"]
    assert "action sequence" in str(blocked["conflicts"])
    with pytest.raises(CommandRejected, match="conflicts"):
        adopt(owner, reviewed)
    assert owner.snapshot("running") == before
    assert owner.current_package_lock("running") == pin
    assert owner.session_context("queue-gm").messages[-1].content == queue
    owner.append_agent_message(
        session_id="queue-gm",
        message_id="queue-cleared",
        kind=SessionMessageKind.EVIDENCE,
        content={**queue, "steps": []},
    )
    assert owner.preview_campaign_rules_update("running", new.candidate_id)["compatible"]


def test_upgrade_keeps_custom_setting_files_and_records(tmp_path):
    fixture = Path(__file__).parent / "fixtures"
    origin = "alpha4" if bundled_srd_version() == "0.3.21" else "integration"
    with zipfile.ZipFile(fixture / "alpha4-lite.sylvia-gm-package") as published:
        world_record = next(
            r
            for r in json.loads(published.read("manifest.json"))["packages"]
            if r["content_kind"] == "campaign_setting"
        )
        world_files = {
            f["path"]: published.read(f"packages/{world_record['id']}/{f['path']}")
            for f in world_record["files"]
        }
    with zipfile.ZipFile(fixture / f"{origin}-lite.sylvia-gm-package") as inherited:
        records = [
            r
            for r in json.loads(inherited.read("manifest.json"))["packages"]
            if r["content_kind"] != "campaign_setting"
        ]
        drafts = [
            NormalizedPackageDraft(
                r,
                {f["path"]: inherited.read(f"packages/{r['id']}/{f['path']}") for f in r["files"]},
                4,
            )
            for r in records
        ]
    world_record = deepcopy(world_record)
    world_record.pop("content_sha256")
    world_record.pop("files")
    world_record.update(version="1.0.0-hotfix-fixture")
    world_record["dependencies"] = [
        {k: v for k, v in d.items() if k != "content_sha256"} for d in world_record["dependencies"]
    ]
    world_files["campaigns/the-rift-awakening/act-1.md"] += (
        b"\nA green bell records a synthetic private garden clue.\n"
    )
    drafts.append(NormalizedPackageDraft(world_record, world_files, 4))
    database = tmp_path / "custom-world.sqlite3"
    with materialize_package_archive(tuple(drafts)) as archive:
        create_cleric_save(database, archive.archive.read())
    with AuthorityOwner.open(database, "hotfix-owner") as owner:
        owner.complete_turn("casting", "casting-turn")
        before = owner.snapshot("casting")
        pin = owner.current_package_lock("casting")
        options = owner.campaign_rules_update_options("casting")
        assert options["candidates"], options
        count = len(owner.package_candidates())
        assert owner.campaign_rules_update_options("casting") == options
        assert len(owner.package_candidates()) == count
        candidate = options["candidates"][0]["candidate_id"]
        preview = owner.preview_campaign_rules_update("casting", candidate)
        assert preview["compatible"], preview["conflicts"]
        # A return to the earlier version is usually blocked (issue 70); the player is told
        # before confirming, not promised a way back.
        assert "Treat this update as one-way." in preview["recovery"]
        assert "keeps your current progress" not in preview["recovery"]
        assert owner.current_package_lock("casting") == pin
        assert owner.snapshot("casting") == before
        adopt(owner, preview)
        after = owner.snapshot("casting")
        assert after.state == before.state and after.participants == before.participants
        assert after.continuity == before.continuity
        world_pin = next(
            p for p in after.package_lock.entries if p.package_id == world_record["id"]
        )
        world = owner._store.exact_package_manifest(
            *(world_pin.package_id, world_pin.version, world_pin.sha256)
        )
        assert world.version == world_record["version"]
        assert world.metadata == world_record["metadata"]
        for descriptor in world.files:
            assert (
                owner._store.package_file_bytes(
                    *world.identity, descriptor.path, maximum_bytes=descriptor.byte_count
                )
                == world_files[descriptor.path]
            )
        assert owner.package_lock_lineage("casting")[0] == pin
    with AuthorityOwner.open(database, "hotfix-owner") as reopened:
        assert reopened.snapshot("casting") == after


PUBLISHED_LITE = {
    "lite-0.3.25": ("0.3.25", "f13148bf1f68e2558e231c1b96da8a0c660e2ed67050d31b2c61edee3716025b"),
    "lite-0.3.24": ("0.3.24", "653952ed03ad691d2d5e8c8d41fe5d93a402b1a9729120d32044251c133d523d"),
    "alpha4": ("0.3.15", "495f41b1d630893ac8b3cdcc1945a332c6db004dfabe212d661fa2e8b5fd497a"),
    "alpha5": ("0.3.21", "075c294aa916c25b598d2f02f299bf437b352dfce38d0c3bd2efa3e5a1cab143"),
    "integration": ("0.3.15", "dd42e6239820558385c316b7e5aacefa2e9b26b457175815570b1faeec2c04eb"),
}


def _projected_lite_panels(owner):
    """Project the panels whose sources gained fields over a campaign saved without them."""
    panels = {s["id"]: s for s in owner.campaign_panels("casting")["surfaces"]}
    for identity in ("dnd-party", "dnd-encounter", "dnd-character"):
        assert "unavailable_reason" not in panels[identity]["data"], identity
        assert panels[identity]["data"]["spell_categories"]["bless"] == "enchantment"
    # The saved campaign has no initiative order yet; the new field projects as empty.
    assert panels["dnd-encounter"]["data"]["order_values"] == {}
    assert panels["dnd-inventory"]["data"]["item_names"]["priests_pack"] == "Priest's Pack"
    return panels


@pytest.mark.parametrize(
    "origin", ["alpha4", "alpha5", "integration", "lite-0.3.24", "lite-0.3.25"]
)
def test_retained_campaign_reviews_adopts_heals_and_reopens_without_losing_state(tmp_path, origin):
    database = tmp_path / "alpha4.sqlite3"
    filename = origin if origin.startswith("lite-") else f"{origin}-lite"
    archive = (Path(__file__).parent / f"fixtures/{filename}.sylvia-gm-package").read_bytes()
    current_archive = bundled_srd_archive()
    with zipfile.ZipFile(io.BytesIO(current_archive)) as bundle:
        current_version = next(
            r["version"]
            for r in json.loads(bundle.read("manifest.json"))["packages"]
            if r["role"] == "primary"
        )
    assert current_version == bundled_srd_version()
    if current_version == "0.3.21" and origin == "integration":
        # Alpha4's schema refuses newer main-only panel metadata before it can be installed.
        with pytest.raises(CommandRejected, match="manifest fields are invalid"):
            create_cleric_save(database, archive)
        with AuthorityOwner.open(database, "hotfix-owner") as owner:
            assert not owner.campaign_index()
        return
    create_cleric_save(database, archive)
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as owner:
        owner.append_agent_message(
            session_id="gm",
            message_id="upgrade-basis",
            kind=SessionMessageKind.USER,
            content={"text": "Remember the established scene."},
        )
        owner.update_campaign_file(
            session_id="gm",
            update_id="saved-before-upgrade",
            turn_id="casting-turn",
            basis_message_ids=("upgrade-basis",),
            updates=(
                {
                    "operation": "record_memory",
                    "fact_id": "retained",
                    "category": "continuity",
                    "text": "The north bell rings in pairs.",
                    "visibility": "player_known",
                    "known_by": ["hero"],
                },
                {
                    "operation": "update_scene",
                    "location_id": "north-belfry",
                    "summary": "Waiting under the bronze bell.",
                },
            ),
        )
        owner.complete_turn("casting", "casting-turn")
        before = owner.snapshot("casting")
        old_lock = owner.current_package_lock("casting")
        primary = next(p for p in old_lock.package_lock.entries if p.package_id == SRD)
        assert (primary.version, primary.sha256) == PUBLISHED_LITE[origin]
        inventory = next(
            s["data"]
            for s in owner.campaign_panels("casting")["surfaces"]
            if s["renderer"] == "inventory"
        )
        assert "priests_pack" in inventory["entities"]["hero"]["inventory"]
        # 0.3.25 introduced declared item names; earlier pins project without them.
        assert ("item_names" in inventory) == (origin == "lite-0.3.25")
        if origin in {"integration", "lite-0.3.24", "lite-0.3.25"}:
            # Packaged startup installs bare Rulesets; Setting descendants are offered lazily.
            owner.install_package_archive(io.BytesIO(current_archive), install_id="startup-current")
        options = owner.campaign_rules_update_options("casting")
        assert options["candidates"], options
        candidate = options["candidates"][0]["candidate_id"]
        preview = owner.preview_campaign_rules_update("casting", candidate)
        assert preview["compatible"], preview["conflicts"]
        assert "Treat this update as one-way." in preview["recovery"]
        assert "Export the campaign first" in preview["recovery"]
        if origin == "lite-0.3.25":
            # 0.3.26 repairs internal spell mechanics only: no GM tool declaration or
            # panel source changes, so the reviewed preview lists no tool changes.
            assert preview["tool_changes"] == []
            assert "Panels show additional information." not in preview["recovery"]
        else:
            assert preview["tool_changes"]
        if origin not in {"integration", "lite-0.3.25"}:
            added = 1 if origin == "lite-0.3.24" else 4
            # Published panels only gained source fields; they are a reviewed change.
            assert any(c.get("added_panel_data") == added for c in preview["tool_changes"])
            assert "Panels show additional information." in preview["recovery"]
        assert owner.current_package_lock("casting") == old_lock
        assert owner.snapshot("casting") == before
        receipt = adopt(owner, preview, "adopt-hotfix")
        after = owner.snapshot("casting")
        assert after.state == before.state
        assert after.participants == before.participants
        assert after.continuity == before.continuity
        assert after.revision == before.revision + 1
        assert owner.package_lock_lineage("casting")[0] == old_lock
        assert adopt(owner, preview, "adopt-hotfix").replayed
        earlier = next(
            option
            for option in owner.campaign_rules_update_options("casting")["candidates"]
            if option["closure_sha256"] == old_lock.closure_sha256
        )
        reverse = owner.preview_campaign_rules_update("casting", earlier["candidate_id"])
        if origin == "lite-0.3.25":
            # 0.3.25 declares the same tools and panels, so a return has no contract
            # conflicts; the preview still advises treating it as one-way.
            assert reverse["compatible"] and not reverse["conflicts"]
            assert "Treat this update as one-way." in reverse["recovery"]
        else:
            assert not reverse["compatible"]
            assert reverse["conflicts"]
        _projected_lite_panels(owner)
        owner.open_turn("casting", "after-upgrade")
        provider = HealingGM()
        play(
            owner,
            provider,
            campaign="casting",
            turn="after-upgrade",
            user_text="I cast Cure Wounds on myself.",
        )
        assert provider.cast_calls == 2
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["hp"] == 9 and hero["resources"]["spell_slot_1"] == 1
        assert saved.continuity == before.continuity
        assert receipt.resulting_revision == after.revision
    with AuthorityOwner.open(database, "hotfix-owner") as owner:
        assert owner.snapshot("casting") == saved
        _projected_lite_panels(owner)


def test_rules_adoption_keeps_historical_tool_calls_representable(tmp_path, monkeypatch):
    """Play the same GM session on both pins and inspect what the scripted provider receives."""
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
    from tests.gm_context_contract import callable_tool_names
    from tests.test_gm_context_contract import ChatGPTContractGM, _check, settle

    class CheckingGM(ChatGPTContractGM):
        def __init__(self):
            super().__init__()
            self.tool_lists = []

        async def generate_stream(self, messages, tools=None, **kwargs):
            self.tool_lists.append(json.dumps(tools).encode())
            # Old and new aliases can both be listed. Pick the one the GM may call now.
            name, arguments = self.steps[self.position]
            callable_names = callable_tool_names(messages, tools)
            (alias,) = [t for t in callable_names if t.endswith("_" + name)]
            self.steps[self.position] = (alias, arguments)
            async for chunk in super().generate_stream(messages, tools, **kwargs):
                yield chunk

    monkeypatch.setenv("SYLVIA_GM_CONTEXT_MODE", "ledger")
    monkeypatch.delenv("SYLVIA_GM_TOOL_LIST", raising=False)
    fixture = Path(__file__).parent / "fixtures/lite-0.3.24.sylvia-gm-package"
    database = create_cleric_save(tmp_path / "upgrade.sqlite3", fixture.read_bytes())
    gm = CheckingGM()
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1) as owner:
        adapter = ProviderSessionAdapter(owner, gm, owner_id="hotfix-owner")

        def check_and_narrate(turn):
            gm.steps.extend([_check("hero", "perception"), settle("The steps are quiet.")])
            request = SessionRunRequest(
                "gm", turn, turn + "-attempt", turn + "-input", "I look for tracks."
            )
            start = len(gm.calls)
            result = CampaignSessionLoop(owner).run(request, adapter.for_turn(turn))
            assert result.interruption is None and gm.position == len(gm.steps)
            assert len(gm.calls) == start + 2
            assert gm.tool_lists[start] == gm.tool_lists[start + 1]
            return gm.calls[start:]

        old = check_and_narrate("casting-turn")
        (old_alias,) = [name for name in old[0]["tools"] if name.endswith("_ability_check")]
        saved = owner.session_context("gm").messages
        assert any(
            m.content.get("kind") == "provider_tool_calls"
            and any(
                c["function"]["name"] == old_alias for c in m.content["assistant"]["tool_calls"]
            )
            for m in saved
        )
        assert any(m.content.get("kind") == "provider_tool_results" for m in saved)
        owner.complete_turn("casting", "casting-turn")
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_srd_archive()), install_id="current"
        )
        preview = owner.preview_campaign_rules_update("casting", candidate.candidate_id)
        assert preview["compatible"], preview["conflicts"]
        adopt(owner, preview)
        owner.open_turn("casting", "after-adoption")
        for request in check_and_narrate("after-adoption"):
            history = [c for m in request["messages"] for c in m.get("tool_calls", ())]
            results = [m["tool_call_id"] for m in request["messages"] if m["role"] == "tool"]
            assert sorted(c["id"] for c in history) == sorted(results)
            assert {c["function"]["name"] for c in history} <= set(request["tools"])


def test_inventory_names_only_change_package_digests_in_gm_tool_aliases(tmp_path, monkeypatch):
    from hashlib import sha256

    from tests.test_gm_memory_casting_hotfix import CaptureGM

    monkeypatch.setenv("SYLVIA_GM_CONTEXT_MODE", "legacy")
    old = Path(__file__).parent / "fixtures/lite-0.3.24.sylvia-gm-package"
    captures = []
    for index, archive in enumerate((old.read_bytes(), bundled_srd_archive())):
        database = create_cleric_save(tmp_path / f"capture-{index}.sqlite3", archive)
        with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1) as owner:
            request = play(owner, CaptureGM(), campaign="casting", turn="casting-turn")
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            packages = json.loads(bundle.read("manifest.json"))["packages"]
        primary = next(p for p in packages if p["role"] == "primary")
        tool_ids = {}
        for t in primary["tools"]:
            identity = [
                primary["id"],
                primary["version"],
                primary["content_sha256"],
                t["name"],
                t["capability"],
            ]
            digest = sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()[:12]
            tool_ids[f"tool_{digest}_{t['name']}"[:64]] = (t["name"], t["capability"])
        captures.append((request, tool_ids))

    (before, old_ids), (after, new_ids) = captures
    assert before["messages"] == after["messages"]
    assert len(json.dumps(before["tools"]).encode()) == len(json.dumps(after["tools"]).encode())
    for prior, current in zip(before["tools"], after["tools"], strict=True):
        old_name = prior["function"]["name"]
        new_name = current["function"]["name"]
        if old_name != new_name:
            assert old_ids[old_name] == new_ids[new_name]
        expected = {**prior, "function": {**prior["function"], "name": new_name}}
        assert json.dumps(expected).encode() == json.dumps(current).encode()
