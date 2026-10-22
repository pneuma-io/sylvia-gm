"""The first fractional printed radius retains all prior integral source definitions."""

from scripts import srd_spell_environment as environment
from scripts.srd_full.srd_environment_flaming_sphere import PROFILE
from scripts.srd_full.srd_spell_environment import PROFILES, _schema


def test_flaming_source_radius_schema_is_numeric_and_preserves_existing_integer_profiles():
    assert PROFILES["flaming_sphere"] == PROFILE
    assert _schema()["properties"]["radius_feet"]["type"] == "number"
    assert PROFILE["radius_feet"] == 2.5
    assert PROFILES["cloudkill"]["radius_feet"] == 20
    assert PROFILES["moonbeam"]["radius_feet"] == 5
    assert PROFILES["call_lightning"]["radius_feet"] == 60


def test_fractional_radius_schema_survives_real_integer_profile_following_it(monkeypatch):
    monkeypatch.setattr(
        environment,
        "PROFILES",
        {
            "flaming_sphere": PROFILES["flaming_sphere"],
            "cloudkill": PROFILES["cloudkill"],
        },
    )
    assert _schema()["properties"]["radius_feet"]["type"] == "number"
