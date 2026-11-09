"""Bind genuine Builder form input to its immutable saved question evidence."""

from collections.abc import Mapping, Sequence

from sylvia_gm.domain.authority import SafePayload, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionContext, SessionMessageKind


def builder_question_details(content: SafePayload) -> dict | None:
    """Recognize the same answerable widget in display projections and submission checks."""
    if content.get("kind") != "builder_tool_result" or content.get("tool_name") != "ask_question":
        return None
    result = content.get("result")
    if not isinstance(result, Mapping) or result.get("success") is not True:
        return None
    widget = result.get("widget")
    if not isinstance(widget, Mapping) or result.get("status") != "awaiting_answer":
        return None
    kind, data = widget.get("type"), widget.get("data")
    if (
        not isinstance(kind, str)
        or kind not in {"text", "select", "cascade", "confirm"}
        or not isinstance(data, Mapping)
    ):
        return None
    if not all(isinstance(data.get(key), str) and data[key].strip() for key in ("id", "question")):
        return None
    if not isinstance(content.get("tool_call_id"), str) or not content["tool_call_id"]:
        return None
    options = []
    raw_options = data.get("options")
    if kind == "confirm" and not raw_options:
        raw_options = ["Yes", "No"]
    if kind != "text":
        if not isinstance(raw_options, Sequence) or isinstance(raw_options, (str, bytes)):
            return None
        for option in raw_options:
            if isinstance(option, str) and option:
                options.append({"value": option, "label": option})
            elif isinstance(option, Mapping) and all(
                isinstance(option.get(key), str) and option[key] for key in ("value", "label")
            ):
                options.append({"value": option["value"], "label": option["label"]})
        if not options:
            return None
    return {
        "tool_call_id": content["tool_call_id"],
        "widget_id": data["id"],
        "type": kind,
        "question": data["question"],
        "options": options,
    }


def builder_question_answer_content(
    context: SessionContext,
    answers: object,
    user_text: str,
) -> SafePayload:
    """Validate the whole active form and derive its snapshots from saved evidence.

    Call again under the existing Builder session work lock before appending input;
    a second submission or a departed session must never run another model turn.
    """
    if context.session.role is not WriterRole.BUILDER:
        raise CommandRejected("Question answers require a Builder session")
    if not isinstance(answers, (list, tuple)) or not answers:
        raise CommandRejected("Question answers must be a nonempty list")
    latest_user = max(
        (row.sequence for row in context.messages if row.kind is SessionMessageKind.USER), default=0
    )
    questions = {}
    for row in context.messages:
        if row.kind is SessionMessageKind.EVIDENCE and row.sequence > latest_user:
            question = builder_question_details(row.content)
            if question:
                questions[row.message_id] = question
    snapshots = []
    seen = set()
    for answer in answers:
        if not isinstance(answer, Mapping) or set(answer) != {
            "question_message_id",
            "value",
            "custom",
        }:
            raise CommandRejected("Question answer fields are invalid")
        identity, value, custom = (
            answer.get(key) for key in ("question_message_id", "value", "custom")
        )
        if not isinstance(identity, str) or identity not in questions or identity in seen:
            raise CommandRejected("Question is stale, duplicated, or belongs to another session")
        if not isinstance(value, str) or not value.strip() or not isinstance(custom, bool):
            raise CommandRejected("Question answer must contain a genuine value")
        question = questions[identity]
        if question["type"] == "text" and custom:
            raise CommandRejected("Text questions do not accept a choice override")
        label = value
        if question["type"] != "text" and not custom:
            options = question["options"]
            selected = next((option for option in options if option["value"] == value), None)
            if selected is None:
                raise CommandRejected("Question answer is not an offered choice")
            label = selected["label"]
            if sum(option["label"] == label for option in options) > 1 and value != label:
                label = f"{label} ({value})"
        seen.add(identity)
        snapshots.append(
            {
                "question_message_id": identity,
                **{key: val for key, val in question.items() if key != "options"},
                "value": value,
                "label": label,
                "custom": custom,
            }
        )
    if set(questions) != seen:
        raise CommandRejected("Answer every current question before submitting")
    if [row["question_message_id"] for row in snapshots] != list(questions):
        raise CommandRejected("Question answers must follow their saved order")
    text = "Here are my answers:\n\n" + "\n\n".join(
        f"{index}. {row['question']}\nAnswer: {row['label']}"
        for index, row in enumerate(snapshots, start=1)
    )
    if user_text != text:
        raise CommandRejected("Question answer text does not match the saved form")
    return {"kind": "builder_question_answer", "text": text, "answers": snapshots}
