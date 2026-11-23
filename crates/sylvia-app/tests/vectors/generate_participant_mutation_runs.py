"""Mechanics that change live characters, through Python's owner: SRD Lite's earned awards and
player-approved level-ups, whose commits replace a participant's data.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_participant_mutation_runs.py

The base is built at test time, not committed (see `installed_base.py`): `sylvia-store`'s small
fresh profile, the bundled SRD Lite package installed through each side's own install path (Rust
checks every table's digest against Python's), then the rows Python's owner wrote with
`tests/test_srd_advancement.py`'s `create`: one Fighter in five campaigns (`advancement`,
`decline` and `unowned` at level 1 by milestone, `constitution` at level 3, `xp` by experience
points). Then, on each, the steps those tests take: the award; the level-up asking its
confirmation, its replay, the answer committing (the participant update, its `participant_updated`
consequence and the entity's new maxima) and the level-up replayed after the commit; a build that
is not earned, refused before any reservation; a Constitution gain applied to earlier levels; a
level-up declined; a participant quarantined through SQL while its level-up waits (the answer
refused, the reservation kept) and validated again (the answer committing); and experience points
short of a level. Each step records its answer (a receipt or an awaiting-choice receipt) or the
failure's class, code and message. The record keeps the installed tables' digests
(`installed_table_digests`), the setup's rows (`setup_tables`) and the final rows of each table
the run changed (`final_changed_tables`), values typed as `{"real": hex}` or `{"blob": hex}`;
every other table ends as the base has it. It writes
`participant_mutations/participant_mutation_runs.json`; `tests/participant_mutations.rs` replays
every step on the Rust owner. Reference tooling only.
"""

from __future__ import annotations

import copy
import dataclasses
import enum
import io
import json
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(0, str(HERE))

import installed_base  # noqa: E402
from sylvia_gm.application.authority import (  # noqa: E402
    ActionCommand,
    AuthorityOwner,
    ResolveMechanicsChoiceCommand,
)
from sylvia_gm.application.bundled_packages import bundled_srd_archive  # noqa: E402
from sylvia_gm.domain.authority import WriterRole  # noqa: E402
from tests.test_srd_advancement import build, create  # noqa: E402
from tests.test_srd_second_wind import PACKAGE_ID  # noqa: E402

OUT = HERE / "participant_mutations"
OWNER = "test"
INSTALL_ID = "srd"


def plain(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, object] = {"__class__": type(value).__name__}
        for field in dataclasses.fields(value):
            out[field.name] = plain(getattr(value, field.name))
        return out
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [plain(item) for item in value]
    return value


def install(database: Path) -> None:
    with AuthorityOwner.open(database, OWNER) as owner:
        owner.install_package_archive(io.BytesIO(bundled_srd_archive()), install_id=INSTALL_ID)
    installed_base.checkpoint(database)


