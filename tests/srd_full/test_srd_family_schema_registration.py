"""Family schema joins must preserve identically named nested and neighbour fields."""

import yaml
from scripts.srd_full.build_srd_5_2_1_spells import _register_spell_family_schema
from scripts.srd_full.srd_arcane_hand import _creator


def _source():
    return yaml.safe_dump(
        {
            "tools": {"create_srd_arcane_hand_scene": _creator()},
            "definition_row_schemas": {
                "definitions.other": {
                    "discriminator": "family",
                    "variants": {"delayed_blast_fireball": {"type": "string"}},
                },
                "definitions.spells": {
                    "discriminator": "family",
                    "variants": {"summoned_creature": {"type": "object"}},
                },
            },
            "definitions": {},
        },
        sort_keys=False,
    )


def test_schema_registration_uses_only_the_spell_variant_anchor():
    source = _source()
    schema = {"type": "object", "properties": {"duration_rounds": {"const": 10}}}
    updated = _register_spell_family_schema(source, "delayed_blast_fireball", schema)
    before = yaml.safe_load(source)
    after = yaml.safe_load(updated)
    assert after["definition_row_schemas"]["definitions.spells"]["variants"] == {
        "delayed_blast_fireball": schema,
        "summoned_creature": {"type": "object"},
    }
    assert after["tools"] == before["tools"]
    assert (
        after["definition_row_schemas"]["definitions.other"]
        == before["definition_row_schemas"]["definitions.other"]
    )
    assert _register_spell_family_schema(updated, "delayed_blast_fireball", schema) == updated


def test_distinct_new_family_schemas_compose_without_renaming_existing_fields():
    source = _source()
    expected = yaml.safe_load(source)["definition_row_schemas"]["definitions.other"]
    for name in ("sunbeam", "arcane_hand", "delayed_blast_fireball", "animate_objects"):
        source = _register_spell_family_schema(source, name, {"type": "object"})
    rows = yaml.safe_load(source)["definition_row_schemas"]
    assert set(rows["definitions.spells"]["variants"]) == {
        "sunbeam",
        "arcane_hand",
        "delayed_blast_fireball",
        "animate_objects",
        "summoned_creature",
    }
    assert rows["definitions.other"] == expected
