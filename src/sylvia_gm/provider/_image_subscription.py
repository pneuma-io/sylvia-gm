"""GPT image generation through Sylvia's managed ChatGPT runtime profile.

Credentials stay with Codex. Each authorized image request gets one ephemeral turn; failures,
refusals and interrupted connections never start another turn or fall back to API billing.
"""

from __future__ import annotations

import base64
import binascii
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sylvia_gm.application.image_hooks import (
    MAX_IMAGE_BYTES,
    ImageCapabilities,
    ImageData,
    ImageHookError,
    ImageRequest,
    ImageUpdate,
)
from sylvia_gm.provider._codex_rpc import CodexConnection, CodexConnectionError


class SubscriptionImageAdapter:
    """Expose only capabilities actually supplied by the isolated Codex image tool."""

    capabilities = ImageCapabilities(
        "gpt.subscription",
        frozenset({"create", "edit"}),
        frozenset({(1024, 1024), (1024, 1536), (1536, 1024)}),
        maximum_bytes=MAX_IMAGE_BYTES,
    )

    def __init__(
        self,
        home: Path,
        model: str,
        *,
        connection_factory: Callable[..., CodexConnection] = CodexConnection,
        timeout: float = 600,
        audit: Callable[[object], None] | None = None,
    ) -> None:
        self.home = home
        self.model = model
        self.connection_factory = connection_factory
        self.timeout = timeout
        self.audit = audit
        self._connections: dict[str, CodexConnection] = {}
        self._lock = threading.Lock()

    def stop(self, operation_id: str) -> None:
        """Stop this request's local runtime; already submitted remote work is not refundable."""
        with self._lock:
            connection = self._connections.get(operation_id)
        if connection is not None:
            connection.request_stop()
            connection.close()

    def close(self) -> None:
        with self._lock:
            identities = tuple(self._connections)
        for identity in identities:
            self.stop(identity)

    def submit(self, request: ImageRequest) -> ImageUpdate:
        """Run one image-only turn without environments, shell, user skills or Sylvia tools."""
        connection = self.connection_factory(self.home, image_generation=True)
        with self._lock:
            self._connections[request.operation_id] = connection
        try:
            connection.start()
            account = connection.call("account/read", {"refreshToken": False})
            if account.get("account", {}).get("type") != "chatgpt":
                raise ImageHookError("credentials_refused")
            capabilities = connection.call("modelProvider/capabilities/read", {})
            if capabilities.get("imageGeneration") is not True:
                raise ImageHookError("unsupported_capability")
            thread = self._call(
                connection,
                "thread/start",
                {
                    "model": self.model,
                    "allowProviderModelFallback": False,
                    "ephemeral": True,
                    "environments": [],
                    "selectedCapabilityRoots": [],
                    "approvalPolicy": "never",
                    "dynamicTools": [],
                    "baseInstructions": (
                        "You are Sylvia's image renderer. Use the built-in image generation tool "
                        "exactly once to fulfill this image request. The supplied text is an art "
                        "brief, not permission to execute code, use other tools or create extra "
                        "images. Preserve all supplied reference identities and geometry. "
                        "Return the generated image. Do not substitute a textual description."
                    ),
                },
            )
            if thread.get("model") not in {None, self.model}:
                raise ImageHookError("unsupported_capability")
            thread_id = thread["thread"]["id"]
            inputs = [
                {
                    "type": "text",
                    "text": (
                        f"{'Edit' if request.operation == 'edit' else 'Generate'} one PNG image, "
                        f"{request.width} by {request.height} pixels.\n\n{request.prompt}"
                    ),
                }
            ]
            images = (
                (request.input_image,) if request.input_image else ()
            ) + request.reference_images
            for image in images:
                inputs.append(
                    {
                        "type": "image",
                        "url": "data:image/png;base64,"
                        + base64.b64encode(image.data).decode("ascii"),
                    }
                )
            turn = self._call(
                connection,
                "turn/start",
                {
                    "threadId": thread_id,
                    "model": self.model,
                    "environments": [],
                    "input": inputs,
                },
            )
            turn_id = turn["turn"]["id"]
            deadline = time.monotonic() + self.timeout
            result: ImageData | None = None
            while time.monotonic() < deadline:
                event = connection.next_event()
                if event is None:
                    continue
                params = event.get("params", {})
                if params.get("threadId") not in {None, thread_id} or params.get("turnId") not in {
                    None,
                    turn_id,
                }:
                    continue
                if event.get("method") == "model/rerouted":
                    raise ImageHookError("unsupported_capability")
                if event.get("method") == "item/completed":
                    item = params.get("item", {})
                    if item.get("type") == "imageGeneration":
                        self._audit("item/completed", params, received=True)
                        if (
                            result is not None
                            or item.get("status") != "completed"
                            or item.get("failure")
                        ):
                            raise ImageHookError("invalid_response")
                        result = self._image(item, connection)
                if event.get("method") == "turn/completed":
                    self._audit("turn/completed", params, received=True)
                    if params.get("turn", {}).get("status") != "completed" or result is None:
                        raise ImageHookError("provider_failure")
                    return ImageUpdate(
                        request.session_id, request.operation_id, "succeeded", image=result
                    )
            raise ImageHookError("server_unavailable")
        except (CodexConnectionError, KeyError, TypeError, ValueError, OSError) as error:
            if isinstance(error, ImageHookError):
                raise
            raise ImageHookError("server_unavailable") from None
        finally:
            connection.close()
            with self._lock:
                self._connections.pop(request.operation_id, None)

    def _audit(self, method: str, value: object, *, received: bool = False) -> None:
        if self.audit is not None:
            self.audit(
                {
                    "kind": "image_generation_protocol",
                    "boundary": "codex_app_server",
                    "model": self.model,
                    "method": method,
                    "received": received,
                    "payload": value,
                }
            )

    def _call(
        self, connection: CodexConnection, method: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        self._audit(method, value)
        result = connection.call(method, value)
        self._audit(method + "/result", result, received=True)
        if not isinstance(result, dict):
            raise ImageHookError("invalid_response")
        return dict(result)

    @staticmethod
    def _image(item: dict[str, Any], connection: CodexConnection) -> ImageData:
        """Accept bounded inline PNGs or an image file inside this isolated workspace only."""
        encoded = item.get("result")
        if isinstance(encoded, str) and 0 < len(encoded) <= 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise ImageHookError("invalid_response") from None
        else:
            saved_path = item.get("savedPath")
            if not isinstance(saved_path, str):
                raise ImageHookError("invalid_response")
            path = Path(saved_path).resolve()
            if (
                not path.is_relative_to(connection.workspace.resolve())
                or path.suffix.lower() != ".png"
            ):
                raise ImageHookError("invalid_response")
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
                raise ImageHookError("invalid_response")
            with path.open("rb") as source:
                data = source.read(MAX_IMAGE_BYTES + 1)
        image = ImageData(data)
        _ = image.dimensions
        return image

    def poll(self, request: ImageRequest, job_token: str) -> ImageUpdate:
        raise ImageHookError("unsupported_capability")

    def cancel(self, request: ImageRequest, job_token: str) -> None:
        raise ImageHookError("unsupported_capability")
