"""Ruleset browsing defers party construction; complete discovery stays complete."""

from types import SimpleNamespace

from sylvia_gm.interfaces import owner_process as owner


def test_setup_catalog_defers_only_participants_and_default_discovery_is_complete(
    monkeypatch, tmp_path
):
    rules = {"id": "example.rules", "version": "1", "content_sha256": "a" * 64}
    setting = {"id": "example.setting", "version": "1", "content_sha256": "b" * 64}
    rows = (
        {
            "kind": "ruleset",
            **rules,
            "title": "Rules",
            "selection_id": rules["id"],
            "compatible_settings": (),
        },
        {"kind": "campaign_setting", **setting, "title": "Setting"},
    )
    monkeypatch.setattr(owner, "bundled_campaign_content_catalog", lambda: rows)
    monkeypatch.setattr(
        owner,
        "bundled_rift_authority",
        lambda: SimpleNamespace(
            ruleset=SimpleNamespace(as_payload=lambda: rules),
            setting=SimpleNamespace(as_payload=lambda: setting),
        ),
    )
    monkeypatch.setattr(owner, "character_profile_catalog", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(owner, "reusable_campaign_plan_catalog", lambda *_args, **_kwargs: ())
    calls = []

    def participants(*_args, **_kwargs):
        calls.append(True)
        return ({"kind": "player_character", "id": "a-real-validated-character"},)

    monkeypatch.setattr(owner, "_reusable_participant_catalog", participants)
    monkeypatch.setattr(owner, "participant_catalog_payload", lambda _owner, value: value)
    authority = SimpleNamespace(package_candidates=lambda: ())
    metadata = owner._campaign_creation_catalog(tmp_path, authority, include_participants=False)
    assert not calls
    complete = owner._campaign_creation_catalog(tmp_path, authority)
    assert calls == [True]
    assert complete == (*metadata, {"kind": "player_character", "id": "a-real-validated-character"})
