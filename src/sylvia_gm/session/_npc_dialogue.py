"""NPC Dialogue Manager — injects NPC persona blocks into the LLM context.

T3.2: When an NPC is present in a scene, their persona is injected
as a system-level context block before the player input. The LLM uses
this to voice the NPC consistently.

v0.1 uses Option A per PRD §4.14: the DM agent voices all NPCs in
a single LLM context. NPC persona and knowledge boundaries are
injected as structured context blocks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sylvia_gm.state.state_api import StateAPI


class NPCDialogueManager:
    """Injects NPC persona blocks into the LLM context.

    When an NPC is present in a scene, their persona is injected
    as a system message before the player input. The LLM uses
    this to voice the NPC consistently.

    Secret knowledge (``is_secret=True``) is excluded from persona
    blocks so the LLM doesn't inadvertently reveal hidden information.
    """

    # ── Constants ────────────────────────────────────────────────────

    NPC_BLOCK_TEMPLATE = (
        "[NPC: {name}]\n"
        "Role: {role}\n"
        "Personality: {personality}\n"
        "Knowledge: {knowledge}\n"
        "Voice: {voice}\n"
        "Relationship to party: {relationship}\n"
        "[/NPC]"
    )

    KNOWLEDGE_SEPARATOR = ", "

    # ── Public API ────────────────────────────────────────────────────

    def get_persona_block(self, npc_id: str, store: "StateAPI") -> str:
        """Build a persona block for an NPC.

        Format::

            [NPC: Yuffie]
            Role: Party member, rogue
            Personality: Loud, bouncy, competitive, leans into emotions
            Knowledge: Knows about riftbeasts, doesn't know about hamsters
            Voice: No shyness, gets LOUDER not quieter, teases Sample Player
            Relationship to party: Close friends, competitive with Sample Player
            [/NPC]

        Args:
            npc_id: The NPC's unique ID.
            store: The StateAPI instance.

        Returns:
            A formatted persona block string.

        Raises:
            EntityNotFoundError: If the NPC doesn't exist.
        """
        npc = store.get_npc(npc_id)

        name = npc.get("name", npc_id)
        role = npc.get("role") or "Unknown role"
        personality = npc.get("personality") or "Not specified"
        voice = npc.get("voice") or "Not specified"
        faction = npc.get("faction")
        attitude = npc.get("attitude", "neutral")
        relationship_score = npc.get("relationship", 0)

        # Build relationship description
        relationship = self._describe_relationship(
            attitude=attitude,
            score=relationship_score,
            faction=faction,
        )

        # Gather non-secret knowledge
        knowledge_entries = self._get_visible_knowledge(npc_id, store)
        knowledge_str = self._format_knowledge(knowledge_entries)

        return self.NPC_BLOCK_TEMPLATE.format(
            name=name,
            role=role,
            personality=personality,
            knowledge=knowledge_str,
            voice=voice,
            relationship=relationship,
        )

    def get_active_npcs(self, store: "StateAPI", scene: str) -> list[str]:
        """Get the IDs of NPCs present in the current scene.

        Args:
            store: The StateAPI instance.
            scene: The current scene/location identifier.

        Returns:
            A list of NPC IDs present in the scene.
        """
        npcs = store.list_npcs(location=scene)
        return [n["id"] for n in npcs]

    def build_npc_blocks_for_scene(
        self, store: "StateAPI", scene: str
    ) -> list[str]:
        """Build persona blocks for all NPCs in a scene.

        Convenience method that combines ``get_active_npcs`` and
        ``get_persona_block`` into one call.

        Args:
            store: The StateAPI instance.
            scene: The current scene/location identifier.

        Returns:
            A list of formatted persona block strings.
        """
        blocks: list[str] = []
        for npc_id in self.get_active_npcs(store, scene):
            blocks.append(self.get_persona_block(npc_id, store))
        return blocks

    # ── Internal Helpers ─────────────────────────────────────────────

    def _get_visible_knowledge(
        self, npc_id: str, store: "StateAPI"
    ) -> dict[str, str]:
        """Collect all non-secret knowledge entries for an NPC.

        Args:
            npc_id: The NPC's unique ID.
            store: The StateAPI instance.

        Returns:
            A dict mapping knowledge keys to their values (secrets excluded).
        """
        npc = store.get_npc(npc_id)
        knowledge: dict[str, dict[str, object]] = npc.get("knowledge", {})
        visible: dict[str, str] = {}
        for key, entry in knowledge.items():
            if isinstance(entry, dict) and not entry.get("is_secret", False):
                visible[key] = str(entry.get("value", ""))
        return visible

    @staticmethod
    def _format_knowledge(entries: dict[str, str]) -> str:
        """Format knowledge entries into a readable string.

        Args:
            entries: Dict of key → description pairs.

        Returns:
            A comma-separated string of knowledge descriptions,
            or a placeholder if no entries exist.
        """
        if not entries:
            return "No special knowledge."
        return NPCDialogueManager.KNOWLEDGE_SEPARATOR.join(
            f"Knows about {key} ({value})" for key, value in entries.items()
        )

    @staticmethod
    def _describe_relationship(
        attitude: str,
        score: int,
        faction: str | None = None,
    ) -> str:
        """Build a human-readable relationship description.

        Args:
            attitude: NPC attitude (hostile, unfriendly, neutral, friendly, allied).
            score: Relationship score (-10 to +10).
            faction: NPC's faction name, if any.

        Returns:
            A string describing the NPC's relationship to the party.
        """
        parts: list[str] = []

        # Attitude
        attitude_labels: dict[str, str] = {
            "hostile": "Hostile to the party",
            "unfriendly": "Unfriendly toward the party",
            "neutral": "Neutral toward the party",
            "friendly": "Friendly toward the party",
            "allied": "Allied with the party",
        }
        parts.append(attitude_labels.get(attitude, f"Attitude: {attitude}"))

        # Score nuance
        if score > 3:
            parts.append(f"strong positive regard (score: {score})")
        elif score < -3:
            parts.append(f"strong negative regard (score: {score})")

        # Faction
        if faction:
            parts.append(f"Member of {faction}")

        return " — ".join(parts)
