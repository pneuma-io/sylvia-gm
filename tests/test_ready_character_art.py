"""Prepared Ready editions and their editable copies reuse each authored character's images."""

import hashlib
import json
from importlib.resources import files

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.ready_campaigns import (
    customize_ready_campaign,
    ready_campaign_catalog,
    resolve_ready_campaign,
)
from sylvia_gm.domain.package_library import canonical_json_bytes


def test_ready_editions_and_customized_files_retain_the_same_prepared_art(tmp_path):
    source = files("sylvia_gm.campaign")
    original = source.joinpath("ready-campaigns.json").read_bytes()
    starters = {
        row["id"]: row for row in json.loads(source.joinpath("srd-starters.json").read_bytes())
    }
    for campaign in json.loads(original)["campaigns"]:
        for member in campaign["party"]:
            character = member.get("character")
            if character is None:
                continue  # The Plan's required Tavin source remains independently authored.
            _, identity, level = character["id"].split("-")
            starter = starters[identity]
            assert character["participant"]["name"] == starter["participant"]["name"]
            assert character["participant"]["level"] == int(level)
            assert character.get("visuals") == starter["visuals"]
            assert member["content_sha256"] == hashlib.sha256(
                canonical_json_bytes(character)
            ).hexdigest()
    row = next(item for item in ready_campaign_catalog() if item["id"] == "last-cart-through")
    with AuthorityOwner.open(tmp_path / "ready-art.db", "ready-art") as owner:
        _, party = resolve_ready_campaign(owner, tmp_path, row["id"], row["content_sha256"])
        expected = {item.name: item.visuals for item in party}
        assert expected == {
            "Bram Alder": starters["fighter"]["visuals"],
            "Oren Reed": starters["cleric"]["visuals"],
        }
        copied = customize_ready_campaign(
            owner, tmp_path, operation_id="edit-ready-party", template_id=row["id"],
            expected_content_sha256=row["content_sha256"],
        )
        for item in copied["characters"]:
            saved = tmp_path / "participants" / item["id"] / "payload.json"
            payload = json.loads(saved.read_bytes())
            assert payload["visuals"] == expected[item["name"]]
    assert source.joinpath("ready-campaigns.json").read_bytes() == original
