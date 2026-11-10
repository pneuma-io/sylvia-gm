"""Production preparation preserves the actual authored cast and exact retained source editions."""

import hashlib
import json
from collections import Counter

from scripts.inventory_authored_artwork import authored_artwork_subjects

from sylvia_gm.application import bundled_character_art as library
from sylvia_gm.domain.package_library import PackageFile
from tests.test_inherited_character_art import character


def test_all_authored_subject_editions_bind_without_merging_group_members(tmp_path, monkeypatch):
    inventory = authored_artwork_subjects()
    subjects = inventory["subjects"]
    assert Counter(item["kind"] for item in subjects) == {"npc": 82, "monster": 136}
    assert sum(len(item["editions"]) for item in subjects) == 273
    assert len({item["id"] for item in subjects}) == 218
    assert len(inventory["excluded_sources"]) == 1
    assert inventory["excluded_sources"][0]["path"].endswith("rift-awakening-opposition.md")
    grouped = [item for item in subjects if item["id"].startswith("severed-npc-lace-brothers-")]
    assert {item["name"] for item in grouped} == {"Thread", "Hook", "Needle"}
    changed = {item["id"] for item in subjects if len(item["editions"]) > 1}
    assert changed == {
        "severed-npc-v", "severed-monster-anchor-sentinel",
        "severed-npc-the-cartographer", "severed-monster-anchor-guardian-flesh",
        "severed-npc-baker-tessa", "severed-npc-guard-miri", "severed-npc-captain-harlan",
        "severed-npc-auntie-salt", "severed-npc-alchemist-fume", "severed-npc-herbalist-saffron",
        "severed-monster-rift-death-hound", "severed-monster-rift-basilisk",
        "severed-monster-rift-darkmantle", "severed-monster-rift-cube",
        "severed-monster-rift-ankheg", "severed-monster-rift-behir",
        "severed-npc-patrol-veteran-stern", "severed-npc-researcher-linden",
        "severed-npc-marla-cook", "severed-npc-waystation-healer",
        "severed-npc-waystation-smith", "severed-npc-the-quartermaster",
        "severed-npc-pike-rookie", "severed-npc-old-crane", "severed-npc-rift-seeker-iro",
        "severed-npc-scavenger-vet-rigg", "severed-npc-streets-kit", "severed-npc-hunter-lorn",
        "severed-npc-lace-brothers-thread", "severed-npc-lace-brothers-hook",
        "severed-npc-lace-brothers-needle",
        "severed-npc-madame-thistle", "severed-npc-grim", "severed-npc-shrike",
        "severed-npc-maw", "severed-npc-tallow", "severed-npc-warrens-fence",
        "severed-npc-brother-ash", "severed-npc-mother-wick", "severed-npc-old-finch",
        "severed-npc-gavin-thatch", "severed-npc-orphanage-keeper", "severed-npc-ratcatcher",
        "severed-npc-archivist-apprentice", "severed-npc-signal-keeper", "severed-npc-candle-maker",
        "severed-npc-fishmonger-kale", "severed-npc-merchant-hennick", "severed-npc-tailor-lace",
        "severed-npc-innkeep-vesper", "severed-npc-stablemaster-corin", "severed-npc-guard-davith",
        "severed-npc-lady-kael", "severed-npc-lady-trask", "severed-npc-lord-dorn",
    }
    catalogue = {
        "format": 1,
        "characters": [
            character(item["id"] + "-" + edition["source"]["sha256"], edition["source"])
            for item in subjects
            for edition in item["editions"]
        ],
    }
    (tmp_path / "character-art.json").write_text(json.dumps(catalogue), encoding="utf-8")
    monkeypatch.setattr(library, "_art_root", lambda: tmp_path)
    for item in subjects:
        for edition in item["editions"]:
            source = edition["source"]
            raw = edition["document"].encode("utf-8")
            assert hashlib.sha256(raw).hexdigest() == source["sha256"]
            assert edition["origins"]
            descriptor = PackageFile(
                path=source["path"],
                byte_count=len(raw),
                sha256=source["sha256"],
            )
            result = library.bundled_source_visuals(
                source["package_id"],
                source["path"],
                descriptor,
                subject_name=item["name"],
                campaign_setting_ids=frozenset({source["campaign_setting_id"]}),
            )
            assert result["appearance"] == item["id"] + "-" + source["sha256"]
