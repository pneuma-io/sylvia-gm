"Source blocks and ordinary actions remain bound to the actual creature."

from scripts.srd_full.build_srd_beast_forms import build_index


def test_beast_block_ends_at_next_creature_even_when_it_is_not_a_beast():
    forms = build_index()["forms"]
    for beast, next_creature in (
        ("elk", "Flying Snake"),
        ("giant_crocodile", "Giant Eagle"),
        ("giant_octopus", "Giant Owl"),
        ("giant_venomous_snake", "Giant Vulture"),
    ):
        assert next_creature not in forms[beast]["source_stat_block"]
    assert len(forms) == 90


def test_source_prone_sizes_and_negative_damage_are_not_replaced_by_defaults():
    forms = build_index()["forms"]
    assert forms["ankylosaurus"]["attacks"]["Tail"]["prone_max_size_rank"] == 4
    assert forms["mastiff"]["attacks"]["Bite"]["prone_max_size_rank"] == 2
    assert forms["jackal"]["attacks"]["Bite"]["damage_modifier"] == -1
    for name in ("ankylosaurus", "mastiff", "jackal"):
        assert forms[name]["execution_supported"] is True
        assert forms[name]["unsupported_clauses"] == []


def test_actual_initiative_and_unimplemented_clauses_remain_source_owned():
    forms = build_index()["forms"]
    assert forms["giant_crocodile"]["initiative_modifier"] == -1
    assert forms["mastiff"]["initiative_modifier"] == 2
    for form in forms.values():
        assert form["execution_supported"] is (not bool(form["unsupported_clauses"]))
    assert forms["ape"]["execution_supported"] is False
    assert "Recharge" in " ".join(forms["ape"]["unsupported_clauses"])


def test_source_damage_adjustments_and_wrapped_condition_immunities_are_exact():
    forms = build_index()["forms"]
    assert forms["polar_bear"]["damage_resistances"] == ["cold"]
    assert forms["polar_bear"]["execution_supported"] is True
    assert forms["badger"]["damage_resistances"] == ["poison"]
    assert forms["badger"]["execution_supported"] is False
    swarm = forms["swarm_of_bats"]
    assert swarm["damage_resistances"] == ["bludgeoning", "piercing", "slashing"]
    assert swarm["condition_immunities"] == [
        "charmed",
        "frightened",
        "grappled",
        "paralyzed",
        "petrified",
        "prone",
        "restrained",
        "stunned",
    ]
    assert swarm["execution_supported"] is False


def test_qualified_adjustment_clause_is_rejected_instead_of_inventing_a_damage_type():
    from scripts.srd_full.build_srd_beast_forms import _source_adjustments

    values, gaps = _source_adjustments(
        "Resistances Cold; damage from nonmagical attacks\nSenses Darkvision 60 ft.\n"
    )
    assert values["damage_resistances"] == []
    assert gaps == ["Resistances: Cold; damage from nonmagical attacks"]


def test_respiration_traits_derive_exact_native_modes_and_duration():
    forms = build_index()["forms"]
    for name in ("archelon", "crab"):
        assert forms[name]["breathes_air"] is True
        assert forms[name]["breathes_water"] is True
        assert forms[name]["execution_supported"] is True
    for name, ticks in (("hippopotamus", 200), ("killer_whale", 600), ("plesiosaurus", 1200)):
        assert forms[name]["hold_breath_ticks"] == ticks
        assert forms[name]["breathes_water"] is False
        assert forms[name]["execution_supported"] is True
    assert forms["giant_octopus"]["breathes_air"] is False
    assert forms["giant_octopus"]["breathes_water"] is True
    assert forms["giant_octopus"]["hold_breath_ticks"] == 1200
    assert forms["giant_octopus"]["execution_supported"] is False


def test_pack_tactics_is_admitted_only_with_its_exact_source_clause():
    forms = build_index()["forms"]
    for name in ("baboon", "dire_wolf", "giant_rat", "hyena", "reef_shark", "vulture", "wolf"):
        assert forms[name]["pack_tactics"] is True
        assert forms[name]["execution_supported"] is True
    assert forms["blood_hawk"]["pack_tactics"] is True
    assert forms["blood_hawk"]["execution_supported"] is True
    assert forms["lion"]["execution_supported"] is False


def test_source_body_light_and_single_fire_damage_use_printed_beetle_clauses():
    forms = build_index()["forms"]
    beetle = forms["giant_fire_beetle"]
    assert beetle["execution_supported"] is True
    assert (beetle["bright_light_feet"], beetle["dim_light_feet"]) == (10, 20)
    assert beetle["damage_resistances"] == ["fire"]
    assert beetle["attacks"]["Bite"]["damage_expression"] == "fixed-1"
    assert beetle["attacks"]["Bite"]["damage_type"] == "fire"
    assert forms["giant_rat"]["dim_light_feet"] == 0


def test_conditional_and_two_type_attacks_keep_the_exact_source_values():
    forms = build_index()["forms"]
    for name in (
        "blood_hawk",
        "giant_shark",
        "hunter_shark",
        "piranha",
        "venomous_snake",
        "giant_venomous_snake",
        "scorpion",
    ):
        assert forms[name]["execution_supported"] is True
    assert forms["blood_hawk"]["attacks"]["Beak"]["bloodied_damage_expression"] == "d8"
    assert forms["hunter_shark"]["attacks"]["Bite"]["target_not_full_hp_advantage"] is True
    assert forms["piranha"]["attacks"]["Bite"]["damage_expression"] == "fixed-1"
    snake = forms["giant_venomous_snake"]["attacks"]["Bite"]
    assert (
        snake["damage_expression"],
        snake["damage_modifier"],
        snake["normal_range"],
        snake["extra_damage_expression"],
        snake["extra_damage_type"],
    ) == ("d4", 4, 10, "d8", "poison")
    assert forms["giant_scorpion"]["execution_supported"] is False
    assert forms["giant_spider"]["execution_supported"] is False
