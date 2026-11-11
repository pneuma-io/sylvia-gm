"""Synthetic hook contracts only: no network, credentials, generated/private media or UI."""

import io
import json
import socket
import struct
import threading
import zipfile
import zlib
from dataclasses import asdict, replace
from pathlib import Path
from uuid import UUID

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.application.image_hooks import (
    ASSOCIATIONS_PATH,
    ImageCapabilities,
    ImageData,
    ImageHookError,
    ImageRequest,
    ImageSession,
    ImageUpdate,
    image_associations,
)
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.domain.authority import WriterRole


def uid(number):
    return str(UUID(int=number))


def png(width=1, height=1, color=0):
    """Supply tiny, deterministic RGB pixels with no text or ancillary metadata."""

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = (b"\0" + bytes([color, 0, 0]) * width) * height
    return ImageData(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class SyncAdapter:
    capabilities = ImageCapabilities(
        "synthetic.sync",
        frozenset({"create", "edit"}),
        frozenset({(1, 1)}),
        maximum_bytes=1024,
    )

    def __init__(self):
        self.submissions = 0
        self.output = png()
        self.failure = None
        self.reply_change = lambda update: update

    def submit(self, request):
        self.submissions += 1
        if self.failure:
            raise self.failure
        if request.operation == "edit":
            assert request.input_image == png()
        return self.reply_change(
            ImageUpdate(request.session_id, request.operation_id, "succeeded", image=self.output)
        )

    def poll(self, *_args):
        pytest.fail("Synchronous adapter must never be polled")

    def cancel(self, *_args):
        pytest.fail("Unsupported remote cancellation must never be invoked")


class AsyncAdapter(SyncAdapter):
    capabilities = ImageCapabilities(
        "synthetic.workflow",
        frozenset({"create", "edit"}),
        frozenset({(1, 1)}),
        workflows=frozenset({"synthetic.flow"}),
        asynchronous=True,
        progress=True,
        cancellation=True,
        maximum_bytes=1024,
    )

    def __init__(self):
        super().__init__()
        self.polls = self.cancels = 0

    def submit(self, request):
        self.submissions += 1
        return ImageUpdate(
            request.session_id,
            request.operation_id,
            "pending",
            progress=0,
            job_token="synthetic-opaque-job",
        )

    def poll(self, request, job_token):
        assert job_token == "synthetic-opaque-job"
        self.polls += 1
        if self.polls % 2:
            return ImageUpdate(
                request.session_id,
                request.operation_id,
                "running",
                progress=0.5,
                job_token=job_token,
            )
        return ImageUpdate(
            request.session_id, request.operation_id, "succeeded", progress=1, image=self.output
        )

    def cancel(self, request, job_token):
        assert job_token == "synthetic-opaque-job"
        self.cancels += 1


class StencilAdapter(SyncAdapter):
    capabilities = ImageCapabilities(
        "synthetic.stencil",
        frozenset({"edit"}),
        frozenset({(2, 1)}),
        maximum_bytes=1024,
    )

    def __init__(self):
        super().__init__()
        self.output = png(2, 1)


def request(adapter, number=2, **changes):
    return replace(
        ImageRequest(
            uid(1),
            uid(number),
            adapter.capabilities.adapter_id,
            "create",
            1,
            1,
            "Synthetic placeholder",
            workflow=("synthetic.flow" if adapter.capabilities.workflows else None),
        ),
        **changes,
    )


def draft():
    return NormalizedPackageDraft({"id": "synthetic.content"}, {"existing.txt": b"unchanged"})


def adopt(session, operation_id, source, expected=None, **changes):
    return session.adopt(
        operation_id,
        source,
        **{
            "association_id": "portrait.main",
            "kind": "portrait",
            "target_id": "character.one",
            "expected_sha256": expected,
            **changes,
        },
    )


@pytest.mark.parametrize("adapter_type", [SyncAdapter, AsyncAdapter])
def test_create_edit_adopt_with_same_lifecycle_and_no_implicit_replacement(adapter_type):
    adapter = adapter_type()
    session = ImageSession(uid(1), [adapter])
    initial = draft()
    create = request(adapter)
    receipt = session.submit(create)
    while receipt.status not in {"succeeded", "failed"}:
        receipt = session.poll(create.operation_id)
    selected = adopt(session, create.operation_id, initial)
    assert initial.payloads == {"existing.txt": b"unchanged"}
    adapter.output = png(color=1)
    edit = request(adapter, 3, operation="edit")
    receipt = session.edit_from_result(edit, create.operation_id)
    while receipt.status not in {"succeeded", "failed"}:
        receipt = session.poll(edit.operation_id)
    assert image_associations(selected)["portrait.main"]["sha256"] == png().sha256
    assert session.submit(create) == session.snapshot(create.operation_id)
    assert adapter.submissions == 2
    with pytest.raises(ImageHookError, match="stale_selection"):
        adopt(session, edit.operation_id, selected)
    replaced = adopt(session, edit.operation_id, selected, png().sha256)
    row = image_associations(replaced)["portrait.main"]
    assert row["sha256"] == png(color=1).sha256
    assert row["input_sha256"] == png().sha256
    assert row["session_id"] == uid(1) and row["operation_id"] == uid(3)
    assert adopt(session, edit.operation_id, replaced, png().sha256) == replaced
    assert set(selected.payloads) <= set(replaced.payloads)  # Previous media is retained.


def test_third_adapter_discloses_its_distinct_capabilities():
    sync, stencil = SyncAdapter(), StencilAdapter()
    session = ImageSession(uid(1), [sync, stencil])
    assert session.capabilities() == (sync.capabilities, stencil.capabilities)
    session.submit(request(sync))
    result = session.edit_from_result(request(stencil, 3, operation="edit", width=2), uid(2))
    assert result.status == "succeeded" and result.progress is None
    selected = adopt(session, uid(3), draft(), kind="background", target_id="location.city")
    assert image_associations(selected)["portrait.main"]["kind"] == "background"
    with pytest.raises(ImageHookError, match="unsupported_capability"):
        session.submit(request(stencil, 4, width=2))
    assert stencil.submissions == 1


@pytest.mark.parametrize(
    "change",
    [
        {"operation": "inpaint"},
        {"width": 2},
        {"workflow": "unsupported.flow"},
        {"operation": "edit"},
        {"input_image": png()},
        {"adapter_id": "synthetic.absent"},
    ],
)
def test_unsupported_request_never_dispatches(change):
    adapter = SyncAdapter()
    session = ImageSession(uid(1), [adapter])
    with pytest.raises(ImageHookError, match="unsupported_capability"):
        session.submit(request(adapter, **change))
    assert adapter.submissions == 0


@pytest.mark.parametrize(
    "failure",
    [
        ImageHookError("rate_limited"),
        ImageHookError("credentials_refused"),
        ImageHookError("server_unavailable"),
        RuntimeError("synthetic sealed exception"),
    ],
)
def test_failures_are_sanitized_and_never_retry_or_replace(failure):
    adapter = SyncAdapter()
    session = ImageSession(uid(1), [adapter])
    session.submit(request(adapter))
    selected = adopt(session, uid(2), draft())
    adapter.failure = failure
    operation = request(adapter, 3)
    result = session.submit(operation)
    assert result.status == "failed"
    assert result.error == (
        failure.code if isinstance(failure, ImageHookError) else "provider_failure"
    )
    assert session.submit(operation) == result
    assert session.poll(operation.operation_id) == result
    assert adapter.submissions == 2
    with pytest.raises(ImageHookError, match="result_unavailable"):
        adopt(session, uid(3), selected, png().sha256)
    assert image_associations(selected)["portrait.main"]["sha256"] == png().sha256


@pytest.mark.parametrize(
    "change",
    [
        {"session_id": uid(99)},
        {"operation_id": uid(99)},
        {"status": "invented"},
        {"image": ImageData(b"not png")},
        {"image": ImageData(png().data, "image/jpeg")},
        {"image": ImageData(b"x" * 1025)},
        {"image": png(2, 1)},
        {"progress": 0.5},
        {"error": "synthetic sealed message"},
        {"image": None},
    ],
)
def test_malformed_response_is_a_terminal_sanitized_failure(change):
    adapter = SyncAdapter()
    adapter.reply_change = lambda update: replace(update, **change)
    session = ImageSession(uid(1), [adapter])
    result = session.submit(request(adapter))
    assert result.status == "failed" and result.error == "invalid_response"
    assert result.result_sha256 is None
    assert session.submit(request(adapter)) == result and adapter.submissions == 1


@pytest.mark.parametrize(
    "bad_data",
    [
        "https://example.invalid/output.png",
        "file:///C:/private/output.png",
        "../../outside.png",
        Path("synthetic-untrusted.png"),
        b"<svg onload='synthetic' />",
        png().data[:-1],
        png().data + b"extra",
        png().data[:30] + b"x" + png().data[31:],
    ],
    ids=["https", "file-url", "traversal", "path-object", "svg", "truncated", "trailing", "crc"],
)
def test_untrusted_inputs_are_never_resolved_or_fetched(bad_data, monkeypatch):
    adapter = SyncAdapter()
    session = ImageSession(uid(1), [adapter])

    def forbidden(*_args, **_kwargs):
        pytest.fail("Image hooks must not open files or connect to endpoints")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    with pytest.raises(ImageHookError, match="invalid_image"):
        session.submit(request(adapter, operation="edit", input_image=ImageData(bad_data)))
    assert adapter.submissions == 0


def test_duplicate_mismatch_and_foreign_session_fail_before_dispatch():
    adapter = SyncAdapter()
    session = ImageSession(uid(1), [adapter])
    session.submit(request(adapter))
    with pytest.raises(ImageHookError, match="identity_collision"):
        session.submit(request(adapter, prompt="Other synthetic placeholder"))
    with pytest.raises(ImageHookError, match="invalid_request"):
        session.submit(request(adapter, 3, session_id=uid(99)))
    assert adapter.submissions == 1


@pytest.mark.parametrize(
    "change",
    [
        {"progress": float("nan")},
        {"progress": -1},
        {"progress": 0.25},
        {"job_token": "different-synthetic-job"},
        {"status": "pending"},
    ],
)
def test_async_status_progress_and_job_identity_cannot_regress(change):
    adapter = AsyncAdapter()
    session = ImageSession(uid(1), [adapter])
    operation = request(adapter)
    session.submit(operation)
    assert session.poll(uid(2)).progress == 0.5
    adapter.poll = lambda req, token: replace(
        ImageUpdate(req.session_id, req.operation_id, "running", progress=0.5, job_token=token),
        **change,
    )
    result = session.poll(uid(2))
    assert result.status == "failed" and result.error == "invalid_response"
    assert session.submit(operation) == result and adapter.submissions == 1


def test_cancel_without_remote_support_never_calls_remote_and_keeps_old_selection():
    adapter = AsyncAdapter()
    session = ImageSession(uid(1), [adapter])
    session.submit(request(adapter))
    session.poll(uid(2))
    session.poll(uid(2))
    selected = adopt(session, uid(2), draft())
    adapter.capabilities = replace(adapter.capabilities, cancellation=False)
    operation = request(adapter, 3)
    session.submit(operation)
    result = session.cancel(uid(3))
    assert result.status == "cancelled" and adapter.cancels == 0
    assert session.poll(uid(3)) == result
    assert adapter.polls == 2 and session.submit(operation) == result
    with pytest.raises(ImageHookError, match="result_unavailable"):
        adopt(session, uid(3), selected, png().sha256)
    assert image_associations(selected)["portrait.main"]["sha256"] == png().sha256


@pytest.mark.parametrize("end", ["cancel", "close"])
@pytest.mark.parametrize("phase", ["submit", "poll"])
def test_inflight_late_result_is_discarded_without_duplicate_dispatch(end, phase):
    adapter = AsyncAdapter()
    session = ImageSession(uid(1), [adapter])
    operation = request(adapter)
    if phase == "poll":
        session.submit(operation)
    entered, release = threading.Event(), threading.Event()

    def late_poll(req, token):
        entered.set()
        assert release.wait(5), "Synthetic late response was not released"
        return ImageUpdate(req.session_id, req.operation_id, "succeeded", image=png())

    if phase == "poll":
        adapter.poll = late_poll
    else:

        def late_submit(req):
            adapter.submissions += 1
            return late_poll(req, None)

        adapter.submit = late_submit
    replies = []
    thread = threading.Thread(
        target=lambda: replies.append(
            session.poll(uid(2)) if phase == "poll" else session.submit(operation)
        )
    )
    thread.start()
    try:
        assert entered.wait(5)
        assert session.poll(uid(2)).status == "pending"
        assert session.submit(operation).status == "pending"
        if end == "cancel":
            session.cancel(uid(2))
            session.cancel(uid(2))
        else:
            session.close()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and len(replies) == 1
    assert replies[0].status == ("cancelled" if end == "cancel" else "interrupted")
    assert replies[0].late_result_discarded and replies[0].result_sha256 is None
    assert adapter.submissions == 1
    assert adapter.cancels == (1 if end == "cancel" and phase == "poll" else 0)
    with pytest.raises(ImageHookError, match="result_unavailable"):
        adopt(session, uid(2), draft())


def test_private_values_are_absent_from_receipts_repr_logs_and_adopted_metadata(caplog, capsys):
    adapter = AsyncAdapter()
    session = ImageSession(uid(1), [adapter])
    marker = "SYNTHETIC-SEALED-PLACEHOLDER"
    operation = request(adapter, prompt=marker)
    assert marker not in repr(operation)
    assert png().data.hex() not in repr(png())
    adapter.submit = lambda req: ImageUpdate(
        req.session_id, req.operation_id, "pending", progress=0, job_token=marker
    )
    pending = session.submit(operation)
    assert marker not in json.dumps(asdict(pending))
    adapter.poll = lambda req, _token: ImageUpdate(
        req.session_id, req.operation_id, "succeeded", image=png()
    )
    session.poll(uid(2))
    selected = adopt(session, uid(2), draft())
    assert marker.encode() not in selected.payloads[ASSOCIATIONS_PATH]
    session.submit(request(adapter, 3, prompt=marker))
    adapter.poll = lambda req, _token: ImageUpdate(
        req.session_id, req.operation_id, "failed", error=marker
    )
    result = session.poll(uid(3))
    assert result.error == "invalid_response"
    assert marker not in json.dumps(asdict(result))
    assert marker not in caplog.text
    captured = capsys.readouterr()
    assert marker not in captured.out + captured.err
    session.close()
    with pytest.raises(ImageHookError, match="session_closed"):
        session.submit(request(adapter, 4))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update({"sha256": "../synthetic-output"}),
        lambda data: data.update({"sha256": "0" * 64}),
        lambda data: data.update({"input_sha256": "https://example.invalid/input.png"}),
        lambda data: data.update({"filename": "synthetic-output.png"}),
    ],
)
def test_tampered_asset_references_and_provenance_never_replace_selected_asset(mutation):
    adapter = SyncAdapter()
    session = ImageSession(uid(1), [adapter])
    session.submit(request(adapter))
    selected = adopt(session, uid(2), draft())
    rows = image_associations(selected)
    mutation(rows["portrait.main"])
    payloads = {**selected.payloads, ASSOCIATIONS_PATH: json.dumps(rows).encode()}
    tampered = replace(selected, payloads=payloads)
    with pytest.raises(ImageHookError, match="invalid_associations"):
        adopt(session, uid(2), tampered, png().sha256)
    assert image_associations(selected)["portrait.main"]["sha256"] == png().sha256


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.payloads.update({ASSOCIATIONS_PATH: b'{"duplicate":0,"duplicate":1}'}),
        lambda d: d.payloads.update({ASSOCIATIONS_PATH.upper(): b"{}"}),
        lambda d: d.payloads.update({ASSOCIATIONS_PATH: b'{"escape":"../../outside"}'}),
        lambda d: d.payloads.update({ASSOCIATIONS_PATH: b'{"url":"https://example.invalid"}'}),
        lambda d: d.payloads.update({ASSOCIATIONS_PATH: b"[" * 100}),
    ],
)
def test_malformed_associations_fail_closed_without_changing_draft(mutation):
    adapter = SyncAdapter()
    session = ImageSession(uid(1), [adapter])
    session.submit(request(adapter))
    source = draft()
    mutation(source)
    original = dict(source.payloads)
    with pytest.raises(ImageHookError, match="invalid_associations"):
        adopt(session, uid(2), source)
    assert source.payloads == original


