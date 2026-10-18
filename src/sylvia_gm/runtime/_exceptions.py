"""Runtime-specific exceptions for the ruleset execution layer."""

# ruff: noqa: N818  # RuntimeException is the base, not a leaf error

from __future__ import annotations


class RuntimeException(Exception):
    """Base exception for all runtime errors."""


class ProcedureDepthError(RuntimeException):
    """Procedure chain exceeded MAX_PROCEDURE_DEPTH.

    Raised when nested procedure calls exceed the recursion guard (10).
    The session runner catches this and surfaces it as a ruleset
    authoring error, not an engine crash.
    """

    def __init__(self, trigger_name: str, depth: int, max_depth: int) -> None:
        super().__init__(
            f"Procedure chain exceeded depth limit ({depth} > {max_depth}) "
            f"at trigger '{trigger_name}'. Possible circular definition."
        )
        self.trigger_name = trigger_name
        self.depth = depth
        self.max_depth = max_depth


class ToolNotFoundError(RuntimeException):
    """Requested tool does not exist in the loaded ruleset."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"Tool '{tool_name}' not found in the loaded ruleset.")
        self.tool_name = tool_name


class ProcedureNotFoundError(RuntimeException):
    """No procedure registered for the given trigger."""

    def __init__(self, trigger_name: str) -> None:
        super().__init__(f"No procedure found for trigger '{trigger_name}'.")
        self.trigger_name = trigger_name


class ToolValidationError(RuntimeException):
    """Tool parameters failed validation."""

    def __init__(self, tool_name: str, errors: list[str]) -> None:
        super().__init__(f"Tool '{tool_name}' parameter validation failed: {'; '.join(errors)}")
        self.tool_name = tool_name
        self.errors = errors


class PreConditionError(RuntimeException):
    """A tool's pre-condition failed.

    This is NOT an engine error — it's the runtime enforcing the rules.
    The caller receives a ToolResult with success=False and the error message.
    """

    def __init__(self, condition: str, message: str) -> None:
        super().__init__(message)
        self.condition = condition


class ExpressionError(RuntimeException):
    """Error during expression parsing or evaluation.

    Contains a human-readable message suitable for surfacing to
    the AI DM or ruleset author.
    """

    def __init__(self, message: str, expr: str = "", position: int = -1) -> None:
        full_msg = message
        if expr and position >= 0:
            full_msg = f"{message} at position {position} in '{expr}'"
        elif expr:
            full_msg = f"{message} in expression '{expr}'"
        super().__init__(full_msg)
        self.expr = expr
        self.position = position


class FieldNotFoundError(ExpressionError):
    """A field path could not be resolved during evaluation."""


class EntityNotFoundRuntimeError(RuntimeException):
    """An entity referenced in a tool call does not exist in state."""

    def __init__(self, entity_id: str) -> None:
        super().__init__(f"Entity '{entity_id}' not found in campaign state.")
        self.entity_id = entity_id


class EffectExecutionError(RuntimeException):
    """Error during effect execution (e.g., dice roller failure)."""

    def __init__(self, effect_type: str, message: str) -> None:
        super().__init__(f"Effect '{effect_type}' failed: {message}")
        self.effect_type = effect_type


class MechanicsChoiceSuspended(RuntimeException):
    """Typed control flow for one bounded post-result package choice."""

    def __init__(self, request: object) -> None:
        super().__init__("Package mechanic is awaiting an authorized choice")
        self.request = request
