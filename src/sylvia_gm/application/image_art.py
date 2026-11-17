"""Owner-controlled image requests, canonical references and shared saved art."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import NAMESPACE_URL, uuid5

from sylvia_gm.application.bundled_art_styles import selected_art_style, style_reference
from sylvia_gm.application.bundled_character_art import bundled_character_image
from sylvia_gm.application.character_art_workflow import CharacterArtWorkflow
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
from sylvia_gm.domain.errors import CommandRejected
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
    from sylvia_gm.application.character_profiles import CharacterProfile
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
        self._builder_targets: dict[str, dict[str, dict[str, Any]]] = {}
        self.characters = CharacterArtWorkflow(self)

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

    def default_style(self, session_id: str) -> dict[str, Any] | None:
        """Expose the selected Setting's rendering default to both GM and Builder."""
        return selected_art_style(self.owner, session_id)

    def restore_visuals(
        self,
        session_id: str,
        target_id: str,
        visuals: object,
        *,
        assets: Mapping[str, bytes] | None = None,
        name: str | None = None,
        asset_reader: Callable[[str], bytes | None] | None = None,
    ) -> None:
        """Rebind immutable character artwork, including packaged bytes on a fresh installation."""
        scope, campaign_id = self._scope(session_id)
        if campaign_id is None and name:
            self._builder_targets.setdefault(session_id, {})[target_id] = {
                "id": target_id,
                "kind": "character",
                "name": name,
            }
        self._restore_visuals(
            scope, campaign_id, session_id, target_id, visuals,
            assets=assets, name=name, asset_reader=asset_reader,
        )

    def retain_campaign_visuals(self, campaign_id: str) -> None:
        """Make used art portable even when no image view or agent session has been opened."""
        for target_id, visuals in self.owner.character_visuals(campaign_id).items():
            # Local copy receipts belong to this campaign, without inventing an agent session.
            self._restore_visuals(campaign_id, campaign_id, campaign_id, target_id, visuals)

    def _restore_visuals(
        self,
        scope: str,
        campaign_id: str | None,
        session_id: str,
        target_id: str,
        visuals: object,
        *,
        assets: Mapping[str, bytes] | None = None,
        name: str | None = None,
        asset_reader: Callable[[str], bytes | None] | None = None,
    ) -> None:
        bundle = validate_character_visuals(visuals)
        if bundle is not None:
            available = dict(assets or {})
            for purpose in ("full_body", "reference_sheet", "portrait"):
                reference = bundle.get(purpose)
                if reference is None or "path" not in reference:
                    continue
                original = self.store.get(reference.get("operation_id", ""))
                if (
                    original is not None
                    and original["status"] == "succeeded"
                    and original["purpose"] == purpose
                    and original["result_sha256"] == reference["sha256"]
                ):
                    continue
                if reference["path"] not in available:
                    image = self.store.retained_character_image(
                        campaign_id=campaign_id, session_id=session_id,
                        target_id=target_id, purpose=purpose, digest=reference["sha256"],
                    )
                    if image is not None:
                        available[reference["path"]] = image
                if reference["path"] not in available and asset_reader is not None:
                    image = asset_reader(reference["path"])
                    if image is not None:
                        available[reference["path"]] = image
                if reference["path"] not in available and campaign_id is not None:
                    package_lock = self.owner._store.current_package_lock(campaign_id).package_lock
                    for entry in package_lock.entries:
                        image = self.owner._store.package_file_bytes(
                            entry.package_id,
                            entry.version,
                            entry.sha256,
                            reference["path"],
                            maximum_bytes=16 * 1024 * 1024,
                        )
                        if (
                            image is not None
                            and hashlib.sha256(image).hexdigest() == reference["sha256"]
                        ):
                            available[reference["path"]] = image
                            break
                if reference["path"] not in available:
                    image = bundled_character_image(reference["path"], reference["sha256"])
                    if image is not None:
                        available[reference["path"]] = image
                if reference["path"] not in available:
                    raise ValueError(
                        "Saved character artwork is missing; restore Sylvia's artwork folder "
                        "or reimport the character's complete asset package"
                    )
            self.store.attach_character_visuals(
                scope_id=scope,
                campaign_id=campaign_id,
                session_id=session_id,
                target_id=target_id,
                visuals=bundle,
                assets=available,
                name=name,
            )

    def restore_profile(
        self,
        session_id: str,
        profile: CharacterProfile,
        storage_root: Path,
    ) -> None:
        """Make a selected reusable profile and its retained card portrait available to Builder."""
        from sylvia_gm.application.character_card_import import card_portrait, load_import_original

        value = cast(dict[str, Any], thaw_json_value(profile.profile))
        self.restore_visuals(
            session_id,
            profile.artifact_id,
            self.store.published_visuals(profile.artifact_id, profile.content_sha256)
            or value.get("visuals"),
            name=value["name"],
        )
        source = value.get("import_source", {})
        if source.get("container") != "png":
            return
        scope, _ = self._scope(session_id)
        if any(
            row["target_id"] == profile.artifact_id and row["purpose"] == "portrait"
            for row in self.store.list_selections(scope)
        ):
            return
        try:
            portrait = card_portrait(load_import_original(storage_root, source["sha256"]))
        except ImageHookError:
            return  # The conversion preview explains unsupported portrait limits.
        biography = value.get("biography", {})
        appearance = biography.get("appearance") or biography.get("description") or value["name"]
        self.restore_card_portrait(
            session_id,
            profile.artifact_id,
            name=value["name"],
            appearance=appearance[:16000],
            image=portrait,
        )
        self.bind_publication(
            session_id,
            profile.artifact_id,
            profile.content_sha256,
            name=value["name"],
            source_target_id=profile.artifact_id,
        )

    def restore_card_portrait(
        self,
        session_id: str,
        target_id: str,
        *,
        name: str,
        appearance: str,
        image: bytes,
    ) -> None:
        """Use a selected local card's portrait even when generation is switched off."""
        scope, campaign_id = self._scope(session_id)
        self.restore_visuals(session_id, target_id, None, name=name)
        self.store.import_portrait(
            scope_id=scope,
            campaign_id=campaign_id,
            session_id=session_id,
            target_id=target_id,
            image=image,
            appearance=appearance,
            name=name,
        )

    def character_assets(self, session_id: str, target_id: str) -> dict[str, bytes]:
        """Collect exact selected image bytes for Character File package publication."""
        scope, _ = self._scope(session_id)
        rows = self.store.list_selections(scope)
        bundle = character_visuals_from_selections(rows, target_id)
        if bundle is None:
            raise ValueError("This character has no selected artwork to publish")
        return {
            reference["path"]: self.store.image(reference["sha256"])
            for purpose in ("full_body", "reference_sheet", "portrait")
            if (reference := bundle.get(purpose)) is not None
        }

    def package_asset(self, session_id: str, reference: Mapping[str, Any]) -> bytes:
        """Publish only images selected or generated in the publishing session's own scope."""
        scope, _ = self._scope(session_id)
        if not any(
            row["sha256"] == reference["sha256"] for row in self.store.list_selections(scope)
        ):
            row = self._owned(session_id, reference.get("operation_id", ""))
            if row["status"] != "succeeded" or row["result_sha256"] != reference["sha256"]:
                raise ValueError("Select this character's artwork before publishing it")
        return self.store.image(reference["sha256"])

    def visuals_for_publication(
        self,
        session_id: str,
        name: str,
        *,
        source_target_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Carry a finished character's artwork into its saved data without model bookkeeping."""
        scope, _ = self._scope(session_id)
        rows = self.store.list_selections(scope)
        if source_target_id is not None:
            return character_visuals_from_selections(rows, source_target_id)
        matches = {row["target_id"] for row in rows if row["metadata"].get("target_name") == name}
        bundles = [
            bundle
            for target in matches
            if (bundle := character_visuals_from_selections(rows, target)) is not None
        ]
        return bundles[0] if len(bundles) == 1 else None

    def ensure_character_art(
        self, session_id: str, *, name: str, appearance: str, target_id: str | None = None,
        source_content_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Make artwork an owned part of saving a character, without retrying an existing flow."""
        if self.settings().get("mode") == "off":
            return {"status": "off"}
        scope, campaign_id = self._scope(session_id)
        if target_id is not None:
            selected = [
                row for row in self.store.list_selections(scope) if row["target_id"] == target_id
            ]
            if not missing_character_art(selected):
                return {"status": "already_available", "target_id": target_id, "name": name}
            if source_content_sha256 is not None:
                progress = self.store.publication_progress(target_id, source_content_sha256)
                if progress is not None and (
                    progress["has_flow"] or not progress["adopted"]
                ):
                    status = progress["status"]
                    if status == "succeeded":
                        status = "partial" if progress["adopted"] else "review"
                    try:
                        self.owner.session_context(progress["source_session_id"])
                    except CommandRejected:
                        return {
                            **progress, "status": "unavailable", "linked": True,
                            "source_unavailable": True, "target_id": target_id,
                            "message": "The original artwork chat is unavailable. Create a new "
                            "artwork request here to continue.",
                        }
                    return {
                        **progress, "status": status, "linked": True, "target_id": target_id,
                        "message": "Continue this artwork in its original Builder chat.",
                    }
        for row in self.store.list_requests(campaign_id=campaign_id, session_id=session_id):
            flow = json.loads(row["request_json"]).get("character_flow")
            if flow and (
                row["target_id"] == target_id if target_id is not None else flow["name"] == name
            ):
                return self._receipt(row)
        try:
            return self.request(
                session_id,
                operation_id="publish:" + hashlib.sha256(
                    canonical_json_bytes({
                        "name": name, "appearance": appearance, "target_id": target_id,
                    })
                ).hexdigest(),
                target_id=target_id,
                purpose="character",
                name=name,
                appearance=appearance[:16000],
                brief=(
                    "Create artwork consistent with this character and their established campaign."
                ),
            )
        except ValueError as error:
            return {"status": "unavailable", "message": str(error)}

    def bind_publication(
        self,
        session_id: str,
        artifact_id: str,
        content_sha256: str,
        *,
        name: str,
        source_target_id: str | None = None,
        source_content_sha256: str | None = None,
    ) -> None:
        """Let a saved character receive already-requested art that finishes after publication."""
        scope, campaign_id = self._scope(session_id)
        rows = self.store.list_requests(campaign_id=campaign_id, session_id=session_id)
        candidates = [
            row
            for row in rows
            if (
                row["target_id"] == source_target_id
                if source_target_id is not None
                else json.loads(row["request_json"]).get("metadata", {}).get("target_name") == name
            )
        ]
        flow = next(
            (
                json.loads(row["request_json"])["character_flow"]
                for row in candidates
                if "character_flow" in json.loads(row["request_json"])
            ),
            None,
        )
        ids = [
            row["operation_id"]
            for row in candidates
            if flow is None
            or json.loads(row["request_json"]).get("character_flow", {}).get("id") == flow["id"]
        ]
        target_ids = {row["target_id"] for row in candidates}
        ids.extend(
            row["operation_id"]
            for row in self.store.list_selections(scope)
            if row["target_id"] in target_ids or row["target_id"] == source_target_id
        )
        for identity in ids:
            self._owned(session_id, identity)
        if source_target_id is not None and source_content_sha256 is not None:
            # Exact library-source links share results, without granting cross-chat actions.
            ids.extend(self.store.publication_operations(source_target_id, source_content_sha256))
        self.store.bind_publication(list(set(ids)), artifact_id, content_sha256)

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
        result = {
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
        flow = json.loads(row["request_json"]).get("character_flow")
        if flow:
            result["character_flow"] = {
                "id": flow["id"],
                "step": flow["steps"].index(row["purpose"]) + 1,
                "total": len(flow["steps"]),
                "name": flow["name"],
            }
        return result

    def targets(self, session_id: str) -> list[dict[str, Any]]:
        _, campaign_id = self._scope(session_id)
        if campaign_id is None:
            builder_targets = dict(self._builder_targets.get(session_id, {}))
            for row in self.store.list_requests(campaign_id=None, session_id=session_id):
                value = json.loads(row["request_json"])
                name = value.get("metadata", {}).get("target_name")
                if name:
                    builder_targets.setdefault(
                        row["target_id"],
                        {
                            "id": row["target_id"],
                            "kind": "character",
                            "name": name,
                            "appearance": value.get("appearance", ""),
                        },
                    )
                    if value.get("character_flow"):
                        builder_targets[row["target_id"]].setdefault(
                            "appearance", value.get("appearance", "")
                        )
            for row in self.store.list_selections(session_id):
                name = row["metadata"].get("target_name")
                if name:
                    builder_targets.setdefault(
                        row["target_id"],
                        {
                            "id": row["target_id"],
                            "kind": "character",
                            "name": name,
                        },
                    )
            return list(builder_targets.values())
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
                self.restore_visuals(session_id, target["id"], visuals, name=target["name"])
            if visuals is not None and visuals.get("status") != "partial" and "portrait" in visuals:
                self._restored_visuals.add(token)
        return list(targets.values())

    def view(self, session_id: str, *, include_targets: bool = True) -> dict[str, Any]:
        scope, campaign_id = self._scope(session_id)
        targets = self.targets(session_id) if include_targets else []
        linked = []
        if campaign_id is not None:
            try:
                party = self.owner._store.campaign_construction_basis(
                    campaign_id
                ).canonical_input.get("selected_party", ())
            except CommandRejected:
                party = ()
            if not isinstance(party, Sequence) or isinstance(party, (str, bytes)):
                party = ()
            prepared = self.owner.character_visuals(campaign_id)
            for member in party:
                if not isinstance(member, Mapping):
                    continue
                character = member.get("character_file", {})
                identity = member.get("participant_id")
                if not isinstance(character, Mapping) or not isinstance(identity, str):
                    continue
                bundle = prepared.get(identity)
                name = member.get("name")
                name = name if isinstance(name, str) else "Character"
                if not include_targets and bundle is not None:
                    self.restore_visuals(session_id, identity, bundle, name=name)
                if (
                    bundle is not None
                    and bundle.get("status") != "partial"
                    and "portrait" in bundle
                ):
                    continue
                source_id = character.get("source_id")
                source_sha = character.get("source_content_sha256") or character.get(
                    "content_sha256"
                )
                if not isinstance(source_id, str) or not isinstance(source_sha, str):
                    continue
                progress = self.store.publication_progress(source_id, source_sha)
                if progress is not None:
                    if progress["status"] == "succeeded":
                        progress["status"] = "review" if not progress["adopted"] else "partial"
                        if (
                            progress["adopted"]
                            and progress["has_flow"]
                            and self.settings().get("mode") != "off"
                        ):
                            try:
                                source = self.owner.session_context(progress["source_session_id"])
                            except CommandRejected:
                                progress.update(status="interrupted", source_unavailable=True)
                            else:
                                if source.session.status.value == "active":
                                    progress["status"] = "advancing"
                    linked.append(
                        {
                            **progress,
                            "target_id": identity,
                            "name": name,
                        }
                    )
            if not include_targets:
                party_ids = {
                    member["participant_id"] for member in party
                    if isinstance(member, Mapping) and isinstance(member.get("participant_id"), str)
                }
                others = set(prepared) - party_ids
                if others:
                    continuity = self.owner._store.campaign_continuity_view(campaign_id)
                    names = {
                        record.record_id: record.payload.get("name")
                        for record in continuity.records
                        if record.kind == "entity"
                    }
                    for identity in others:
                        name = names.get(identity)
                        self.restore_visuals(
                            session_id, identity, prepared[identity],
                            name=name if isinstance(name, str) else None,
                        )
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
            "style_default": self.default_style(session_id),
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
            "pending_character_art": linked,
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

    def request(self, session_id: str, **parameters: Any) -> dict[str, Any]:
        """Select the complete character workflow or an explicit individual image operation."""
        if parameters.get("purpose") == "character":
            return self.characters.request(
                session_id, **{key: value for key, value in parameters.items() if key != "purpose"}
            )
        return self._request_single(session_id, **parameters)

    def _request_single(
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
        name: str = "",
        character_flow: dict[str, Any] | None = None,
        origin_session_id: str | None = None,
    ) -> dict[str, Any]:
        scope, campaign_id = self._scope(session_id)
        origin_session_id = origin_session_id or session_id
        if origin_session_id != session_id and (
            character_flow is None
            or campaign_id is None
            or self.owner.session_context(origin_session_id).session.campaign_id != campaign_id
        ):
            raise ValueError("A character workflow can continue only within its original campaign")
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
            uuid5(NAMESPACE_URL, f"sylvia-image-operation:{origin_session_id}:{operation_id}")
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
            metadata = {"appearance": appearance, "target_name": name}
            if campaign_id is None and name:
                self._builder_targets.setdefault(session_id, {})[target_id] = {
                    "id": target_id,
                    "kind": "character",
                    "name": name,
                }
        value = {
            "brief": brief,
            "appearance": appearance,
            "style": (
                character_flow.get("style") if character_flow is not None
                else self.default_style(session_id)
            ),
            "metadata": metadata,
            "map_projection": projection,
            "edit_sha256": edit_sha256,
            "adapter_id": self.adapters[0].capabilities.adapter_id,
            **({"character_flow": character_flow} if character_flow else {}),
        }
        fingerprint = hashlib.sha256(
            canonical_json_bytes(
                {
                    "session_id": origin_session_id,
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
                "session_id": origin_session_id,
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
        scope, _ = self._scope(session_id)
        selected = [
            item
            for item in self.store.list_selections(scope)
            if item["target_id"] == row["target_id"]
        ]
        if row["purpose"] == "portrait" and not {"full_body", "reference_sheet"} <= {
            item["purpose"] for item in selected
        }:
            raise ImageHookError("missing_character_references")
        if row["purpose"] == "reference_sheet" and "full_body" in missing_character_art(selected):
            raise ImageHookError("missing_character_references")
        if row["purpose"] == "portrait" and "reference_sheet" in missing_character_art(selected):
            raise ImageHookError("missing_character_references")
        references = []
        seen = set()
        canonical_purposes = (
            {"full_body"}
            if row["purpose"] == "reference_sheet"
            else {
                "full_body",
                "reference_sheet",
            }
        )
        needs_body = "full_body" in missing_character_art(selected)
        for item in selected:
            if (
                row["purpose"] != "map_underlay"
                and (
                    item["purpose"] in canonical_purposes
                    or (
                        item["purpose"] == "portrait"
                        and (
                            item["metadata"].get("source") == "character_card" or needs_body
                        )
                    )
                )
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
        style = value.get("style")
        style_prompt = ""
        if style is not None:
            references.append(style_reference(style))
            style_prompt = (
                "\n\nSelected Setting's default rendering style:\n" + style["brief"]
                + "\nThe LAST supplied reference is for rendering style ONLY, not this "
                "subject's identity, costume, species, equipment or map layout. Preserve the "
                "subject's own established appearance and canonical references. For maps, "
                "the map-data reference controls exact geometry; do not force a night scene "
                "or portrait lighting. Any explicit different style in the art brief takes "
                "precedence over this default. Keep portraits readable at small UI sizes."
            )
            metadata["style"] = {
                "id": style["id"], "reference_sha256": style["reference"]["sha256"],
            }
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
            + style_prompt
            + "\n\nArt brief:\n"
            + value["brief"],
            input_image=original,
            reference_images=tuple(references),
        )
        runtime.prepare(request_value)
        metadata["references"] = {
            item["purpose"]: item["sha256"]
            for item in selected
            if item["purpose"] in canonical_purposes
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
        selected = self.store.adopt(
            scope_id=scope,
            operation_id=operation_id,
            expected_sha256=expected_sha256,
            frame=previous["frame"] if previous else PortraitFrame().as_payload(),
            metadata=value["metadata"],
        )
        next_request = self.characters.after_adoption(session_id, row)
        return {**selected, **({"next_request": next_request} if next_request else {})}

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
