"""Filesystem reader for Campaign Setting (CS) options.

Discovers rulesets, settings, campaign plans, characters, and companions
from the on-disk CS directory: ``~/.sylvia-gm/campaign-settings/``.

Used by the builder options API endpoints and the cascading interview flow.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Default CS base path
_DEFAULT_CS_BASE = Path.home() / ".sylvia-gm" / "campaign-settings"


def _cs_base(base_path: Path | str | None = None) -> Path:
    """Return the CS base directory, with optional override."""
    return Path(base_path) if base_path else _DEFAULT_CS_BASE


# ── Rulesets ──────────────────────────────────────────────────────────


def list_rulesets(base_path: Path | str | None = None) -> list[dict[str, Any]]:
    """List all available ruleset directories in the CS base path.

    A ruleset is a top-level subdirectory of ``campaign-settings/``
    that contains at least one Campaign Setting subdirectory.

    Returns a list of dicts with keys: ``id``, ``name``, ``settings_count``.
    """
    base = _cs_base(base_path)
    if not base.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for ruleset_dir in sorted(base.iterdir()):
        if not ruleset_dir.is_dir():
            continue
        # Count settings (subdirectories with setting.json)
        settings = list_settings(ruleset_dir.name, base_path)
        results.append({
            "id": ruleset_dir.name,
            "name": ruleset_dir.name.replace("-", " ").title(),
            "settings_count": len(settings),
        })
    return results


# ── Settings ───────────────────────────────────────────────────────────


def list_settings(
    ruleset: str,
    base_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """List all Campaign Settings for a given ruleset.

    Returns a list of dicts with keys: ``slug``, ``name``, ``version``,
    ``description``, ``extends``.
    """
    base = _cs_base(base_path)
    ruleset_dir = base / ruleset
    if not ruleset_dir.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for cs_dir in sorted(ruleset_dir.iterdir()):
        if not cs_dir.is_dir():
            continue
        setting_json = cs_dir / "setting.json"
        if not setting_json.is_file():
            continue

        try:
            data = json.loads(setting_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        meta = data.get("meta", {})
        results.append({
            "slug": meta.get("slug", cs_dir.name),
            "name": meta.get("name", cs_dir.name.replace("-", " ").title()),
            "version": meta.get("version", "0.0.0"),
            "description": meta.get("description", ""),
            "extends": meta.get("extends", ""),
        })
    return results


# ── Campaign Plans ─────────────────────────────────────────────────────


def list_plans(
    setting: str,
    ruleset: str | None = None,
    base_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """List all campaign plans for a given Campaign Setting.

    Plans are subfolders under ``<setting>/campaigns/`` each containing
    an ``index.md`` + ``act-N.md`` files.  Flat ``.md`` files at the
    campaigns root are also supported for backward compatibility.

    If ``ruleset`` is not provided, searches all ruleset directories
    for the given setting slug.

    Returns a list of dicts with keys: ``slug``, ``name``, ``path``, ``act_count``.
    """
    base = _cs_base(base_path)

    cs_dirs = _find_setting_dirs(base, setting, ruleset)
    if not cs_dirs:
        return []

    results: list[dict[str, Any]] = []
    for cs_dir in cs_dirs:
        campaigns_dir = cs_dir / "campaigns"
        if not campaigns_dir.is_dir():
            continue

        # ── New format: subfolders with index.md + act-N.md ──
        for plan_dir in sorted(campaigns_dir.iterdir()):
            if not plan_dir.is_dir():
                continue
            index_md = plan_dir / "index.md"
            if not index_md.is_file():
                continue
            name = _extract_title_from_md(index_md)
            # Count act files for reference
            act_count = len(list(plan_dir.glob("act-*.md")))
            results.append({
                "slug": plan_dir.name,
                "name": name or plan_dir.name.replace("-", " ").title(),
                "path": str(index_md),
                "act_count": act_count,
            })

        # ── Backward compat: flat .md files at campaigns root ──
        if not results:
            for md_file in sorted(campaigns_dir.glob("*.md")):
                name = _extract_title_from_md(md_file)
                results.append({
                    "slug": md_file.stem,
                    "name": name or md_file.stem.replace("-", " ").title(),
                    "path": str(md_file),
                    "act_count": 0,
                })

    return results


# ── Characters / PCs ───────────────────────────────────────────────────


def list_characters(
    setting: str,
    ruleset: str | None = None,
    base_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """List player-characters for a given Campaign Setting.

    Looks in ``<setting>/characters/`` for .md or .json files.
    If that directory doesn't exist, returns an empty list.

    Returns a list of dicts with keys: ``slug``, ``name``, ``path``.
    """
    base = _cs_base(base_path)

    cs_dirs = _find_setting_dirs(base, setting, ruleset)
    if not cs_dirs:
        return []

    results: list[dict[str, Any]] = []
    for cs_dir in cs_dirs:
        for chars_subdir in ("characters", "pcs"):
            chars_dir = cs_dir / chars_subdir
            if not chars_dir.is_dir():
                continue
            for file in sorted(chars_dir.iterdir()):
                if file.suffix in (".md", ".json"):
                    name = _extract_title_from_md(file) if file.suffix == ".md" else file.stem.replace("-", " ").title()
                    results.append({
                        "slug": file.stem,
                        "name": name or file.stem.replace("-", " ").title(),
                        "path": str(file),
                    })
    return results


# ── Companions ─────────────────────────────────────────────────────────


def list_companions(
    setting: str,
    ruleset: str | None = None,
    base_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """List companion characters for a given Campaign Setting.

    Looks in ``<setting>/companions/`` for .md or .json files.
    If that directory doesn't exist, returns all NPCs as potential
    companions from ``<setting>/npcs/``.

    Returns a list of dicts with keys: ``slug``, ``name``, ``path``, ``source``.
    """
    base = _cs_base(base_path)

    cs_dirs = _find_setting_dirs(base, setting, ruleset)
    if not cs_dirs:
        return []

    results: list[dict[str, Any]] = []
    for cs_dir in cs_dirs:
        # First try explicit companions/ directory
        companions_dir = cs_dir / "companions"
        if companions_dir.is_dir():
            for file in sorted(companions_dir.iterdir()):
                if file.suffix in (".md", ".json"):
                    name = _extract_title_from_md(file) if file.suffix == ".md" else file.stem.replace("-", " ").title()
                    results.append({
                        "slug": file.stem,
                        "name": name or file.stem.replace("-", " ").title(),
                        "path": str(file),
                        "source": "companions",
                    })

        # Fall back to npcs/ if no companions/ found
        if not results:
            npcs_dir = cs_dir / "npcs"
            if npcs_dir.is_dir():
                for file in sorted(npcs_dir.iterdir()):
                    if file.suffix in (".md", ".json"):
                        name = _extract_title_from_md(file) if file.suffix == ".md" else file.stem.replace("-", " ").title()
                        results.append({
                            "slug": file.stem,
                            "name": name or file.stem.replace("-", " ").title(),
                            "path": str(file),
                            "source": "npcs",
                        })
    return results


# ── CS content summary (for planning context) ──────────────────────────


def get_setting_summary(
    setting: str,
    ruleset: str | None = None,
    base_path: Path | str | None = None,
) -> dict[str, Any]:
    """Return a summary of what's available in a Campaign Setting.

    Returns keys: ``setting``, ``ruleset``, ``plans``, ``characters``,
    ``companions``, ``npc_count``, ``monster_count``, ``item_count``,
    ``spell_count``, ``lore_files``, ``dm_tools``.
    """
    base = _cs_base(base_path)

    cs_dirs = _find_setting_dirs(base, setting, ruleset)
    if not cs_dirs:
        return {"error": f"Setting '{setting}' not found"}

    cs_dir = cs_dirs[0]

    def _count_md(d: Path) -> int:
        return len(list(d.glob("*.md"))) if d.is_dir() else 0

    return {
        "setting": setting,
        "ruleset": _find_ruleset_for_setting(base, setting) or ruleset or "unknown",
        "plans": len(list_plans(setting, ruleset, base_path)),
        "characters": len(list_characters(setting, ruleset, base_path)),
        "companions": len(list_companions(setting, ruleset, base_path)),
        "npc_count": _count_md(cs_dir / "npcs"),
        "monster_count": _count_md(cs_dir / "content" / "monsters"),
        "item_count": _count_md(cs_dir / "content" / "items"),
        "spell_count": _count_md(cs_dir / "content" / "spells"),
        "lore_files": _count_md(cs_dir / "lore"),
        "dm_tools": _count_md(cs_dir / "dm-tools"),
    }


# ── Helpers ────────────────────────────────────────────────────────────


def _find_setting_dirs(
    base: Path,
    setting: str,
    ruleset: str | None = None,
) -> list[Path]:
    """Find CS directories for a given setting slug (and optional ruleset)."""
    if ruleset:
        cs_dir = base / ruleset / setting
        return [cs_dir] if cs_dir.is_dir() else []

    # Search all ruleset dirs
    results: list[Path] = []
    for ruleset_dir in sorted(base.iterdir()):
        if not ruleset_dir.is_dir():
            continue
        cs_dir = ruleset_dir / setting
        if cs_dir.is_dir():
            results.append(cs_dir)
    return results


def _find_ruleset_for_setting(base: Path, setting: str) -> str | None:
    """Find which ruleset directory contains a given setting slug."""
    for ruleset_dir in sorted(base.iterdir()):
        if not ruleset_dir.is_dir():
            continue
        if (ruleset_dir / setting).is_dir():
            return ruleset_dir.name
    return None


def _extract_title_from_md(path: Path) -> str | None:
    """Extract the first H1 title from a Markdown file."""
    try:
        text = path.read_text(encoding="utf-8")
        # Skip YAML frontmatter
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                text = text[end + 3:]
        # Find first H1
        m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip()
    except (OSError, UnicodeDecodeError):
        pass
    return None
