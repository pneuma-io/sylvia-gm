"""The existing ordinary-action fence protects the factual eye recorder after reopen."""

from copy import deepcopy

import pytest
import yaml
from scripts import srd_reaction_spells as reactions
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_scene_sight_origins as eye
from tests.srd_full.test_srd_action_budget import SRD
from tests.srd_full.test_srd_npc_spell_casting import _act


def test_actual_pending_cast_fences_eye_metadata_atomically_after_reopen(tmp_path, monkeypatch):
    runtime = deepcopy(eye._runtime()[0])
    original = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    final = reactions.bind_pending_support_fences(original)
    assert reactions.bind_pending_support_fences(final) == final
    runtime = _load_runtime(final)
    encoded = final.encode()
    monkeypatch.setattr(eye, "_runtime", lambda: (runtime, encoded))
    eye._archive.cache_clear()
    monkeypatch.setattr(public_casting, "bundled_srd_archive", eye._archive)
    monkeypatch.setattr(public_casting, "_spell_case", eye._public_case)
    database = tmp_path / "eye-pending.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "misty_step")
        begun, command = _act(owner, "begin_srd_spell_action", **params)
        assert begun.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.state"]["pending_srd_action_stack"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(command) == begun
        with pytest.raises(MechanicsRejected, match="pending reaction window"):
            _act(owner, "record_srd_scene_sight_origin", observer_state="hero", offset=[0, 0, 5])
        assert owner.snapshot("casting") == saved
        assert "srd_sight_origin" not in eye._hero(saved.state)["metadata"]
    eye._archive.cache_clear()
