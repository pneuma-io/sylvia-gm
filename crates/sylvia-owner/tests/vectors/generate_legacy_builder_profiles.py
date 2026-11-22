"""Generate Python-made profiles with and without former campaign-creation content chats.

Run from the repository root after `uv sync --extra dev`:

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_legacy_builder_profiles.py

Each profile is built with the set-up of `tests/test_builder_session_migration.py` (read, never
changed) and saved as a gzipped database under `legacy_builder/`, with what Python's own
`legacy_builder_contexts()` finds in it:
- `character-open`: a player-character content chat still open in its interview;
- `character-returned`: the same chat after it returned and the interview moved on;
- `customization`: a campaign-customization subflow, which is not reusable content and stays
  attached, so its profile has none.
Reference tooling for the port, retired with the Python source.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUTPUT = Path(__file__).with_name("legacy_builder")


def customization(owner) -> None:
    draft = owner.create_guided_campaign_draft(
        operation_id="customization-draft",
        display_name="Source context",
        builder_session_id="source-interview",
        builder_provider_id="test",
        builder_model_id="test",
    )
    for step, answer in (
        ("ruleset", {"candidate_id": None}),
        ("name", {"display_name": "Source context"}),
        ("boundaries_preferences", {"boundaries": "", "preferences": ""}),
    ):
        draft = owner.advance_guided_campaign_draft(
            operation_id=f"choose-{step}",
            campaign_id=draft.campaign_id,
            builder_session_id=draft.builder_session_id,
            expected_construction_revision=draft.construction_revision,
            current_step=step,
            visible_answer=step,
            answer=answer,
        )
    owner.start_guided_creation_subflow(
        operation_id="customize",
        campaign_id=draft.campaign_id,
        parent_session_id=draft.builder_session_id,
        expected_construction_revision=draft.construction_revision,
        purpose="campaign_customization",
        visible_answer="Move the starting town",
        child_session_id="customize-chat",
    )


def main() -> None:
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    from sylvia_gm.application.authority import AuthorityOwner
    from tests.test_builder_session_migration import seed_legacy_character

    OUTPUT.mkdir(exist_ok=True)
    profiles = {}
    for name in ("character-open", "character-returned", "customization"):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "authority.sqlite3"
            with AuthorityOwner.open(database, f"seed-{name}") as owner:
                if name == "customization":
                    customization(owner)
                else:
                    seed_legacy_character(owner, Path(folder), returned=name == "character-returned")
                found = [context.session.session_id for context in owner.legacy_builder_contexts()]
            data = database.read_bytes()
            for marker in (folder.encode(), str(ROOT).encode()):
                assert marker not in data, "the profile names a local path"
            (OUTPUT / f"{name}.sqlite3.gz").write_bytes(gzip.compress(data, mtime=0))
            profiles[name] = {"sha256": hashlib.sha256(data).hexdigest(), "legacy_builder_contexts": found}
    (OUTPUT / "profiles.json").write_text(
        json.dumps({"python": sys.version.split()[0], "profiles": profiles}, indent=1) + "\n",
        encoding="utf-8", newline="\n",
    )


if __name__ == "__main__":
    main()
