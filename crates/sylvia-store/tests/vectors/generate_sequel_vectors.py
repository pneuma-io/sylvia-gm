"""Function-level vectors for `sqlite_sequel.verify_sequel`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_sequel_vectors.py

A reviewed sequel can only be stored after the owner previews an adventure from the local
library, so these cases build the sequel Plan the way `campaign_sequels.append` builds it
(`prepare_story`, the preview basis, the request, the content and its identities) and call
Python's `verify_sequel(plan, previous)` on it, then on one-field changes of it. It writes
`sequel/sequel_vectors.json`; `tests/sequel.rs` replays every case. Reference tooling only.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

from sylvia_gm.domain.authority import thaw_json_value  # noqa: E402
from sylvia_gm.domain.campaign_sequel import digest, prepare_story, review_payload  # noqa: E402
from sylvia_gm.domain.session import (  # noqa: E402
    CampaignPlan,
    PlanAcceptanceStatus,
    PlanValidationOutcome,
    campaign_plan_payload_sha256,
)
from sylvia_gm.infrastructure.sqlite_sequel import verify_sequel  # noqa: E402

OUT = HERE / "sequel"
CAMPAIGN, SESSION, LOCK = "campaign-story", "dm-1", "lock-" + "e" * 40
BINDINGS = {"ruleset": None, "campaign_setting": None}


def plan_fields(content: dict[str, object], *, plan_id: str, revision: int, resulting: int,
                session: str = SESSION, evidence: str = "ev") -> dict[str, object]:
    fields = {
        "plan_id": plan_id, "campaign_id": CAMPAIGN, "package_lock_id": LOCK, "source_session_id": session,
        "content_sha256": digest(content), "content": content, "provenance_evidence_ids": [evidence],
        "validation_outcome": "valid", "acceptance_status": "accepted",
        "plan_revision": revision, "resulting_campaign_revision": resulting,
    }
    fields["payload_sha256"] = campaign_plan_payload_sha256(
        plan_id=plan_id, campaign_id=CAMPAIGN, source_session_id=session, expected_revision=resulting - 1,
        expected_package_lock_id=LOCK, content_sha256=fields["content_sha256"], content=content,
        provenance_evidence_ids=(evidence,), validation_outcome=PlanValidationOutcome.VALID,
        acceptance_status=PlanAcceptanceStatus.ACCEPTED)
    return fields


PREVIOUS_CONTENT = {"campaign_plan": {
    "title": "The Drowned Bell", "bindings": BINDINGS, "boundaries": ["no gore", "fade to black"],
    "structure": [{"id": "act-1", "objectives": []}],
}}
PREVIOUS = plan_fields(PREVIOUS_CONTENT, plan_id="plan-1", revision=1, resulting=1, session="builder-1")

SOURCE = {
    "title": "The Second Tide", "bindings": BINDINGS, "boundaries": ["fade to black", "no spiders"],
    "structure": [{"id": "act-a", "objectives": ["obj-a"]}, {"id": "act-b", "objectives": []}],
    "objectives": [{"id": "obj-a", "text": "Find the bell."}],
    "secrets": [{"id": "secret-a", "reveal": {"from_act": "act-b"}},
                {"id": "kept", "setting_fact_id": "fact-1", "reveal": {"from_act": "act-a"}}],
    "ending": {"mode": "finite", "outcomes": [{"id": "end-a", "open_world": True, "after_story": None}]},
}
MARKDOWN = "# The Second Tide\n\nA reviewed sequel."


def sequel_plan(*, number: int = 2, operation: str = "op-1", previous: dict[str, object] = PREVIOUS) -> dict[str, object]:
    basis = {
        "campaign_id": CAMPAIGN, "session_id": SESSION, "expected_revision": 5, "latest_message_sequence": 9,
        "previous_plan_id": previous["plan_id"], "previous_plan_sha256": previous["content_sha256"],
        "source_id": "library:second-tide", "source_sha256": "c" * 64,
        "source_plan_sha256": digest(SOURCE), "source_markdown_sha256": digest(MARKDOWN), "number": number,
    }
    review = thaw_json_value(review_payload({"connection": "  The bell rings again.  ", "timeline": "after",
                                             "reviewed": True}))
    request = {"operation_id": operation, "source_id": basis["source_id"], "expected_revision": 5,
               "preview_sha256": digest(basis), "review": dict(review), "session_id": SESSION}
    prepared, mapping = prepare_story(SOURCE, number)
    prepared = thaw_json_value(prepared)
    payload = dict(prepared)
    payload["boundaries"] = list(dict.fromkeys((
        *previous["content"]["campaign_plan"].get("boundaries", ()),  # type: ignore[index, union-attr]
        *prepared.get("boundaries", ()))))
    content = json.loads(json.dumps({
        "campaign_plan": payload, "campaign_plan_markdown": MARKDOWN,
        "sequel": {"previous_plan_id": previous["plan_id"], "source_id": basis["source_id"], "source_plan": SOURCE,
                   "identity_mapping": mapping, "preview": basis, "request": request},
    }))
    plan_id = "campaign-sequel-" + digest({"campaign": CAMPAIGN, "operation": operation})[:32]
    return plan_fields(content, plan_id=plan_id, revision=int(previous["plan_revision"]) + 1, resulting=6,
                       evidence=plan_id + "-review")


def changed(fields: dict[str, object], path: list[str], value: object = "<delete>") -> dict[str, object]:
    out = copy.deepcopy(fields)
    target = out
    for key in path[:-1]:
        target = target[key]  # type: ignore[assignment, index]
    if value == "<delete>":
        del target[path[-1]]  # type: ignore[attr-defined]
    else:
        target[path[-1]] = value  # type: ignore[index]
    return out


VALID = sequel_plan()
S = ["content", "sequel"]
CASES: list[tuple[str, dict[str, object], dict[str, object]]] = [
    ("valid", VALID, PREVIOUS),
    ("previous_plan_id", changed(VALID, S + ["previous_plan_id"], "plan-0"), PREVIOUS),
    ("campaign", changed(VALID, ["campaign_id"], "campaign-other"), PREVIOUS),
    ("package_lock", changed(VALID, ["package_lock_id"], "lock-" + "f" * 40), PREVIOUS),
    ("basis_previous_sha", changed(VALID, S + ["preview", "previous_plan_sha256"], "0" * 64), PREVIOUS),
    ("basis_previous_id", changed(VALID, S + ["preview", "previous_plan_id"], "plan-0"), PREVIOUS),
    ("basis_expected_revision", changed(VALID, S + ["preview", "expected_revision"], 4), PREVIOUS),
    ("request_expected_revision", changed(VALID, S + ["request", "expected_revision"], 4), PREVIOUS),
    ("request_source", changed(VALID, S + ["request", "source_id"], "library:other"), PREVIOUS),
    ("metadata_source", changed(VALID, S + ["source_id"], "library:other"), PREVIOUS),
    ("request_session", changed(VALID, S + ["request", "session_id"], "dm-2"), PREVIOUS),
    ("basis_session", changed(VALID, S + ["preview", "session_id"], "dm-2"), PREVIOUS),
    ("basis_campaign", changed(VALID, S + ["preview", "campaign_id"], "campaign-other"), PREVIOUS),
    ("basis_number", changed(VALID, S + ["preview", "number"], 3), PREVIOUS),
    ("basis_number_text", changed(VALID, S + ["preview", "number"], "2"), PREVIOUS),
    ("basis_number_float", changed(VALID, S + ["preview", "number"], 2.0), PREVIOUS),
    ("plan_revision", changed(VALID, ["plan_revision"], 3), PREVIOUS),
    ("preview_sha", changed(VALID, S + ["request", "preview_sha256"], "1" * 64), PREVIOUS),
    ("source_plan", changed(VALID, S + ["source_plan", "title"], "Another"), PREVIOUS),
    ("markdown", changed(VALID, ["content", "campaign_plan_markdown"], "# Changed"), PREVIOUS),
    ("operation", changed(VALID, S + ["request", "operation_id"], "op-2"), PREVIOUS),
    ("prepared_plan", changed(VALID, ["content", "campaign_plan", "title"], "Edited"), PREVIOUS),
    ("identity_mapping", changed(VALID, S + ["identity_mapping", "act-a"], "act-a"), PREVIOUS),
    ("identity_mapping_list", changed(VALID, S + ["identity_mapping"], []), PREVIOUS),
    ("missing_request", changed(VALID, S + ["request"]), PREVIOUS),
    ("missing_review", changed(VALID, S + ["request", "review"]), PREVIOUS),
    ("review_unconfirmed", changed(VALID, S + ["request", "review", "reviewed"], False), PREVIOUS),
    ("missing_markdown", changed(VALID, ["content", "campaign_plan_markdown"]), PREVIOUS),
    ("sequel_not_object", changed(VALID, S, "plan-1"), PREVIOUS),
    ("previous_without_plan", VALID, changed(PREVIOUS, ["content", "campaign_plan"])),
    ("previous_boundaries_unhashable", VALID, changed(PREVIOUS, ["content", "campaign_plan", "boundaries"], [["x"]])),
    ("previous_boundaries_text", VALID, changed(PREVIOUS, ["content", "campaign_plan", "boundaries"], "ab")),
]
THIRD_PREVIOUS = sequel_plan()
CASES.append(("third_adventure", sequel_plan(number=3, operation="op-3", previous=THIRD_PREVIOUS), THIRD_PREVIOUS))
CASES.append(("third_adventure_wrong_number", sequel_plan(number=2, operation="op-3", previous=THIRD_PREVIOUS),
              THIRD_PREVIOUS))


def to_plan(fields: dict[str, object]) -> CampaignPlan:
    values = dict(fields)
    values["provenance_evidence_ids"] = tuple(values["provenance_evidence_ids"])  # type: ignore[arg-type]
    values["validation_outcome"] = PlanValidationOutcome(values["validation_outcome"])
    values["acceptance_status"] = PlanAcceptanceStatus(values["acceptance_status"])
    return CampaignPlan(**values)  # type: ignore[arg-type]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    results = []
    for name, plan, previous in CASES:
        try:
            verify_sequel(to_plan(plan), to_plan(previous))
            outcome: dict[str, object] = {"ok": None}
        except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
            outcome = {"error": type(error).__name__, "message": str(error)}
        results.append({"name": name, "plan": plan, "previous": previous, "outcome": outcome})
    record = {"python": sys.version.split()[0], "cases": results}
    (OUT / "sequel_vectors.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
