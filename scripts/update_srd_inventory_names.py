"""Rebuild the Lite Inventory's display data and materialize its versioned package record."""

import json
import zipfile
from dataclasses import replace

from scripts.srd_lite_catalogue import LITE, VARIANT_VERSION, write_catalogue

from sylvia_gm.application.bundled_packages import _srd_source_drafts
from sylvia_gm.application.package_materialization import materialize_package_archive

# SRD 5.2.1 Equipment, starting equipment choices and material spell components.
GEAR = {
    "18_gp": "18 GP",  # Tavin Bell's starting purse in the bundled Plan snapshot.
    "arcane_focus": "Arcane Focus",
    "arrow": "Arrow",
    "book_history": "Book (History)",
    "book_occult_lore": "Book (Occult Lore)",
    "book_prayers": "Book (Prayers)",
    "burglars_pack": "Burglar's Pack",
    "calligraphers_supplies": "Calligrapher's Supplies",
    "crowbar": "Crowbar",
    "druidic_focus": "Druidic Focus",
    "dungeoneers_pack": "Dungeoneer's Pack",
    "entertainers_pack": "Entertainer's Pack",
    "explorers_pack": "Explorer's Pack",
    "flute": "Flute",
    "gaming_set_dice": "Dice",
    "gp": "Gold Piece",
    "healers_kit": "Healer's Kit",
    "herbalism_kit": "Herbalism Kit",
    "holy_symbol": "Holy Symbol",
    "lodestones": "Lodestones",
    "lute": "Lute",
    "parchment": "Parchment",
    "phosphorescent_moss": "Phosphorescent Moss",
    "pinch_of_dirt": "Pinch of Dirt",
    "pouch": "Pouch",
    "priests_pack": "Priest's Pack",
    "quiver": "Quiver",
    "robe": "Robe",
    "scholars_pack": "Scholar's Pack",
    "spellbook": "Spellbook",
    "thieves_tools": "Thieves' Tools",
    "travelers_clothes": "Traveler's Clothes",
}


def inventory_names(record):
    """Use the package's own weapon and armor names, including starter armor aliases."""
    inventory = next(s for s in record["ui_surfaces"] if s["renderer"] == "inventory")
    weapons = inventory["source"]["fields"]["equipment"]["value"]["weapons"]
    player = next(p for p in record["participant_artifacts"] if p["kind"] == "player_character")
    armor = player["initial_state_validation"]["definitions"]["srd_armor"]
    return {
        **GEAR,
        **{w["id"]: w["label"] for w in weapons},
        **{key: value["name"] for key, value in armor.items()},
        "leather": armor["leather_armor"]["name"],
        "studded_leather": armor["studded_leather_armor"]["name"],
    }


def _write_record(filename, record):
    (LITE / filename).write_bytes((json.dumps(record, indent=2, sort_keys=True) + "\n").encode())


def update():
    write_catalogue()
    identity = json.loads((LITE / "package-identity.json").read_bytes())
    identity["primary"]["version"] = VARIANT_VERSION
    _write_record("package-identity.json", identity)
    primary, reference = _srd_source_drafts()
    record = dict(primary.record, version=VARIANT_VERSION)
    inventory = next(s for s in record["ui_surfaces"] if s["renderer"] == "inventory")
    inventory["source"]["fields"]["item_names"] = {
        "kind": "literal",
        "value": inventory_names(record),
    }
    with materialize_package_archive((replace(primary, record=record), reference)) as artifact:
        with zipfile.ZipFile(artifact.archive) as archive:
            packages = json.loads(archive.read("manifest.json"))["packages"]
        _write_record("package-record.json", next(p for p in packages if p["id"] == record["id"]))


if __name__ == "__main__":
    update()
