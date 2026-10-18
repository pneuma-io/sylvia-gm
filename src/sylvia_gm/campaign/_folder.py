"""Campaign folder creation — pure filesystem operations.

Creates self-contained campaign directories on disk by forking a Campaign
Setting, copying act plans and character files, and writing manifest,
status, and handoff files.  No LLM calls, no async I/O, no server deps.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Regex for campaign slug validation ──────────────────────────
# Portable compatibility-folder slugs use lowercase alphanumeric characters and
# hyphens, with an alphanumeric character at each end.
_CAMPAIGN_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

# ── Subdirectories created inside every campaign folder ─────────
_CAMPAIGN_SUBDIRS: tuple[str, ...] = ("acts", "characters", "session")


@dataclass
class CreateCampaignResult:
    """Return value from :func:`create_campaign_folder`."""

    success: bool
    campaign_folder: Path | None = None
    campaign_slug: str | None = None
    handoff_path: Path | None = None
    error: str | None = None
    missing_fields: list[str] = field(default_factory=list)


def create_campaign_folder(
    *,
    ruleset: str,
    setting_slug: str,
    plan_slug: str,
    selected_characters: list[str],
    selected_companions: list[str],
    difficulty: str = "normal",
    tone: str = "dark",
    boundaries: list[str] | None = None,
    freeform_requests: str = "",
    campaign_slug_override: str | None = None,
    cs_base: Path | None = None,
    campaigns_base: Path | None = None,
) -> CreateCampaignResult:
    """Create a self-contained campaign folder on disk.

    Ten-step process: validate sources → check conflicts → create
    directories → clone CS → copy acts → copy characters → write
    manifest → write status → write handoff → write changelog.

    All parameters are keyword-only for clarity (M3).

    Parameters
    ----------
    ruleset:
        Ruleset package id, e.g. ``"example.ruleset"``.
    setting_slug:
        Setting directory name, e.g. ``"severed-lands"``.
    plan_slug:
        Campaign plan directory name, e.g. ``"the-rift-awakening"``.
    selected_characters:
        List of character slugs to copy, e.g. ``["sample_player"]``.
    selected_companions:
        List of companion slugs to copy, e.g. ``["sample_companion"]`` or ``[]``.
    difficulty:
        One of ``"normal"``, ``"hard"``, ``"deadly"``.  Default ``"normal"``.
    tone:
        One of ``"dark"``, ``"heroic"``, ``"whimsical"``, ``"gritty"``,
        ``"mysterious"``.  Default ``"dark"``.
    boundaries:
        Content boundary strings, e.g. ``["no sexual content"]``.
        Default ``None`` (treated as empty list).
    freeform_requests:
        Player freeform requests string.  Default ``""``.
    campaign_slug_override:
        Custom folder name; ``None`` derives from ``plan_slug``.
    cs_base:
        Root of campaign-settings tree; defaults to
        ``~/.sylvia-gm/campaign-settings/``.
    campaigns_base:
        Root of campaigns output tree; defaults to
        ``~/.sylvia-gm/campaigns/``.

    Returns
    -------
    CreateCampaignResult
    """
    boundaries = boundaries or []

    # ── Step 0: resolve paths ───────────────────────────────────
    cs_base = (cs_base or Path.home() / ".sylvia-gm" / "campaign-settings").resolve()
    campaigns_base = (campaigns_base or Path.home() / ".sylvia-gm" / "campaigns").resolve()

    # ── Step 1: derive & validate campaign slug (C1) ─────────────
    campaign_slug = campaign_slug_override or _derive_campaign_slug(plan_slug)
    if not _CAMPAIGN_ID_RE.match(campaign_slug):
        return CreateCampaignResult(
            success=False,
            error=f"Invalid campaign slug: '{campaign_slug}'",
            campaign_slug=campaign_slug,
        )

    # ── Step 1a: validate sources exist ─────────────────────────
    cs_source = cs_base / ruleset / setting_slug
    if not cs_source.is_dir():
        return CreateCampaignResult(
            success=False,
            error=f"Campaign Setting not found: {cs_source}",
        )

    plan_source = cs_source / "campaigns" / plan_slug
    if not plan_source.is_dir():
        return CreateCampaignResult(
            success=False,
            error=f"Campaign Plan not found: {plan_source}",
        )

    # Check for at least one act-*.md
    act_files = sorted(plan_source.glob("act-*.md"))
    if not act_files:
        return CreateCampaignResult(
            success=False,
            error=f"Campaign Plan has no act-*.md files: {plan_source}",
        )

    # ── Step 1b: check for conflicts ────────────────────────────
    campaign_dir = campaigns_base / campaign_slug
    if campaign_dir.exists():
        return CreateCampaignResult(
            success=False,
            error=f"Campaign folder already exists: {campaign_dir}",
        )

    # ── Step 1c: path traversal guard ───────────────────────────
    try:
        resolved = campaign_dir.resolve()
    except Exception:
        resolved = campaign_dir
    if not str(resolved).startswith(str(campaigns_base.resolve())):
        return CreateCampaignResult(
            success=False,
            error=f"Campaign slug '{campaign_slug}' resolves outside campaigns base",
        )

    # ── Main try/cleanup block ──────────────────────────────────
    campaign_dir_created = False
    try:
        # ── Step 2: create directory tree ───────────────────────
        campaign_dir.mkdir(parents=True, exist_ok=False)
        campaign_dir_created = True
        for sub in _CAMPAIGN_SUBDIRS:
            (campaign_dir / sub).mkdir(exist_ok=True)

        # ── Step 3: clone Campaign Setting (H1) ─────────────────
        setting_dst = campaign_dir / setting_slug
        _clone_campaign_setting(
            cs_source,
            setting_dst,
            exclude_names={"campaigns", ".git", "__pycache__", "node_modules"},
        )

        # ── Step 4: copy act files ──────────────────────────────
        acts_dir = campaign_dir / "acts"
        for af in act_files:
            shutil.copy2(af, acts_dir / af.name)

        # ── Step 5: copy character files ────────────────────────
        _copy_character_files(cs_source, campaign_dir, selected_characters)

        # ── Step 6: copy companion files (M2) ───────────────────
        _copy_companion_files(cs_source, campaign_dir, selected_companions)

        # ── Step 7: write manifest.md (C2) ──────────────────────
        campaign_name = _get_display_name(campaign_slug)
        pc_names = _resolve_character_names(campaign_dir / "characters", selected_characters)
        companion_names = _resolve_companion_names(campaign_dir / "characters", selected_companions)
        _write_manifest(
            campaign_dir,
            campaign_name,
            campaign_slug,
            ruleset,
            setting_slug,
            act_files,
            selected_characters,
            selected_companions,
            pc_names,
            companion_names,
        )

        # ── Step 8: atomic write status.json (M1) ───────────────
        now_iso = datetime.now(UTC).isoformat()
        _write_status_atomic(campaign_dir, campaign_slug, now_iso)

        # ── Step 9: write handoff.md (M2) ───────────────────────
        handoff_path = _write_handoff(
            campaign_dir,
            campaign_name,
            campaign_slug,
            ruleset,
            setting_slug,
            plan_slug,
            difficulty,
            tone,
            boundaries,
            freeform_requests,
            selected_characters,
            selected_companions,
            pc_names,
            companion_names,
        )

        # ── Step 10: create session changelog template ──────────
        _write_act_changelog(campaign_dir, 1)

        return CreateCampaignResult(
            success=True,
            campaign_folder=campaign_dir,
            campaign_slug=campaign_slug,
            handoff_path=handoff_path,
        )

    except Exception as exc:
        logger.exception("Campaign folder creation failed for '%s'", campaign_slug)
        # Clean up partial directory
        if campaign_dir_created and campaign_dir.exists():
            shutil.rmtree(campaign_dir, ignore_errors=True)
        return CreateCampaignResult(
            success=False,
            error=str(exc),
            campaign_slug=campaign_slug,
        )


# ═══════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════


def _derive_campaign_slug(plan_slug: str) -> str:
    """Derive campaign folder name from plan_slug (C1, C2).

    The plan_slug is already a valid directory name from the CS
    hierarchy.  We lower-case it, collapse non-alphanumeric runs
    to single hyphens, strip leading/trailing hyphens, and
    validate against ``_CAMPAIGN_ID_RE``.
    """
    slug = plan_slug.lower().strip()
    # Collapse ALL non-alphanumeric runs (hyphens included) to a single
    # hyphen — matches the UI slugifier in index.html exactly. Previously
    # hyphens were preserved ([^a-z0-9-]+), so "A - B" slugified to
    # "a---b" server-side but "a-b" in the UI, causing delete-by-id 404s
    # and orphaned folders on rebuild.
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    if not _CAMPAIGN_ID_RE.match(slug):
        raise ValueError(
            f"Campaign slug '{slug}' is invalid (derived from plan_slug '{plan_slug}')"
        )
    return slug


def _get_display_name(slug: str) -> str:
    """Derive a human-readable display name from a slug (C2).

    Replaces hyphens with spaces and title-cases each word.
    Example: ``"the-rift-awakening"`` → ``"The Rift Awakening"``.
    """
    return slug.replace("-", " ").title()


def _extract_h1_name(md_path: Path) -> str | None:
    """Extract the first H1 heading from a Markdown file.

    Returns ``None`` if the file doesn't exist or has no ``# ...`` line.
    """
    if not md_path.is_file():
        return None
    try:
        for line in md_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                return stripped[2:].strip()
    except Exception:
        pass
    return None


def _resolve_character_names(char_dir: Path, slugs: list[str]) -> dict[str, str]:
    """Map character slugs to display names (H1 from file, or title-cased slug)."""
    names: dict[str, str] = {}
    for slug in slugs:
        h1 = _extract_h1_name(char_dir / f"{slug}.md")
        names[slug] = h1 if h1 else _get_display_name(slug)
    return names


def _resolve_companion_names(char_dir: Path, slugs: list[str]) -> dict[str, str]:
    """Map companion slugs to display names."""
    return _resolve_character_names(char_dir, slugs)


def _clone_campaign_setting(src: Path, dst: Path, exclude_names: set[str]) -> None:
    """Clone the Campaign Setting, excluding certain directories (H1, H3).

    Uses ``shutil.copytree`` with ``dirs_exist_ok=True`` on all
    platforms — no robocopy subprocess.
    """

    def _ignore(path: str, names: list[str]) -> set[str]:  # noqa: ARG001 — path unused
        return {n for n in names if n in exclude_names}

    shutil.copytree(src, dst, symlinks=True, ignore=_ignore, dirs_exist_ok=True)
    logger.info("Cloned Campaign Setting %s → %s", src, dst)


def _copy_character_files(
    cs_source: Path,
    campaign_dir: Path,
    slugs: list[str],
) -> None:
    """Copy character .md files into the campaign's characters/ directory.

    Checks ``cs_source/characters/{slug}.md`` first, then falls back
    to ``cs_source/pcs/{slug}.md``.  Missing files are logged as
    warnings but do not abort.
    """
    chars_dir = campaign_dir / "characters"
    for slug in slugs:
        src = cs_source / "characters" / f"{slug}.md"
        if not src.is_file():
            src = cs_source / "pcs" / f"{slug}.md"
        if src.is_file():
            shutil.copy2(src, chars_dir / f"{slug}.md")
        else:
            logger.warning("Character file not found for slug '%s' — skipping", slug)


def _copy_companion_files(
    cs_source: Path,
    campaign_dir: Path,
    slugs: list[str],
) -> None:
    """Copy companion .md files into the campaign's characters/ directory.

    Companions live alongside PCs in ``characters/``.  Empty slug list
    is a no-op (M2).  Missing files log a warning but do not abort.
    """
    if not slugs:
        return
    chars_dir = campaign_dir / "characters"
    for slug in slugs:
        src = cs_source / "companions" / f"{slug}.md"
        if src.is_file():
            shutil.copy2(src, chars_dir / f"{slug}.md")
        else:
            logger.warning("Companion file not found for slug '%s' — skipping", slug)


def _write_manifest(
    campaign_dir: Path,
    campaign_name: str,
    campaign_slug: str,
    ruleset: str,
    setting_slug: str,
    act_files: list[Path],
    pc_slugs: list[str],
    companion_slugs: list[str],
    pc_names: dict[str, str],
    companion_names: dict[str, str],
) -> None:
    """Write the root ``manifest.md`` for the campaign (C2)."""
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines: list[str] = []
    lines.append(f"# {campaign_name} — Campaign Manifest")
    lines.append("")
    lines.append(f"**Campaign:** `{campaign_slug}`  ")
    lines.append(f"**Ruleset:** `{ruleset}`  ")
    lines.append(f"**Setting:** `{setting_slug}`  ")
    lines.append(f"**Created:** {now_iso}  ")
    lines.append("")
    lines.append("## Campaign Files")
    lines.append("")
    lines.append("- [Handoff](handoff.md) — Single source of truth for DM Agent boot")
    lines.append("- [Status](status.json) — Campaign state machine")
    lines.append("")

    # Acts
    lines.append("## Acts")
    lines.append("")
    for af in sorted(act_files, key=lambda p: p.name):
        act_num = af.stem  # e.g. "act-1"
        act_label = act_num.replace("-", " ").title()  # "Act 1"
        lines.append(f"- [{act_label}](acts/{af.name})")
    lines.append("")

    # Characters
    lines.append("## Characters")
    lines.append("")
    for slug in pc_slugs:
        name = pc_names.get(slug, _get_display_name(slug))
        lines.append(f"- [{name}](characters/{slug}.md)")
    for slug in companion_slugs:
        name = companion_names.get(slug, _get_display_name(slug))
        lines.append(f"- [{name} (Companion)](characters/{slug}.md)")
    lines.append("")

    # Session
    lines.append("## Session")
    lines.append("")
    lines.append("- [Session directory](session/) — Changelogs and act summaries")
    lines.append("")

    # CS delegation
    lines.append("## Campaign Setting")
    lines.append("")
    lines.append(f"The Campaign Setting is a full fork of `{setting_slug}` at handoff time.")
    # Try MANIFEST.md first (actual CS uses uppercase), fall back to manifest.md.
    # On case-insensitive filesystems (Windows), Path.exists() can't distinguish
    # between the two, so we check directory contents explicitly.
    cs_manifest = f"{setting_slug}/MANIFEST.md"
    setting_dir = campaign_dir / setting_slug
    dir_contents = {p.name for p in setting_dir.iterdir()} if setting_dir.is_dir() else set()
    if "MANIFEST.md" not in dir_contents and "manifest.md" in dir_contents:
        cs_manifest = f"{setting_slug}/manifest.md"
    elif "MANIFEST.md" not in dir_contents and "MANIFEST.md" not in dir_contents:
        # Neither exists — still link to MANIFEST.md as the canonical name
        pass
    lines.append(f"See [{cs_manifest}]({cs_manifest}) for the complete CS file index.")
    lines.append("")

    (campaign_dir / "manifest.md").write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote manifest.md for campaign '%s'", campaign_slug)


def _write_status_atomic(
    campaign_dir: Path,
    campaign_slug: str,
    now_iso: str,
) -> None:
    """Atomically write ``status.json`` with initial ``"building"`` state (M1)."""
    data: dict[str, Any] = {
        "status": "building",
        "current_act": 1,
        "last_session_id": None,
        "locked": False,
        "campaign_slug": campaign_slug,
        "created": now_iso,
        "modified": now_iso,
    }

    tmp_path = campaign_dir / ".status.json.tmp"
    final_path = campaign_dir / "status.json"
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp_path, final_path)  # atomic on same filesystem
    logger.info("Wrote status.json (building) for campaign '%s'", campaign_slug)


def _update_status(
    campaign_dir: Path,
    *,
    status: str,
    last_session_id: str | None = None,
) -> None:
    """Atomically update ``status.json`` (promote building → active, etc.).

    Reads the current status, updates the given fields, and writes
    back atomically.
    """
    status_path = campaign_dir / "status.json"
    if status_path.exists():
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    now_iso = datetime.now(UTC).isoformat()
    data["status"] = status
    data["modified"] = now_iso
    if last_session_id is not None:
        data["last_session_id"] = last_session_id
    if "created" not in data:
        data["created"] = now_iso
    if "campaign_slug" not in data:
        data["campaign_slug"] = ""

    tmp_path = campaign_dir / ".status.json.tmp"
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp_path, status_path)


def _write_handoff(
    campaign_dir: Path,
    campaign_name: str,
    campaign_slug: str,
    ruleset: str,
    setting_slug: str,
    plan_slug: str,
    difficulty: str,
    tone: str,
    boundaries: list[str],
    freeform_requests: str,
    pc_slugs: list[str],
    companion_slugs: list[str],
    pc_names: dict[str, str],
    companion_names: dict[str, str],
) -> Path:
    """Write ``handoff.md`` — the single source of truth for DM Agent boot (M2)."""
    # Formatting helpers per M2
    boundaries_formatted = ", ".join(boundaries) if boundaries else "None — maximum explicitness"

    if pc_slugs:
        chars_f = ", ".join(pc_names.get(s, _get_display_name(s)) for s in pc_slugs)
    else:
        chars_f = "None (add during play)"

    if companion_slugs:
        comps_f = ", ".join(companion_names.get(s, _get_display_name(s)) for s in companion_slugs)
    else:
        comps_f = "Solo run (no companions)"

    freeform_f = freeform_requests if freeform_requests.strip() else "None"

    lines: list[str] = []
    lines.append("# Campaign Handoff — Builder → DM Agent")
    lines.append("")
    lines.append(f"**Campaign:** {campaign_name}  ")
    lines.append(f"**Ruleset:** {ruleset}  ")
    lines.append(f"**Setting:** {setting_slug}  ")
    lines.append(f"**Plan:** {plan_slug}  ")
    lines.append("**Status:** First boot — Act 1  ")
    lines.append("")
    lines.append("## Player Choices")
    lines.append("")
    lines.append(f"- **Difficulty:** {difficulty}")
    lines.append(f"- **Tone:** {tone}")
    lines.append(f"- **Content Boundaries:** {boundaries_formatted}")
    lines.append(f"- **Player Characters:** {chars_f}")
    lines.append(f"- **Companions:** {comps_f}")
    lines.append("")
    lines.append("## Freeform Requests")
    lines.append("")
    lines.append(freeform_f)
    lines.append("")
    lines.append("## Starting Point")
    lines.append("")
    lines.append(
        "Act 1. Read `acts/act-1.md` for the full plan.  "
        "Narrate the opening scene and invite the player to act."
    )
    lines.append("")
    lines.append("## DM Instructions")
    lines.append("")
    lines.append(
        '- Use second-person narration ("You...") — never refer to the player by character name'
    )
    lines.append("- Adhere to the content boundaries above. If none: push to maximum explicitness.")
    lines.append(f"- The tone is {tone}. Maintain it consistently.")
    lines.append(f"- Combat difficulty: {difficulty}.")
    lines.append(
        f"- Refer to `{setting_slug}/DM-SECRETS.md` for locked truths that "
        "must not be revealed prematurely."
    )
    lines.append("- Track narrative developments in `session/act-1-changelog.md`.")
    lines.append("- On Act completion, rewrite this handoff.md for Act N+1.")
    lines.append("")

    handoff_path = campaign_dir / "handoff.md"
    handoff_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote handoff.md for campaign '%s'", campaign_slug)
    return handoff_path


def _write_act_changelog(campaign_dir: Path, act_num: int) -> None:
    """Write an empty act changelog template in ``session/``."""
    lines = [
        f"# Act {act_num} Changelog",
        "",
        "> Agent-maintained running log during play.",
        "> Narrative developments, NPC relationship changes, player decisions,"
        " emergent world state.",
        "",
    ]
    path = campaign_dir / "session" / f"act-{act_num}-changelog.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.debug("Wrote changelog template %s", path)
