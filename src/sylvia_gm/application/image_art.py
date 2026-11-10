"""Owner-controlled image requests, canonical references and shared saved art."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast
from uuid import NAMESPACE_URL, uuid5

from sylvia_gm.application.image_hooks import (
    ImageAdapter,
    ImageData,
    ImageHookError,
    ImageRequest,
    ImageSession,
)
from sylvia_gm.application.map_art_reference import map_art_reference
from sylvia_gm.application.tactical_grid import project_tactical_grid
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.image_art import (
    IMAGE_PURPOSES,
    IMAGE_SIZES,
    PURPOSE_INSTRUCTIONS,
    PortraitFrame,
    character_visuals_from_selections,
    missing_character_art,
    validate_character_visuals,
)
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.infrastructure.sqlite_images import ImageStore

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.session import SessionContext


class ImageArt:
    """Keep network execution outside transactions and all selection changes inside the owner."""

    def __init__(
        self,
        owner: AuthorityOwner,
        *,
        adapters: Sequence[ImageAdapter] = (),
        settings: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.owner = owner
        self.store: ImageStore = owner._store.images
        self.adapters = tuple(adapters)
        self.settings = settings or (lambda: {"mode": "off", "connection": "none"})
        self._sessions: dict[str, ImageSession] = {}
        self._lock = threading.RLock()
        self._recovered = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sylvia-images")
        self._futures: dict[str, Future[dict[str, Any]]] = {}
        self._closed = False
        self._restored_visuals: set[tuple[str, str]] = set()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for session in self._sessions.values():
                session.close()
            for adapter in self.adapters:
                close = getattr(adapter, "close", None)
                if callable(close):
                    with suppress(Exception):
                        close()
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _context(self, session_id: str) -> SessionContext:
        context = self.owner.session_context(session_id)
        if context.session.status.value != "active":
            raise ValueError("Image generation requires an active GM or Builder session")
        with self._lock:
            if not self._recovered:
                self.store.interrupt_pending()
                self._recovered = True
        return context

    def _scope(self, session_id: str) -> tuple[str, str | None]:
        context = self._context(session_id)
        campaign_id = context.session.campaign_id
        return campaign_id or session_id, campaign_id

    def restore_visuals(self, session_id: str, target_id: str, visuals: object) -> None:
        """Rebind locally retained references from a selected immutable character source."""
        scope, campaign_id = self._scope(session_id)
        bundle = validate_character_visuals(visuals)
        if bundle is not None:
            self.store.attach_character_visuals(
                scope_id=scope,
                campaign_id=campaign_id,
                session_id=session_id,
                target_id=target_id,
                visuals=bundle,
            )

    def _runtime(self, session_id: str) -> ImageSession:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = ImageSession(
                    str(uuid5(NAMESPACE_URL, "sylvia-image-art:" + session_id)),
                    self.adapters,
                )
            return self._sessions[session_id]

    @staticmethod
    def _receipt(row: dict[str, Any]) -> dict[str, Any]:
        """Routine projections contain identity and status, never prompts or raw provider output."""
        return {
            key: row.get(key)
            for key in (
                "operation_id",
                "target_id",
                "purpose",
                "status",
                "error",
                "result_sha256",
            )
        }

    def targets(self, session_id: str) -> list[dict[str, Any]]:
        _, campaign_id = self._scope(session_id)
        if campaign_id is None:
            return []
        panels = cast(dict[str, Any], thaw_json_value(self.owner.campaign_panels(campaign_id)))
        targets: dict[str, dict[str, Any]] = {}
        for surface in panels.get("surfaces", []):
            data = surface.get("data", {})
            if surface.get("renderer") in {"coordinate_map", "tactical_grid"}:
                map_value = data.get("map", {})
                if map_value.get("id"):
                    targets["map:" + map_value["id"]] = {
                        "id": map_value["id"],
                        "kind": "map",
                        "name": surface.get("title", map_value["id"]),
                        "projection": data,
                        "layout_sha256": map_art_reference(data)[1]["layout_sha256"],
                    }
                for entity in data.get("entities", []):
                    actor_id = entity.get("actor_ref") or (
                        entity.get("display", {}).get("participant_ref")
                        or entity.get("participant_ref")
                        or entity["id"]
                    )
                    targets.setdefault(
                        "character:" + actor_id,
                        {
                            "id": actor_id,
                            "kind": "character",
                            "name": entity.get("display", {}).get("label", actor_id),
                        },
                    )
            for participant in data.get("participants", []):
                target = {
                    "id": participant["id"],
                    "kind": "character",
                    "name": participant.get("data", {}).get("name", participant["id"]),
                }
                targets["character:" + target["id"]] = target
            entities = data.get("entities", {})
            if isinstance(entities, dict):
                for actor_id, entity in entities.items():
                    if isinstance(entity, dict) and entity.get("name"):
                        targets.setdefault(
                            "character:" + actor_id,
                            {
                                "id": actor_id,
                                "kind": "character",
                                "name": entity["name"],
                            },
                        )
        snapshot = self.owner.snapshot(campaign_id)
        for participant in snapshot.participants:
            name = participant.data.get("name")
            if participant.data.get("visibility") != "gm_private" and isinstance(name, str):
                targets.setdefault(
                    "character:" + participant.participant_id,
                    {
                        "id": participant.participant_id,
                        "kind": "character",
                        "name": name,
                    },
                )
        latest = {}
        for record in sorted(snapshot.continuity.records, key=lambda item: item.admitted_revision):
            if record.kind == "entity":
                latest[record.record_id] = record
        for identity, record in latest.items():
            targets.pop("character:" + identity, None)
            name = record.payload.get("name")
            if (
                record.status == "active"
                and record.payload.get("visibility") == "player_known"
                and isinstance(name, str)
                and name.strip()
            ):
                targets["character:" + identity] = {
                    "id": identity,
                    "kind": "character",
                    "name": name,
                }
        prepared = self.owner.character_visuals(campaign_id)
        for target in targets.values():
            token = (campaign_id, target["id"])
            if target["kind"] != "character" or token in self._restored_visuals:
                continue
            visuals = prepared.get(target["id"])
            if visuals is not None:
                self.store.attach_character_visuals(
                    scope_id=campaign_id,
                    campaign_id=campaign_id,
                    session_id=session_id,
                    target_id=target["id"],
                    visuals=visuals,
                )
            self._restored_visuals.add(token)
        return list(targets.values())

    def view(self, session_id: str, *, include_targets: bool = True) -> dict[str, Any]:
        scope, campaign_id = self._scope(session_id)
        targets = self.targets(session_id) if include_targets else []
        rows = self.store.list_selections(scope)
        needs = []
        for target in targets:
            selected = {row["purpose"]: row for row in rows if row["target_id"] == target["id"]}
            if target["kind"] == "map":
                image = selected.get("map_underlay")
                missing = (
                    ["map_underlay"]
                    if image is None
                    or image["metadata"].get("layout_sha256") != target["layout_sha256"]
                    else []
                )
            else:
                missing = missing_character_art(list(selected.values()))
            if missing:
                needs.append({"target_id": target["id"], "purposes": missing})
        return {
            "settings": self.settings(),
            "capabilities": [
                {
                    "adapter_id": adapter.capabilities.adapter_id,
                    "operations": sorted(adapter.capabilities.operations),
                    "sizes": [list(size) for size in sorted(adapter.capabilities.sizes)],
                    "synthetic": adapter.capabilities.synthetic,
                }
                for adapter in self.adapters
            ],
            "targets": [
                {k: v for k, v in target.items() if k != "projection"} for target in targets
            ],
            "selections": rows,
            "character_visuals": {
                target: value
                for target in {row["target_id"] for row in rows}
                if (value := character_visuals_from_selections(rows, target)) is not None
            },
            "requests": [
                self._receipt(row)
                for row in self.store.list_requests(
                    campaign_id=campaign_id,
                    session_id=session_id,
                )
            ],
            "purposes": sorted(IMAGE_PURPOSES),
            "needed_art": needs,
        }

    def _owned(self, session_id: str, operation_id: str) -> dict[str, Any]:
        _, campaign_id = self._scope(session_id)
        row = self.store.get(operation_id)
        if row is None or (
            row["session_id"] != session_id
            and (campaign_id is None or row["campaign_id"] != campaign_id)
        ):
            raise ValueError("Image operation is unavailable in this session")
        return row

    def request(
        self,
        session_id: str,
        *,
        operation_id: str,
        target_id: str,
        purpose: str,
        brief: str,
        appearance: str = "",
        map_scene: Mapping[str, Any] | None = None,
        edit_sha256: str | None = None,
    ) -> dict[str, Any]:
        scope, campaign_id = self._scope(session_id)
        settings = self.settings()
        if settings.get("mode") == "off" or not self.adapters:
            raise ValueError("Enable an image connection in Settings before requesting art")
        if (
            purpose not in IMAGE_PURPOSES
            or not isinstance(brief, str)
            or not 0 < len(brief) <= 12000
        ):
            raise ValueError(
                "Supply one supported image purpose and a brief of at most 12000 characters"
            )
        if not isinstance(target_id, str) or not 0 < len(target_id) <= 128 or "\x00" in target_id:
            raise ValueError("Image target identity is invalid")
        if not isinstance(appearance, str) or len(appearance) > 16000:
            raise ValueError("Character appearance is too long")
        requested_appearance = appearance
        if not isinstance(operation_id, str) or not 0 < len(operation_id) <= 128:
            raise ValueError("Supply a stable operation identity")
        identity = str(
            uuid5(NAMESPACE_URL, "sylvia-image-operation:" + session_id + ":" + operation_id)
        )
        metadata: dict[str, Any] = {"appearance": appearance}
        projection = None
        if campaign_id is not None:
            target = next(
                (
                    target
                    for target in self.targets(session_id)
                    if target["id"] == target_id
                    and target["kind"] == ("map" if purpose == "map_underlay" else "character")
                ),
                None,
            )
            if target is None:
                raise ValueError("Choose an existing player-known character or map")
            projection = target.get("projection")
        elif purpose == "map_underlay":
            if map_scene is None:
                raise ValueError("A Builder map underlay requires the authored spatial scene")
            projection = cast(
                dict[str, Any], thaw_json_value(project_tactical_grid({"scene": dict(map_scene)}))
            )
            if projection["map"]["id"] != target_id:
                raise ValueError("The reference map does not match the requested map")
        if purpose == "map_underlay":
            if projection is None:
                raise ValueError("The selected map has no supported spatial projection")
            _, metadata = map_art_reference(projection)
            projection = {key: projection[key] for key in ("map", "geometry", "terrain")}
        else:
            prior = next(
                (
                    row
                    for row in self.store.list_selections(scope)
                    if row["target_id"] == target_id and row["metadata"].get("appearance")
                ),
                None,
            )
            appearance = appearance or (prior["metadata"]["appearance"] if prior else brief)
            metadata = {"appearance": appearance}
        value = {
            "brief": brief,
            "appearance": appearance,
            "metadata": metadata,
            "map_projection": projection,
            "edit_sha256": edit_sha256,
            "adapter_id": self.adapters[0].capabilities.adapter_id,
        }
        fingerprint = hashlib.sha256(
            canonical_json_bytes(
                {
                    "session_id": session_id,
                    "target_id": target_id,
                    "purpose": purpose,
                    "brief": brief,
                    "appearance": requested_appearance,
                    "edit_sha256": edit_sha256,
                    "map_scene": dict(map_scene) if map_scene is not None else None,
                }
            )
        ).hexdigest()
        # Every request is first proposed; the sole dispatch claim advances it to pending.
        row = self.store.admit(
            {
                "operation_id": identity,
                "session_id": session_id,
                "campaign_id": campaign_id,
                "target_id": target_id,
                "purpose": purpose,
                "request_sha256": fingerprint,
                "request_json": canonical_json_bytes(value).decode(),
                "status": "proposed",
            }
        )
        if settings.get("mode") == "auto" and row["status"] == "proposed":
            return self.generate(session_id, identity, automatic=True)
        return self._receipt(row)

    def generate(
        self, session_id: str, operation_id: str, *, automatic: bool = False
    ) -> dict[str, Any]:
        row = self._owned(session_id, operation_id)
        settings = self.settings()
        if settings.get("mode") == "off" or automatic and settings.get("mode") != "auto":
            raise ValueError("Automatic image generation is disabled")
        if row["status"] != "proposed":
            return self._receipt(row)
        if not self.store.approve(operation_id):
            return self._receipt(self._owned(session_id, operation_id))
        with self._lock:
            if self._closed:
                return self._receipt(self.store.finish(operation_id, status="interrupted"))
            self._futures[operation_id] = self._executor.submit(
                self._execute_safely,
                session_id,
                operation_id,
                automatic,
            )
        return self._receipt(self._owned(session_id, operation_id))

    def _execute_safely(
        self, session_id: str, operation_id: str, automatic: bool
    ) -> dict[str, Any]:
        try:
            return self._execute(session_id, operation_id, automatic=automatic)
        except Exception as error:
            code = error.code if isinstance(error, ImageHookError) else "provider_failure"
            with self._lock:
                if not self._closed:
                    return self._receipt(
                        self.store.finish(operation_id, status="failed", error=code)
                    )
            return {"status": "interrupted", "operation_id": operation_id}

        finally:
            # The sole worker has finished; durable records now own replay and asset retention.
            with self._lock:
                for runtime in self._sessions.values():
                    runtime.close()
                self._sessions.clear()

    def _execute(self, session_id: str, operation_id: str, *, automatic: bool) -> dict[str, Any]:
        row = self._owned(session_id, operation_id)
        if row["status"] != "pending":
            return self._receipt(row)
        if self.settings().get("mode") == "off":
            return self.cancel(session_id, operation_id)
        # Dispatch uses the owning session even when another session in the campaign approves it.
        runtime = self._runtime(row["session_id"])
        value = json.loads(row["request_json"])
        scope, _ = self._scope(row["session_id"])
        selected = [
            item
            for item in self.store.list_selections(scope)
            if item["target_id"] == row["target_id"]
        ]
        if row["purpose"] == "portrait" and not {"full_body", "reference_sheet"} <= {
            item["purpose"] for item in selected
        }:
            raise ImageHookError("missing_character_references")
        if row["purpose"] == "reference_sheet" and not any(
            item["purpose"] == "full_body" for item in selected
        ):
            raise ImageHookError("missing_character_references")
        if row["purpose"] == "portrait" and "reference_sheet" in missing_character_art(selected):
            raise ImageHookError("missing_character_references")
        references = []
        seen = set()
        for item in selected:
            if (
                row["purpose"] != "map_underlay"
                and item["purpose"] in {"full_body", "reference_sheet"}
                and item["sha256"] not in seen
            ):
                references.append(ImageData(self.store.image(item["sha256"])))
                seen.add(item["sha256"])
        metadata = value["metadata"]
        if row["purpose"] == "map_underlay":
            if row["campaign_id"] is not None:
                target = next(
                    (
                        target
                        for target in self.targets(session_id)
                        if target["kind"] == "map" and target["id"] == row["target_id"]
                    ),
                    None,
                )
                if target is None or target["layout_sha256"] != value["metadata"]["layout_sha256"]:
                    raise ImageHookError("stale_selection")
            reference, metadata = map_art_reference(value["map_projection"])
            references.append(reference)
        original = None
        if value.get("edit_sha256"):
            if not any(item["sha256"] == value["edit_sha256"] for item in selected):
                raise ValueError("The image selected for editing is no longer current")
            original = ImageData(self.store.image(value["edit_sha256"]))
        width, height = IMAGE_SIZES[row["purpose"]]
        request_value = ImageRequest(
            runtime.session_id,
            operation_id,
            value["adapter_id"],
            "edit" if original else "create",
            width,
            height,
            PURPOSE_INSTRUCTIONS[row["purpose"]]
            + "\n\nEstablished appearance:\n"
            + value["appearance"]
            + "\n\nArt brief:\n"
            + value["brief"],
            input_image=original,
            reference_images=tuple(references),
        )
        runtime.prepare(request_value)
        metadata["references"] = {
            item["purpose"]: item["sha256"]
            for item in selected
            if item["purpose"] in {"full_body", "reference_sheet"}
        }
        value["metadata"] = metadata
        value["reference_sha256s"] = [image.sha256 for image in references]
        if not self.store.bind_inputs(operation_id, value):
            runtime.cancel(operation_id)
            return self._receipt(self._owned(session_id, operation_id))
        if self.owner.admin_full_audit_enabled():
            self.owner.record_admin_full_audit(
                {
                    "kind": "image_generation_application_request",
                    "operation_id": operation_id,
                    "adapter_id": request_value.adapter_id,
                    "purpose": row["purpose"],
                    "width": width,
                    "height": height,
                    "prompt": request_value.prompt,
                    "reference_sha256s": value["reference_sha256s"],
                    "input_sha256": original.sha256 if original else None,
                }
            )
        snapshot = runtime.dispatch(operation_id)
        image = (
            runtime.result_for_owner(operation_id).data if snapshot.status == "succeeded" else None
        )
        if self._closed:
            return {"status": "interrupted", "operation_id": operation_id}
        result = self.store.finish(
            operation_id, status=snapshot.status, image=image, error=snapshot.error
        )
        self.owner.record_admin_full_audit(
            {
                "kind": "image_generation_application_result",
                **self._receipt(result),
            }
        )
        if automatic and result["status"] == "succeeded" and self.settings().get("mode") == "auto":
            previous = next((item for item in selected if item["purpose"] == row["purpose"]), None)
            self.adopt(
                session_id, operation_id, expected_sha256=previous["sha256"] if previous else None
            )
        return self._receipt(result)

    def adopt(
        self, session_id: str, operation_id: str, *, expected_sha256: str | None
    ) -> dict[str, Any]:
        row = self._owned(session_id, operation_id)
        scope, _ = self._scope(session_id)
        value = json.loads(row["request_json"])
        previous = next(
            (
                item
                for item in self.store.list_selections(scope)
                if item["target_id"] == row["target_id"] and item["purpose"] == row["purpose"]
            ),
            None,
        )
        return self.store.adopt(
            scope_id=scope,
            operation_id=operation_id,
            expected_sha256=expected_sha256,
            frame=previous["frame"] if previous else PortraitFrame().as_payload(),
            metadata=value["metadata"],
        )

    def cancel(self, session_id: str, operation_id: str) -> dict[str, Any]:
        row = self._owned(session_id, operation_id)
        # Persist cancellation before stopping transport so a racing result cannot be adopted.
        cancelled = self.store.cancel(operation_id)
        assert cancelled is not None
        runtime = self._sessions.get(row["session_id"])
        if runtime is not None:
            with suppress(ImageHookError):
                runtime.cancel(operation_id)
        for adapter in self.adapters:
            stop = getattr(adapter, "stop", None)
            if callable(stop):
                stop(operation_id)
        return self._receipt(cancelled)

    def frame(self, session_id: str, target_id: str, digest: str, value: Mapping[str, Any]) -> None:
        scope, _ = self._scope(session_id)
        self.store.frame(scope, target_id, digest, PortraitFrame(**value).as_payload())

    def asset(self, session_id: str, digest: str) -> dict[str, Any]:
        scope, campaign_id = self._scope(session_id)
        available = any(item["sha256"] == digest for item in self.store.list_selections(scope))
        available = available or any(
            row["result_sha256"] == digest
            for row in self.store.list_requests(
                campaign_id=campaign_id,
                session_id=session_id,
            )
        )
        if not available:
            raise ValueError("This image is unavailable in the selected session")
        image = ImageData(self.store.image(digest))
        _ = image.dimensions
        return {
            "sha256": digest,
            "media_type": "image/png",
            "data_url": "data:image/png;base64," + base64.b64encode(image.data).decode(),
        }
