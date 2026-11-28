"""Character card conversion and the desktop import lifecycle through Python's own code.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_character_card_vectors.py

Two groups, written to `character_card/card_vectors.json`:
- `previews`: `preview_character_card` on cards built as `tests/test_rich_character_profiles.py`
  builds them (`card()`, `png()`), V1/V2/V3 in JSON and PNG, and on edits of them that reach
  every check and mapping: envelopes and versions, required fields and types, names, the
  optional fields, the character book and its entries, assets and their URIs, root fields and
  their collisions, and PNG chunk structure, checksums, dimensions and metadata. Each source is
  kept as base64 (an oversized source is a `repeat`, and the long PNG metadata a `png_text`
  recipe over the shared `png_chunks`, with its SHA-256); each outcome is the profile, digest and
  report, or the exception's class, message and diagnostics.
- `lifecycle`: steps on one fresh storage folder: `stage_card_preview` (a selected file),
  `confirm_card_preview`, `save_character_card_import`, `load_import_original`, file tampering,
  the 64-preview capacity and the retirement of completed previews. Each step records its
  outcome and every file under the storage folder afterwards (path and SHA-256). An `OSError`
  outcome records its class only: its text names a machine path and differs per platform.
It also writes `vela-v2.json` and `vela-v3.png`, the files the `card-import` comparison session
selects. The output is the same on Windows and Linux. Reference tooling only.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import struct
import sys
import tempfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))

from sylvia_gm.application import character_card_import as importer  # noqa: E402
from tests.test_rich_character_profiles import card, png  # noqa: E402

OUT = HERE / "character_card"
OS_ERRORS = {"OSError", "FileNotFoundError", "FileExistsError", "PermissionError", "IsADirectoryError",
             "NotADirectoryError"}


def chunk(kind: bytes, data: bytes, crc: int | None = None) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) if crc is None else crc)


SIGNATURE = b"\x89PNG\r\n\x1a\n"
IHDR = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
IDAT = chunk(b"IDAT", zlib.compress(b"\0\0\0\0\0"))
IEND = chunk(b"IEND", b"")


def text_chunk(keyword: bytes, value: object) -> bytes:
    return chunk(b"tEXt", keyword + b"\0" + base64.b64encode(json.dumps(value).encode()))


def edited(value: dict, *changes: tuple) -> dict:
    """`("set", path, value)` or `("delete", path)` on a copy, a path being a list of keys."""
    value = copy.deepcopy(value)
    for change in changes:
        target = value
        for key in change[1][:-1]:
            target = target[key]
        if change[0] == "set":
            target[change[1][-1]] = change[2]
        else:
            del target[change[1][-1]]
    return value


def raw(value: object) -> bytes:
    return json.dumps(value).encode()


def previews() -> list[tuple[str, bytes]]:
    v1, v2, v3 = card(1), card(2), card(3)
    d = ["data"]
    long_name = "n" * 161
    book_entry = ["data", "character_book", "entries", 0]
    asset = ["data", "assets", 0]
    cases: list[tuple[str, bytes]] = [
        ("v1 json", raw(v1)), ("v1 png", png(v1, "chara")),
        ("v2 json", raw(v2)), ("v2 png", png(v2, "chara")),
        ("v3 json", raw(v3)), ("v3 png", png(v3, "ccv3")),
        ("archive", b"PK\x03\x04evil"), ("version 9", b'{"spec":"chara_card_v9"}'),
        ("duplicate keys", b'{"name":"x","name":"y"}'), ("NaN", b'{"name":NaN}'),
        ("nesting", b"[" * 34 + b"0" + b"]" * 34), ("signature only", SIGNATURE), ("empty object", b"{}"),
        ("empty", b""), ("root list", b"[]"), ("byte order mark", b"\xef\xbb\xbf" + raw(v1)),
        ("not JSON", b"not json"),
        ("v2 version 2.1", raw(edited(v2, ("set", ["spec_version"], "2.1")))),
        ("v2 without spec_version", raw(edited(v2, ("delete", ["spec_version"])))),
        ("v2 data a list", raw(edited(v2, ("set", ["data"], [])))),
        ("v2 without data", raw(edited(v2, ("delete", ["data"])))),
        ("v2 spec a number", raw(edited(v2, ("set", ["spec"], 2)))),
        ("v1 with spec_version", raw(edited(v1, ("set", ["spec_version"], "1.0")))),
        ("v1 without first_mes", raw(edited(v1, ("delete", ["first_mes"])))),
        ("v1 description a number", raw(edited(v1, ("set", ["description"], 1)))),
        ("v2 without system_prompt", raw(edited(v2, ("delete", d + ["system_prompt"])))),
        ("name padded", raw(edited(v1, ("set", ["name"], "  Vela \n")))),
        ("name blank", raw(edited(v1, ("set", ["name"], "   ")))),
        ("name 161", raw(edited(v1, ("set", ["name"], long_name)))),
        ("name 160 wide", raw(edited(v1, ("set", ["name"], "é" * 160)))),
        ("v2 without tags", raw(edited(v2, ("delete", d + ["tags"])))),
        ("v2 greeting a number", raw(edited(v2, ("set", d + ["alternate_greetings"], ["a", 1])))),
        ("v2 without extensions", raw(edited(v2, ("delete", d + ["extensions"])))),
        ("v2 extensions a list", raw(edited(v2, ("set", d + ["extensions"], [])))),
        ("v3 without group greetings", raw(edited(v3, ("delete", d + ["group_only_greetings"])))),
        ("v1 optional fields", raw(edited(v1, ("set", ["nickname"], "Lantern"), ("set", ["source"], ["a", "b"]),
                                          ("set", ["creation_date"], 1700000000), ("set", ["modification_date"], 1.5),
                                          ("set", ["creator_notes_multilingual"], {"fr": "Notes.", "en": "N."}),
                                          ("set", ["extra"], {"x": 1}), ("set", ["another"], 2)))),
        ("nickname a number", raw(edited(v3, ("set", d + ["nickname"], 1)))),
        ("creation date true", raw(edited(v2, ("set", d + ["creation_date"], True)))),
        ("multilingual value a number", raw(edited(v3, ("set", d + ["creator_notes_multilingual"], {"en": 1})))),
        ("source a string", raw(edited(v3, ("set", d + ["source"], "x")))),
        ("v2 root fields", raw(edited(v2, ("set", ["outer"], {"a": 1}), ("set", ["zeta"], True)))),
        ("v2 root collision", raw(edited(v2, ("set", ["outer"], 1), ("set", d + ["outer_extensions"], "x")))),
        ("v2 root double collision", raw(edited(v2, ("set", ["outer"], 1), ("set", d + ["outer_extensions"], "x"),
                                               ("set", d + ["outer_extensions_root"], "y")))),
        ("v1 without book", raw(v1)),
        ("book a list", raw(edited(v2, ("set", d + ["character_book"], [])))),
        ("book without extensions", raw(edited(v2, ("delete", d + ["character_book", "extensions"])))),
        ("book scan depth text", raw(edited(v2, ("set", d + ["character_book", "scan_depth"], "0")))),
        ("book named", raw(edited(v2, ("set", d + ["character_book", "name"], "Lore"),
                                  ("set", d + ["character_book", "description"], "About."),
                                  ("set", d + ["character_book", "token_budget"], 512)))),
        ("book entries not a list", raw(edited(v2, ("set", d + ["character_book", "entries"], {})))),
        ("entry not an object", raw(edited(v2, ("set", d + ["character_book", "entries"], [1])))),
        ("entry without content", raw(edited(v2, ("delete", book_entry + ["content"])))),
        ("entry key a number", raw(edited(v2, ("set", book_entry + ["keys"], [1])))),
        ("entry secondary keys text", raw(edited(v2, ("set", book_entry + ["secondary_keys"], "a")))),
        ("entry secondary keys list", raw(edited(v2, ("set", book_entry + ["secondary_keys"], ["a", 2])))),
        ("entry id text", raw(edited(v2, ("set", book_entry + ["id"], "lore-a"), ("set", book_entry + ["unknown"], [1]),
                                     ("set", book_entry + ["priority"], 3), ("set", book_entry + ["position"], "after")))),
        ("entry id a list", raw(edited(v2, ("set", book_entry + ["id"], [])))),
        ("entry enabled a number", raw(edited(v2, ("set", book_entry + ["enabled"], 1)))),
        ("v3 entry without regex", raw(edited(v3, ("delete", book_entry + ["use_regex"])))),
        ("assets a dict", raw(edited(v3, ("set", d + ["assets"], {})))),
        ("asset not an object", raw(edited(v3, ("set", d + ["assets"], ["x"])))),
        ("asset without ext", raw(edited(v3, ("delete", asset + ["ext"])))),
        ("asset name a number", raw(edited(v3, ("set", asset + ["name"], 1)))),
        ("asset file", raw(edited(v3, ("set", asset + ["uri"], "file:///secret")))),
        ("asset credentials", raw(edited(v3, ("set", asset + ["uri"], "https://user:secret@invalid.example/a")))),
        ("asset empty user", raw(edited(v3, ("set", asset + ["uri"], "https://@invalid.example/a")))),
        ("asset no host", raw(edited(v3, ("set", asset + ["uri"], "https:///a")))),
        ("asset bad IPv6", raw(edited(v3, ("set", asset + ["uri"], "https://[::1/a")))),
        ("asset space", raw(edited(v3, ("set", asset + ["uri"], "https://a.example/a b")))),
        ("asset ideographic space", raw(edited(v3, ("set", asset + ["uri"], "https://a.example/a　b")))),
        ("asset long", raw(edited(v3, ("set", asset + ["uri"], "https://a.example/" + "a" * 4080)))),
        ("asset embedded", raw(edited(v3, ("set", asset + ["uri"], "embeded://assets/./icon.png")))),
        ("asset embedded parent", raw(edited(v3, ("set", asset + ["uri"], "embeded://a/../b")))),
        ("asset embedded absolute", raw(edited(v3, ("set", asset + ["uri"], "embeded:///etc/a")))),
        ("asset embedded backslash", raw(edited(v3, ("set", asset + ["uri"], "embeded://a\\b")))),
        ("asset embedded colon", raw(edited(v3, ("set", asset + ["uri"], "embeded://c:a")))),
        ("asset embedded empty", raw(edited(v3, ("set", asset + ["uri"], "embeded://")))),
        ("asset default", raw(edited(v3, ("set", asset + ["uri"], "ccdefault:")))),
        ("asset inline", raw(edited(v3, ("set", asset + ["uri"], "data:image/png;base64,AAAA")))),
        ("asset ftp", raw(edited(v3, ("set", asset + ["uri"], "ftp://a.example/a")))),
        ("two assets in a png", png(edited(v3, ("set", d + ["assets"], [v3["data"]["assets"][0]] * 2)), "ccv3")),
    ]
    v2_text, v3_text = text_chunk(b"chara", v2), text_chunk(b"ccv3", v3)
    cases += [
        ("png both keywords", SIGNATURE + IHDR + v2_text + v3_text + IDAT + IEND),
        ("png duplicate chara", SIGNATURE + IHDR + v2_text + v2_text + IDAT + IEND),
        ("png ccv3 holding v2", SIGNATURE + IHDR + text_chunk(b"ccv3", v2) + IDAT + IEND),
        ("png chara without separator", SIGNATURE + IHDR + chunk(b"tEXt", b"chara") + IDAT + IEND),
        ("png bad base64", SIGNATURE + IHDR + chunk(b"tEXt", b"chara\0!!!!") + IDAT + IEND),
        ("png non-ascii base64", SIGNATURE + IHDR + chunk(b"tEXt", b"chara\0\xff\xfe") + IDAT + IEND),
        ("png other keyword", SIGNATURE + IHDR + chunk(b"tEXt", b"Comment\0hello") + v2_text + IDAT + IEND),
        ("png no metadata", SIGNATURE + IHDR + IDAT + IEND),
        ("png no image", SIGNATURE + IHDR + v2_text + IEND),
        ("png no end", SIGNATURE + IHDR + v2_text + IDAT),
        ("png trailing data", SIGNATURE + IHDR + v2_text + IDAT + IEND + b"x"),
        ("png end with data", SIGNATURE + IHDR + v2_text + IDAT + chunk(b"IEND", b"x")),
        ("png bad checksum", SIGNATURE + IHDR + v2_text + chunk(b"IDAT", b"x", crc=0) + IEND),
        ("png first chunk not header", SIGNATURE + IDAT + IHDR + v2_text + IEND),
        ("png short header", SIGNATURE + chunk(b"IHDR", b"\0" * 12) + v2_text + IDAT + IEND),
        ("png zero width", SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", 0, 1, 8, 6, 0, 0, 0)) + v2_text + IDAT + IEND),
        ("png too wide", SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", 8193, 1, 8, 6, 0, 0, 0)) + v2_text + IDAT + IEND),
        ("png too many pixels", SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", 8192, 8192, 8, 6, 0, 0, 0)) + v2_text + IDAT + IEND),
        ("png largest image", SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", 4096, 4096, 8, 6, 0, 0, 0)) + v2_text + IDAT + IEND),
        ("png second header", SIGNATURE + IHDR + IHDR + v2_text + IDAT + IEND),
        ("png truncated chunk", SIGNATURE + IHDR + v2_text[:10]),
        ("png chunk past the end", SIGNATURE + IHDR + struct.pack(">I", 100) + b"IDAT" + b"x" * 10),
    ]
    return cases


# One long source is described rather than stored: the shared PNG chunks around one `tEXt`
# chunk of `keyword`, a NUL and `count` copies of `byte`.
LONG_METADATA = {"keyword": "chara", "byte": "A", "count": 350_004}


def long_metadata() -> bytes:
    text = LONG_METADATA["keyword"].encode() + b"\0" + LONG_METADATA["byte"].encode() * LONG_METADATA["count"]
    return SIGNATURE + IHDR + chunk(b"tEXt", text) + IDAT + IEND


def outcome(call) -> dict:
    try:
        return {"ok": call()}
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        name = type(error).__name__
        if name in OS_ERRORS:
            return {"error": name}
        return {"error": name, "message": str(error),
                "diagnostics": [item.as_payload() for item in getattr(error, "diagnostics", ())]}


def preview_payload(preview) -> dict:
    return {"profile": preview.profile, "source_sha256": preview.source_sha256, "report": preview.report()}


def tree(storage: Path) -> list[list[str]]:
    return [[path.relative_to(storage).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()]
            for path in sorted(storage.rglob("*")) if path.is_file()]


def lifecycle_files() -> dict[str, bytes]:
    files = {}
    for index in range(3):
        value = card(2)
        value["data"]["name"] = f"Card {index}"
        files[f"card-{index}.json"] = raw(value)
    files["card-3.png"] = png(card(3), "ccv3")
    files["card.txt"] = files["card-0.json"]
    files["broken.json"] = b"{}"
    for index in range(64):
        value = card(1)
        value["name"] = f"Many {index}"
        files[f"many-{index:02d}.json"] = raw(value)
    return files


def lifecycle_steps(files: dict[str, bytes]) -> list[dict]:
    digest = lambda name: hashlib.sha256(files[name]).hexdigest()  # noqa: E731
    first, second, third = digest("card-0.json"), digest("card-1.json"), digest("card-2.json")
    steps: list[dict] = [
        {"op": "stage", "file": "card-0.json"},
        {"op": "stage", "file": "card-0.json"},
        {"op": "stage", "file": "card.txt"},
        {"op": "stage", "file": "missing.json"},
        {"op": "stage", "file": "folder.json"},
        {"op": "stage", "file": "broken.json"},
        {"op": "confirm", "digest": "not-a-digest"},
        {"op": "confirm", "digest": "0" * 64},
        {"op": "confirm", "digest": first},
        {"op": "confirm", "digest": first},
        {"op": "load_original", "digest": first},
        {"op": "load_original", "digest": second},
        {"op": "stage", "file": "card-1.json"},
        {"op": "tamper", "path": f"character-card-previews/{second}", "data": "e30="},
        {"op": "confirm", "digest": second},
        # A pending preview whose import then completes elsewhere is retired by the next stage.
        {"op": "stage", "file": "card-2.json"},
        {"op": "save", "operation_id": f"desktop-card:{third}", "file": "card-2.json"},
        {"op": "save", "operation_id": f"desktop-card:{third}", "file": "card-2.json"},
        {"op": "stage", "file": "card-1.json"},
        {"op": "save", "operation_id": "builder-png", "file": "card-3.png"},
        {"op": "save", "operation_id": "builder-broken", "file": "broken.json"},
        {"op": "tamper", "path": f"character-card-imports/{first}/source.bin", "data": "e30="},
        {"op": "confirm", "digest": first},
        {"op": "load_original", "digest": first},
        {"op": "stage", "file": "card-2.json"},
        {"op": "confirm", "digest": third},
        {"op": "load_original", "digest": "../x"},
    ]
    steps += [{"op": "stage", "file": f"many-{index:02d}.json"} for index in range(64)]
    return steps


def run_step(step: dict, storage: Path, folder: Path) -> dict:
    op = step["op"]
    if op == "stage":
        return outcome(lambda: importer.stage_card_preview(storage, folder / step["file"]))
    if op == "confirm":
        return outcome(lambda: importer.confirm_card_preview(storage, step["digest"]))
    if op == "save":
        source = (folder / step["file"]).read_bytes()
        return outcome(lambda: importer.save_character_card_import(
            storage, operation_id=step["operation_id"], source=source))
    if op == "load_original":
        return outcome(lambda: hashlib.sha256(importer.load_import_original(storage, step["digest"])).hexdigest())
    if op == "tamper":
        (storage / step["path"]).write_bytes(base64.b64decode(step["data"]))
        return {"ok": None}
    raise AssertionError(op)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    recorded = []
    for name, source in previews():
        recorded.append({"name": name, "source": base64.b64encode(source).decode(),
                         "outcome": outcome(lambda: preview_payload(importer.preview_character_card(source)))})
    long = long_metadata()
    recorded.append({"name": "png long metadata", "png_text": LONG_METADATA, "sha256": hashlib.sha256(long).hexdigest(),
                     "outcome": outcome(lambda: preview_payload(importer.preview_character_card(long)))})
    oversized = b"x" * (importer.MAX_CARD_BYTES + 1)
    recorded.append({"name": "oversized", "repeat": ["x", len(oversized)],
                     "outcome": outcome(lambda: preview_payload(importer.preview_character_card(oversized)))})
    files = lifecycle_files()
    steps = []
    with tempfile.TemporaryDirectory() as scratch:
        storage, folder = Path(scratch) / "storage", Path(scratch) / "files"
        storage.mkdir()
        folder.mkdir()
        (folder / "folder.json").mkdir()
        for name, data in files.items():
            (folder / name).write_bytes(data)
        planned = lifecycle_steps(files)
        for index, step in enumerate(planned):
            result = {**step, "outcome": run_step(step, storage, folder)}
            # The capacity steps differ only by one preview each: their file count, then the
            # whole folder after the last.
            if str(step.get("file", "")).startswith("many-") and index != len(planned) - 1:
                result["tree_size"] = len(tree(storage))
            else:
                result["tree"] = tree(storage)
            steps.append(result)
    record = {
        "python": ".".join(map(str, sys.version_info[:2])),
        "png_chunks": {"signature": base64.b64encode(SIGNATURE).decode(), "ihdr": base64.b64encode(IHDR).decode(),
                       "idat": base64.b64encode(IDAT).decode(), "iend": base64.b64encode(IEND).decode()},
        "previews": recorded,
        "files": {name: base64.b64encode(data).decode() for name, data in files.items()},
        "lifecycle": steps,
    }
    (OUT / "card_vectors.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))
    # The `card-import` comparison session's selected files.
    (OUT / "vela-v2.json").write_bytes(raw(card(2)))
    (OUT / "vela-v3.png").write_bytes(png(card(3), "ccv3"))


if __name__ == "__main__":
    main()
