"""Bounded CC v1/v2/v3 JSON/PNG conversion; no prompt execution or asset fetching."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import struct
import tempfile
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from sylvia_gm.application.character_profiles import (
    _parse,
    load_character_profile,
    save_character_profile,
    validate_character_profile,
)
from sylvia_gm.application.reusable_library_deletion import is_link_or_reparse, validate_owned_files
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes

MAX_CARD_BYTES = 16 * 1024 * 1024
MAX_PENDING_PREVIEWS = 64
_PREVIEW_LOCK = threading.RLock()
SPEC_URLS = (
    "https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v1.md",
    "https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md",
    "https://github.com/kwaroran/character-card-spec-v3/blob/main/SPEC_V3.md",
)
COMMON = ("name", "description", "personality", "scenario", "first_mes", "mes_example")
V2_TEXT = (
    "creator_notes",
    "system_prompt",
    "post_history_instructions",
    "creator",
    "character_version",
)
BOOK_FIELDS = {
    "name": str,
    "description": str,
    "scan_depth": float,
    "token_budget": float,
    "recursive_scanning": bool,
    "extensions": dict,
    "entries": list,
}
ENTRY_FIELDS = {
    "keys": list,
    "secondary_keys": (list, str),
    "content": str,
    "extensions": dict,
    "enabled": bool,
    "insertion_order": float,
    "case_sensitive": bool,
    "name": str,
    "priority": float,
    "id": (str, float),
    "comment": str,
    "selective": bool,
    "constant": bool,
    "position": str,
    "use_regex": bool,
}


def _check(value, kind, path):
    kinds = kind if isinstance(kind, tuple) else (kind,)
    valid = any((type(value) in (int, float) if k is float else type(value) is k) for k in kinds)
    if not valid:
        raise ValueError(
            f"Invalid card field type at {path}; consult the supported card definition"
        )


def _strings(value, path):
    _check(value, list, path)
    for item in value:
        _check(item, str, path + "[]")


def _png_json(source):
    offset = 8
    found = {}
    chunks = 0
    ended = False
    header = False
    image = False
    while offset < len(source):
        if offset + 12 > len(source):
            raise ValueError("Truncated PNG chunk; use a complete character-card PNG")
        size = struct.unpack_from(">I", source, offset)[0]
        kind = source[offset + 4 : offset + 8]
        end = offset + 12 + size
        if end > len(source) or size > MAX_CARD_BYTES or chunks >= 4096:
            raise ValueError("PNG chunk/size limit exceeded or truncated")
        data = source[offset + 8 : offset + 8 + size]
        crc = struct.unpack_from(">I", source, offset + 8 + size)[0]
        if zlib.crc32(kind + data) != crc:
            raise ValueError("PNG checksum invalid; re-export the original card")
        if chunks == 0:
            if kind != b"IHDR" or size != 13:
                raise ValueError("PNG requires an initial 13-byte IHDR")
            width, height = struct.unpack_from(">II", data)
            if (
                not width
                or not height
                or width > 8192
                or height > 8192
                or width * height > 16777216
            ):
                raise ValueError("PNG portrait dimensions exceed supported asset limits")
            header = True
        elif kind == b"IHDR":
            raise ValueError("Ambiguous PNG header")
        if kind == b"IDAT":
            image = True
        if kind == b"tEXt":
            keyword, sep, encoded = data.partition(b"\0")
            if keyword in (b"chara", b"ccv3"):
                if not sep or keyword in found:
                    raise ValueError("Ambiguous duplicate card metadata in PNG")
                if len(encoded) > 350000:
                    raise ValueError("Embedded card JSON exceeds 256 KiB")
                try:
                    found[keyword] = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error) as error:
                    raise ValueError("Card metadata must be strict base64 UTF-8 JSON") from error
        offset = end
        chunks += 1
        if kind == b"IEND":
            if size or offset != len(source):
                raise ValueError("PNG has trailing data or invalid IEND")
            ended = True
            break
    if not ended or not header or not image or not found:
        raise ValueError("PNG needs complete image chunks and chara/ccv3 tEXt card metadata")
    selected = b"ccv3" if b"ccv3" in found else b"chara"
    value = _parse(found[selected])
    if selected == b"ccv3" and (
        not isinstance(value, dict) or value.get("spec") != "chara_card_v3"
    ):
        raise ValueError("ccv3 metadata must identify Character Card V3")
    return value, len(found) > 1


def _asset_uri(uri):
    _check(uri, str, "data.assets[].uri")
    if len(uri) > 4096 or "\x00" in uri or any(c.isspace() for c in uri):
        raise ValueError("Invalid asset reference; maximum URI length is 4096")
    if uri.startswith(("http://", "https://")):
        parsed = urlsplit(uri)
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Invalid remote asset reference; credentials are prohibited")
        return uri
    if uri.startswith("embeded://"):
        path = uri[len("embeded://") :]
        pure = PurePosixPath(path)
        if not path or pure.is_absolute() or ".." in pure.parts or "\\" in path or ":" in path:
            raise ValueError("Unsafe embedded asset path")
        return "card://embedded/" + path
    if uri.startswith("ccdefault:"):
        return "card://default/" + hashlib.sha256(uri.encode()).hexdigest()
    if uri.startswith("data:"):
        # Never decode or expose inline asset bytes as a prompt. Original retains them.
        return "card://inline/" + hashlib.sha256(uri.encode()).hexdigest()
    raise ValueError(
        "Unsupported or unsafe asset URI; use http(s), embeded, "
        "ccdefault or bounded data references"
    )


def _foreign(value, version):
    return {"status": "preserved_only", "format": version, "settings": value}


@dataclass(frozen=True)
class CardPreview:
    profile: dict
    source_sha256: str
    conversion: dict

    def report(self):
        return dict(self.conversion)


def preview_character_card(source: bytes) -> CardPreview:
    if not isinstance(source, bytes) or not source or len(source) > MAX_CARD_BYTES:
        raise ValueError("Character card must be nonempty and at most 16 MiB; JSON at most 256 KiB")
    if source.startswith(b"PK"):
        raise ValueError(
            "CHARX/ZIP archives are unsupported; export card JSON or a "
            "card PNG. No archive was extracted."
        )
    container = "png" if source.startswith(b"\x89PNG\r\n\x1a\n") else "json"
    value, dual = _png_json(source) if container == "png" else (_parse(source), False)
    if not isinstance(value, dict):
        raise ValueError("Card root must be an object")
    if "spec" not in value and "spec_version" not in value and all(k in value for k in COMMON):
        version = 1
        data = value
        outer = {}
    elif value.get("spec") in ("chara_card_v2", "chara_card_v3"):
        version = 2 if value["spec"] == "chara_card_v2" else 3
        if value.get("spec_version") != f"{version}.0":
            raise ValueError(
                "Unsupported card version; supported external versions are V1, V2 2.0 and V3 3.0"
            )
        data = value.get("data")
        _check(data, dict, "data")
        outer = {k: v for k, v in value.items() if k not in ("spec", "spec_version", "data")}
    else:
        raise ValueError("Unsupported card envelope; use flat V1 or explicit V2 2.0 / V3 3.0")
    for key in COMMON + (V2_TEXT if version > 1 else ()):
        if key not in data:
            raise ValueError(f"Missing required card field: data.{key}")
        _check(data[key], str, "data." + key)
    if not data["name"].strip() or len(data["name"].strip()) > 160:
        raise ValueError("Card name must contain text and fit the native 160-character name limit")
    if version > 1:
        for key in ("alternate_greetings", "tags"):
            if key not in data:
                raise ValueError("Missing required card array: data." + key)
            _strings(data[key], "data." + key)
        if "extensions" not in data:
            raise ValueError("Missing required data.extensions object")
        _check(data["extensions"], dict, "data.extensions")
    if version == 3:
        if "group_only_greetings" not in data:
            raise ValueError("Missing required V3 data.group_only_greetings")
        _strings(data["group_only_greetings"], "data.group_only_greetings")
    label = f"character-card-v{version}"
    digest = hashlib.sha256(source).hexdigest()
    report = {
        k: [] for k in ("imported", "transformed", "preserved_only", "unsupported", "omitted")
    }
    p = {
        "format_id": "sylvia-gm-character-profile",
        "format_version": 2,
        "name": data["name"].strip(),
        "summary": "Imported character source; explicit target adaptation required.",
        "biography": {"description": data["description"], "personality": data["personality"]},
        "portrayal": {"scenario": data["scenario"]},
        "greetings": {"default": data["first_mes"]},
        "dialogue_examples": [{"id": "card-dialogue", "text": data["mes_example"]}],
        "import_source": {
            "sha256": digest,
            "format": label,
            "container": container,
            "byte_count": len(source),
        },
    }
    report["imported"] += list(COMMON)
    if p["name"] != data["name"]:
        report["transformed"].append("name: removed outer whitespace; original retained")
    consumed = set(COMMON)
    if version > 1:
        p["metadata"] = {
            "creator": data["creator"],
            "creator_notes": data["creator_notes"],
            "tags": data["tags"],
            "revision": data["character_version"],
        }
        p["greetings"]["alternatives"] = data["alternate_greetings"]
        consumed.update(
            ("creator", "creator_notes", "tags", "character_version", "alternate_greetings")
        )
        report["imported"] += [
            "creator",
            "creator_notes",
            "tags",
            "character_version",
            "alternate_greetings",
        ]
    for key in (
        "nickname",
        "source",
        "creator_notes_multilingual",
        "creation_date",
        "modification_date",
        "group_only_greetings",
    ):
        if key not in data:
            continue
        if key == "nickname":
            _check(data[key], str, "data." + key)
            p["identity"] = {"aliases": [data[key]]}
        elif key == "group_only_greetings":
            _strings(data[key], "data." + key)
            p["greetings"]["group_only"] = data[key]
        elif key == "source":
            _strings(data[key], "data." + key)
            p.setdefault("metadata", {})["sources"] = data[key]
        elif key == "creator_notes_multilingual":
            _check(data[key], dict, "data." + key)
            for item in data[key].values():
                _check(item, str, "data." + key)
            p.setdefault("metadata", {})[key] = data[key]
            p["metadata"]["languages"] = list(data[key])
        else:
            _check(data[key], float, "data." + key)
            p.setdefault("metadata", {})[key] = data[key]
        consumed.add(key)
        report["imported"].append(key)
    if "character_book" in data:
        book = data["character_book"]
        _check(book, dict, "data.character_book")
        for key, kind in BOOK_FIELDS.items():
            if key in book:
                _check(book[key], kind, "data.character_book." + key)
        if "entries" not in book or "extensions" not in book:
            raise ValueError("Character book requires entries and extensions")
        lore = {"entries": []}
        for key in ("name", "description"):
            if key in book:
                lore[key] = book[key]
        for index, entry in enumerate(book["entries"]):
            _check(entry, dict, f"character_book.entries[{index}]")
            for key in ("keys", "content", "extensions", "enabled", "insertion_order"):
                if key not in entry:
                    raise ValueError(f"Missing lore field: character_book.entries[{index}].{key}")
            if version == 3 and "use_regex" not in entry:
                raise ValueError(
                    f"Missing V3 lore field: character_book.entries[{index}].use_regex"
                )
            for key, kind in ENTRY_FIELDS.items():
                if key in entry:
                    _check(entry[key], kind, f"character_book.entries[{index}].{key}")
            _strings(entry["keys"], "character_book.entries[].keys")
            if isinstance(entry.get("secondary_keys"), list):
                _strings(entry["secondary_keys"], "character_book.entries[].secondary_keys")
            mapped = {"id": f"card-lore-{index}"}
            for key in ENTRY_FIELDS:
                if key not in entry:
                    continue
                if key == "id":
                    mapped["source_id"] = entry[key]
                elif key != "extensions":
                    mapped[key] = entry[key]
            extra = {k: v for k, v in entry.items() if k not in ENTRY_FIELDS or k == "extensions"}
            if extra:
                mapped["foreign_configuration"] = _foreign(extra, label)
            lore["entries"].append(mapped)
        settings = {k: v for k, v in book.items() if k not in ("name", "description", "entries")}
        if settings:
            lore["foreign_configuration"] = _foreign(settings, label)
        p["lore"] = lore
        consumed.add("character_book")
        report["imported"].append("character_book: text, keys, source IDs, order and enabled flags")
        report["preserved_only"].append(
            "lore activation, regex, insertion and extension settings: "
            "no scanning or prompt injection"
        )
    if "assets" in data:
        _check(data["assets"], list, "data.assets")
        p["assets"] = []
        for index, asset in enumerate(data["assets"]):
            _check(asset, dict, "data.assets[]")
            for key in ("type", "uri", "name", "ext"):
                if key not in asset:
                    raise ValueError("Missing asset field: data.assets[]." + key)
                _check(asset[key], str, "data.assets[]." + key)
            p["assets"].append(
                {
                    "id": f"card-asset-{index}",
                    "kind": asset["type"],
                    "name": asset["name"],
                    "format": asset["ext"],
                    "uri": _asset_uri(asset["uri"]),
                    "status": "reference_only",
                }
            )
        report["transformed"].append(
            "assets: inert references; embedded/data URIs identify retained original content"
        )
        # Retain full descriptors (including unknown asset metadata) as preserved-only.
    if container == "png":
        p.setdefault("assets", []).append(
            {
                "id": "card-portrait",
                "uri": "card://portrait",
                "kind": "icon",
                "status": "reference_only",
            }
        )
        report["preserved_only"].append(
            "PNG portrait and auxiliary chunks retained in original; no"
            " image decode or asset loading"
        )
    foreign = {k: v for k, v in data.items() if k not in consumed}
    report["preserved_only"].extend(
        ("data." if version > 1 and outer else "") + key for key in sorted(foreign)
    )
    if outer:
        # A card may itself contain any of these names. Never overwrite source fields.
        outer_key = "outer_extensions"
        if outer_key in foreign:
            outer_key = "outer_extensions_root"
            suffix = 0
            while outer_key in foreign:
                suffix += 1
                outer_key = f"outer_extensions_root_{suffix}"
            report["transformed"].append(
                "Root fields grouped under foreign_configuration.settings."
                + outer_key
                + "; colliding data fields retained unchanged"
            )
        foreign[outer_key] = outer
        report["preserved_only"].extend(
            f"root.{key}: foreign_configuration.settings.{outer_key}" for key in sorted(outer)
        )
    if foreign:
        p["foreign_configuration"] = _foreign(foreign, label)
    report["unsupported"] += [
        (
            "foreign prompt precedence, macros, scripts, automatic lore"
            " activation, external asset loading"
        )
    ]
    if dual:
        report["transformed"].append(
            "PNG has chara and ccv3: V3 selected per spec; V2 retained in original"
        )
    report["omitted"] = ["No source bytes discarded; unsupported behavior is not activated."]
    validate_character_profile(canonical_json_bytes(p))
    return CardPreview(p, digest, report)


def _original_path(storage_root, digest):
    if not isinstance(digest, str) or re.fullmatch("[0-9a-f]{64}", digest) is None:
        raise ValueError("Invalid import source identity")
    root = storage_root / "character-card-imports"
    if os.path.lexists(root) and (is_link_or_reparse(root) or not root.is_dir()):
        raise ValueError("Unsafe imported-source library")
    target = root / digest
    if os.path.lexists(target):
        if is_link_or_reparse(target) or not target.is_dir():
            raise ValueError("Unsafe imported-source entry")
        validate_owned_files(target, frozenset({"source.bin", "record.json"}))
    return target


def load_import_original(storage_root: Path, digest: str) -> bytes:
    target = _original_path(storage_root, digest)
    record = _parse((target / "record.json").read_bytes())
    with (target / "source.bin").open("rb") as handle:
        source = handle.read(MAX_CARD_BYTES + 1)
    if (
        record
        != {
            "format_id": "sylvia-gm-character-card-source",
            "format_version": 1,
            "sha256": digest,
            "byte_count": len(source),
        }
        or len(source) > MAX_CARD_BYTES
        or hashlib.sha256(source).hexdigest() != digest
    ):
        raise ValueError("Imported original identity/checksum is invalid")
    return source


def save_character_card_import(storage_root: Path, *, operation_id: str, source: bytes) -> dict:
    preview = preview_character_card(source)
    target = _original_path(storage_root, preview.source_sha256)
    if target.exists():
        if load_import_original(storage_root, preview.source_sha256) != source:
            raise ValueError("Original-source conflict") from None
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".card-import-", dir=storage_root) as temporary:
            staged = Path(temporary) / preview.source_sha256
            staged.mkdir()
            for name, data in [
                ("source.bin", source),
                (
                    "record.json",
                    canonical_json_bytes(
                        {
                            "format_id": "sylvia-gm-character-card-source",
                            "format_version": 1,
                            "sha256": preview.source_sha256,
                            "byte_count": len(source),
                        }
                    ),
                ),
            ]:
                with (staged / name).open("wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            try:
                staged.rename(target)
            except FileExistsError:
                if load_import_original(storage_root, preview.source_sha256) != source:
                    raise ValueError("Original-source conflict") from None
    saved = save_character_profile(
        storage_root, operation_id=operation_id, source=canonical_json_bytes(preview.profile)
    )
    return {
        **saved.catalog_payload(),
        "import_source_sha256": preview.source_sha256,
        "conversion_preview": preview.report(),
    }


def _preview_root(storage_root: Path) -> Path:
    root = storage_root / "character-card-previews"
    if os.path.lexists(root) and (is_link_or_reparse(root) or not root.is_dir()):
        raise ValueError("Unsafe preview storage")
    return root


def _read_pending_preview(target: Path, digest: str) -> bytes:
    if is_link_or_reparse(target) or not target.is_file():
        raise ValueError("Unsafe preview entry")
    with target.open("rb") as handle:
        source = handle.read(MAX_CARD_BYTES + 1)
    if len(source) > MAX_CARD_BYTES or hashlib.sha256(source).hexdigest() != digest:
        raise ValueError("Pending preview changed; preview the source again")
    return source


def _confirmed_card_result(storage_root: Path, digest: str, source: bytes | None = None) -> dict:
    """Verify a prior desktop confirmation without creating a profile on a retry."""
    original = load_import_original(storage_root, digest)
    if source is not None and source != original:
        raise ValueError("Original-source conflict")
    preview = preview_character_card(original)
    profile_digest = hashlib.sha256(canonical_json_bytes(preview.profile)).hexdigest()
    operation_hash = hashlib.sha256(("desktop-card:" + digest).encode()).hexdigest()[:24]
    profile_id = f"character-profile-{operation_hash}-{profile_digest[:16]}"
    saved = load_character_profile(storage_root, profile_id, profile_digest)
    return {
        **saved.catalog_payload(),
        "import_source_sha256": digest,
        "conversion_preview": preview.report(),
    }


def _retire_pending_preview(storage_root: Path, digest: str, source: bytes) -> None:
    """Remove only a verified temporary duplicate after durable confirmation."""
    _confirmed_card_result(storage_root, digest, source)
    target = _preview_root(storage_root) / digest
    if _read_pending_preview(target, digest) != source:
        raise ValueError("Pending preview changed; preview the source again")
    target.unlink()


def _retire_completed_previews(storage_root: Path, root: Path) -> None:
    """Release old completed staging slots, leaving pending/unknown/unsafe entries intact."""
    for target in root.iterdir():
        if re.fullmatch("[0-9a-f]{64}", target.name) is None:
            continue
        try:
            if not _original_path(storage_root, target.name).exists():
                continue
            source = _read_pending_preview(target, target.name)
            _retire_pending_preview(storage_root, target.name, source)
        except (OSError, ValueError, CommandRejected):
            # Failed saves, tampered content and paths we do not own still consume a slot.
            continue


def stage_card_preview(storage_root: Path, source_path: Path) -> dict:
    """Capture one desktop-selected local file without trusting it as executable content."""
    if (
        source_path.suffix.lower() not in (".json", ".png")
        or is_link_or_reparse(source_path)
        or not source_path.is_file()
    ):
        raise ValueError("Select a regular local .json or .png character-card file")
    with source_path.open("rb") as handle:
        source = handle.read(MAX_CARD_BYTES + 1)
    preview = preview_character_card(source)
    with _PREVIEW_LOCK:
        root = _preview_root(storage_root)
        root.mkdir(parents=True, exist_ok=True)
        _retire_completed_previews(storage_root, root)
        target = root / preview.source_sha256
        if os.path.lexists(target):
            if _read_pending_preview(target, preview.source_sha256) != source:
                raise ValueError("Preview identity conflict")
        else:
            if len(tuple(root.iterdir())) >= MAX_PENDING_PREVIEWS:
                raise ValueError(
                    "Preview capacity reached; confirm an existing pending preview "
                    "before adding another"
                )
            with target.open("xb") as handle:
                handle.write(source)
                handle.flush()
                os.fsync(handle.fileno())
    return {
        "status": "preview",
        "source_sha256": preview.source_sha256,
        "name": preview.profile["name"],
        "summary": preview.profile["biography"]["description"][:320],
        "conversion_preview": preview.report(),
        "campaign_ready": False,
        "adaptation_required": True,
    }


def confirm_card_preview(storage_root: Path, digest: str) -> dict:
    if not isinstance(digest, str) or re.fullmatch("[0-9a-f]{64}", digest) is None:
        raise ValueError("Select the exact pending card preview")
    with _PREVIEW_LOCK:
        target = _preview_root(storage_root) / digest
        if os.path.lexists(target):
            source = _read_pending_preview(target, digest)
            result = save_character_card_import(
                storage_root, operation_id="desktop-card:" + digest, source=source
            )
            _retire_pending_preview(storage_root, digest, source)
        else:
            try:
                result = _confirmed_card_result(storage_root, digest)
            except (FileNotFoundError, CommandRejected) as error:
                raise ValueError(
                    "Pending preview unavailable; select and preview the source again"
                ) from error
    return {"status": "imported", "profile": result}