def test_actual_lite_create_edit_adopt_save_reopen_preserves_all_original_content(tmp_path):
    """Use current Lite 117 bytes, the ordinary materializer and the real SQLite owner."""
    original = bundled_srd_archive()
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        drafts = []
        for record in manifest["packages"]:
            prefix = "packages/" + record["id"] + "/"
            payloads = {
                item["path"]: archive.read(prefix + item["path"]) for item in record["files"]
            }
            clean = {k: v for k, v in record.items() if k not in {"files", "content_sha256"}}
            drafts.append(NormalizedPackageDraft(clean, payloads, manifest["format"]))
    primary = next(d for d in drafts if d.record["id"] == "sylvia.srd-lite.bg3")
    assert primary.record["version"] == "0.3.15"
    catalogue = json.loads(primary.payloads["content/spell-catalogue.json"])
    assert catalogue["count"] == 117
    adapter = SyncAdapter()
    session = ImageSession(uid(1), [adapter])
    session.submit(request(adapter))
    selected = adopt(session, uid(2), primary)
    adapter.output = png(color=1)
    session.edit_from_result(request(adapter, 3, operation="edit"), uid(2))
    selected = adopt(session, uid(3), selected, png().sha256)
    selected = adopt(
        session,
        uid(3),
        selected,
        association_id="background.city",
        kind="background",
        target_id="location.city",
    )
    declaration_path = primary.record["content_declarations"]
    assert all(
        selected.payloads[p] == value
        for p, value in primary.payloads.items()
        if p != declaration_path
    )
    old_declarations = json.loads(primary.payloads[declaration_path])
    new_declarations = json.loads(selected.payloads[declaration_path])
    assert [
        row for row in new_declarations["inclusions"] if row["id"] != "image-hooks-selected-assets"
    ] == old_declarations["inclusions"]
    assert {k: v for k, v in old_declarations.items() if k != "inclusions"} == {
        k: v for k, v in new_declarations.items() if k != "inclusions"
    }
    with materialize_package_archive(
        tuple(selected if d is primary else d for d in drafts)
    ) as built:
        expected = built.archive.read()
    database = tmp_path / "authority.sqlite3"
    owner = AuthorityOwner.open(database, "synthetic-image-owner")
    try:
        candidate = owner.install_package_archive(io.BytesIO(expected), install_id="image-hooks")
        state = owner.create_campaign(
            CampaignCreate(
                campaign_id="synthetic-images",
                display_name="Synthetic images",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="synthetic-turn",
            )
        )
        saved_revision = state.revision
        candidate_id = candidate.candidate_id
    finally:
        owner.close()
        session.close()
    reopened = AuthorityOwner.open(database, "synthetic-image-reopen")
    try:
        assert reopened.snapshot("synthetic-images").revision == saved_revision
        exported = io.BytesIO()
        result = reopened.export_package_candidate(candidate_id, exported)
        assert result["byte_count"] == len(expected)
        assert exported.getvalue() == expected
        with zipfile.ZipFile(io.BytesIO(exported.getvalue())) as archive:
            prefix = "packages/sylvia.srd-lite.bg3/"
            for path, data in primary.payloads.items():
                assert archive.read(prefix + path) == (
                    selected.payloads[path] if path == declaration_path else data
                )
            recovered = NormalizedPackageDraft(
                selected.record,
                {path: archive.read(prefix + path) for path in selected.payloads},
                selected.format_version,
            )
            rows = image_associations(recovered)
            assert rows["portrait.main"]["sha256"] == png(color=1).sha256
            assert rows["background.city"]["target_id"] == "location.city"
            assert rows["portrait.main"]["input_sha256"] == png().sha256
    finally:
        reopened.close()
