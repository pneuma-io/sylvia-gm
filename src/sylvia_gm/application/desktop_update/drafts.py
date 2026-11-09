"""Local unsent text, scoped to the selected authority data root; never executable work."""

from __future__ import annotations

from pathlib import Path

from .manifest import MANIFEST_LIMIT, UpdateError, bounded_json, canonical_bytes
from .storage import atomic_receipt, checked_file, directory_identity, operation_id

MAX_SESSIONS = 128
MAX_QUESTIONS = 256
MAX_TEXT = 256 * 1024


def _text(value: object, maximum: int, *, nonempty: bool = False) -> bool:
    return isinstance(value, str) and (not nonempty or bool(value)) and len(value) <= maximum


def validate_drafts(value: object) -> dict[str, object]:
    """Reject whole invalid snapshots rather than truncating or dropping unsent fields."""
    if (
        not isinstance(value, dict)
        or not {"format_version", "sessions"} <= set(value) <= {
            "format_version", "sessions", "update_view",
        }
        or type(value["format_version"]) is not int
        or value["format_version"] != 1
        or not isinstance(value["sessions"], list)
        or len(value["sessions"]) > MAX_SESSIONS
    ):
        raise UpdateError("invalid_drafts", "Unsent drafts could not be saved safely.")
    if "update_view" in value:
        view = value["update_view"]
        if (
            not isinstance(view, dict)
            or set(view) != {"operation_id", "kind", "campaign_id", "session_id"}
            or view["kind"] not in ("home", "session")
            or not all(item is None or _text(item, 256, nonempty=True)
                       for item in (view["campaign_id"], view["session_id"]))
            or (view["kind"] == "home" and (view["campaign_id"] is not None
                                           or view["session_id"] is not None))
            or (view["kind"] == "session" and view["session_id"] is None)
        ):
            raise UpdateError("invalid_drafts", "The saved update view is invalid.")
        operation_id(view["operation_id"])
    sessions: set[str] = set()
    for row in value["sessions"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"session_id", "text", "ooc_text", "ooc_open", "questions"}
            or not _text(row["session_id"], 256, nonempty=True)
            or row["session_id"] in sessions
            or not _text(row["text"], MAX_TEXT)
            or not _text(row["ooc_text"], MAX_TEXT)
            or type(row["ooc_open"]) is not bool
            or not isinstance(row["questions"], list)
            or len(row["questions"]) > MAX_QUESTIONS
        ):
            raise UpdateError("invalid_drafts", "Unsent drafts could not be saved safely.")
        sessions.add(row["session_id"])
        questions: set[str] = set()
        for question in row["questions"]:
            if (
                not isinstance(question, dict)
                or not {"question_key"} <= question.keys() <= {
                    "question_key", "text", "custom", "selection"
                }
                or not _text(question["question_key"], 256, nonempty=True)
                or question["question_key"] in questions
                or any(not _text(item, MAX_TEXT) for key, item in question.items()
                       if key != "question_key")
            ):
                raise UpdateError("invalid_drafts", "Unsent question fields could not be saved.")
            questions.add(question["question_key"])
    if len(canonical_bytes(value)) > MANIFEST_LIMIT:
        raise UpdateError(
            "draft_limit", "Unsent drafts are too large to save. Keep this window open."
        )
    return value


class DraftStore:
    def __init__(self, storage_root: Path) -> None:
        self.root = storage_root
        self.identity = directory_identity(storage_root)
        self.path = storage_root / ".desktop-drafts.json"

    def _check(self) -> None:
        if directory_identity(self.root) != self.identity:
            raise UpdateError("changed_draft_root", "The selected draft storage folder changed.")

    def load(self) -> dict[str, object]:
        self._check()
        if not self.path.exists() and not self.path.is_symlink():
            return {"format_version": 1, "sessions": []}
        return validate_drafts(bounded_json(checked_file(self.path, maximum=MANIFEST_LIMIT)))

    def save(self, value: object) -> dict[str, str]:
        self._check()
        validate_drafts(value)
        atomic_receipt(self.path, value)
        return {"status": "saved"}
