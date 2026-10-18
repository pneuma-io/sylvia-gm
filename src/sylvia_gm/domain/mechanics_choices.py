"""Bounded public choice records used by durable deterministic action reservations."""

from collections.abc import Mapping, Sequence

from sylvia_gm.domain.authority import (
    MechanicOutcome,
    MechanicsChoiceAnswer,
    MechanicsChoiceOption,
    MechanicsChoiceRequest,
    SafePayload,
    mechanic_outcome_is_safe,
    owned_payload,
    validate_authority_identity,
)


def choice_payload(choice: MechanicsChoiceRequest) -> SafePayload:
    """Capture only the public descriptor, without an interpreter continuation."""
    return owned_payload(
        {
            "choice_id": choice.choice_id,
            "prompt": choice.prompt,
            "options": [{"option_id": o.option_id, "label": o.label} for o in choice.options],
        }
    )


def choice_from_payload(value: object) -> MechanicsChoiceRequest:
    """Reject malformed saved requests before replaying any authorized answer."""
    if not isinstance(value, Mapping) or set(value) != {"choice_id", "prompt", "options"}:
        raise ValueError("Saved mechanics choice fields are invalid")
    options = value["options"]
    if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
        raise ValueError("Saved mechanics choice options are invalid")
    if any(not isinstance(o, Mapping) or set(o) != {"option_id", "label"} for o in options):
        raise ValueError("Saved mechanics choice option fields are invalid")
    choice = MechanicsChoiceRequest(
        value["choice_id"],
        value["prompt"],
        tuple(MechanicsChoiceOption(o["option_id"], o["label"]) for o in options),
    )
    if not mechanic_outcome_is_safe(MechanicOutcome(result=owned_payload({}), choice=choice)):
        raise ValueError("Saved mechanics choice is invalid")
    return choice


def answer_payload(answer: MechanicsChoiceAnswer) -> SafePayload:
    """Retain the exact request and caller identity needed for safe retries."""
    return owned_payload(
        {
            "resolution_id": answer.resolution_id,
            "request": choice_payload(answer.request),
            "option_id": answer.option_id,
            "session_id": answer.session_id,
        }
    )


def answers_from_payload(value: object) -> tuple[MechanicsChoiceAnswer, ...]:
    """Validate at most 63 prior answers, leaving room for the current 64th decision."""
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) >= 64:
        raise ValueError("Saved mechanics choice sequence is invalid")
    answers = []
    identities = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "resolution_id",
            "request",
            "option_id",
            "session_id",
        }:
            raise ValueError("Saved mechanics choice answer fields are invalid")
        validate_authority_identity(item["resolution_id"], "Choice resolution identity")
        if item["resolution_id"] in identities:
            raise ValueError("Saved mechanics choice answers repeat a resolution identity")
        identities.add(item["resolution_id"])
        request = choice_from_payload(item["request"])
        if item["option_id"] not in {o.option_id for o in request.options}:
            raise ValueError("Saved mechanics choice option is invalid")
        if item["session_id"] is not None:
            validate_authority_identity(item["session_id"], "Choice session identity")
        answers.append(
            MechanicsChoiceAnswer(
                item["resolution_id"], request, item["option_id"], item["session_id"]
            )
        )
    return tuple(answers)
