"""Sibling tool compilation reuses exact bytes without crossing package identities."""

import io
import json
import zipfile
from copy import deepcopy
from dataclasses import replace

import pytest
import yaml

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.decision_scope import project_model_decision_scope
from sylvia_gm.application.package_runtime import (
    PACKAGE_RUNTIME_BYTE_LIMIT,
    PACKAGE_RUNTIME_PATH,
    package_model_decision_boundary,
)
from sylvia_gm.domain.authority import StateValue, WriterRole
from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record
from tests.test_alpha_f3_package_execution import _archive, _command, _create, _runtime_yaml
from tests.test_side_decision_scope import PACKAGE_ID as DECISION_PACKAGE
from tests.test_side_decision_scope import _archive as decision_archive
from tests.test_side_decision_scope import _state as decision_state


def _two_tools(version, extra):
    runtime = yaml.safe_load(_runtime_yaml())
    runtime["tools"]["advance"]["effects"][0]["increment"]["amount"] = f"steps + {extra}"
    runtime["tools"]["second"] = deepcopy(runtime["tools"]["advance"])
    raw = _archive(runtime_bytes=yaml.safe_dump(runtime, sort_keys=False).encode())
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(files.pop("manifest.json"))
    record = manifest["packages"][0]
    record["version"] = version
    second = deepcopy(record["tools"][0])
    second["name"] = "second"
    record["tools"].append(second)
    record.pop("content_sha256")
    record["content_sha256"] = content_root_for_record(record)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        target.writestr("manifest.json", canonical_json_bytes(manifest))
        for name, value in files.items():
            target.writestr(name, value)
    return output.getvalue()


def test_sibling_tool_runtime_read_once_and_changed_identity_has_its_own_effects(
    tmp_path, monkeypatch
):
    with AuthorityOwner.open(tmp_path / "cache.sqlite3", "owner-runtime") as owner:
        _create(owner, _two_tools("1.0.0", 0))
        reads = []
        original = owner._store.package_file_bytes

        def observed(*args, **kwargs):
            if args[3] == "runtime/ruleset.yaml":
                reads.append(args[:3])
            return original(*args, **kwargs)

        monkeypatch.setattr(owner._store, "package_file_bytes", observed)
        assert {tool.name for tool in owner.catalog("campaign-runtime")} == {"advance", "second"}
        assert len(reads) == 1
        owner.execute_action(_command(revision=0, suffix="first", steps=3))
        owner.execute_action(
            replace(
                _command(revision=1, suffix="second", steps=2),
                owner_id="owner-runtime",
                tool_name="second",
            )
        )
        assert owner.snapshot("campaign-runtime").state["synthetic.clockwork.state"]["counter"] == 5
        candidate = owner.install_package_archive(
            io.BytesIO(_two_tools("2.0.0", 1)), install_id="changed"
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="changed",
                display_name="Changed identity",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn-runtime",
                initial_state=(
                    StateValue("synthetic.clockwork", "state", {"counter": 0, "bonus": 0}),
                ),
            )
        )
        assert {tool.name for tool in owner.catalog("changed")} == {"advance", "second"}
        assert len(reads) == 2 and reads[0] != reads[1]
        owner.execute_action(
            replace(_command(revision=0, suffix="changed", steps=3), campaign_id="changed")
        )
        assert owner.snapshot("changed").state["synthetic.clockwork.state"]["counter"] == 4
        assert owner.snapshot("campaign-runtime").state["synthetic.clockwork.state"]["counter"] == 5


@pytest.fixture
def decision_case(tmp_path):
    with AuthorityOwner.open(tmp_path / "decision-cache.sqlite3", "decision-cache") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(decision_archive()), install_id="scope"
        )
        state = decision_state()
        state["stale_eligible_actor_ids"] = ["bravo"]
        owner.create_campaign(
            CampaignCreate(
                campaign_id="decision-cache",
                display_name="Decision cache",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="decision-turn",
                initial_state=(StateValue(DECISION_PACKAGE, "state", state),),
            )
        )
        snapshot = owner.snapshot("decision-cache")
        (primary,) = snapshot.package_lock.entries
        identity = (primary.package_id, primary.version, primary.sha256)
        assert owner.catalog("decision-cache")
        assert identity in owner._runtime_payloads
        yield owner, snapshot, identity


def _decision_reads(owner, monkeypatch):
    reads = []
    original = owner._store.package_file_bytes

    def observed(*args, **kwargs):
        reads.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(owner._store, "package_file_bytes", observed)
    return reads


def test_decision_lookup_cache_hit_and_bounded_store_fallback_preserve_the_same_decisions(
    decision_case,
    monkeypatch,
):
    owner, snapshot, identity = decision_case
    reads = _decision_reads(owner, monkeypatch)
    cached = owner._decision_scope_declaration(snapshot)
    warm = owner.model_decision_boundary("decision-cache")
    assert reads == []
    assert cached is not None and cached["eligible_actors"]["key"] == "state.eligible_actor_ids"
    assert warm is not None and warm.actor_id == "alpha" and warm.owner.value == "gm"
    assert [actor.actor_id for actor in warm.eligible_actors] == ["alpha", "bravo"]

    # Removing the exact entry models eviction of the already verified bounded cache.
    owner._runtime_payloads.pop(identity)
    assert owner._decision_scope_declaration(snapshot) == cached
    assert owner.model_decision_boundary("decision-cache") == warm
    assert reads == [
        ((*identity, PACKAGE_RUNTIME_PATH), {"maximum_bytes": PACKAGE_RUNTIME_BYTE_LIMIT}),
        ((*identity, PACKAGE_RUNTIME_PATH), {"maximum_bytes": PACKAGE_RUNTIME_BYTE_LIMIT}),
    ]
    assert owner.snapshot("decision-cache") == snapshot


@pytest.mark.parametrize("identity_field", ["package_id", "version", "sha256"])
def test_decision_lookup_rejects_stale_cached_bytes_from_each_different_identity_component(
    decision_case,
    monkeypatch,
    identity_field,
):
    owner, snapshot, identity = decision_case
    expected = owner.model_decision_boundary("decision-cache")
    assert expected is not None and expected.actor_id == "alpha"
    stale = yaml.safe_load(owner._runtime_payloads[identity])
    stale["model_decision_scope"]["eligible_actors"]["key"] = "state.stale_eligible_actor_ids"
    stale_bytes = yaml.safe_dump(stale, sort_keys=False).encode()
    stale_declaration = package_model_decision_boundary(
        package_id=DECISION_PACKAGE,
        runtime_bytes=stale_bytes,
    )
    assert project_model_decision_scope(snapshot, stale_declaration).actor_id == "bravo"
    nearby = list(identity)
    index = ["package_id", "version", "sha256"].index(identity_field)
    nearby[index] = ["synthetic.other-package", "2.0.0", "f" * 64][index]
    assert nearby[index] != identity[index]
    owner._runtime_payloads[tuple(nearby)] = stale_bytes
    reads = _decision_reads(owner, monkeypatch)

    # A nearby identity cannot shadow the exact hit or substitute for an evicted entry.
    assert owner.model_decision_boundary("decision-cache") == expected
    assert reads == []
    owner._runtime_payloads.pop(identity)
    assert owner.model_decision_boundary("decision-cache") == expected
    assert reads == [
        ((*identity, PACKAGE_RUNTIME_PATH), {"maximum_bytes": PACKAGE_RUNTIME_BYTE_LIMIT}),
    ]
    assert owner.snapshot("decision-cache") == snapshot
