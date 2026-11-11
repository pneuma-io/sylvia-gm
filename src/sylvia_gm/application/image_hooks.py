"""Explicit owner-side image operations; no provider connection or automatic dispatch on restore.

The caller owns one session for its lifetime and serializes adoption with ordinary content
commands. Results remain private and provisional until adopted into an existing package draft.
Adapters receive bytes, never paths or URLs. No adapter is installed or selected by this module.
"""

from __future__ import annotations

import hashlib
import re
import struct
import threading
import zlib
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, cast
from uuid import UUID

from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.package_library import (
    canonical_json_bytes,
    parse_content_declarations_bytes,
    parse_content_json_bytes,
    validate_portable_path,
)

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 4 * 1024 * 1024
ASSOCIATIONS_PATH = "assets/image-hooks/associations.json"
_LABEL = re.compile(r"[a-z][a-z0-9_.:-]{0,95}")
_SHA = re.compile(r"[0-9a-f]{64}")
_ERRORS = frozenset(
    {
        "provider_failure",
        "rate_limited",
        "credentials_refused",
        "server_unavailable",
        "invalid_response",
        "unsupported_capability",
        "invalid_request",
        "invalid_image",
        "identity_collision",
        "session_closed",
        "operation_unavailable",
        "result_unavailable",
        "stale_selection",
        "invalid_associations",
        "session_full",
        "missing_character_references",
    }
)
_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "interrupted"})


class ImageHookError(ValueError):
    """Expose only a fixed error code, never provider exception text or request values."""

    def __init__(self, code: str):
        self.code = code if code in _ERRORS else "provider_failure"
        super().__init__(self.code)


def _identity(value: object) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def _label(value: object) -> bool:
    return isinstance(value, str) and _LABEL.fullmatch(value) is not None


@dataclass(frozen=True)
class ImageData:
    """Sealed byte input/output with bounded, validated PNG image data."""

    data: bytes = field(repr=False)
    media_type: str = "image/png"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def dimensions(self) -> tuple[int, int]:
        return _dimensions(self)


