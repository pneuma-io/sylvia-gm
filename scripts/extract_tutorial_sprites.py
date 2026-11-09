"""Extract existing Sylvia artwork without redrawing, resizing or equal-cell clipping.

Run with Pillow installed: python scripts/extract_tutorial_sprites.py
Full approved originals remain in branding/sprites. The authored regions admit detached
effects; connected-component masks exclude neighbouring characters inside those regions.
"""

from __future__ import annotations

import hashlib
import json
from array import array
from collections import deque
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SHEETS = {
    "tutorial-poses": (
        "ChatGPT Image Sep 10, 2026, 08_50_47 PM.png",
        [
            ("speaking", (150, 180), (0, 0, 310, 368)),
            ("point-left-full", (150, 550), (0, 360, 310, 718)),
            ("point-right-full", (470, 550), (300, 360, 624, 718)),
            ("welcome", (160, 900), (0, 705, 330, 1086)),
            ("thumbs-up", (470, 900), (320, 708, 635, 1086)),
            ("listening", (1050, 900), (925, 710, 1199, 1086)),
        ],
    ),
    "general-emotes": (
        "ChatGPT Image Sep 10, 2026, 08_58_57 PM.png",
        [
            ("reading", (450, 180), (330, 15, 625, 385)),
            ("thinking", (750, 550), (602, 386, 890, 750)),
            ("celebrating", (1010, 920), (840, 735, 1150, 1086)),
        ],
    ),
    "warm-expressions": (
        "ChatGPT Image Sep 10, 2026, 08_24_01 PM.png",
        [
            ("reassuring", (750, 540), (620, 366, 887, 723)),
            ("sparkle", (1020, 200), (875, 10, 1168, 378)),
            ("warm-smile", (1040, 900), (890, 710, 1160, 1086)),
        ],
    ),
    "playful-expressions": (
        "ChatGPT Image Sep 10, 2026, 08_20_18 PM.png",
        [
            ("presenting", (760, 550), (605, 370, 918, 738)),
            ("shush", (170, 190), (0, 0, 342, 375)),
            ("confident", (150, 900), (0, 730, 304, 1086)),
        ],
    ),
}
EFFECTS = {
    "welcome": [(45, 720, 125, 812)],
    "thumbs-up": [(330, 815, 385, 883)],
    "listening": [(935, 716, 1000, 783)],
    "thinking": [(846, 440, 890, 505)],
    "celebrating": [(840, 740, 935, 855), (1100, 750, 1148, 825)],
    "sparkle": [(875, 120, 929, 195)],
    "warm-smile": [(920, 716, 985, 780)],
    "shush": [(8, 95, 68, 163)],
}


def component_labels(alpha: bytes, width: int, height: int):
    """Separate opaque shapes first, then retain their original faint alpha fringes."""
    labels = array("I", [0]) * len(alpha)
    components = {}
    for start, opacity in enumerate(alpha):
        if opacity < 16 or labels[start]:
            continue
        identity = len(components) + 1
        pending = [start]
        labels[start] = identity
        count, left, top, right, bottom = 0, width, height, 0, 0
        while pending:
            position = pending.pop()
            y, x = divmod(position, width)
            count += 1
            left, top = min(left, x), min(top, y)
            right, bottom = max(right, x + 1), max(bottom, y + 1)
            for neighbour in neighbours(position, width, height):
                if not labels[neighbour] and alpha[neighbour] >= 16:
                    labels[neighbour] = identity
                    pending.append(neighbour)
        components[identity] = (count, (left, top, right, bottom))
    fringe = deque(
        position
        for position, identity in enumerate(labels)
        if identity and any(
            alpha[n] and not labels[n] for n in neighbours(position, width, height)
        )
    )
    while fringe:
        position = fringe.popleft()
        for neighbour in neighbours(position, width, height):
            if alpha[neighbour] and not labels[neighbour]:
                labels[neighbour] = labels[position]
                fringe.append(neighbour)
    return labels, components


def neighbours(position: int, width: int, height: int):
    y, x = divmod(position, width)
    if x:
        yield position - 1
    if x + 1 < width:
        yield position + 1
    if y:
        yield position - width
    if y + 1 < height:
        yield position + width


def extract():
    output = ROOT / "src-tauri/frontend/assets/tutorial"
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"format": 1, "method": "Original RGBA pixels, component masks, six-pixel padding",
                "sheets": [], "sprites": []}
    for name, (original, poses) in SHEETS.items():
        source = ROOT / "branding/sprites" / (name + ".png")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        image = Image.open(source)
        if image.mode != "RGBA" or image.size != (1448, 1086):
            raise ValueError("Unexpected full source sheet: " + name)
        width, height = image.size
        alpha = image.getchannel("A").tobytes()
        if min(alpha) != 0 or max(alpha) != 255:
            raise ValueError("Expected genuine full-range transparency: " + name)
        labels, components = component_labels(alpha, width, height)
        manifest["sheets"].append({"file": source.relative_to(ROOT).as_posix(),
                                  "original_filename": original, "sha256": source_hash,
                                  "size": list(image.size), "mode": image.mode})
        for pose, seed, region in poses:
            identity = labels[seed[1] * width + seed[0]]
            count, box = components[identity]
            if count < 15000 or box[2] - box[0] > 400 or box[3] - box[1] > 390:
                raise ValueError("Pose is missing or connected to a neighbour: " + pose)
            left, top, right, bottom = region
            if not (left <= box[0] < box[2] <= right and top <= box[1] < box[3] <= bottom):
                raise ValueError("Main silhouette escaped its authored region: " + pose)
            admitted = {identity}
            for key, (area, bounds) in components.items():
                cx, cy = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
                if area < 5000 and any(
                    x0 <= cx < x1 and y0 <= cy < y1
                    for x0, y0, x1, y1 in EFFECTS.get(pose, [])
                ):
                    admitted.add(key)
            retained_alpha = bytes(
                opacity if labels[i] in admitted else 0
                for i, opacity in enumerate(alpha)
            )
            isolated = image.copy()
            isolated.putalpha(Image.frombytes("L", image.size, retained_alpha))
            bounds = isolated.getchannel("A").getbbox()
            cropped = ImageOps.expand(isolated.crop(bounds), border=6, fill=(0, 0, 0, 0))
            destination = output / ("sylvia-" + pose + ".png")
            cropped.save(destination, optimize=True)
            manifest["sprites"].append({
                "file": destination.relative_to(ROOT).as_posix(), "sheet": name,
                "source_sha256": source_hash, "source_bounds": list(bounds),
                "body_region": list(region),
                "effect_regions": EFFECTS.get(pose, []), "size": list(cropped.size),
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            })
            print("Extracted " + pose, cropped.size, flush=True)
    (ROOT / "branding/sprites/provenance.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    extract()
