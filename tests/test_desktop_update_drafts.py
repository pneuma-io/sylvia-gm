"""Draft persistence is local text, never provider requests or action replay."""

import copy

import pytest

from sylvia_gm.application.desktop_update import drafts
from sylvia_gm.application.desktop_update.manifest import UpdateError


def snapshot(session="builder-fictional"):
    return {"format_version": 1, "sessions": [{
        "session_id": session, "text": "Fictional unsent action 🦉", "ooc_text": "Keep private",
        "ooc_open": True, "questions": [{"question_key": "question-1", "text": "A monk"},
                                     {"question_key": "question-2", "custom": "My answer",
                                      "selection": "custom"}],
    }]}


def test_save_reopen_and_independent_data_root_keep_exact_fields(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    store = drafts.DraftStore(first)
    assert store.load() == {"format_version": 1, "sessions": []}
    value = snapshot()
    assert store.save(value) == {"status": "saved"}
    assert drafts.DraftStore(first).load() == value
    assert drafts.DraftStore(second).load()["sessions"] == []
    assert sorted(path.name for path in first.iterdir()) == [".desktop-drafts.json"]


def test_update_view_is_bounded_text_for_one_operation_and_retains_unsent_fields(tmp_path):
    store = drafts.DraftStore(tmp_path)
    value = snapshot()
    value["update_view"] = {"operation_id": "a" * 32, "kind": "session",
                            "campaign_id": "campaign-a", "session_id": "dm-a"}
    store.save(value)
    assert drafts.DraftStore(tmp_path).load() == value
    before = store.path.read_bytes()
    for change in [
        {"kind": "execute"}, {"url": "https://invalid.example"}, {"operation_id": "invalid"},
        {"session_id": None}, {"session_id": "x" * 257},
        {"kind": "home", "session_id": "dm-a"},
    ]:
        invalid = copy.deepcopy(value)
        invalid["update_view"].update(change)
        with pytest.raises(UpdateError):
            store.save(invalid)
        assert store.path.read_bytes() == before


@pytest.mark.parametrize("change", [
    lambda value: value.update(format_version=True),
    lambda value: value.update(url="https://untrusted.invalid"),
    lambda value: value["sessions"].append(copy.deepcopy(value["sessions"][0])),
    lambda value: value["sessions"][0].update(text="x" * (drafts.MAX_TEXT + 1)),
    lambda value: value["sessions"][0].update(ooc_open="true"),
    lambda value: value["sessions"][0]["questions"][0].update(execute_action="no"),
    lambda value: value["sessions"][0]["questions"].append(
        copy.deepcopy(value["sessions"][0]["questions"][0])),
    lambda value: value["sessions"][0].update(questions=[{"question_key": ""}]),
])
def test_invalid_snapshot_preserves_prior_bytes(tmp_path, change):
    store = drafts.DraftStore(tmp_path)
    store.save(snapshot())
    before = store.path.read_bytes()
    value = snapshot()
    change(value)
    with pytest.raises(UpdateError):
        store.save(value)
    assert store.path.read_bytes() == before


def test_incompatible_or_duplicate_key_file_is_retained(tmp_path):
    store = drafts.DraftStore(tmp_path)
    for raw in [b'{"format_version":2,"sessions":[]}',
                b'{"format_version":1,"sessions":[],"sessions":[]}']:
        store.path.write_bytes(raw)
        with pytest.raises(UpdateError):
            store.load()
        assert store.path.read_bytes() == raw


def test_total_and_session_limits_do_not_truncate_existing_drafts(tmp_path):
    store = drafts.DraftStore(tmp_path)
    store.save(snapshot())
    before = store.path.read_bytes()
    too_many = {"format_version": 1, "sessions": [
        snapshot(f"session-{index}")["sessions"][0] for index in range(drafts.MAX_SESSIONS + 1)
    ]}
    with pytest.raises(UpdateError):
        store.save(too_many)
    oversized = {"format_version": 1, "sessions": [
        {**snapshot(f"session-{index}")["sessions"][0], "text": "x" * drafts.MAX_TEXT}
        for index in range(9)
    ]}
    with pytest.raises(UpdateError) as rejected:
        store.save(oversized)
    assert rejected.value.code == "draft_limit"
    assert store.path.read_bytes() == before


def test_failed_write_preserves_previous_snapshot(tmp_path, monkeypatch):
    store = drafts.DraftStore(tmp_path)
    store.save(snapshot())
    before = store.path.read_bytes()

    def full(_path, _value):
        raise UpdateError("journal_failed", "Controlled disk-full failure")

    monkeypatch.setattr(drafts, "atomic_receipt", full)
    with pytest.raises(UpdateError, match="disk-full"):
        store.save(snapshot("second"))
    assert store.path.read_bytes() == before


def test_real_owner_roundtrip_survives_ordinary_reopen_without_model_work(tmp_path, monkeypatch):
    from sylvia_gm.interfaces import owner_process
    from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness

    monkeypatch.setattr(owner_process, "_preinstall_bundled_srd", lambda _bus: None)
    monkeypatch.setattr(owner_process, "_preinstall_bundled_riichi", lambda _bus: None)
    database = tmp_path / "authority.sqlite3"
    for index in range(2):
        harness = _OwnerHarness(database, None)
        try:
            if index == 0:
                assert harness.request("save", "save_desktop_drafts", {"drafts": snapshot()}) == {
                    "status": "saved"
                }
            assert harness.request("load", "load_desktop_drafts") == snapshot()
            harness.send("bad-shape", {"method": "save_desktop_drafts", "params": {
                "drafts": snapshot(), "destination_path": "untrusted"
            }})
            assert harness.read()["ok"] is False
            assert harness.request("after-rejection", "load_desktop_drafts") == snapshot()
            assert harness.request("no-campaigns", "campaign_index") == []
            assert harness.request("no-chats", "builder_chat_index") == []
        finally:
            harness.close()
