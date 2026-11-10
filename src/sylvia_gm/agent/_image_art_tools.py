"""The same image-art tools for GM and Builder; permission remains with the owner."""

from __future__ import annotations

import hashlib
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool
from sylvia_gm.domain.image_art import IMAGE_AGENT_INSTRUCTIONS, IMAGE_PURPOSES
from sylvia_gm.tools._spec import ToolSpec
from sylvia_gm.tools._toolsets import ToolsetName

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner

IMAGE_TOOL_NAMES = frozenset({"get_image_art", "request_image_art"})
_REQUEST = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "operation_id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "description": (
                "Stable identity for this deliberate request; reuse on uncertain replies."
            ),
        },
        "target_id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "description": "The saved character ID or actual map ID, never a file path.",
        },
        "purpose": {"type": "string", "enum": sorted(IMAGE_PURPOSES)},
        "brief": {
            "type": "string",
            "minLength": 1,
            "maxLength": 12000,
            "description": (
                "Visual style and requested content; Sylvia adds the purpose-specific format."
            ),
        },
        "appearance": {
            "type": "string",
            "maxLength": 16000,
            "description": (
                "Complete established character appearance, equipment and distinctive details."
            ),
        },
        "map_scene": {
            "type": "object",
            "description": (
                "Builder only: the actual authored spatial scene. GM maps are resolved "
                "by the owner."
            ),
        },
        "edit_sha256": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
            "description": (
                "A currently selected image to edit, retaining canonical character references."
            ),
        },
    },
    "required": ["operation_id", "target_id", "purpose", "brief"],
}


def image_art_model_tools(owner: AuthorityOwner) -> tuple[ModelMechanicsTool, ...]:
    """Use the established application-tool catalog without pretending art is a Ruleset mechanic."""
    if not callable(getattr(owner, "image_art_service", None)):
        return ()
    digest = hashlib.sha256(IMAGE_AGENT_INSTRUCTIONS.encode()).hexdigest()
    return (
        ModelMechanicsTool(
            "sylvia.application",
            "1",
            digest,
            "get_image_art",
            "image_art.read",
            {
                "type": "object",
                "properties": {
                    "wait_for_operation_id": {
                        "type": "string",
                        "maxLength": 128,
                        "description": (
                            "Optionally wait up to 20 seconds for one pending image, without "
                            "submitting another request."
                        ),
                    },
                },
                "additionalProperties": False,
            },
            description=IMAGE_AGENT_INSTRUCTIONS,
            category="reference",
        ),
        ModelMechanicsTool(
            "sylvia.application",
            "1",
            digest,
            "request_image_art",
            "image_art.request",
            _REQUEST,
            description=(
                "Request generation or editing of one character reference, portrait or "
                "map underlay. "
                "The owner adds saved canonical references and renders the actual map reference. "
                "Review mode proposes it; Auto generates and uses it. A failed request "
                "never retries itself."
            ),
            category="image_generation",
        ),
    )


def call_image_art_tool(
    owner: AuthorityOwner, session_id: str, name: str, parameters: dict[str, Any]
) -> dict[str, Any]:
    """An agent can inspect or request; user approval and mode changes are separate UI commands."""
    service = owner.image_art_service()
    if name == "get_image_art":
        identity = parameters.get("wait_for_operation_id")
        if identity is not None:
            service._owned(session_id, identity)
            future = service._futures.get(identity)
            if future is not None:
                with suppress(FutureTimeoutError):
                    future.result(timeout=20)
        return {"success": True, "result": service.view(session_id)}
    if name == "request_image_art":
        return {"success": True, "result": service.request(session_id, **parameters)}
    raise ValueError("Unsupported image tool")


def image_art_builder_tools(owner: AuthorityOwner, session_id: str) -> tuple[ToolSpec, ...]:
    """Expose the same owner-controlled operations to ordinary and guided Builder chats."""
    result = []
    for tool in image_art_model_tools(owner):

        def execute(
            parameters: dict[str, Any], context: Any, name: str = tool.name
        ) -> dict[str, Any]:
            try:
                return call_image_art_tool(owner, session_id, name, parameters)
            except ValueError as error:
                return {"success": False, "error": str(error)}

        result.append(
            ToolSpec.from_openai_schema(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": dict(tool.input_schema),
                    },
                },
                handler=execute,
                toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
            )
        )
    return tuple(result)
