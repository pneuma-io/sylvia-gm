"""PackLoader — load, list, and validate premium campaign packs."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class PackMetadata:
    """Metadata for an installed campaign pack."""

    name: str
    author: str = ""
    version: str = "1.0.0"
    price: float = 0.0
    description: str = ""
    installed_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "author": self.author,
            "version": self.version,
            "price": self.price,
            "description": self.description,
            "installed_path": self.installed_path,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PackMetadata:
        return cls(
            name=str(d.get("name", "")),
            author=str(d.get("author", "")),
            version=str(d.get("version", "1.0.0")),
            price=float(d.get("price", 0.0)),
            description=str(d.get("description", "")),
            installed_path=str(d.get("installed_path", "")),
        )


class PackLoader:
    """Loader for premium campaign packs in ZIP format.

    Pack structure (inside ZIP):
        campaign.yaml    — campaign plan (required)
        ruleset.yaml     — ruleset snapshot (optional)
        assets/          — images, maps, etc. (optional)
        pack.json        — metadata (required)

    Usage::

        loader = PackLoader()
        metadata = loader.load(Path("my_pack.zip"), Path("installed/"))
        packs = loader.list_packs(Path("installed/"))
        errors = loader.validate_pack(Path("my_pack.zip"))
    """

    _REQUIRED_FILES: tuple[str, ...] = ("pack.json", "campaign.yaml")

    def load(self, pack_path: Path, install_dir: Path) -> PackMetadata:
        """Load a campaign pack from a ZIP file into *install_dir*.

        Returns the pack's metadata.  Creates *install_dir* if needed.
        """
        errors = self.validate_pack(pack_path)
        if errors:
            raise ValueError(f"Invalid pack: {'; '.join(errors)}")

        with zipfile.ZipFile(pack_path, "r") as zf:
            # Read pack.json first to get the name
            meta_raw = json.loads(zf.read("pack.json").decode("utf-8"))
            pack_name = meta_raw.get("name", pack_path.stem)

            dest = install_dir / pack_name
            dest.mkdir(parents=True, exist_ok=True)

            zf.extractall(dest)

        meta_raw["installed_path"] = str(dest)
        return PackMetadata.from_dict(meta_raw)

    def list_packs(self, packs_dir: Path) -> list[PackMetadata]:
        """List all installed packs in *packs_dir*.

        Each sub-directory that contains both pack.json and campaign.yaml is
        treated as an installed pack.
        """
        if not packs_dir.is_dir():
            return []

        results: list[PackMetadata] = []
        for child in sorted(packs_dir.iterdir()):
            if not child.is_dir():
                continue
            meta_path = child / "pack.json"
            campaign_path = child / "campaign.yaml"
            if meta_path.is_file() and campaign_path.is_file():
                raw = json.loads(meta_path.read_text(encoding="utf-8"))
                raw["installed_path"] = str(child)
                results.append(PackMetadata.from_dict(raw))

        return results

    def validate_pack(self, pack_path: Path) -> list[str]:
        """Validate a pack ZIP structure without extracting.

        Returns a list of error strings (empty list = valid).
        """
        errors: list[str] = []

        if not pack_path.is_file():
            errors.append(f"Pack file not found: {pack_path}")
            return errors

        if not zipfile.is_zipfile(pack_path):
            errors.append(f"Not a valid ZIP file: {pack_path}")
            return errors

        try:
            with zipfile.ZipFile(pack_path, "r") as zf:
                names = set(zf.namelist())

                for required in self._REQUIRED_FILES:
                    if required not in names:
                        errors.append(f"Missing required file: {required}")

                # Validate pack.json structure
                if "pack.json" in names:
                    raw = json.loads(zf.read("pack.json").decode("utf-8"))
                    if not isinstance(raw.get("name"), str) or not raw["name"].strip():
                        errors.append("pack.json: 'name' is required and must be non-empty")

                # Validate campaign.yaml is parseable
                if "campaign.yaml" in names:
                    try:
                        yaml.safe_load(zf.read("campaign.yaml").decode("utf-8"))
                    except yaml.YAMLError as exc:
                        errors.append(f"campaign.yaml: invalid YAML — {exc}")

        except zipfile.BadZipFile as exc:
            errors.append(f"Corrupted ZIP: {exc}")
        except Exception as exc:
            errors.append(f"Validation error: {exc}")

        return errors
