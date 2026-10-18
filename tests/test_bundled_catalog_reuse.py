"""Repeated library reads reuse bundled validation without sharing mutable projections."""

from __future__ import annotations

from copy import deepcopy

import pytest

from sylvia_gm.application import bundled_packages


def test_bundled_catalog_reuses_validation_and_returns_owned_nested_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = bundled_packages.bundled_campaign_content_catalog()
    original = deepcopy(expected)
    expected_authority = bundled_packages.bundled_rift_authority()

    def unexpected_materialization(*_args: object, **_kwargs: object) -> object:
        pytest.fail("Repeated library projection rebuilt immutable bundled packages")

    monkeypatch.setattr(
        bundled_packages,
        "materialize_package_archive",
        unexpected_materialization,
    )
    second = bundled_packages.bundled_campaign_content_catalog()
    assert second == original
    assert second is not expected
    second[0]["title"] = "Caller-local title"
    plan = next(item for item in second if item["kind"] == "campaign_plan")
    plan["ruleset"]["id"] = "caller-local-ruleset"
    plan["starting_party"]["player_character"]["mode"] = "caller-local-mode"

    assert expected == original
    assert bundled_packages.bundled_campaign_content_catalog() == original
    assert bundled_packages.bundled_rift_authority() == expected_authority
