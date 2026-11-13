"""Required Setting characters reuse shared art without replacing authored or custom sources."""

import copy
import io
import json
from dataclasses import fields, replace
from pathlib import Path

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_character_art import bundled_source_visuals
from sylvia_gm.application.campaign_library_selection import (
    read_required_plan_characters,
    resolve_campaign_plan_selection,
)
from sylvia_gm.application.reusable_participants import parse_setting_character
from sylvia_gm.domain.authority import PackageRole
from sylvia_gm.domain.campaign_plan import parse_starting_party_policy

ROOT = Path(__file__).resolve().parents[1]


def test_required_character_reuses_exact_shared_art_and_preserves_custom_visuals(
    tmp_path, monkeypatch
):
    with AuthorityOwner.open(tmp_path / "owner.db", "required-character-art") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(
                (ROOT / "campaign-plans/a-debt-in-iron/setting.sylvia-gm-package").read_bytes()
            ),
            install_id="actual-lite",
        )
        selected = resolve_campaign_plan_selection(
            owner,
            tmp_path / "content",
            candidate_id=candidate.candidate_id,
            setting_id="pneuma.severed-lands",
            plan_id="a-debt-in-iron",
        )
        prepared = selected.required_participants[0]
        assert prepared.name == "Tavin Bell"
        primary = next(p for p in candidate.package_lock.entries if p.role is PackageRole.PRIMARY)
        declarations = owner.exact_participant_declarations(
            primary.package_id,
            primary.version,
            primary.sha256,
        )
        declaration = next(d for d in declarations if d.schema_id == prepared.schema_id)
        source = selected.sources.read_setting("characters/tavin-bell.json")
        original = json.loads(source)
        assert "visuals" not in original
        parsed = parse_setting_character(
            source,
            character_id="tavin-bell",
            role="player_character",
            package=primary,
            declaration=declaration,
            campaign_setting=selected.sources.setting,
            setting_character_schema=selected.sources.setting_character_schema,
        )
        assert parsed.visuals is None
        expected = bundled_source_visuals(
            selected.sources.setting.package_id,
            "characters/tavin-bell.json",
            source.encode("utf-8"),
            subject_name=prepared.name,
            campaign_setting_ids=frozenset({selected.sources.setting.package_id}),
        )
        assert expected is not None and prepared.visuals == expected
        for field in fields(prepared):
            if field.name != "visuals":
                assert getattr(prepared, field.name) == getattr(parsed, field.name)
        policy = parse_starting_party_policy(selected.plan.plan.payload["starting_party"])

        supplied = {
            "appearance": "Player-selected portrait; leave missing reference views unfilled.",
            "portrait": copy.deepcopy(expected["portrait"]),
            "frame": {"x": 0.31, "y": 0.42, "zoom": 1.35},
            "status": "partial",
            "missing": ["full_body", "reference_sheet"],
        }
        explicit = {**original, "visuals": supplied}

        def must_not_lookup(*args, **kwargs):
            raise AssertionError("Embedded player art must take precedence over shared defaults")

        with monkeypatch.context() as patch:
            patch.setattr(
                "sylvia_gm.application.campaign_library_selection.bundled_source_visuals",
                must_not_lookup,
            )
            preserved = read_required_plan_characters(
                replace(selected.sources, read_setting=lambda path: json.dumps(explicit)),
                policy,
                primary,
                (declaration,),
                require_complete=True,
            )[0]
        assert preserved.visuals == supplied
        assert preserved.participant == prepared.participant

        changed = {
            **original,
            "authored_profile": original["authored_profile"] + "\nPlayer chose a different look.",
        }
        with monkeypatch.context() as patch:
            unmatched = read_required_plan_characters(
                replace(selected.sources, read_setting=lambda path: json.dumps(changed)),
                policy,
                primary,
                (declaration,),
                require_complete=True,
            )[0]
        assert unmatched.visuals is None
        assert unmatched.content_sha256 != prepared.content_sha256
        assert unmatched.artifact_id == prepared.artifact_id
