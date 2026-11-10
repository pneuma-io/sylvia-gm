"""Rasterize the owner-projected map layout for image generation, without tokens or labels."""

from __future__ import annotations

import hashlib
import math
import struct
import zlib
from collections.abc import Mapping, Sequence
from typing import Any

from sylvia_gm.application.image_hooks import ImageData
from sylvia_gm.domain.package_library import canonical_json_bytes


def map_art_reference(
    projection: Mapping[str, Any], *, width: int = 768, height: int = 512
) -> tuple[ImageData, dict[str, Any]]:
    """Use the same bounds and orientation as the playable map; ignore all dynamic actors."""
    if (width, height) not in {(768, 512), (512, 512), (512, 768)}:
        raise ValueError("Unsupported map reference size")
    map_value = projection["map"]
    lower, upper = map_value["bounds"]["min"], map_value["bounds"]["max"]
    coordinates = [*lower[:2], *upper[:2]]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in coordinates):
        raise ValueError("Map bounds must be finite")
    dx, dy = upper[0] - lower[0], upper[1] - lower[1]
    if dx <= 0 or dy <= 0:
        raise ValueError("Map bounds must have positive area")
    pixels = bytearray(bytes((236, 234, 224)) * width * height)
    painted = 0
    geometry = []

    def rectangle(
        minimum: Sequence[float], maximum: Sequence[float], color: tuple[int, int, int]
    ) -> None:
        nonlocal painted
        x0 = max(0, min(width, math.floor((minimum[0] - lower[0]) / dx * width)))
        x1 = max(0, min(width, math.ceil((maximum[0] - lower[0]) / dx * width)))
        y0 = max(0, min(height, math.floor((minimum[1] - lower[1]) / dy * height)))
        y1 = max(0, min(height, math.ceil((maximum[1] - lower[1]) / dy * height)))
        if x1 <= x0 or y1 <= y0:
            return
        painted += (x1 - x0) * (y1 - y0)
        if painted > 64 * 1024 * 1024:
            raise ValueError("Map reference exceeds the bounded raster workload")
        row = bytes(color) * (x1 - x0)
        for y in range(y0, y1):
            start = (y * width + x0) * 3
            pixels[start : start + len(row)] = row

    terrain = []
    grid = map_value["grid"]
    size, origin = grid["cell_size"], grid["origin"]
    for item in projection.get("terrain", ()):
        cell = item["cell"]
        minimum = [origin[axis] + cell[axis] * size for axis in (0, 1)]
        maximum = [coordinate + size for coordinate in minimum]
        rectangle(minimum, maximum, (172, 177, 147))
        terrain.append({key: item[key] for key in ("cell", "layer_id", "cost")})

    # Only player-projected static geometry is supplied by the owning application service.
    for item in projection.get("geometry", ()):
        if not item.get("active"):
            continue
        volume = item["volume"]
        if volume["kind"] != "box":
            raise ValueError("Unsupported map geometry for an underlay")
        display = item.get("display", {})
        color = (157, 117, 66) if display.get("kind") == "door" else (55, 61, 67)
        rectangle(volume["min"], volume["max"], color)
        geometry.append({"volume": volume, "kind": display.get("kind", "structure")})

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    raw = b"".join(b"\0" + pixels[y * width * 3 : (y + 1) * width * 3] for y in range(height))
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    metadata = {
        "map_id": map_value["id"],
        "bounds": map_value["bounds"],
        "layout_sha256": hashlib.sha256(
            canonical_json_bytes(
                {
                    "bounds": map_value["bounds"],
                    "geometry": geometry,
                    "terrain": terrain,
                }
            )
        ).hexdigest(),
        "orientation": "map-positive-y-down",
        "reference_sha256": hashlib.sha256(data).hexdigest(),
    }
    return ImageData(data), metadata
