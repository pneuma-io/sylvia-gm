"""Shared Plan admission and on-demand act reads preserve usable completion requirements."""

import copy
import json
from pathlib import Path

import pytest

from sylvia_gm.application.authority import _read_structured_campaign_plan
from sylvia_gm.domain._content_schema_plan import EXAMPLE
from sylvia_gm.domain.campaign_plan import PackageBinding, validate_campaign_plan
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes


def validate(plan):
    """Use the real portable Plan validator with the example's exact source bindings."""
    return validate_campaign_plan(
        plan,
        readable_markdown="# Signal Relay",
        expected_ruleset=PackageBinding("example.signal-rules", "1.0.0", "1" * 64),
        expected_setting=PackageBinding("example.signal-setting", "1.0.0", "2" * 64),
        expected_closure_sha256="3" * 64,
        setting_paths=frozenset({"locations/relay.md"}),
    )


@pytest.mark.parametrize(
    "references,diagnostic",
    [
        ([], "objectives"),
        (["understand-signal", "understand-signal"], "repeats an objective"),
        (["missing-ending"], "references missing objective missing-ending"),
        (["Learn enough to continue"], "references missing objective"),
    ],
)
def test_admission_rejects_acts_without_resolvable_required_objectives(references, diagnostic):
    plan = copy.deepcopy(EXAMPLE)
    plan["structure"][0]["objectives"] = references
    with pytest.raises(ValueError, match=diagnostic):
        validate(plan)


def test_non_rift_act_read_includes_all_required_endings_without_future_preparation():
    plan = copy.deepcopy(EXAMPLE)
    plan["objectives"].append(
        {
            "id": "return-recorder",
            "title": "Return the recorder",
            "description": "Return the borrowed equipment.",
            "completion_conditions": ["The duty operator has received the borrowed recorder."],
        }
    )
    plan["structure"][0]["objectives"].append("return-recorder")
    later = copy.deepcopy(plan["structure"][0])
    later.update(id="act-2", title="Later expedition", objectives=["future-ending"])
    plan["structure"].append(later)
    plan["objectives"].append(
        {
            "id": "future-ending",
            "title": "Concealed destination",
            "description": "Private future preparation.",
            "completion_conditions": ["The distant ship is recovered."],
        }
    )
    accepted = validate(plan)
    before = copy.deepcopy(accepted.payload)
    result = json.loads(canonical_json_bytes(
        _read_structured_campaign_plan(accepted.payload, "structure/act-1")
    ))
    assert result["act"] == plan["structure"][0]
    assert result["required_objectives"] == plan["objectives"][:2]
    assert "future-ending" not in str(result)
    assert "distant ship" not in str(result)
    assert accepted.payload == before
    assert _read_structured_campaign_plan(
        accepted.payload, "objectives/return-recorder/completion_conditions"
    ) == accepted.payload["objectives"][1]["completion_conditions"]


def test_old_admitted_act_with_missing_objective_fails_clearly_without_inventing_an_ending():
    plan = copy.deepcopy(EXAMPLE)
    plan["structure"][0]["objectives"] = ["missing-ending"]
    with pytest.raises(CommandRejected, match="completion references are unavailable"):
        _read_structured_campaign_plan(plan, "structure/act-1")


def test_saved_act_reference_keeps_conditions_and_source_identity_on_reopen(tmp_path: Path):
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
    from sylvia_gm.interfaces.local_api import LocalCommandBus
    from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor
    from tests.test_alpha_05_rift_journey import _construct_rift

    database = tmp_path / "act-reference.db"
    supervisor = LocalOwnerSupervisor()
    issued = supervisor.acquire_owner(database, "act-reference")
    supervisor.complete_handshake(derive_owner_response(issued.secret_hex))
    owner = supervisor.application_owner()
    try:
        campaign, _ = _construct_rift(
            LocalCommandBus(supervisor), owner, tmp_path,
            lane="quick_start", companion_ids=(),
        )
        reference = owner.read_campaign_reference(
            campaign, source="campaign_plan", path="structured/structure/act-1",
        )
        whole = json.loads(owner.read_campaign_reference(
            campaign, source="campaign_plan", path="structured", maximum_bytes=262144,
        )["content"])
        body = json.loads(reference["content"])
        assert body["act"] == whole["structure"][0]
        required = set(body["act"]["objectives"])
        assert body["required_objectives"] == [
            objective for objective in whole["objectives"] if objective["id"] in required
        ]
        assert reference["visibility"] == "gm_private_preparation"
        assert reference["content_sha256"]
        with pytest.raises(CommandRejected, match="exceeds the read bound"):
            owner.read_campaign_reference(
                campaign, source="campaign_plan", path="structured/structure/act-1",
                maximum_bytes=1024,
            )
    finally:
        supervisor.shutdown()
    with AuthorityOwner.open(database, "act-reference-reopen") as reopened:
        assert reopened.read_campaign_reference(
            campaign, source="campaign_plan", path="structured/structure/act-1",
        ) == reference
