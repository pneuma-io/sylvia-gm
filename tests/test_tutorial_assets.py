"""The original artwork and every runtime crop retain verifiable, packaged identities."""

import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def png_shape(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert data[25] == 6  # RGBA, including the original alpha channel.
    return data, [width, height]


def test_complete_sources_and_runtime_crops_match_their_recorded_bytes():
    manifest = json.loads((ROOT / "branding/sprites/provenance.json").read_text("utf-8"))
    inventory = json.loads((ROOT / "src-tauri/webui-data-files.json").read_text("utf-8"))
    assert len(manifest["sheets"]) == 4
    assert {sprite["sheet"] for sprite in manifest["sprites"]} == {
        Path(sheet["file"]).stem for sheet in manifest["sheets"]
    }
    for sheet in manifest["sheets"]:
        data, size = png_shape(ROOT / sheet["file"])
        assert size == [1448, 1086] == sheet["size"]
        assert hashlib.sha256(data).hexdigest() == sheet["sha256"]
    for sprite in manifest["sprites"]:
        data, size = png_shape(ROOT / sprite["file"])
        assert hashlib.sha256(data).hexdigest() == sprite["sha256"]
        assert size == sprite["size"]
        runtime_path = Path(sprite["file"]).relative_to("src-tauri/frontend").as_posix()
        assert runtime_path in inventory["files"]
        left, top, right, bottom = sprite["source_bounds"]
        assert size == [right - left + 12, bottom - top + 12]
