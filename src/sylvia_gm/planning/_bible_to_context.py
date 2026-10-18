"""Bible-to-context translator — CampaignBible → PromptAssembler campaign_plan.

Translates the planning output (CampaignBible + chapter_*.md files) into
the ``campaign_plan`` dict consumed by ``PromptAssembler.build_context()``.

This is how the DM knows about NPCs, locations, and quests (H10 FIX).

V4-L3: Result stored in StateAPI state under ``campaign.chapters`` via
``store.set_chapter(index, content)``.  Existing ``_build_context()`` reads
this unchanged.

V4-L4: Guarded against missing ``planning_dir`` — returns a sensible default
plan instead of crashing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sylvia_gm.planning._campaign_bible import CampaignBible

logger = logging.getLogger(__name__)


def bible_to_campaign_plan(
    bible: CampaignBible,
    planning_dir: Path,
    chapter_num: int = 1,
) -> dict[str, Any]:
    """Translate CampaignBible + chapter files into PromptAssembler's format.

    Chapters are loaded from ``planning_dir / "chapter_*.md"`` (separate files),
    NOT from ``bible.chapters`` (CampaignBible has no chapters field — NEW-1 FIX).

    V4-L4: When ``planning_dir`` does not exist, returns a sensible default
    campaign plan so the DM session can start without planning data.

    Args:
        bible: The parsed CampaignBible.
        planning_dir: Directory containing ``chapter_*.md`` files.
        chapter_num: The current chapter number (1-indexed).  Defaults to 1.

    Returns:
        A dict suitable for ``PromptAssembler.build_context()``.
    """
    # V4-L4: Guard — return default plan when planning_dir is missing
    if not planning_dir.exists():
        logger.info(
            "Planning directory %s does not exist — returning default campaign plan",
            planning_dir,
        )
        return _default_plan(bible)

    # Load chapter files from disk (sorted alphabetically → 01, 02, ...)
    chapters_data: list[dict[str, Any]] = []
    for ch_path in sorted(planning_dir.glob("chapter_*.md")):
        raw = ch_path.read_text(encoding="utf-8")
        fm_str = ""
        body = raw
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                fm_str = parts[1]
                body = parts[2]

        frontmatter: dict[str, Any] = {}
        if fm_str.strip():
            try:
                import yaml

                parsed = yaml.safe_load(fm_str)
                if isinstance(parsed, dict):
                    frontmatter = parsed
            except Exception as exc:
                logger.warning(
                    "Failed to parse YAML frontmatter in %s: %s",
                    ch_path.name, exc,
                )

        chapters_data.append(
            {"frontmatter": frontmatter, "body": body.strip()}
        )

    # Find current chapter
    current_chapter: dict[str, Any] | None = None
    for ch_data in chapters_data:
        fm = ch_data.get("frontmatter", {})
        if isinstance(fm, dict) and fm.get("chapter") == chapter_num:
            current_chapter = ch_data
            break

    # Build campaign plan dict
    campaign_name = bible.world.name if bible.world else "Unknown Campaign"
    current_scene = "Chapter 1"
    if current_chapter:
        fm = current_chapter.get("frontmatter", {})
        if isinstance(fm, dict):
            current_scene = fm.get("title", "Chapter 1")

    current_location = (
        bible.locations[0].name if bible.locations else "Unknown"
    )

    # Active NPCs: those listed in the current chapter's npcs_appearing
    active_npcs: list[str] = []
    if current_chapter:
        fm = current_chapter.get("frontmatter", {})
        if isinstance(fm, dict):
            npc_ids = fm.get("npcs_appearing", [])
            if isinstance(npc_ids, (list, tuple)):
                for npc in bible.npcs:
                    if npc.id in npc_ids:
                        active_npcs.append(npc.name)

    # Active quests: first few locations as exploration hooks
    active_quests = [
        f"Explore {loc.name}" for loc in bible.locations[:3]
    ]

    # Chapter list for the CONTEXT tier
    chapter_list: list[dict[str, Any]] = []
    for i, ch in enumerate(chapters_data):
        fm = ch.get("frontmatter", {})
        if not isinstance(fm, dict):
            fm = {}
        chapter_list.append({
            "title": fm.get("title", f"Chapter {i + 1}"),
            "summary": fm.get("synopsis", ""),
            "status": "active" if i == chapter_num - 1 else "compressed",
            "key_notes": fm.get("key_locations", [])[:3],
        })

    return {
        "campaign_name": campaign_name,
        "current_scene": current_scene,
        "current_location": current_location,
        "time_of_day": "morning",
        "day": 1,
        "active_quests": active_quests,
        "active_npcs": active_npcs,
        "recent_events": [],
        "chapters": chapter_list,
    }


def _default_plan(bible: CampaignBible) -> dict[str, Any]:
    """Return a minimal default campaign plan when no chapter files exist.

    Used as a fallback so DM sessions can start without pre-generated planning
    (backward compatibility — §12).
    """
    campaign_name = bible.world.name if bible.world else "Unknown Campaign"
    current_location = (
        bible.locations[0].name if bible.locations else "Unknown"
    )

    # Build a single default chapter from world data
    default_chapter = {
        "title": "Chapter 1",
        "summary": f"Begin your adventure in {current_location}.",
        "status": "active",
        "key_notes": [],
    }

    return {
        "campaign_name": campaign_name,
        "current_scene": "Chapter 1",
        "current_location": current_location,
        "time_of_day": "morning",
        "day": 1,
        "active_quests": [
            f"Explore {loc.name}" for loc in bible.locations[:3]
        ],
        "active_npcs": [npc.name for npc in bible.npcs[:5]],
        "recent_events": [],
        "chapters": [default_chapter],
    }
