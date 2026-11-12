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
    assert sum(len(item["editions"]) for item in subjects) == 482
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
        "severed-npc-lord-merrick", "severed-npc-lord-voss", "severed-npc-castellan-voss",
        "severed-npc-iron-mother-hest", "severed-npc-prospector-joss", "severed-npc-kell",
        "severed-npc-councillor-merrow", "severed-npc-deacon-ash", "severed-npc-doctor-cipher",
        "severed-npc-father-glass", "severed-npc-fortune-teller", "severed-npc-gareth-red-banner",
        "severed-npc-healer-cassian", "severed-npc-hollowed-prophet",
        "severed-npc-librarian-vesper",
        "severed-npc-margin-herbalist", "severed-npc-market-barker-kestrel",
        "severed-npc-nexus-guildmaster",
        "severed-npc-nexus-innkeep", "severed-npc-orin-thatch",
        "severed-npc-quarter-merchant-rook",
        "severed-npc-salt-ash", "severed-npc-scaled-hunter", "severed-npc-scribe-apprentice",
        "severed-npc-sister-candle", "severed-npc-sister-iron", "severed-npc-tavern-bard-echo",
        "severed-npc-the-archivist", "severed-npc-the-glass-monk", "severed-npc-the-inkwell",
        "severed-npc-the-silent", "severed-npc-tide-mother", "severed-npc-undercroft-guide",
        "severed-npc-war-captain-korr", "severed-npc-war-captain-vex",
        "severed-monster-anchor-trap", "severed-monster-broken-one",
        "severed-monster-cave-hollowed",
        "severed-monster-cavern-hollowed", "severed-monster-chitin-riftborn",
        "severed-monster-deep-dwarf", "severed-monster-door-guardian",
        "severed-monster-field-warden", "severed-monster-hollow-brute",
        "severed-monster-hollowed-enforcer", "severed-monster-hollowling",
        "severed-monster-pack-hollowed", "severed-monster-pond-hollowed",
        "severed-monster-rift-air", "severed-monster-rift-banshee",
        "severed-monster-rift-boulder", "severed-monster-rift-bulette",
        "severed-monster-rift-centaur", "severed-monster-rift-cinder",
        "severed-monster-rift-cockatrice", "severed-monster-rift-cyclops",
        "severed-monster-rift-doppelganger", "severed-monster-rift-dust",
        "severed-monster-rift-earth", "severed-monster-rift-ettercap",
        "severed-monster-rift-ettin", "severed-monster-rift-fire",
        "severed-monster-rift-flameskull", "severed-monster-rift-forge",
        "severed-monster-rift-fungus", "severed-monster-rift-fungus-violet",
        "severed-monster-rift-ghast", "severed-monster-rift-ghost",
        "severed-monster-rift-giant-cloud", "severed-monster-rift-giant-fire",
        "severed-monster-rift-giant-frost", "severed-monster-rift-giant-hill",
        "severed-monster-rift-giant-stone", "severed-monster-rift-giant-storm",
        "severed-monster-rift-ghoul", "severed-monster-rift-gorgon",
        "severed-monster-rift-grick", "severed-monster-rift-griffon",
        "severed-monster-rift-harpy", "severed-monster-rift-hippogriff",
        "severed-monster-rift-hydra", "severed-monster-rift-ice",
        "severed-monster-rift-jelly", "severed-monster-rift-kraken",
        "severed-monster-rift-lamia", "severed-monster-rift-lich",
        "severed-monster-rift-magma", "severed-monster-rift-medusa",
        "severed-monster-rift-merrow", "severed-monster-rift-mimic",
        "severed-monster-rift-minotaur", "severed-monster-rift-mud",
        "severed-monster-rift-mummy", "severed-monster-rift-mummy-lord",
        "severed-monster-rift-ogre", "severed-monster-rift-oni",
        "severed-monster-rift-ooze", "severed-monster-rift-owlbear",
        "severed-monster-rift-peryton", "severed-monster-rift-phase-spider",
        "severed-monster-rift-piercer", "severed-monster-rift-pudding",
        "severed-monster-rift-purple-worm", "severed-monster-rift-raider",
        "severed-monster-rift-remorhaz", "severed-monster-rift-revenant",
        "severed-monster-rift-roc", "severed-monster-rift-roper",
        "severed-monster-rift-rust-monster", "severed-monster-rift-salamander",
        "severed-monster-rift-seeker", "severed-monster-rift-shadow",
        "severed-monster-rift-shrieker", "severed-monster-rift-skeleton",
        "severed-monster-rift-smoke", "severed-monster-rift-specter",
        "severed-monster-rift-spore", "severed-monster-rift-stalker",
        "severed-monster-rift-steam", "severed-monster-rift-tarrasque",
        "severed-monster-rift-treant", "severed-monster-rift-troll",
        "severed-monster-rift-vampire",
        "severed-monster-rift-vampire-spawn",
        "severed-monster-rift-warhorse",
        "severed-monster-rift-water",
        "severed-monster-rift-weird",
        "severed-monster-rift-wight",
        "severed-monster-rift-winter-wolf",
        "severed-monster-rift-wisp",
        "severed-monster-rift-worg",
        "severed-monster-rift-wraith",
        "severed-monster-rift-xorn",
        "severed-monster-rift-yeti",
        "severed-monster-rift-zombie",
        "severed-monster-rifthollowed-bear",
        "severed-monster-rifthollowed-boar",
        "severed-monster-rifthollowed-rat",
        "severed-monster-rifthollowed-tiger",
        "severed-monster-rifthollowed-wolf",
        "severed-monster-rifthound",
        "severed-monster-rifthunter",
        "severed-monster-rifthunter-priestess",
        "severed-monster-riftpond-scavenger",
        "severed-monster-serpent-hollowed",
        "severed-monster-tunnel-wretch",
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
