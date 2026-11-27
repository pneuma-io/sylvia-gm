"""Python's `campaign_index` over a database its owner built, for the campaign-reads family.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_campaign_index.py

It writes, under `campaigns/`:

- `seed.sqlite3.gz`: a database the Python owner built: a No Ruleset campaign carried through
  confirmation, construction and finalisation (Builder session, plan, Builder-to-DM handoff,
  DM session); a DM campaign on one small synthetic package with session messages; a
  default-titled interview campaign whose interview state names an accepted answer; and
  campaigns whose names differ only in case;
- `campaign_index.json`: every `CampaignIndexRecord` the Python store returns for it, and
  every `CampaignIndexEntry` `AuthorityOwner.campaign_index()` projects from them.

`tests/campaigns.rs` reads the same database with the Rust store and must return the same
records. Reference tooling only.
"""

from __future__ import annotations

import gzip
import io
import json
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# The owner draws campaign identities from uuid.uuid4 (some modules bind it at import), and the
# session and handoff identities derive from them. Seed it before anything imports it, so a
# re-run writes byte-identical files.
import random  # noqa: E402
import uuid  # noqa: E402

_IDENTITIES = random.Random(20261121)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from generate_action_runs import synthetic_archive  # noqa: E402

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate  # noqa: E402
from sylvia_gm.domain.authority import PackageLock, WriterRole  # noqa: E402
from sylvia_gm.domain.model_capability import model_guidance_payload  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402
from sylvia_gm.infrastructure import sqlite_authority as a  # noqa: E402

OUT = Path(__file__).resolve().parent / "campaigns"


