"""Admission reuse cannot share mutable campaign state or accept changed bytes."""

import hashlib

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import StateValue, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from tests.admitted_library_fixture import (
    _admitted_seed,
    admitted_fixture_owner,
    prepare_library_database,
)
from tests.test_alpha_f3_package_execution import _archive, _command, _runtime_yaml


def test_admitted_library_seed_preserves_isolation_and_revalidates_changed_bytes(
    tmp_path, monkeypatch
):
    raw = _archive()
    imports = []
    original = AuthorityOwner.install_package_archive

    def observed(owner, source, **kwargs):
        imports.append(kwargs["install_id"])
        return original(owner, source, **kwargs)

    monkeypatch.setattr(AuthorityOwner, "install_package_archive", observed)
    for index, amount in [(0, 7), (1, 0)]:
        with admitted_fixture_owner(tmp_path / f"{index}.sqlite3", "owner-runtime", raw) as (
            owner,
            candidate,
        ):
            with pytest.raises(CommandRejected):
                owner.snapshot("campaign-runtime")
            owner.create_campaign(
                CampaignCreate(
                    campaign_id="campaign-runtime",
                    display_name="Isolated",
                    writer=WriterRole.DM,
                    package_lock=candidate.package_lock,
                    turn_id="turn-runtime",
                    initial_state=(
                        StateValue("synthetic.clockwork", "state", {"counter": 0, "bonus": 0}),
                    ),
                )
            )
            assert (
                owner.snapshot("campaign-runtime").state["synthetic.clockwork.state"]["counter"]
                == 0
            )
            if index == 0:
                original_lock = candidate.package_lock
            if amount:
                owner.execute_action(_command(revision=0, suffix=str(index), steps=amount))
                assert (
                    owner.snapshot("campaign-runtime").state["synthetic.clockwork.state"]["counter"]
                    == amount
                )
    assert len(imports) == 1
    seed = _admitted_seed(raw)
    assert list(seed.parent.glob("*.sqlite3")) == [seed]
    assert (
        not seed.with_name(seed.name + "-wal").exists()
        or seed.with_name(seed.name + "-wal").stat().st_size == 0
    )
    seed_hash = hashlib.sha256(seed.read_bytes()).hexdigest()
    changed = _archive(
        runtime_bytes=_runtime_yaml().replace(b"amount: steps", b"amount: steps + 1")
    )
    with admitted_fixture_owner(tmp_path / "changed.sqlite3", "owner-runtime", changed) as (
        owner,
        candidate,
    ):
        assert candidate.package_lock != original_lock
    assert len(imports) == 2
    assert hashlib.sha256(seed.read_bytes()).hexdigest() == seed_hash
    with pytest.raises(ValueError, match="fresh isolated"):
        prepare_library_database(tmp_path / "0.sqlite3", raw)
