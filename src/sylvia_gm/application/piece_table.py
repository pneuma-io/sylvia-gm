"""Project a Ruleset-declared table without exposing another owner's pieces.

This is presentation data, not a game engine. The caller supplies viewer identities
from the admitted player-control scope, never from a renderer request or table data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from typing import Any, cast

from sylvia_gm.domain.authority import SafePayload, owned_payload


class PieceTableError(ValueError):
    """A table declaration cannot be safely presented."""


def _object(value: object, fields: Set[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) - fields:
        raise PieceTableError("Invalid piece table")
    return value


def _items(value: object, maximum: int) -> Sequence[object]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) > maximum
    ):
        raise PieceTableError("Invalid piece table")
    return value


def _label(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(ord(char) < 32 for char in value)
    ):
        raise PieceTableError("Invalid piece table")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise PieceTableError("Invalid piece table") from error
    if size > 256:
        raise PieceTableError("Invalid piece table")
    return value


def project_piece_table(
    value: object, *, viewer_ids: Set[str] = frozenset(),
    action_ids: Set[str] = frozenset(),
) -> SafePayload:
    """Return only declared public fields and pieces owned by this viewer.

    Hidden groups optionally disclose a count. They never disclose piece identities,
    labels, action references or arbitrary metadata. Actions are references to the
    panel's existing admitted controls; this projection grants no command authority.
    """

    table = _object(value, {"format", "title", "status", "areas"})
    if type(table.get("format")) is not int or table["format"] != 1:
        raise PieceTableError("Invalid piece table")
    result: dict[str, object] = {"format": 1, "title": _label(table.get("title"))}
    status = []
    for raw in _items(table.get("status", ()), 16):
        row = _object(raw, {"label", "value"})
        status.append({"label": _label(row.get("label")), "value": _label(row.get("value"))})
    result["status"] = status
    areas = []
    identities: set[str] = set()
    piece_total = 0
    group_total = 0
    positions: set[str] = set()
    for raw in _items(table.get("areas"), 5):
        area = _object(raw, {"id", "label", "position", "groups"})
        identity = _label(area.get("id"))
        position = area.get("position")
        if (
            not isinstance(position, str)
            or identity in identities or position in positions
            or position not in {"top", "left", "center", "right", "bottom"}
        ):
            raise PieceTableError("Invalid piece table")
        identities.add(identity)
        positions.add(str(area["position"]))
        groups = []
        for group_raw in _items(area.get("groups"), 64):
            group_total += 1
            if group_total > 64:
                raise PieceTableError("Invalid piece table")
            group = _object(group_raw, {
                "id", "label", "visibility", "owner", "show_count", "pieces",
            })
            visibility = group.get("visibility")
            if not isinstance(visibility, str) or visibility not in {"public", "owner"}:
                raise PieceTableError("Invalid piece table")
            owner = _label(group.get("owner")) if visibility == "owner" else None
            show_count = group.get("show_count", False)
            if type(show_count) is not bool:
                raise PieceTableError("Invalid piece table")
            pieces = _items(group.get("pieces"), 256)
            piece_total += len(pieces)
            if piece_total > 1024:
                raise PieceTableError("Invalid piece table")
            safe_group: dict[str, object] = {
                "id": _label(group.get("id")), "label": _label(group.get("label")),
            }
            if visibility == "owner" and owner not in viewer_ids:
                safe_group["concealed"] = True
                if show_count:
                    safe_group["count"] = len(pieces)
            else:
                safe_pieces = []
                piece_ids: set[str] = set()
                for piece_raw in pieces:
                    piece = _object(piece_raw, {"id", "label", "detail", "emphasis", "action_id"})
                    piece_id = _label(piece.get("id"))
                    if piece_id in piece_ids:
                        raise PieceTableError("Invalid piece table")
                    piece_ids.add(piece_id)
                    safe_piece: dict[str, object] = {
                        "id": piece_id, "label": _label(piece.get("label")),
                    }
                    if "detail" in piece:
                        safe_piece["detail"] = _label(piece["detail"])
                    if "emphasis" in piece:
                        if (
                            not isinstance(piece["emphasis"], str)
                            or piece["emphasis"] not in {"normal", "accent", "muted"}
                        ):
                            raise PieceTableError("Invalid piece table")
                        safe_piece["emphasis"] = piece["emphasis"]
                    if "action_id" in piece:
                        action_id = _label(piece["action_id"])
                        if action_id not in action_ids:
                            raise PieceTableError("Invalid piece table")
                        safe_piece["action_id"] = action_id
                    safe_pieces.append(safe_piece)
                safe_group["pieces"] = safe_pieces
            groups.append(safe_group)
        areas.append({
            "id": identity, "label": _label(area.get("label")),
            "position": area["position"], "groups": groups,
        })
    result["areas"] = areas
    return owned_payload(result)


def resolve_piece_action_parameters(
    action: Mapping[str, object], selection: object, projected_table: SafePayload,
) -> SafePayload:
    """Bind an advertised piece identity only from the viewer's current safe table."""

    bindings = action.get("bindings")
    fixed = action.get("params")
    if (
        not isinstance(bindings, Mapping) or len(bindings) != 1
        or tuple(bindings.values()) != ("piece",)
        or not isinstance(fixed, Mapping) or set(bindings) & set(fixed)
        or not isinstance(selection, Mapping) or set(selection) != {"piece"}
    ):
        raise PieceTableError("Invalid piece selection")
    piece_id = _label(selection["piece"])
    permitted = any(
        piece["id"] == piece_id and piece.get("action_id") == action.get("id")
        for area in cast(Sequence[Mapping[str, Any]], projected_table["areas"])
        for group in area["groups"]
        for piece in group.get("pieces", ())
    )
    if not permitted:
        raise PieceTableError("Invalid piece selection")
    return owned_payload({**fixed, next(iter(bindings)): piece_id})
