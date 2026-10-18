"""Scene Awareness — auto-inject location, NPC, and faction data into LLM context.

Provides:
  SceneAwarenessBuilder — reads current scene, fetches location/NPC/faction data
                          from Campaign Setting files and StateBridge, formats a
                          compact context block for injection before [STATE].
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default CS base path: ~/.sylvia-gm/campaign-settings/
_DEFAULT_CS_BASE = Path.home() / ".sylvia-gm" / "campaign-settings"

# Known faction summaries — hardcoded for Severed Lands as a fallback.
# When no faction files exist in CS, these provide basic context.
_FACTION_SUMMARIES: dict[str, str] = {
    "returners": (
        "The Returners are Riftborn who seek to reactivate the dormant Anchor Door "
        "beneath Waystation, believing it is their path home. Led by V, they operate "
        "from a fortified mountain pass."
    ),
    "thornwall council": (
        "The Thornwall Council is a body of five merchant families (Voss, Kael, Dorn, "
        "Merrick, Trask) that governs Thornwall. Conservative and trade-focused, they "
        "maintain careful distance from Riftborn."
    ),
    "anchor church": (
        "The Anchor Church venerates the Wardens as prophets and the Doors as sacred. "
        "Founded by a blacksmith named Iron, it has grown rapidly among Riftborn seeking "
        "spiritual meaning."
    ),
    "athenaeum": (
        "The Athenaeum is the Severed Lands' greatest repository of knowledge, a complex "
        "of libraries and research chambers in Nexus dedicated to the study of the Rift, "
        "the Wardens, and the Anchor Doors."
    ),
    "rift raiders": (
        "Rift Raiders are an entire company of soldiers pulled through the Rift mid-battle. "
        "They never stopped fighting, claiming the Cairnfields as their new warzone."
    ),
    "hollowed": (
        "The Hollowed are people transformed by concentrated Rift exposure — violet-eyed, "
        "fragmented of speech, shifting in perception. Some worship the Rift; others simply endure."
    ),
    "stayers": (
        "Stayers are Riftborn who have given up on returning home, building permanent "
        "communities in the Severed Lands instead. The Nexus Riftborn Quarter is their strongest foothold."
    ),
}


class SceneAwarenessBuilder:
    """Builds a scene-awareness context block for LLM prompt injection.

    Reads the current scene from a SceneManager, fetches location data from
    Campaign Setting (CS) markdown files, NPC data from StateBridge and CS,
    and faction data from CS lore or known summaries.

    The resulting context block is compact and designed to be injected before
    the [STATE] block in the LLM prompt.

    Usage::

        builder = SceneAwarenessBuilder()
        block = builder.build(
            scene_manager,
            state_bridge,
            ruleset="sylvia.srd-lite.bg3",
            slug="severed-lands",
        )
        # → "[SCENE: Thornwall]\\nLocation: ...\\nPresent NPCs: ..."
    """

    def __init__(self, cs_base_path: Path | str | None = None):
        """Initialize the builder.

        Args:
            cs_base_path: Base path for Campaign Setting files.
                          Defaults to ~/.sylvia-gm/campaign-settings/.
        """
        self._cs_base = Path(cs_base_path) if cs_base_path else _DEFAULT_CS_BASE

    # ── Public API ────────────────────────────────────────────────────────

    def build(
        self,
        scene_manager: Any,
        state_bridge: Any = None,
        ruleset: str = "sylvia.srd-lite.bg3",
        slug: str = "severed-lands",
    ) -> str:
        """Build a scene-awareness context block.

        Args:
            scene_manager: SceneManager instance with current_scene_id / current_scene.
            state_bridge: Optional StateBridge for entity data (NPCs).
            ruleset: Ruleset package id (e.g. "example.ruleset").
            slug: Campaign Setting slug (e.g. "severed-lands").

        Returns:
            A formatted context block string, or an empty string if no scene is active.
        """
        scene = getattr(scene_manager, "current_scene", None)
        if scene is None:
            return ""

        cs_loc = self._cs_base / ruleset / slug

        lines: list[str] = []

        # ── Location block ────────────────────────────────────────────
        location_block = self._build_location_block(scene, cs_loc)
        if location_block:
            lines.append(location_block)

        # ── NPC block ─────────────────────────────────────────────────
        npc_block = self._build_npc_block(scene, cs_loc, state_bridge)
        if npc_block:
            lines.append(npc_block)

        # ── Faction block ─────────────────────────────────────────────
        faction_block = self._build_faction_block(scene, cs_loc)
        if faction_block:
            lines.append(faction_block)

        return "\n\n".join(lines)

    # ── Location ──────────────────────────────────────────────────────────

    def _build_location_block(self, scene: Any, cs_loc: Path) -> str:
        """Build the location context section.

        Tries CS locations/*.md first, falls back to Scene dataclass fields.
        """
        scene_id: str = getattr(scene, "scene_id", "")
        scene_name: str = getattr(scene, "name", scene_id)

        # Try to read CS location file
        cs_data = self._read_location_cs(cs_loc, scene_id)

        if cs_data:
            lines = [f"[SCENE: {cs_data.get('name', scene_name)}]"]
            if cs_data.get("type"):
                lines.append(f"Type: {cs_data['type']}")
            if cs_data.get("region"):
                lines.append(f"Region: {cs_data['region']}")
            if cs_data.get("description"):
                lines.append(cs_data["description"])
            if cs_data.get("atmosphere"):
                lines.append(f"Atmosphere: {cs_data['atmosphere']}")
            return "\n".join(lines)

        # Fallback: use Scene dataclass fields
        desc = getattr(scene, "description", "") or ""
        location_type = getattr(scene, "location_type", "") or ""
        lighting = getattr(scene, "lighting", "") or ""
        time_of_day = getattr(scene, "time_of_day", "") or ""

        lines = [f"[SCENE: {scene_name}]"]
        if desc:
            lines.append(desc)
        extras = []
        if location_type:
            extras.append(f"Type: {location_type}")
        if lighting:
            extras.append(f"Lighting: {lighting}")
        if time_of_day:
            extras.append(f"Time: {time_of_day}")
        if extras:
            lines.append(" | ".join(extras))
        return "\n".join(lines)

    def _read_location_cs(self, cs_loc: Path, scene_id: str) -> dict[str, str] | None:
        """Read location data from CS locations/{scene_id}.md.

        Returns a dict with keys: name, type, region, description, atmosphere.
        Returns None if the file can't be read.
        """
        md_path = cs_loc / "locations" / f"{scene_id}.md"
        if not md_path.is_file():
            return None

        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception:
            logger.debug("Failed to read CS location: %s", md_path)
            return None

        return _parse_location_md(content)

    # ── NPCs ──────────────────────────────────────────────────────────────

    def _build_npc_block(
        self,
        scene: Any,
        cs_loc: Path,
        state_bridge: Any = None,
    ) -> str:
        """Build the present-NPCs context section.

        For each NPC listed in scene.npcs_present, fetches:
        - CS NPC file for personality line and role
        - StateBridge for entity data
        """
        npc_ids: list[str] = list(getattr(scene, "npcs_present", []) or [])
        if not npc_ids:
            return ""

        npc_lines: list[str] = []
        for npc_id in npc_ids:
            npc_info = self._build_single_npc(npc_id, cs_loc, state_bridge)
            if npc_info:
                npc_lines.append(npc_info)

        if not npc_lines:
            return ""

        return "Present NPCs:\n" + "\n".join(npc_lines)

    def _build_single_npc(
        self,
        npc_id: str,
        cs_loc: Path,
        state_bridge: Any = None,
    ) -> str:
        """Build a single NPC line item.

        Format: - Name (Role) — Disposition | One-line personality
        """
        # Try CS NPC file first (by npc_id or by mapping common names)
        cs_data = self._read_npc_cs(cs_loc, npc_id)

        name = npc_id
        role = ""
        disposition = ""
        personality = ""

        if cs_data:
            name = cs_data.get("name", npc_id)
            role = cs_data.get("role", "")
            disposition = cs_data.get("disposition", "")
            personality = cs_data.get("personality", "")

        # Fallback: try StateBridge for entity data
        if state_bridge is not None and not cs_data:
            try:
                if state_bridge.entity_exists(npc_id):
                    entity = state_bridge.get_entity(npc_id)
                    name = entity.get("name", npc_id)
                    # Use kind/archetype as role if available
                    kind = entity.get("kind", "")
                    archetype = entity.get("archetype", "")
                    if archetype and archetype != "n/a":
                        role = archetype
                    elif kind:
                        role = kind.capitalize()
            except Exception:
                pass

        # Build the line
        parts = [f"- {name}"]
        if role:
            parts.append(f"({role})")

        suffix_parts = []
        if disposition:
            suffix_parts.append(disposition)
        if personality:
            suffix_parts.append(personality)

        line = " ".join(parts)
        if suffix_parts:
            line += " — " + " | ".join(suffix_parts)

        return line

    def _read_npc_cs(self, cs_loc: Path, npc_id: str) -> dict[str, str] | None:
        """Read NPC data from CS npcs/{npc_id}.md.

        Tries exact match first, then a 'slugified' match (hyphens for spaces).
        Returns a dict with keys: name, role, disposition, personality.
        Returns None if the file can't be read.
        """
        # Try exact filename match
        md_path = cs_loc / "npcs" / f"{npc_id}.md"
        if not md_path.is_file():
            # Try slugified: replace underscores/spaces with hyphens
            slug = npc_id.lower().replace("_", "-").replace(" ", "-")
            md_path = cs_loc / "npcs" / f"{slug}.md"

        if not md_path.is_file():
            return None

        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception:
            logger.debug("Failed to read CS NPC: %s", md_path)
            return None

        return _parse_npc_md(content)

    # ── Factions ──────────────────────────────────────────────────────────

    def _build_faction_block(self, scene: Any, cs_loc: Path) -> str:
        """Build the controlling-faction context section.

        Checks scene.props and scene.meta for a 'controlling_faction' key.
        Tries CS lore file, falls back to known summaries.
        """
        props: dict = getattr(scene, "props", {}) or {}
        meta: dict = getattr(scene, "meta", {}) or {}

        faction_name = props.get("controlling_faction") or meta.get("controlling_faction")
        if not faction_name:
            return ""

        # Try CS-based faction summary
        summary = self._read_faction_cs(cs_loc, faction_name)
        if not summary:
            # Fallback to known summaries
            summary = _FACTION_SUMMARIES.get(faction_name.lower())

        if summary:
            return f"Controlling Faction — {faction_name}:\n{summary}"

        return f"Controlling Faction: {faction_name}"

    def _read_faction_cs(self, cs_loc: Path, faction_name: str) -> str | None:
        """Try to find faction data in CS files.

        Checks: lore/*.md, dm-tools/*.md, DM-SECRETS.md for faction mentions.
        Returns a brief summary string or None.
        """
        # Simple approach: search lore/history-timeline.md for faction mentions
        lore_files = [
            cs_loc / "lore" / "history-timeline.md",
            cs_loc / "DM-SECRETS.md",
        ]
        for lf in lore_files:
            if not lf.is_file():
                continue
            try:
                content = lf.read_text(encoding="utf-8")
            except Exception:
                continue

            # Look for a section about this faction
            # Patterns: "## The Returners", "**The Returners**", etc.
            pattern = re.compile(
                rf"(?:^|\n)#{{1,3}}\s+[^\n]*{re.escape(faction_name)}[^\n]*\n"
                rf"((?:(?!\n#{{1,3}}\s).|\n)*)",
                re.IGNORECASE,
            )
            match = pattern.search(content)
            if match:
                text = match.group(1).strip()
                # Take first ~300 chars as summary
                if len(text) > 300:
                    text = text[:300].rsplit(".", 1)[0] + "."
                return text

        return None


# ---------------------------------------------------------------------------
# CS file parsing helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML-like frontmatter from CS markdown files.

    Frontmatter is delimited by --- lines at the top of the file.
    Returns (frontmatter_dict, body_text).
    """
    fm: dict[str, str] = {}
    body = content

    stripped = content.lstrip()
    if stripped.startswith("---"):
        # Find closing ---
        rest = stripped[3:]
        end_idx = rest.find("\n---")
        if end_idx != -1:
            fm_block = rest[:end_idx]
            body = rest[end_idx + 4:].strip()
            # Parse simple key: "value" lines
            for line in fm_block.strip().split("\n"):
                line = line.strip()
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip().lower()
                    val = val.strip().strip('"').strip("'")
                    fm[key] = val

    return fm, body


def _parse_location_md(content: str) -> dict[str, str]:
    """Parse a CS location markdown file.

    Extracts: name (from # heading), type, region, overview (description),
    and atmosphere.
    """
    result: dict[str, str] = {}

    # Parse frontmatter if present
    fm, body = _parse_frontmatter(content)
    if fm:
        result.update(fm)

    # Extract # Heading as name
    heading_match = re.match(r"^#\s+(.+)$", body, re.MULTILINE)
    if heading_match:
        result.setdefault("name", heading_match.group(1).strip())

    # Extract **Type:** line
    type_match = re.search(r"\*\*Type:\*\*\s*(.+)$", body, re.MULTILINE)
    if type_match:
        result["type"] = type_match.group(1).strip()

    # Extract **Region:** line
    region_match = re.search(r"\*\*Region:\*\*\s*(.+)$", body, re.MULTILINE)
    if region_match:
        result["region"] = region_match.group(1).strip()

    # Extract ## Overview section as description (first paragraph)
    overview_match = re.search(
        r"^##\s+Overview\s*\n\n((?:(?!^##\s).|\n)+?)(?=\n##\s|\Z)",
        body,
        re.MULTILINE,
    )
    if overview_match:
        overview_text = overview_match.group(1).strip()
        # Take the first paragraph
        first_para = overview_text.split("\n\n")[0].strip()
        # Remove markdown links but keep text
        first_para = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", first_para)
        result["description"] = first_para

    # Extract ## Atmosphere section (first paragraph)
    atmos_match = re.search(
        r"^##\s+Atmosphere\s*\n\n((?:(?!^##\s).|\n)+?)(?=\n##\s|\Z)",
        body,
        re.MULTILINE,
    )
    if atmos_match:
        atmos_text = atmos_match.group(1).strip()
        first_para = atmos_text.split("\n\n")[0].strip()
        result["atmosphere"] = first_para

    return result


def _parse_npc_md(content: str) -> dict[str, str]:
    """Parse a CS NPC markdown file.

    Extracts: name, role, disposition (from frontmatter or body),
    and a one-line personality from the Personality section.
    """
    result: dict[str, str] = {}

    # Frontmatter has structured data
    fm, body = _parse_frontmatter(content)
    if fm:
        result["name"] = fm.get("name", "")
        result["role"] = fm.get("role", "")
        result["disposition"] = fm.get("disposition", "")
        if "species" in fm:
            result["species"] = fm["species"]

    # Extract # Heading as name fallback
    heading_match = re.match(r"^#\s+(.+)$", body, re.MULTILINE)
    if heading_match and not result.get("name"):
        result["name"] = heading_match.group(1).strip()

    # Extract ## Personality section — first sentence
    pers_match = re.search(
        r"^##\s+Personality\s*\n\n((?:(?!^##\s).|\n)+?)(?=\n##\s|\Z)",
        body,
        re.MULTILINE,
    )
    if pers_match:
        pers_text = pers_match.group(1).strip()
        # Take first sentence
        first_sentence = re.split(r"(?<=[.!?])\s+", pers_text)[0].strip()
        result["personality"] = first_sentence

    return result
