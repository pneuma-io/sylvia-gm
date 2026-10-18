"""Restricted artifact-owned questions for the guided Campaign Interview."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from sylvia_gm.domain.authority import SafePayload, owned_payload
from sylvia_gm.domain.content_validation import validate_content_shape
from sylvia_gm.domain.package_library import parse_content_json_bytes

INTERVIEW_CONTRIBUTION_PATH = "interview-contribution.json"
INTERVIEW_CONTRIBUTION_FORMAT_ID = "sylvia-gm-interview-contribution"
INTERVIEW_CONTRIBUTION_FORMAT_VERSION = 1
INTERVIEW_CONTRIBUTION_BYTE_LIMIT = 32 * 1024
_OWNER_KINDS = frozenset({"ruleset", "campaign_setting", "campaign_plan"})


@dataclass(frozen=True)
class InterviewContribution:
    """One validated, data-only question group owned by an immutable artifact."""

    owner_kind: str
    contribution_id: str
    title: str
    questions: tuple[SafePayload, ...]

    def as_payload(self) -> SafePayload:
        return owned_payload(
            {
                "format_id": INTERVIEW_CONTRIBUTION_FORMAT_ID,
                "format_version": INTERVIEW_CONTRIBUTION_FORMAT_VERSION,
                "owner_kind": self.owner_kind,
                "id": self.contribution_id,
                "title": self.title,
                "questions": [dict(question) for question in self.questions],
            }
        )

    def question_text(self) -> str:
        prompts = "\n".join(
            f"{index}. {question['prompt']}"
            for index, question in enumerate(self.questions, start=1)
        )
        return f"{self.title}\n{prompts}"


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _text(value: object, label: str, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > maximum
        or "\x00" in value
    ):
        raise ValueError(f"{label} is invalid")
    return value


def parse_interview_contribution_bytes(
    value: bytes,
    *,
    expected_owner_kind: str,
) -> InterviewContribution:
    """Check the shared field definition, then ownership and ordered question references."""

    if expected_owner_kind not in _OWNER_KINDS:
        raise ValueError("Interview Contribution owner kind is unsupported")
    if not value or len(value) > INTERVIEW_CONTRIBUTION_BYTE_LIMIT:
        raise ValueError("Interview Contribution is empty or oversized")
    raw = parse_content_json_bytes(
        value, label="Interview Contribution", maximum_depth=12, maximum_nodes=10_000,
    )
    validate_content_shape("interview_contribution", raw, file=INTERVIEW_CONTRIBUTION_PATH)
    contribution = cast(SafePayload, raw)
    if contribution["owner_kind"] != expected_owner_kind:
        raise ValueError("Interview Contribution owner is unsupported")
    questions = cast(Sequence[SafePayload], contribution["questions"])
    prior_ids: set[str] = set()
    for question in questions:
        question_id = cast(str, question["id"])
        if question_id in prior_ids:
            raise ValueError("Interview question identities must be unique")
        options = cast(Sequence[SafePayload], question["options"])
        option_ids = [option["id"] for option in options]
        if len(set(option_ids)) != len(option_ids):
            raise ValueError("Interview choice identities must be unique")
        condition = question["condition"]
        if isinstance(condition, Mapping) and condition["question_id"] not in prior_ids:
            raise ValueError("Interview conditions may reference only prior questions")
        prior_ids.add(question_id)
    return InterviewContribution(
        owner_kind=expected_owner_kind,
        contribution_id=cast(str, contribution["id"]),
        title=cast(str, contribution["title"]),
        questions=tuple(questions),
    )


def validate_interview_contribution_answers(
    contribution: InterviewContribution,
    value: object,
) -> SafePayload:
    """Validate exact typed answers against one already validated contribution."""

    answer = _object(value, "Interview Contribution answer")
    if set(answer) != {"contribution_id", "answers"}:
        raise ValueError("Interview Contribution answer fields are invalid")
    if answer["contribution_id"] != contribution.contribution_id:
        raise ValueError("Interview Contribution answer identity is stale")
    answers = _object(answer["answers"], "Interview Contribution answers")
    captured: dict[str, object] = {}
    known_ids = {str(question["id"]) for question in contribution.questions}
    if set(answers) - known_ids:
        raise ValueError("Interview Contribution contains unknown answers")
    for question in contribution.questions:
        question_id = str(question["id"])
        condition = question["condition"]
        active = True
        if isinstance(condition, Mapping):
            active = captured.get(str(condition["question_id"])) == condition["equals"]
        if not active:
            if question_id in answers:
                raise ValueError("Inactive Interview question cannot be answered")
            continue
        raw_value = answers.get(question_id)
        if raw_value is None:
            if question["required"]:
                raise ValueError("Required Interview question is unanswered")
            continue
        if question["type"] == "choice":
            options = cast(Sequence[Mapping[str, object]], question["options"])
            allowed = {str(option["id"]) for option in options}
            if not isinstance(raw_value, str) or raw_value not in allowed:
                raise ValueError("Interview choice answer is invalid")
            captured[question_id] = raw_value
        else:
            captured[question_id] = _text(
                raw_value, "Interview free-text answer", maximum=2_000
            )
    return owned_payload(
        {"contribution_id": contribution.contribution_id, "answers": captured}
    )
