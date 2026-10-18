"""Domain-level authority failures."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RulesetDiagnostic:
    """One bounded, portable validation failure shared by Builder and the desktop."""

    code: str
    phase: str
    message: str
    file: str | None = None
    path: str | None = None
    package_id: str | None = None
    item_id: str | None = None
    expected: str | None = None
    actual: str | None = None
    guidance: str | None = None

    def as_payload(self) -> dict[str, str]:
        """Return safe size-bounded fields without machine-private paths or source dumps."""

        payload: dict[str, str] = {}
        for name in (
            "code",
            "phase",
            "message",
            "file",
            "path",
            "package_id",
            "item_id",
            "expected",
            "actual",
            "guidance",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            text = str(value).replace("\\", "/")
            if name == "file" and (text.startswith("/") or ":/" in text):
                text = text.rsplit("/", 1)[-1]
            payload[name] = text[:500]
        return payload


class AuthorityError(RuntimeError):
    """Base class for fail-closed authority behavior."""


class SchemaIdentityError(AuthorityError):  # noqa: N818 - domain contract name
    """The live database does not match the deterministic authority schema."""

    code = "schema_identity_mismatch"


class OwnerConflict(AuthorityError):  # noqa: N818 - domain contract name
    """Another application owner already holds the live database."""


class WrongOwner(AuthorityError):  # noqa: N818 - domain contract name
    """A command did not originate from this application owner."""

    code = "wrong_owner"


class WrongWriter(AuthorityError):  # noqa: N818 - domain contract name
    """A command used a role that is not the campaign writer."""

    code = "wrong_writer"


class StaleRevision(AuthorityError):  # noqa: N818 - domain contract name
    """A command expected an obsolete campaign revision."""

    code = "stale_revision"


class IdempotencyConflict(AuthorityError):  # noqa: N818 - domain contract name
    """An idempotency key was reused with different semantics."""

    code = "idempotency_conflict"


class CommandRejected(AuthorityError):  # noqa: N818 - domain contract name
    """Command admission rejected an unavailable or invalid operation."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "command_rejected",
        diagnostics: tuple[RulesetDiagnostic, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics[:16]


@dataclass(frozen=True)
class MechanicsValidationIssue:
    """One privacy-safe schema problem that a model can correct without seeing values."""

    path: str
    kind: str
    expected_type: str | None = None
    actual_type: str | None = None
    parent_path: str | None = None
    allowed_values: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    actual_length: int | None = None

    def as_payload(self) -> dict[str, object]:
        """Project schema facts and submitted types/lengths, never submitted text."""

        payload: dict[str, object] = {"path": self.path, "kind": self.kind}
        for name in (
            "expected_type", "actual_type", "parent_path", "minimum", "maximum", "actual_length",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.allowed_values:
            payload["allowed_values"] = list(self.allowed_values)
        return payload


class MechanicsRejected(CommandRejected):  # noqa: N818 - domain contract name
    """Privacy-safe mechanics rejection suitable for one model correction."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        field_paths: tuple[str, ...] = (),
        validation_issues: tuple[MechanicsValidationIssue, ...] = (),
        state_changed: bool = False,
    ) -> None:
        super().__init__(message, code=code)
        self.field_paths = field_paths
        self.validation_issues = validation_issues[:64]
        self.state_changed = state_changed


class RulesetPreconditionRejected(MechanicsRejected):  # noqa: N818
    """An admitted Ruleset's bounded, model-safe precondition rejection."""

    def __init__(self, safe_reason: str) -> None:
        if (
            not isinstance(safe_reason, str)
            or not safe_reason
            or safe_reason != safe_reason.strip()
            or len(safe_reason) > 500
            or any(ord(character) < 32 for character in safe_reason)
        ):
            raise ValueError("Ruleset precondition reason is not model-safe")
        super().__init__(
            safe_reason,
            code="ruleset_precondition_failed",
            state_changed=False,
        )
        self.safe_reason = safe_reason
