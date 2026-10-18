"""Admit a Ruleset's declarative starting arrangement for its game panels."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from sylvia_gm.domain.authority import SafePayload, owned_payload, thaw_json_value
from sylvia_gm.domain.content_validation import validate_content_shape
from sylvia_gm.domain.package_library import SurfaceDeclaration, parse_canonical_json_bytes

PANEL_LAYOUT_PATH = "ui/panel-layout.json"
PANEL_LAYOUT_BYTE_LIMIT = 64 * 1024


def parse_panel_layout(value: bytes, surfaces: Sequence[SurfaceDeclaration]) -> SafePayload:
    """Validate closed layout data and exact panel references before package admission."""
    if len(value) > PANEL_LAYOUT_BYTE_LIMIT:
        raise ValueError("Panel starting layout exceeds the byte limit")
    raw = thaw_json_value(
        parse_canonical_json_bytes(
            value,
            maximum_depth=20,
            maximum_nodes=4096,
            maximum_object_members=8,
        )
    )
    validate_content_shape("panel_layout", raw, file=PANEL_LAYOUT_PATH)
    raw = cast(dict[str, object], raw)
    panels = {item.identity: item for item in surfaces if item.renderer is not None}
    used_panels: set[str] = set()
    node_ids: set[str] = set()
    window_ids: set[str] = set()

    def identity(value: object, used: set[str]) -> str:
        identity_value = cast(str, value)
        if identity_value in used:
            raise ValueError("Panel layout identities must be unique")
        used.add(identity_value)
        return identity_value

    def panel(value: object, placement: str | None) -> str:
        if not isinstance(value, str) or value not in panels or value in used_panels:
            raise ValueError("Panel layout references an unknown or repeated panel")
        if placement is not None and placement not in panels[value].allowed_placements:
            raise ValueError("Panel layout uses a placement the panel does not allow")
        used_panels.add(value)
        return value

    def tree(value: object, placement: str, depth: int = 0) -> dict[str, object] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping) or depth > 16 or len(node_ids) >= 256:
            raise ValueError("Panel layout tree is invalid or too deep")
        kind = value.get("kind")
        if kind == "tabs":
            node_id = identity(value["id"], node_ids)
            members = [panel(item, placement) for item in cast(list[object], value["panels"])]
            if value["active"] not in members:
                raise ValueError("Panel tab group must select one of its own panels")
            return {"kind": kind, "id": node_id, "panels": members, "active": value["active"]}
        if kind == "split":
            node_id = identity(value["id"], node_ids)
            ratio = value["ratio"]
            first = tree(value["first"], placement, depth + 1)
            second = tree(value["second"], placement, depth + 1)
            if first is None or second is None:
                raise ValueError("Panel split must contain two groups")
            return {
                "kind": kind,
                "id": node_id,
                "axis": value["axis"],
                "ratio": ratio,
                "first": first,
                "second": second,
            }
        raise ValueError("Panel layout tree kind is unsupported")

    sidebar = tree(raw["sidebar"], "docked")
    windows = []
    for window in cast(list[dict[str, object]], raw["windows"]):
        window_id = identity(window["id"], window_ids)
        title = cast(str, window["title"])
        if len(title.encode("utf-8")) > 256:
            raise ValueError("Panel starting window title is invalid")
        window_tree = tree(window["tree"], "window")
        if window_tree is None:
            raise ValueError("Panel starting window must contain a panel")
        windows.append({"id": window_id, "title": title, "tree": window_tree})
    hidden = [panel(item, None) for item in cast(list[object], raw["hidden"])]
    return owned_payload(
        cast(
            SafePayload,
            {
                "version": 1,
                "sidebar": sidebar,
                "windows": windows,
                "hidden": hidden,
                "known": sorted(panels),
                "panel_sizes": {},
                "sidebar_width": None,
                "views": {},
            },
        )
    )
