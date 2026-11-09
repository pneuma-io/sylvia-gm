"""Reviewed story additions; source preparation never reinitializes live campaign state."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, cast

from sylvia_gm.domain.authority import SafePayload, owned_payload
from sylvia_gm.domain.package_library import canonical_json_bytes

MAX_ADVENTURES = 32


def json_object(value: object) -> Mapping[str, Any]:
    """Narrow an already validated JSON object for preparation and receipt projection."""
    if not isinstance(value, Mapping):
        raise ValueError("Campaign adventure metadata must be an object")
    return cast(Mapping[str, Any], value)


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def prepare_story(source: Mapping[str, object], number: int) -> tuple[SafePayload, dict[str, str]]:
    """Allocate reviewed, deterministic story IDs without renaming world/participant identities."""
    plan = json.loads(canonical_json_bytes(source))
    prefix = f"adventure-{number}-{digest(source)[:8]}:"
    mapping: dict[str, str] = {}
    for field in ("structure", "objectives", "secrets"):
        for item in plan.get(field, ()):
            if isinstance(item, Mapping) and "id" in item and "setting_fact_id" not in item:
                old = str(item["id"])
                if field in {"structure", "objectives"} and old in mapping:
                    raise ValueError(
                        "Prepared story identities overlap; author distinct IDs in Builder"
                    )
                mapping[old] = prefix + old
    ending = plan.get("ending", {})
    for item in ending.get("outcomes", ()):
        old = str(item["id"])
        mapping[old] = prefix + old
    if any(len(item) > 160 for item in mapping.values()):
        raise ValueError("An authored identity is too long to keep separate; shorten it in Builder")
    for field in ("structure", "objectives", "secrets"):
        for item in plan.get(field, ()):
            if not isinstance(item, dict):
                continue
            if "setting_fact_id" not in item and "id" in item:
                item["id"] = mapping[str(item["id"])]
            if field == "structure":
                item["objectives"] = [mapping[str(key)] for key in item["objectives"]]
            if field == "secrets":
                item["reveal"]["from_act"] = mapping[item["reveal"]["from_act"]]
    for item in ending.get("outcomes", ()):
        item["id"] = mapping[str(item["id"])]
    return owned_payload(plan), mapping


def review_payload(value: object) -> SafePayload:
    """Require an explicit narrative connection, including treatment of any narrated future."""
    if not isinstance(value, Mapping) or set(value) != {"connection", "timeline", "reviewed"}:
        raise ValueError("Review how the adventure connects to this campaign before appending")
    note = value["connection"]
    if not isinstance(note, str) or not note.strip() or len(note) > 4000:
        raise ValueError("Describe how this adventure fits the retained campaign facts")
    if value["timeline"] != "after" or value["reviewed"] is not True:
        raise ValueError("Confirm the reviewed timeline and preserved campaign facts")
    return owned_payload({**value, "connection": note.strip()})


def sequel_markdown(content: Mapping[str, object]) -> str:
    """Make the active preparation and the retained-state rule explicit to the GM."""
    metadata = content.get("sequel")
    text = str(content.get("campaign_plan_markdown", ""))
    if not isinstance(metadata, Mapping):
        return text
    review = metadata["request"]["review"]
    return (
        "# Appended authored adventure\n\n"
        "This is new private preparation in the SAME campaign. All played history, characters, "
        "world facts, boundaries, unresolved consequences and mechanical values remain true. "
        "Do not apply this source's initial_state, starting party, starting location, or party "
        "placements as a reset. Establish any transition through play. Earlier narrated futures "
        "remain binding; do not rewind or erase them.\n\n"
        f"Reviewed timeline: {review['timeline']}. Connection: {review['connection']}\n\n"
        "Read campaign_plan path 'adventures' for the complete retained sequence; use "
        "'adventures/N/structured/...' or 'adventures/N/markdown' for earlier preparation. "
        "The active structured Plan uses the reviewed identity mapping below. Use its exact "
        "IDs in scenes, progress and reveals, even when the original prose uses older IDs.\n\n"
        f"Identity mapping: {dict(metadata['identity_mapping'])}\n\n" + text
    )


def sequel_preparation(content: Mapping[str, object]) -> SafePayload | None:
    """Project application-owned continuation instructions into the structured GM context."""
    metadata = content.get("sequel")
    if not isinstance(metadata, Mapping):
        return None
    review = metadata["request"]["review"]
    return owned_payload(
        {
            "number": metadata["preview"]["number"],
            "connection": review["connection"],
            "timeline": review["timeline"],
            "continuity_rule": "Continue the SAME campaign. Keep played events, characters, world "
            "facts, boundaries, unresolved consequences and mechanical values true.",
            "setup_rule": "Do not apply this source's initial_state, starting party, starting "
            "location or placements as a reset. Establish any transition through play.",
            "timeline_rule": "Already narrated futures remain binding. Follow them; do not rewind "
            "or erase them. Read the saved history when their details are needed.",
            "retained_preparation": {
                "source": "campaign_plan",
                "path": "adventures",
                "detail_paths": ["adventures/N/structured/...", "adventures/N/markdown"],
            },
            "identity_rule": "Use the active structured Plan's mapped IDs for scenes, progress "
            "and reveals even if source prose uses older IDs. World identities are unchanged.",
            "identity_mapping": metadata["identity_mapping"],
        }
    )
