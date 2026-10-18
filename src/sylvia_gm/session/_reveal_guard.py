"""RevealGuard — anti-spoiler guardrails for DM sessions.

Manages the reveal registry and builds the DO NOT REVEAL section for the
DM system prompt.  Replaces the spoil_confirm widget (C5 FIX) with a
text-based !spoil command flow that works in DM session mode.

V4-L1: Constructor accepts ``registry_path: Path`` and stores
``self._registry_path``.  Caller passes ``planning_dir / "reveal_registry.json"``.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from sylvia_gm.planning._reveal_registry import RevealRegistry, SecretEntry

logger = logging.getLogger(__name__)


class RevealGuard:
    """Manages the reveal registry and builds the DO NOT REVEAL section.

    Replaces the spoil_confirm widget (C5 FIX) with a text-based
    confirmation flow that works in DM session mode.
    """

    def __init__(self, registry_path: Path) -> None:
        """Initialise the guard with a path to ``reveal_registry.json``.

        V4-L1: Accepts ``registry_path`` directly (not a parsed registry).
        The caller passes ``planning_dir / "reveal_registry.json"``.
        """
        self._registry_path: Path = Path(registry_path)
        self._pending_spoil: str | None = None  # secret ID awaiting confirmation
        self._spoil_override_active: bool = False
        self._revealed_in_session: set[str] = set()

    # ── Registry loading ──────────────────────────────────────────────

    def load_registry(self) -> RevealRegistry:
        """Load and parse the RevealRegistry from disk.

        Returns:
            Parsed RevealRegistry object.  Returns an empty registry
            (with ``campaign_id=""``) when the file is missing or unreadable.
        """
        if not self._registry_path.exists():
            logger.warning(
                "Reveal registry not found at %s — guardrails inactive",
                self._registry_path,
            )
            return RevealRegistry(campaign_id="unknown", secrets=[])

        try:
            raw = self._registry_path.read_text()
            return RevealRegistry.model_validate_json(raw)
        except Exception as exc:
            logger.warning(
                "Failed to parse reveal registry at %s: %s",
                self._registry_path, exc,
            )
            return RevealRegistry(campaign_id="unknown", secrets=[])

    # ── DO NOT REVEAL section ─────────────────────────────────────────

    def build_do_not_reveal_section(self) -> str:
        """Build the DO NOT REVEAL section for the DM system prompt.

        Lists all secrets with ``revealed=False``.  Capped at 2000 chars.
        V4-L5: Returns ``""`` when there are no hidden secrets — callers
        should omit the section entirely.

        Returns:
            The DO NOT REVEAL markdown string, or ``""`` if empty.
        """
        registry = self.load_registry()
        hidden = [s for s in registry.secrets if not s.revealed]
        if not hidden:
            return ""

        lines = [
            "## 🚫 DO NOT REVEAL (Anti-Spoiler Guardrails)",
            "The following secrets MUST NOT be revealed or hinted at by NPC dialogue,",
            "environmental description, or any other means. The player must discover",
            "these through play. If a player action would naturally uncover a secret,",
            "pause and ask the player to confirm with the !spoil command.",
            "",
        ]

        # Truncate to top-N by criticality
        critical = [s for s in hidden if s.consequences_if_revealed_early]
        normal = [s for s in hidden if not s.consequences_if_revealed_early]

        for s in critical[:10] + normal[:5]:
            lines.append(
                f"- **{s.title}** (chapter {s.planted_in_chapter}): "
                f"{s.summary[:200]}"
            )
            if s.consequences_if_revealed_early:
                lines.append(
                    f"  ⚠️ Consequence of early reveal: "
                    f"{s.consequences_if_revealed_early[:150]}"
                )

        result = "\n".join(lines)
        # Truncate to 2000 chars max
        if len(result) > 2000:
            result = result[:1997] + "..."
        return result

    # ── !spoil confirmation flow ──────────────────────────────────────

    def attempt_spoil(self, secret_id: str) -> str:
        """Initiate the !spoil confirmation flow.

        Returns a text-based confirmation prompt instead of a widget (C5 FIX).

        Args:
            secret_id: The secret ID to attempt revealing.

        Returns:
            A confirmation prompt string.
        """
        registry = self.load_registry()
        secret = next(
            (s for s in registry.secrets if s.id == secret_id), None
        )
        if not secret:
            available = ", ".join(s.id for s in registry.secrets)
            return (
                f"⚠️ Unknown secret ID: '{secret_id}'. "
                f"Available secrets: {available}"
            )

        if secret.revealed:
            return f"ℹ️ Secret '{secret.title}' is already revealed."

        self._pending_spoil = secret_id
        return (
            f"⚠️ **SPOIL CONFIRMATION REQUIRED**\n\n"
            f"You are about to reveal: **{secret.title}**\n"
            f"Summary: {secret.summary}\n"
            f"Consequence: {secret.consequences_if_revealed_early or 'None specified'}\n\n"
            f"To confirm, type exactly: `!spoil confirm {secret_id}`\n"
            f"To cancel, type: `!spoil cancel`"
        )

    def confirm_spoil(self, secret_id: str) -> str:
        """Confirm the spoil. Marks the secret as revealed and persists to disk.

        V4-L1 / NEW-3: Calls ``_save_registry()`` for atomic persistence.
        """
        if self._pending_spoil != secret_id:
            return "⚠️ No pending spoil confirmation. Use !spoil <id> first."

        registry = self.load_registry()
        secret = next(
            (s for s in registry.secrets if s.id == secret_id), None
        )
        if not secret:
            self._pending_spoil = None
            return "⚠️ Secret not found."

        secret.revealed = True
        self._revealed_in_session.add(secret_id)
        self._pending_spoil = None
        self._spoil_override_active = False
        self._save_registry(registry)
        return (
            f"✅ Secret '{secret.title}' is now revealed. "
            "The DM may now reference it."
        )

    def cancel_spoil(self) -> str:
        """Cancel a pending spoil confirmation."""
        self._pending_spoil = None
        return "❌ Spoil cancelled."

    # ── Queries ───────────────────────────────────────────────────────

    def get_hidden_secrets(self) -> list[SecretEntry]:
        """Return all secrets still marked ``revealed=False``."""
        registry = self.load_registry()
        return [s for s in registry.secrets if not s.revealed]

    def get_revealed_in_session(self) -> set[str]:
        """Return the set of secret IDs revealed in this session."""
        return self._revealed_in_session

    # ── Persistence ───────────────────────────────────────────────────

    def _save_registry(self, registry: RevealRegistry) -> None:
        """Persist the registry to disk using an atomic write pattern (§9).

        Writes to a temp file then calls ``os.replace`` so readers never
        see a half-written file.
        """
        fd, tmp_path_str = tempfile.mkstemp(
            suffix=".json",
            prefix=".reveal-registry-",
            dir=self._registry_path.parent,
        )
        try:
            os.write(fd, registry.model_dump_json(indent=2).encode("utf-8"))
        finally:
            os.close(fd)

        tmp_path = Path(tmp_path_str)
        try:
            os.replace(tmp_path_str, str(self._registry_path))
        except Exception:
            # Clean up temp file on failure
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

        logger.debug(
            "Reveal registry persisted atomically to %s",
            self._registry_path,
        )