def _dimensions(image: ImageData, maximum_bytes: int = MAX_IMAGE_BYTES) -> tuple[int, int]:
    """Validate PNG structure, checksums and bounded decompression without reading metadata."""
    if (
        not isinstance(image, ImageData)
        or image.media_type != "image/png"
        or not isinstance(image.data, bytes)
        or not 0 < len(image.data) <= maximum_bytes
        or not image.data.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        raise ImageHookError("invalid_image")
    data, offset, compressed = image.data, 8, bytearray()
    width = height = depth = color = interlace = 0
    palette = 0
    transparency = False
    background = False
    saw_data = ended_data = ended = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ImageHookError("invalid_image")
        length, kind = struct.unpack_from(">I4s", data, offset)
        end = offset + 12 + length
        if end > len(data):
            raise ImageHookError("invalid_image")
        body = data[offset + 8 : end - 4]
        crc = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(kind + body) != crc:
            raise ImageHookError("invalid_image")
        if offset == 8:
            if kind != b"IHDR" or length != 13:
                raise ImageHookError("invalid_image")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", body
            )
            if (
                not width
                or not height
                or width > 8192
                or height > 8192
                or width * height > MAX_PIXELS
                or depth
                not in {
                    0: (1, 2, 4, 8, 16),
                    2: (8, 16),
                    3: (1, 2, 4, 8),
                    4: (8, 16),
                    6: (8, 16),
                }.get(color, ())
                or compression
                or filtering
                or interlace not in (0, 1)
            ):
                raise ImageHookError("invalid_image")
        elif kind == b"PLTE" and not saw_data and not palette and not background:
            if color in (0, 4) or not length or length % 3 or length > 768:
                raise ImageHookError("invalid_image")
            if color == 3 and length // 3 > 2**depth:
                raise ImageHookError("invalid_image")
            palette = length // 3
        elif kind == b"bKGD" and not saw_data and not background:
            # A display hint may be retained without changing the reference's original pixels.
            if length != {0: 2, 2: 6, 3: 1, 4: 2, 6: 6}[color] or (
                color == 3 and (not palette or body[0] >= palette)
            ):
                raise ImageHookError("invalid_image")
            background = True
        elif kind == b"tRNS" and not saw_data and not transparency:
            if not (
                (color == 0 and length == 2)
                or (color == 2 and length == 6)
                or (color == 3 and 0 < length <= palette)
            ):
                raise ImageHookError("invalid_image")
            transparency = True
        elif kind == b"IDAT" and not ended_data:
            compressed.extend(body)
            saw_data = True
        elif kind == b"IEND" and not length and saw_data and end == len(data):
            ended = True
        elif kind in (b"sRGB", b"gAMA", b"pHYs"):
            if length != {b"sRGB": 1, b"gAMA": 4, b"pHYs": 9}[kind]:
                raise ImageHookError("invalid_image")
            ended_data = saw_data
        elif kind in (b"iCCP", b"cHRM", b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"caBX"):
            # Retain provider color/provenance metadata as opaque bytes, bounded by the PNG limit.
            # It is never interpreted as a path, tool instruction or routine receipt field.
            ended_data = saw_data
        else:
            raise ImageHookError("invalid_image")
        offset = end
    if not ended or (color == 3 and not palette):
        raise ImageHookError("invalid_image")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    passes = (
        ((0, 0, 1, 1),)
        if not interlace
        else (
            (0, 0, 8, 8),
            (4, 0, 8, 8),
            (0, 4, 4, 8),
            (2, 0, 4, 4),
            (0, 2, 2, 4),
            (1, 0, 2, 2),
            (0, 1, 1, 2),
        )
    )
    scanlines = []
    for x, y, dx, dy in passes:
        columns, rows = max(0, (width - x + dx - 1) // dx), max(0, (height - y + dy - 1) // dy)
        if columns and rows:
            scanlines.extend([1 + (columns * channels * depth + 7) // 8] * rows)
    expected = sum(scanlines)
    try:
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(compressed, expected + 1)
        if (
            len(decoded) != expected
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
        ):
            raise ImageHookError("invalid_image")
        offset = 0
        for stride in scanlines:
            if decoded[offset] > 4:
                raise ImageHookError("invalid_image")
            offset += stride
    except zlib.error:
        raise ImageHookError("invalid_image") from None
    return width, height


@dataclass(frozen=True)
class ImageCapabilities:
    """Actual adapter limits; sizes and workflows are explicit supported alternatives."""

    adapter_id: str
    operations: frozenset[str]
    sizes: frozenset[tuple[int, int]]
    workflows: frozenset[str] = frozenset()
    asynchronous: bool = False
    progress: bool = False
    cancellation: bool = False
    maximum_bytes: int = MAX_IMAGE_BYTES
    media_types: frozenset[str] = frozenset({"image/png"})
    input_media_types: frozenset[str] = frozenset({"image/png"})
    synthetic: bool = False
    flexible_output_size: bool = False


@dataclass(frozen=True)
class ImageRequest:
    session_id: str
    operation_id: str
    adapter_id: str
    operation: str
    width: int
    height: int
    prompt: str = field(repr=False)
    input_image: ImageData | None = field(default=None, repr=False)
    workflow: str | None = None
    reference_images: tuple[ImageData, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class ImageUpdate:
    """Adapter reply. Opaque job tokens and output bytes never enter routine projections."""

    session_id: str
    operation_id: str
    status: str
    progress: float | None = None
    error: str | None = None
    image: ImageData | None = field(default=None, repr=False)
    job_token: str | None = field(default=None, repr=False)


class ImageAdapter(Protocol):
    capabilities: ImageCapabilities

    def submit(self, request: ImageRequest) -> ImageUpdate: ...

    def poll(self, request: ImageRequest, job_token: str) -> ImageUpdate: ...

    def cancel(self, request: ImageRequest, job_token: str) -> None: ...


@dataclass(frozen=True)
class ImageSnapshot:
    session_id: str
    operation_id: str
    adapter_id: str
    status: str
    progress: float | None = None
    error: str | None = None
    result_sha256: str | None = None
    late_result_discarded: bool = False


@dataclass(repr=False)
class _Operation:
    request: ImageRequest
    adapter: ImageAdapter
    snapshot: ImageSnapshot
    image: ImageData | None = None
    job_token: str | None = None
    inflight: bool = False
    dispatched: bool = False


class ImageSession:
    """One bounded owner-side lifetime with at-most-once submission per operation identity.

    There is deliberately no restore/resubmit method. An owner restart interrupts pending
    work; durable dispatch/recovery requires the integrator's existing session authority.
    """

    def __init__(self, session_id: str, adapters: Iterable[ImageAdapter]):
        if not _identity(session_id):
            raise ImageHookError("invalid_request")
        self.session_id = session_id
        self._adapters: dict[str, ImageAdapter] = {}
        for adapter in adapters:
            cap = adapter.capabilities
            if (
                not isinstance(cap, ImageCapabilities)
                or not _label(cap.adapter_id)
                or cap.adapter_id in self._adapters
                or not cap.operations
                or not cap.operations <= {"create", "edit"}
                or not cap.sizes
                or any(
                    not isinstance(size, tuple)
                    or len(size) != 2
                    or any(type(n) is not int or n <= 0 for n in size)
                    or size[0] * size[1] > MAX_PIXELS
                    for size in cap.sizes
                )
                or any(not _label(value) for value in cap.workflows)
                or type(cap.maximum_bytes) is not int
                or not 1 <= cap.maximum_bytes <= MAX_IMAGE_BYTES
                or cap.media_types != {"image/png"}
                or not cap.input_media_types <= {"image/png"}
                or ("edit" in cap.operations and not cap.input_media_types)
                or any(
                    type(flag) is not bool
                    for flag in (
                        cap.asynchronous, cap.progress, cap.cancellation, cap.flexible_output_size
                    )
                )
                or (cap.progress and not cap.asynchronous)
            ):
                raise ImageHookError("unsupported_capability")
            self._adapters[cap.adapter_id] = adapter
        self._operations: dict[str, _Operation] = {}
        self._closed = False
        self._lock = threading.RLock()

    def capabilities(self) -> tuple[ImageCapabilities, ...]:
        return tuple(adapter.capabilities for adapter in self._adapters.values())

    def snapshot(self, operation_id: str) -> ImageSnapshot:
        with self._lock:
            return self._operation(operation_id).snapshot

    def result_for_owner(self, operation_id: str) -> ImageData:
        """Read provisional bytes only for the owning content adapter, never routine diagnostics."""
        with self._lock:
            record = self._operation(operation_id)
            if self._closed or record.snapshot.status != "succeeded" or record.image is None:
                raise ImageHookError("result_unavailable")
            return record.image

    def _operation(self, operation_id: str) -> _Operation:
        if not _identity(operation_id) or operation_id not in self._operations:
            raise ImageHookError("operation_unavailable")
        return self._operations[operation_id]

    def prepare(self, request: ImageRequest) -> ImageSnapshot:
        """Register one validated request so owner admission can finish before dispatch."""
        with self._lock:
            if not isinstance(request, ImageRequest) or not _identity(request.operation_id):
                raise ImageHookError("invalid_request")
            if self._closed:
                raise ImageHookError("session_closed")
            previous = self._operations.get(request.operation_id)
            if previous:
                if previous.request != request:
                    raise ImageHookError("identity_collision")
                return previous.snapshot
            if len(self._operations) >= 128:
                raise ImageHookError("session_full")
            if (
                request.session_id != self.session_id
                or not _label(request.adapter_id)
                or not isinstance(request.operation, str)
                or (request.workflow is not None and not _label(request.workflow))
                or not isinstance(request.prompt, str)
                or not request.prompt
                or len(request.prompt) > 65536
                or "\x00" in request.prompt
                or type(request.width) is not int
                or type(request.height) is not int
            ):
                raise ImageHookError("invalid_request")
            adapter = self._adapters.get(request.adapter_id)
            if adapter is None:
                raise ImageHookError("unsupported_capability")
            cap = adapter.capabilities
            if (
                request.operation not in cap.operations
                or (request.width, request.height) not in cap.sizes
                or (
                    request.workflow not in cap.workflows
                    if cap.workflows
                    else request.workflow is not None
                )
                or (request.operation == "edit") != (request.input_image is not None)
            ):
                raise ImageHookError("unsupported_capability")
            if request.input_image is not None:
                _dimensions(request.input_image, cap.maximum_bytes)
                if request.input_image.media_type not in cap.input_media_types:
                    raise ImageHookError("unsupported_capability")
            if not isinstance(request.reference_images, tuple) or len(request.reference_images) > 8:
                raise ImageHookError("invalid_request")
            for image in request.reference_images:
                _dimensions(image, cap.maximum_bytes)
                if image.media_type not in cap.input_media_types:
                    raise ImageHookError("unsupported_capability")
            record = _Operation(
                request,
                adapter,
                ImageSnapshot(self.session_id, request.operation_id, cap.adapter_id, "pending"),
            )
            self._operations[request.operation_id] = record
        return record.snapshot

    def submit(self, request: ImageRequest) -> ImageSnapshot:
        """Dispatch once after capability checks; uncertain delivery never retries itself."""
        self.prepare(request)
        return self.dispatch(request.operation_id)

    def dispatch(self, operation_id: str) -> ImageSnapshot:
        """Submit a prepared request once, honoring cancellation before external work begins."""
        with self._lock:
            record = self._operation(operation_id)
            if record.dispatched or record.snapshot.status in _TERMINAL:
                return record.snapshot
            record.dispatched = record.inflight = True
            request = record.request
        return self._invoke(record, lambda: record.adapter.submit(request))

    def _invoke(self, record: _Operation, call: Callable[[], ImageUpdate]) -> ImageSnapshot:
        try:
            update = call()
        except Exception as error:
            code = error.code if isinstance(error, ImageHookError) else "provider_failure"
            update = ImageUpdate(self.session_id, record.request.operation_id, "failed", error=code)
        with self._lock:
            record.inflight = False
            if record.snapshot.status in _TERMINAL:
                if isinstance(update, ImageUpdate) and update.image is not None:
                    record.snapshot = replace(record.snapshot, late_result_discarded=True)
                return record.snapshot
            try:
                self._accept(record, update)
            except (ImageHookError, TypeError, ValueError, AttributeError):
                record.snapshot = replace(
                    record.snapshot, status="failed", error="invalid_response"
                )
            return record.snapshot

    def _accept(self, record: _Operation, update: ImageUpdate) -> None:
        cap = record.adapter.capabilities
        if (
            not isinstance(update, ImageUpdate)
            or update.session_id != self.session_id
            or update.operation_id != record.request.operation_id
            or update.status not in {"pending", "running", "succeeded", "failed"}
            or (record.snapshot.status == "running" and update.status == "pending")
            or (
                update.progress is not None
                and (
                    not cap.progress
                    or type(update.progress) not in (int, float)
                    or not 0 <= update.progress <= 1
                    or (
                        record.snapshot.progress is not None
                        and update.progress < record.snapshot.progress
                    )
                )
            )
            or (update.error is not None and update.error not in _ERRORS)
            or (update.status == "failed") != (update.error is not None)
            or (update.status == "succeeded") != (update.image is not None)
        ):
            raise ImageHookError("invalid_response")
        if update.status in {"pending", "running"}:
            if (
                not cap.asynchronous
                or not isinstance(update.job_token, str)
                or not 0 < len(update.job_token) <= 4096
                or (record.job_token is not None and update.job_token != record.job_token)
            ):
                raise ImageHookError("invalid_response")
            record.job_token = update.job_token
        if update.image is not None:
            width, height = _dimensions(update.image, cap.maximum_bytes)
            requested_width, requested_height = record.request.width, record.request.height
            # Subscription images can vary in pixel size. Preserve the requested shape
            # within one pixel of rounding and reject unusably small results.
            if (width, height) != (requested_width, requested_height) and not (
                cap.flexible_output_size
                and width * 2 >= requested_width
                and height * 2 >= requested_height
                and abs(width * requested_height - height * requested_width)
                <= max(requested_width, requested_height)
            ):
                raise ImageHookError("invalid_response")
            record.image = update.image
        record.snapshot = replace(
            record.snapshot,
            status=update.status,
            progress=(update.progress if update.progress is not None else record.snapshot.progress),
            error=update.error,
            result_sha256=record.image.sha256 if record.image else None,
        )

    def poll(self, operation_id: str) -> ImageSnapshot:
        """Poll the original job only; overlapping calls cannot duplicate submission."""
        with self._lock:
            record = self._operation(operation_id)
            if record.snapshot.status in _TERMINAL or record.inflight or not record.dispatched:
                return record.snapshot
            token = record.job_token
            if token is None:
                raise ImageHookError("operation_unavailable")
            record.inflight = True
            request = record.request
        return self._invoke(record, lambda: record.adapter.poll(request, token))

    def cancel(self, operation_id: str) -> ImageSnapshot:
        """Always discard locally; contact the adapter only if it advertises cancellation."""
        with self._lock:
            record = self._operation(operation_id)
            if record.snapshot.status in _TERMINAL:
                return record.snapshot
            record.snapshot = replace(record.snapshot, status="cancelled", progress=None)
            token = record.job_token
            request = record.request
        if record.adapter.capabilities.cancellation and token is not None:
            # Local cancellation is final even if the remote job cannot be stopped.
            with suppress(Exception):
                record.adapter.cancel(request, token)
        return self.snapshot(operation_id)

    def close(self) -> None:
        """Fence late responses and erase provisional media; never dispatch on interruption."""
        with self._lock:
            self._closed = True
            for record in self._operations.values():
                if record.snapshot.status not in _TERMINAL:
                    record.snapshot = replace(record.snapshot, status="interrupted", progress=None)
                record.image = None
                record.request = replace(
                    record.request, prompt="", input_image=None, reference_images=()
                )
                record.job_token = None

    def edit_from_result(self, request: ImageRequest, input_operation_id: str) -> ImageSnapshot:
        """Send a successful result to the editing adapter without projecting bytes."""
        with self._lock:
            source = self._operation(input_operation_id)
            if (
                self._closed
                or source.snapshot.status != "succeeded"
                or source.image is None
                or request.operation != "edit"
                or request.input_image is not None
            ):
                raise ImageHookError("result_unavailable")
            request = replace(request, input_image=source.image)
        return self.submit(request)

    def adopt(
        self,
        operation_id: str,
        draft: NormalizedPackageDraft,
        *,
        association_id: str,
        kind: str,
        target_id: str,
        expected_sha256: str | None,
    ) -> NormalizedPackageDraft:
        """Return a new draft after deliberate compare-and-swap selection; do not save or activate.

        Portrait/background/reference/scene rows are reusable associations, not renderer controls.
        Prior bytes stay in the draft. The owner must publish through normal content admission.
        """
        with self._lock:
            record = self._operation(operation_id)
            if self._closed or record.snapshot.status != "succeeded" or record.image is None:
                raise ImageHookError("result_unavailable")
            if (
                not _label(association_id)
                or not _label(target_id)
                or kind not in {"portrait", "background", "reference", "scene"}
                or (
                    expected_sha256 is not None
                    and (
                        not isinstance(expected_sha256, str) or not _SHA.fullmatch(expected_sha256)
                    )
                )
            ):
                raise ImageHookError("invalid_request")
            associations = image_associations(draft)
            digest = record.image.sha256
            row = {
                "kind": kind,
                "target_id": target_id,
                "sha256": digest,
                "session_id": self.session_id,
                "operation_id": operation_id,
                "adapter_id": record.request.adapter_id,
                "operation": record.request.operation,
                "input_sha256": (
                    record.request.input_image.sha256 if record.request.input_image else None
                ),
            }
            previous = associations.get(association_id)
            if previous != row and (previous["sha256"] if previous else None) != expected_sha256:
                raise ImageHookError("stale_selection")
            associations[association_id] = row
            if len(associations) > 64:
                raise ImageHookError("invalid_associations")
            payloads = dict(draft.payloads)
            path = _image_path(digest)
            if any(p.casefold() == path.casefold() and p != path for p in payloads):
                raise ImageHookError("invalid_associations")
            if path in payloads and payloads[path] != record.image.data:
                raise ImageHookError("invalid_associations")
            payloads[path] = record.image.data
            payloads[ASSOCIATIONS_PATH] = canonical_json_bytes(associations)
            _declare_images(draft, payloads)
            return NormalizedPackageDraft(dict(draft.record), payloads, draft.format_version)


def _image_path(digest: str) -> str:
    return f"assets/image-hooks/{digest}.png"


def _declare_images(
    draft: NormalizedPackageDraft,
    payloads: dict[str, bytes],
    *,
    character_assets: frozenset[str] = frozenset(),
) -> None:
    """Extend content declarations with one nonmechanical image inclusion."""
    path = draft.record.get("content_declarations")
    if path is None:
        return
    try:
        path = validate_portable_path(path)
        encoded = payloads[path]
        parse_content_declarations_bytes(encoded)
        declarations = cast(dict[str, Any], thaw_json_value(parse_content_json_bytes(encoded)))
        inclusion: dict[str, Any] = {
            "id": "image-hooks-selected-assets",
            "kind": "image_assets",
            "family": "images",
            "disposition": "included_nonmechanical",
            "classifications": ["authored_content"],
            "sources": [],
            "evidence": {"tests": []},
            "links": {
                key: []
                for key in (
                    "artifacts",
                    "schemas",
                    "state_namespaces",
                    "mechanics",
                    "procedures",
                    "capabilities",
                    "tools",
                    "prompt_fragments",
                    "ui_surfaces",
                )
            },
        }
        for previous in declarations["inclusions"]:
            if previous["id"] == inclusion["id"]:
                expected = {
                    **inclusion,
                    "links": {
                        **inclusion["links"],
                        "artifacts": previous["links"]["artifacts"],
                    },
                }
                if previous != expected:
                    raise ValueError
        inclusion["links"]["artifacts"] = sorted(
            p
            for p in payloads
            if p in character_assets
            or p == ASSOCIATIONS_PATH
            or (
                p.startswith("assets/image-hooks/")
                and p.endswith(".png")
                and _SHA.fullmatch(p[len("assets/image-hooks/") : -4])
            )
        )
        declarations["inclusions"] = sorted(
            [row for row in declarations["inclusions"] if row["id"] != inclusion["id"]]
            + [inclusion],
            key=lambda row: row["id"],
        )
        updated = canonical_json_bytes(declarations)
        parse_content_declarations_bytes(updated)
        payloads[path] = updated
    except (KeyError, ValueError, TypeError):
        raise ImageHookError("invalid_associations") from None


def image_associations(draft: NormalizedPackageDraft) -> dict[str, dict[str, Any]]:
    """Read exact reusable selections from existing package bytes; never resolve a URL or path."""
    try:
        if any(
            p.casefold() == ASSOCIATIONS_PATH.casefold() and p != ASSOCIATIONS_PATH
            for p in draft.payloads
        ):
            raise ValueError
        encoded = draft.payloads.get(ASSOCIATIONS_PATH, b"{}")
        if not isinstance(encoded, bytes) or len(encoded) > 65536:
            raise ValueError
        rows = thaw_json_value(
            parse_content_json_bytes(
                encoded,
                maximum_depth=4,
                maximum_nodes=1024,
                maximum_object_members=64,
            )
        )
        if not isinstance(rows, dict) or len(rows) > 64:
            raise ValueError
        for identity, row in rows.items():
            if (
                not _label(identity)
                or not isinstance(row, dict)
                or set(row)
                != {
                    "kind",
                    "target_id",
                    "sha256",
                    "session_id",
                    "operation_id",
                    "adapter_id",
                    "operation",
                    "input_sha256",
                }
                or row["kind"] not in {"portrait", "background", "reference", "scene"}
                or not _label(row["target_id"])
                or not _label(row["adapter_id"])
                or not _identity(row["session_id"])
                or not _identity(row["operation_id"])
                or row["operation"] not in {"create", "edit"}
                or not isinstance(row["sha256"], str)
                or not _SHA.fullmatch(row["sha256"])
                or (
                    row["input_sha256"] is not None
                    and (
                        not isinstance(row["input_sha256"], str)
                        or not _SHA.fullmatch(row["input_sha256"])
                    )
                )
                or (row["operation"] == "edit") != (row["input_sha256"] is not None)
            ):
                raise ValueError
            image = ImageData(draft.payloads[_image_path(row["sha256"])])
            _dimensions(image)
            if image.sha256 != row["sha256"]:
                raise ValueError
        return cast(dict[str, dict[str, Any]], rows)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ImageHookError("invalid_associations") from None
