"""Generate reference vectors for sylvia_domain::session and ::model_capability.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_session_vectors.py

Each record type is built from a valid set of arguments and from variants that change one or
two fields (blank, padded, non-ASCII, out-of-range and inconsistent values). Python's outcome
is the exception class and message, or the record's payload hash. The output is deterministic
(fixed seed) and checked in, so the Rust tests need no Python. This script is reference
tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.domain.authority import WriterRole, thaw_json_value  # noqa: E402
from sylvia_gm.domain.model_capability import (  # noqa: E402
    ModelCapabilityPlacement,
    ModelCapabilityRegistry,
    model_guidance_from_payload,
    model_guidance_payload,
    model_guidance_payload_sha256,
)
from sylvia_gm.domain.session import (  # noqa: E402
    BuilderChatRecord,
    BuilderDMHandoff,
    BuilderEvidence,
    CampaignPlan,
    ChildEvidence,
    EvidenceSourceKind,
    PlanAcceptanceStatus,
    PlanValidationOutcome,
    SessionMessage,
    SessionMessageKind,
    SessionRecord,
    SessionStatus,
    agent_session_payload_sha256,
    builder_chat_payload_sha256,
    builder_dm_handoff_payload_sha256,
    campaign_plan_payload_sha256,
)

OUTPUT = Path(__file__).with_name("session_vectors.json")
rng = random.Random(20261121)
SHA = "a" * 64
TEXTS = ["", " ", "x", " x", "x ", "\x1cx", "e\u0301", "\u00e9t\u00e9", "A" * 64, "g" * 64, SHA, "B" * 64]
INTS = [-1, 0, 1, 2, 2**31, 2**62]


def outcome(call):
    try:
        return ["ok", thaw_json_value(call())]
    except Exception as error:  # the class is behaviour
        return [type(error).__name__, str(error)]


def variants(base, choices, count):
    """`base`, each field replaced by each choice in turn, and random two-field changes."""
    cases = [dict(base)]
    for field, options in choices.items():
        for option in options:
            cases.append({**base, field: option})
    for _ in range(count):
        case = dict(base)
        for field in rng.sample(list(choices), min(2, len(choices))):
            case[field] = rng.choice(choices[field])
        cases.append(case)
    return cases


GUIDANCE = {
    "minimum_tier_id": "sylvia.capability.t05",
    "model_id": "model-1",
    "override_accepted": True,
    "placement_evidence_sha256": SHA,
    "placement_tier_id": "sylvia.capability.t03",
    "registry_version": "sylvia.capability.registry.v1",
    "status": "below",
    "warning_code": "model_below_ruleset_minimum",
}
TIERS = [None, "sylvia.capability.t00", "sylvia.capability.t03", "sylvia.capability.t05", "sylvia.capability.t09",
         "sylvia.capability.t10", " sylvia.capability.t03", 3]


def guidance_cases():
    choices = {
        "minimum_tier_id": TIERS,
        "model_id": TEXTS[:6] + [None],
        "override_accepted": [True, False, 1, None],
        "placement_evidence_sha256": [None, SHA, "A" * 64, "", 5],
        "placement_tier_id": TIERS,
        "registry_version": ["sylvia.capability.registry.v1", "v2", None],
        "status": ["not_applicable", "unrated", "below", "meets", "other", 1],
        "warning_code": [None, "model_below_ruleset_minimum", "model_capability_unrated", "x"],
    }
    cases = []
    payloads = variants(GUIDANCE, choices, 300)
    payloads += [{key: None for key in GUIDANCE}, {**GUIDANCE, "extra": 1}, ["x"], "x"]
    for payload in payloads:
        cases.append({"payload": payload, "outcome": outcome(
            lambda: [model_guidance_payload_sha256(decision := model_guidance_from_payload(payload)),
                     model_guidance_payload(decision), decision.warning_required, decision.may_continue])})
    return cases


def placement_cases():
    base = {"model_id": "model-1", "tier_id": "sylvia.capability.t04", "benchmark_suite_id": "suite",
            "benchmark_suite_version": "1.0", "benchmark_suite_sha256": SHA, "result_sha256": "b" * 64}
    choices = {key: TEXTS[:8] + ["sylvia.capability.t02"] for key in base}
    return [{"fields": fields, "outcome": outcome(lambda: ModelCapabilityPlacement(**fields).evidence_sha256)}
            for fields in variants(base, choices, 40)]


def assess_cases():
    placements = [
        ModelCapabilityPlacement("model-a", "sylvia.capability.t02", "suite", "1", SHA, "b" * 64),
        ModelCapabilityPlacement("model-b", "sylvia.capability.t07", "suite", "1", SHA, "c" * 64),
    ]
    registry = ModelCapabilityRegistry(tuple(placements))
    cases = []
    for model_id in ["model-a", "model-b", "model-c", " model-a", ""]:
        for minimum in [None, "sylvia.capability.t00", "sylvia.capability.t02", "sylvia.capability.t05",
                        "sylvia.capability.t09", "sylvia.capability.t99"]:
            for override in (True, False):
                cases.append({"model_id": model_id, "minimum": minimum, "override": override, "outcome": outcome(
                    lambda: [model_guidance_payload_sha256(decision := registry.assess(
                        model_id=model_id, minimum_tier_id=minimum, override_accepted=override)),
                        model_guidance_payload(decision)])})
    duplicate = outcome(lambda: ModelCapabilityRegistry((placements[0], placements[0])) and None)
    return {"cases": cases, "duplicate": duplicate}


SESSION = {"session_id": "session-1", "campaign_id": "campaign-1", "role": "builder", "provider_id": "provider",
           "model_id": "model", "status": "active", "parent_session_id": None,
           "selected_skill": "BUILD RULESET", "scope": [], "reasoning_effort": None, "reasoning_effort_once": None}


def session_record(fields):
    return SessionRecord(**{**fields, "role": WriterRole(fields["role"]), "status": SessionStatus(fields["status"]),
                            "scope": tuple(fields["scope"])})


def session_cases():
    choices = {
        "session_id": TEXTS[:6],
        "campaign_id": [None, "campaign-1", "", " c"],
        "role": ["builder", "dm", "none"],
        "provider_id": TEXTS[:5],
        "model_id": TEXTS[:5],
        "status": ["active", "stopped", "completed", "review_required", "handoff_closed"],
        "parent_session_id": [None, "parent", "", " p"],
        "selected_skill": [None, "BUILD RULESET", "BUILD CAMPAIGN SETTING", "BUILD CAMPAIGN PLAN", "OTHER", ""],
        "scope": [[], ["a"], ["a"] * 8, ["a"] * 9, ["a", ""]],
        "reasoning_effort": [None, "high", "x", "", "High", "1x", "a" * 32, "a" * 33, "a b", "h\u00e9", "low-1_"],
        "reasoning_effort_once": [None, "medium", "", "Z"],
    }
    return [{"fields": fields, "outcome": outcome(lambda: agent_session_payload_sha256(session_record(fields)))}
            for fields in variants(SESSION, choices, 300)]


HANDOFF = {"handoff_id": "handoff-1", "campaign_id": "campaign-1", "builder_session_id": "b-1",
           "dm_session_id": "d-1", "package_lock_id": "lock-1", "package_candidate_id": None, "plan_id": "plan-1",
           "plan_revision": 1, "plan_content_sha256": SHA, "expected_revision": 3,
           "resulting_campaign_revision": 4, "chronology_ordinal": 1, "completed_turn_id": "turn-1",
           "payload_sha256": "c" * 64}


def handoff_cases():
    choices = {key: TEXTS[:6] for key in HANDOFF if isinstance(HANDOFF[key], str)}
    choices.update({key: INTS for key in ("plan_revision", "expected_revision", "resulting_campaign_revision",
                                          "chronology_ordinal")})
    choices["package_candidate_id"] = [None, "cand", "", " c"]
    decision = model_guidance_from_payload(GUIDANCE)
    return [{"fields": fields, "outcome": outcome(
        lambda: BuilderDMHandoff(**fields, model_guidance=decision) and None)}
            for fields in variants(HANDOFF, choices, 200)]


def handoff_payload_cases():
    cases = []
    for _ in range(120):
        request = {
            "handoff_id": rng.choice(TEXTS), "campaign_id": rng.choice(TEXTS), "builder_session_id": "b",
            "dm_session_id": "d", "dm_provider_id": rng.choice(TEXTS), "dm_model_id": "m",
            "expected_writer": rng.choice(["builder", "dm", "none"]), "expected_revision": rng.choice(INTS),
            "expected_package_lock_id": "l", "expected_package_candidate_id": rng.choice([None, "c", "\u00e9"]),
            "plan_id": "p", "expected_plan_revision": rng.choice(INTS), "expected_plan_content_sha256": SHA,
            "completed_turn_id": "t", "model_guidance_sha256": "d" * 64,
        }
        cases.append({"request": request, "sha256": builder_dm_handoff_payload_sha256(
            **{**request, "expected_writer": WriterRole(request["expected_writer"])})})
    return cases


PLAN = {"plan_id": "plan-1", "campaign_id": "campaign-1", "package_lock_id": "lock-1", "source_session_id": "s-1",
        "content_sha256": SHA, "content": {"title": "A plan", "acts": [1, 2.5, None]},
        "provenance_evidence_ids": ["e-1", "e-2"], "validation_outcome": "valid", "acceptance_status": "accepted",
        "plan_revision": 1, "resulting_campaign_revision": 2, "payload_sha256": "c" * 64}


def plan_cases():
    choices = {key: TEXTS[:6] for key in ("plan_id", "campaign_id", "package_lock_id", "source_session_id",
                                           "content_sha256", "payload_sha256")}
    choices["content"] = [{}, {"\u00e9": [1, {"x": True}]}, [1], "x", None]
    choices["provenance_evidence_ids"] = [[], ["a"], ["a", "a"], ["a", ""], [" a"], ["b", "a"]]
    choices["plan_revision"] = INTS
    choices["resulting_campaign_revision"] = INTS

    def build(fields):
        return CampaignPlan(**{**fields, "provenance_evidence_ids": tuple(fields["provenance_evidence_ids"]),
                               "validation_outcome": PlanValidationOutcome.VALID,
                               "acceptance_status": PlanAcceptanceStatus.ACCEPTED})

    def payload(fields):
        return campaign_plan_payload_sha256(
            plan_id=fields["plan_id"], campaign_id=fields["campaign_id"], source_session_id=fields["source_session_id"],
            expected_revision=fields["plan_revision"], expected_package_lock_id=fields["package_lock_id"],
            content_sha256=fields["content_sha256"], content=fields["content"],
            provenance_evidence_ids=tuple(fields["provenance_evidence_ids"]),
            validation_outcome=PlanValidationOutcome.VALID, acceptance_status=PlanAcceptanceStatus.ACCEPTED)

    return [{"fields": fields, "outcome": outcome(lambda: build(fields) and None),
             "payload": outcome(lambda: payload(fields))} for fields in variants(PLAN, choices, 120)]


def evidence_cases():
    builder = {"evidence_id": "e-1", "source_kind": "open_source", "content_sha256": SHA,
               "source_locator": "https://example.org/x", "safe_excerpt": "excerpt", "claims": ["c"]}
    builder_choices = {"evidence_id": TEXTS[:6], "source_kind": ["open_source", "user_provided"],
                       "content_sha256": [SHA, "A" * 64, "a" * 63, "\u00e9" * 64, ""],
                       "source_locator": [None, "", "x"], "safe_excerpt": ["", "x"], "claims": [[], ["a"], ["a", ""]]}
    child = {"evidence_id": "e-1", "child_session_id": "c-1", "parent_session_id": "p-1", "content_sha256": SHA,
             "safe_summary": "summary", "claims": ["c"], "authoritative": False}
    child_choices = {"evidence_id": TEXTS[:4], "child_session_id": TEXTS[:4], "parent_session_id": TEXTS[:4],
                     "content_sha256": [SHA, "A" * 64, ""], "safe_summary": ["", "x"], "claims": [[], ["a", ""]],
                     "authoritative": [False, True]}
    builder_cases = [{"fields": f, "outcome": outcome(lambda: BuilderEvidence(
        **{**f, "source_kind": EvidenceSourceKind(f["source_kind"]), "claims": tuple(f["claims"])}).payload())}
        for f in variants(builder, builder_choices, 60)]
    child_cases = [{"fields": f, "outcome": outcome(lambda: ChildEvidence(
        **{**f, "claims": tuple(f["claims"])}).payload())} for f in variants(child, child_choices, 60)]
    return builder_cases, child_cases


def message_cases():
    base = {"session_id": "s-1", "sequence": 1, "message_id": "m-1", "kind": "talk", "content": {"text": "hi"},
            "canonical_sequence": None}
    choices = {"session_id": TEXTS[:4], "sequence": INTS, "message_id": TEXTS[:4],
               "kind": ["user", "safe_think", "talk", "steering", "evidence", "canonical_reference", "human_review"],
               "content": [{}, {"reasoning_raw": 1}, {"a": {"traceback": 1}}, [1], [{"scratchpad": 1}], "x", None],
               "canonical_sequence": [None, 0, 1, -1, 5]}
    return [{"fields": f, "outcome": outcome(lambda: SessionMessage(
        **{**f, "kind": SessionMessageKind(f["kind"])}) and None)} for f in variants(base, choices, 120)]


def chat_cases():
    cases = []
    sessions = [dict(SESSION, campaign_id=None, selected_skill=None), dict(SESSION), dict(SESSION, role="dm",
                selected_skill=None)]
    for index, fields in enumerate(sessions):
        for name in ["Chat", "", " Chat", "Chat\u3000", "x" * 120, "x" * 121, "\u00e9" * 120, "\x1cChat"]:
            for ordinal in (1, 0, -1, 7):
                cases.append({"session": index, "display_name": name, "created_ordinal": ordinal, "outcome": outcome(
                    lambda: builder_chat_payload_sha256(BuilderChatRecord(session_record(fields), name, ordinal)))})
    return {"sessions": sessions, "cases": cases}


def main() -> None:
    builder_cases, child_cases = evidence_cases()
    vectors = {
        "python": sys.version.split()[0],
        "guidance": guidance_cases(),
        "placements": placement_cases(),
        "assess": assess_cases(),
        "sessions": session_cases(),
        "handoffs": handoff_cases(),
        "handoff_guidance": GUIDANCE,
        "handoff_payloads": handoff_payload_cases(),
        "plans": plan_cases(),
        "builder_evidence": builder_cases,
        "child_evidence": child_cases,
        "messages": message_cases(),
        "chats": chat_cases(),
    }
    text = json.dumps(vectors, indent=None, ensure_ascii=True, separators=(",", ":")) + "\n"
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(len(text) // 1000, "KB", hashlib.sha256(text.encode()).hexdigest()[:12])


if __name__ == "__main__":
    main()
