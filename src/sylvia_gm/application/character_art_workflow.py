"""Guide character artwork through the existing image requests, permissions and saved selections."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

from sylvia_gm.domain.image_art import missing_character_art
from sylvia_gm.domain.package_library import canonical_json_bytes

if TYPE_CHECKING:
    from sylvia_gm.application.image_art import ImageArt

STEPS = ("full_body", "reference_sheet", "portrait")


class CharacterArtWorkflow:
    """One ordinary action; Review approves each generation, Auto continues without retries."""

    def __init__(self, art: ImageArt) -> None:
        self.art = art

    def request(
        self,
        session_id: str,
        *,
        operation_id: str,
        target_id: str | None = None,
        name: str = "",
        brief: str,
        appearance: str = "",
        edit_sha256: str | None = None,
    ) -> dict[str, Any]:
        scope, campaign_id = self.art._scope(session_id)
        if not isinstance(operation_id, str) or not 0 < len(operation_id) <= 128:
            raise ValueError("Supply a stable artwork request identity")
        if not isinstance(name, str) or len(name) > 160 or "\x00" in name:
            raise ValueError("Use a character name of at most 160 characters")
        root = str(uuid5(NAMESPACE_URL, f"sylvia-character-art:{session_id}:{operation_id}"))
        fingerprint = hashlib.sha256(
            canonical_json_bytes(
                {
                    "target_id": target_id,
                    "name": name,
                    "brief": brief,
                    "appearance": appearance,
                    "edit_sha256": edit_sha256,
                }
            )
        ).hexdigest()
        existing = []
        for purpose in STEPS:
            row = self.art.store.get(self._identity(session_id, root, purpose))
            if row is not None:
                flow = json.loads(row["request_json"])["character_flow"]
                if flow["fingerprint"] != fingerprint:
                    raise ValueError("Artwork request identity conflicts with its saved request")
                existing.append(row)
        if existing:
            return self.art._receipt(existing[-1])
        targets = self.art.targets(session_id)
        target = next((item for item in targets if item["id"] == target_id), None)
        if target_id is None:
            matches = [
                item for item in targets if item["kind"] == "character" and item["name"] == name
            ]
            if len(matches) > 1 or (campaign_id is not None and not matches):
                raise ValueError("Choose the character whose artwork you want to create")
            if matches:
                target = matches[0]
                target_id = target["id"]
        if target_id is None:
            if not name.strip():
                raise ValueError("Enter the character's name")
            target_id = "draft:" + str(uuid5(NAMESPACE_URL, f"{session_id}:{name.strip()}"))
        name = name.strip() or (target["name"] if target else target_id)
        selected = [
            row for row in self.art.store.list_selections(scope) if row["target_id"] == target_id
        ]
        if not appearance and not any(row["metadata"].get("appearance") for row in selected):
            raise ValueError(
                "Describe the character's established appearance before creating artwork"
            )
        missing = missing_character_art(selected)
        if edit_sha256 is not None:
            if not any(
                row["purpose"] == "full_body" and row["sha256"] == edit_sha256 for row in selected
            ):
                raise ValueError(
                    "Choose the current full-body reference to update the character's appearance"
                )
            missing = list(STEPS)
        if not missing:
            return {"target_id": target_id, "status": "already_available", "name": name}
        flow = {
            "id": root,
            "fingerprint": fingerprint,
            "target_id": target_id,
            "name": name,
            "steps": missing,
            "brief": brief,
            "appearance": appearance,
        }
        return self._propose(session_id, flow, missing[0], edit_sha256=edit_sha256)

    @staticmethod
    def _identity(session_id: str, root: str, purpose: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"sylvia-image-operation:{session_id}:{root}:{purpose}"))

    def _propose(
        self,
        session_id: str,
        flow: dict[str, Any],
        purpose: str,
        *,
        edit_sha256: str | None = None,
    ) -> dict[str, Any]:
        return self.art._request_single(
            session_id,
            operation_id=f"{flow['id']}:{purpose}",
            target_id=flow["target_id"],
            purpose=purpose,
            brief=flow["brief"],
            appearance=flow["appearance"],
            name=flow["name"],
            edit_sha256=edit_sha256,
            character_flow=flow,
        )

    def after_adoption(self, session_id: str, row: dict[str, Any]) -> dict[str, Any] | None:
        """Advance only a successfully adopted stage; restart and failure never dispatch work."""
        flow = json.loads(row["request_json"]).get("character_flow")
        if flow is None or self.art.settings().get("mode") == "off":
            return None
        scope, _ = self.art._scope(session_id)
        selected = [
            item
            for item in self.art.store.list_selections(scope)
            if item["target_id"] == row["target_id"]
        ]
        missing = missing_character_art(selected)
        following = flow["steps"][flow["steps"].index(row["purpose"]) + 1 :]
        purpose = next((purpose for purpose in following if purpose in missing), None)
        return self._propose(row["session_id"], flow, purpose) if purpose else None