def setup(database: Path) -> None:
    with AuthorityOwner.open(database, OWNER) as owner:
        # `create` installs the bundled package under one install identity; it is installed already.
        installed = owner.package_candidates()[0]
        owner.install_package_archive = lambda archive, install_id: installed  # type: ignore[method-assign]
        create(owner, campaign_id="advancement")
        create(owner, campaign_id="decline")
        create(owner, campaign_id="constitution", level=3)
        create(owner, campaign_id="xp", method="xp")
        create(owner, campaign_id="unowned")
    installed_base.checkpoint(database)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    runs: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        installed_base.seed_copy(database)
        install(database)
        digests = installed_base.table_digests(database)
        installed = Path(scratch) / "installed.sqlite3"
        shutil.copyfile(database, installed)
        setup(database)
        setup_rows = installed_base.changed_rows(installed, database)
        base = Path(scratch) / "base.sqlite3"
        shutil.copyfile(database, base)
        with AuthorityOwner.open(database, OWNER) as owner:
            waiting: dict[str, object] = {}

            def record(name: str, kind: str, command: dict[str, object], call) -> object:  # noqa: ANN001
                answer = None
                try:
                    answer = call()
                    outcome: dict[str, object] = {"ok": plain(answer)}
                except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                    outcome = {"error": type(error).__name__, "code": getattr(error, "code", None),
                               "message": str(error)}
                runs.append({"name": name, "kind": kind, "command": command, "outcome": outcome})
                return answer

            sent: dict[tuple[str, str], dict[str, object]] = {}

            def act(name: str, campaign: str, tool: str, parameters: dict[str, object], action: str) -> None:
                command = {"campaign_id": campaign, "action_id": action, "idempotency_key": action,
                           "owner_id": OWNER, "writer": "dm",
                           "expected_revision": owner.snapshot(campaign).revision,
                           "turn_id": "turn", "package_id": PACKAGE_ID, "tool_name": tool,
                           "parameters": {"character": "hero", "character_state": "hero", **parameters}}
                sent[(campaign, action)] = command
                send(name, command)

            def send(name: str, command: dict[str, object]) -> None:
                answer = record(name, "act", command, lambda: owner.execute_action(
                    ActionCommand(**{**command, "writer": WriterRole.DM})))
                if answer is not None and hasattr(answer, "choice"):
                    waiting[str(command["campaign_id"])] = answer

            def sql(name: str, statement: str, parameters: list[str]) -> None:
                connection = sqlite3.connect(database)
                with connection:
                    connection.execute(statement, parameters)
                connection.close()
                runs.append({"name": name, "kind": "sql",
                             "command": {"statement": statement, "parameters": parameters}, "outcome": None})

            def resolve(name: str, campaign: str, option: str, resolution: str = "confirm") -> None:
                pending = waiting[campaign]
                command = {"campaign_id": campaign, "action_id": pending.action_id,  # type: ignore[attr-defined]
                           "resolution_id": resolution, "owner_id": OWNER,
                           "expected_revision": pending.basis_revision,  # type: ignore[attr-defined]
                           "choice_id": pending.choice.choice_id,  # type: ignore[attr-defined]
                           "option_id": option, "session_id": None, "choice_sequence": 0}
                record(name, "resolve", command, lambda: owner.resolve_mechanics_choice(
                    ResolveMechanicsChoiceCommand(**command)))

            def award(campaign: str, xp: int = 0) -> None:
                act(f"{campaign}: the award", campaign, "award_srd_advancement",
                    {"award_id": "rescue", "xp": xp, "evidence": "The rescue was resolved in play."}, "award")

            def proposal(campaign: str) -> dict[str, object]:
                # `build` reads the campaign called "advancement"; read this campaign's abilities.
                result = build(owner) if campaign == "advancement" else copy.deepcopy(build(owner))
                abilities = dict(owner.snapshot(campaign).participants[0].data["abilities"])
                result["build"][0]["abilities"] = abilities  # type: ignore[index]
                return result

            award("advancement")
            unearned = proposal("advancement")
            unearned["build"][0]["abilities"]["strength"] = 12  # type: ignore[index]
            act("advancement: a build that is not earned", "advancement", "level_up_srd_character", unearned,
                "unearned")
            level = proposal("advancement")
            act("advancement: the level-up asking its confirmation", "advancement", "level_up_srd_character",
                level, "level")
            send("advancement: its replay", sent[("advancement", "level")])
            resolve("advancement: the answer committing", "advancement", "apply")
            send("advancement: the level-up replayed after the commit", sent[("advancement", "level")])

            award("decline")
            act("decline: the level-up asking its confirmation", "decline", "level_up_srd_character",
                proposal("decline"), "level")
            options = [option.option_id for option in waiting["decline"].choice.options]  # type: ignore[attr-defined]
            resolve("decline: the level-up declined", "decline", next(o for o in options if o != "apply"))

            award("constitution")
            gain = proposal("constitution")
            gain["build"][0]["abilities"]["constitution"] = 12  # type: ignore[index]
            gain["build"][0]["resources"] = [  # type: ignore[index]
                {"id": "action_surge", "maximum": 1, "source": "SRD Fighter"}]
            act("constitution: the level-up asking its confirmation", "constitution", "level_up_srd_character",
                gain, "level")
            resolve("constitution: the gain applied to earlier levels", "constitution", "apply")

            # The participant loses its validated proof while the level-up waits: the answer can't
            # commit the update, so the reservation stays; with the proof back, it commits.
            award("unowned")
            act("unowned: the level-up asking its confirmation", "unowned", "level_up_srd_character",
                proposal("unowned"), "level")
            status = "UPDATE participants SET validation_status = ? WHERE campaign_id = ?"
            sql("unowned: the participant quarantined", status, ["quarantined", "unowned"])
            resolve("unowned: the answer refused for an unowned participant", "unowned", "apply", "first")
            sql("unowned: the participant validated again", status, ["validated", "unowned"])
            resolve("unowned: the answer committing after", "unowned", "apply", "second")

            award("xp", xp=100)
            act("xp: a level not yet earned", "xp", "level_up_srd_character", proposal("xp"), "not-ready")
        installed_base.checkpoint(database)
        changed = installed_base.changed_rows(base, database)
    record_out = {"python": sys.version.split()[0], "owner_id": OWNER, "install_id": INSTALL_ID,
                  "installed_table_digests": digests, "setup_tables": setup_rows, "runs": runs,
                  "final_changed_tables": changed}
    (OUT / "participant_mutation_runs.json").write_bytes(
        (json.dumps(record_out, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
