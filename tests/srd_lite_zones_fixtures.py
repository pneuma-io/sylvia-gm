"""Actual admitted casts and consumers, with persisted public-command replay."""

import json
from copy import deepcopy
from tests.lite_imported_fixture import ImportedCase
from tests.lite_fixture_archive import archive_closed_case
from tests.test_srd_lite_foundation import VARIANT_ID
from sylvia_gm.domain.authority import thaw_json_value


def zone_case(path, spell, initial_spell=None, **changes):
    source = "cleric" if spell == "silence" else "druid"
    spells = [initial_spell or spell]
    return ImportedCase(path, spell,
        participant_changes={
            "classes": [{"id": source, "level": 17}],
            "wizard_spellbook": [],
            "abilities": {"strength":16,"dexterity":14,"constitution":14,
                          "intelligence":18,"wisdom":18,"charisma":10},
            "spellcasting_grants":[{"source":source,"ability":"wisdom","spells":spells},
                                  {"source":"magic_initiate_wizard","ability":"intelligence",
                                   "spells":["blade_ward","true_strike"]}],
        }, entity_changes={"hero":{"prepared_spells":spells+['blade_ward','true_strike']}}, **changes)


def retain(case, receipt, command):
    saved = case.snapshot()
    assert case.execute(command) == receipt and case.snapshot() == saved
    case.reopen()
    assert case.snapshot() == saved
    assert case.execute(command) == receipt and case.snapshot() == saved