def ordered(value: object) -> object:
    """Plain JSON in Python's own key order; `_dump` would sort every mapping."""
    if isinstance(value, Mapping):
        return {str(key): ordered(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [ordered(item) for item in value]
    return value


def build(database: Path) -> None:
    with AuthorityOwner.open(database, "campaign-index") as owner:
        owner.prepare_new_campaign_storage()
        candidate = owner.install_package_archive(
            io.BytesIO(synthetic_archive()), install_id="install-campaign-index"
        )

        # A No Ruleset campaign carried through construction to its DM handoff.
        confirmed = owner.confirm_new_campaign(
            operation_id="confirm-ruleless",
            display_name="Ruleless Road",
            package_lock=PackageLock(entries=()),
            turn_id="turn-ruleless-builder",
            builder_session_id="builder-ruleless",
            builder_provider_id="synthetic.provider",
            builder_model_id="synthetic.provider/model",
        )
        owner.begin_campaign_construction(
            operation_id="begin-ruleless",
            campaign_id=confirmed.campaign_id,
            lane="customize",
            expected_revision=confirmed.resulting_revision,
            canonical_input={"fixture": "campaign-index"},
        )
        owner.finalize_campaign_construction(
            campaign_id=confirmed.campaign_id,
            dm_provider_id="synthetic.provider",
            dm_model_id="synthetic.provider/model",
            plan_artifact_text="# Ruleless Road\n",
        )

        # A DM campaign on the synthetic package, with a session and its messages.
        owner.create_campaign(
            CampaignCreate(
                display_name="clockwork Vale",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn-clockwork",
                campaign_id="campaign-clockwork",
            )
        )
        session = owner.create_agent_session(
            campaign_id="campaign-clockwork",
            role=WriterRole.DM,
            provider_id="synthetic.provider",
            model_id="synthetic.provider/model",
            session_id="dm-clockwork",
        )
        for index in range(3):
            owner.append_agent_message(
                session_id=session.session_id,
                message_id=f"clockwork-{index}",
                kind=SessionMessageKind.TALK,
                content={"text": f"Line {index}."},
            )

        # The default-titled interview, its state naming an accepted answer.
        interview = owner.confirm_new_campaign(
            operation_id="confirm-interview",
            display_name="New campaign interview",
            package_lock=PackageLock(entries=()),
            turn_id="turn-interview",
            builder_session_id="builder-interview",
            builder_provider_id="synthetic.provider",
            builder_model_id="synthetic.provider/model",
        )
        owner.append_agent_message(
            session_id="builder-interview",
            message_id="answer-name",
            kind=SessionMessageKind.USER,
            content={"text": "Moonlit Keep", "interview_value": {"display_name": "  Moonlit Keep  "}},
        )
        owner.append_agent_message(
            session_id="builder-interview",
            message_id="interview-state",
            kind=SessionMessageKind.EVIDENCE,
            content={"kind": "campaign_interview_state", "answers": {"name": "answer-name"}},
        )
        del interview

        # Names that differ only in case, and an empty-lock DM campaign.
        for campaign_id, name in (("campaign-b", "beta"), ("campaign-a2", "Alpha"), ("campaign-a1", "alpha")):
            owner.create_campaign(
                CampaignCreate(
                    display_name=name,
                    writer=WriterRole.DM,
                    package_lock=PackageLock(entries=()),
                    turn_id=f"turn-{campaign_id}",
                    campaign_id=campaign_id,
                )
            )


def record_json(record: a.CampaignIndexRecord) -> dict[str, object]:
    campaign = record.campaign
    lock = record.package_lock
    return {
        "campaign": {
            "campaign_id": campaign.campaign_id,
            "display_name": campaign.display_name,
            "lifecycle": campaign.lifecycle.value,
            "writer": campaign.writer.value,
            "revision": campaign.revision,
        },
        "package_lock": None if lock is None else [
            {
                "package_id": entry.package_id,
                "version": entry.version,
                "sha256": entry.sha256,
                "role": entry.role.value,
                "load_order": entry.load_order,
                "dependencies": [
                    [d.package_id, d.version, d.sha256, d.kind, d.namespace] for d in entry.dependencies
                ],
                "capabilities": list(entry.capabilities),
            }
            for entry in lock.entries
        ],
        "package_labels": [list(item) for item in record.package_labels],
        "session_activity_ordinal": record.session_activity_ordinal,
        "turns": [[turn_id, status.value] for turn_id, status in record.turns],
        "top_level_sessions": [
            {
                "session_id": s.session_id,
                "campaign_id": s.campaign_id,
                "role": s.role.value,
                "provider_id": s.provider_id,
                "model_id": s.model_id,
                "status": s.status.value,
                "parent_session_id": s.parent_session_id,
                "selected_skill": s.selected_skill,
                "scope": list(s.scope),
                "reasoning_effort": s.reasoning_effort,
                "reasoning_effort_once": s.reasoning_effort_once,
            }
            for s in record.top_level_sessions
        ],
        "handoffs": [
            {
                "handoff_id": h.handoff_id,
                "campaign_id": h.campaign_id,
                "builder_session_id": h.builder_session_id,
                "dm_session_id": h.dm_session_id,
                "package_lock_id": h.package_lock_id,
                "package_candidate_id": h.package_candidate_id,
                "plan_id": h.plan_id,
                "plan_revision": h.plan_revision,
                "plan_content_sha256": h.plan_content_sha256,
                "expected_revision": h.expected_revision,
                "resulting_campaign_revision": h.resulting_campaign_revision,
                "chronology_ordinal": h.chronology_ordinal,
                "completed_turn_id": h.completed_turn_id,
                "model_guidance": ordered(model_guidance_payload(h.model_guidance)),
                "payload_sha256": h.payload_sha256,
            }
            for h in record.handoffs
        ],
        "interview_name": record.interview_name,
    }


def entry_json(entry: object) -> dict[str, object]:
    """`CampaignIndexEntry` field by field, in declaration order."""
    values = dict(vars(entry)) if hasattr(entry, "__dict__") else {
        name: getattr(entry, name) for name in entry.__dataclass_fields__  # type: ignore[attr-defined]
    }
    out: dict[str, object] = {}
    for name in entry.__dataclass_fields__:  # type: ignore[attr-defined]
        value = values[name]
        if name == "sessions":
            value = [
                {
                    **{
                        key: getattr(session, key)
                        for key in session.__dataclass_fields__
                        if key != "model_guidance"
                    },
                    "model_guidance": None
                    if session.model_guidance is None
                    else ordered(model_guidance_payload(session.model_guidance)),
                }
                for session in value
            ]
        out[name] = value
    return out


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    with tempfile.TemporaryDirectory(dir=OUT.parent) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        build(database)
        checkpoint = a.sqlite3.connect(database)
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        checkpoint.close()
        (OUT / "seed.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        seed_bytes = database.read_bytes()
        store = a._open_for_application_owner(database)
        try:
            records = [record_json(record) for record in store.campaign_index()]
        finally:
            store.close()
        # The owner-level projection, read from its own copy of the same seed.
        owner_copy = Path(scratch) / "owner" / "authority.sqlite3"
        owner_copy.parent.mkdir()
        owner_copy.write_bytes(seed_bytes)
        with AuthorityOwner.open(owner_copy, "campaign-index-entries") as owner:
            entries = [entry_json(entry) for entry in owner.campaign_index()]
    record = {
        "python": sys.version.split()[0],
        "campaign_index": records,
        "campaign_index_entries": entries,
    }
    text = json.dumps(record, indent=1, ensure_ascii=True) + "\n"
    (OUT / "campaign_index.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
