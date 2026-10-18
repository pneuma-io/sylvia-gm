"""Small hostile imports fail at their resource boundary, before ZIP allocation."""

import io
import struct
import zipfile

import pytest

from sylvia_gm.application import import_repair, plan_bundle
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.infrastructure import campaign_archive, package_archive
from sylvia_gm.infrastructure.zip_bounds import preflight_zip_directory


def archive_bytes(count=1, content=b"test"):
    """Create tiny disposable members; tests reduce limits instead of exhausting resources."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index in range(count):
            archive.writestr(f"item-{index}.txt", content)
    return output.getvalue()


def test_package_source_read_stops_at_total_limit(monkeypatch):
    monkeypatch.setattr(package_archive, "PACKAGE_ARCHIVE_BYTE_LIMIT", 128, raising=False)
    source = io.BytesIO(b"x" * 1024)
    with pytest.raises(CommandRejected) as failure:
        package_archive.admit_package_archive(source)
    assert failure.value.code == "archive_limit"
    assert source.tell() == 129


@pytest.mark.parametrize("route", ["package", "plan", "repair"])
@pytest.mark.parametrize("lie_about_count", [False, True])
def test_excess_members_rejected_before_zipfile_allocation(monkeypatch, route, lie_about_count):
    data = archive_bytes(6)
    if lie_about_count:
        data = bytearray(data)
        struct.pack_into("<HH", data, len(data) - 14, 1, 1)
        data = bytes(data)
    monkeypatch.setattr(package_archive, "PACKAGE_MEMBER_COUNT_LIMIT", 5, raising=False)
    monkeypatch.setattr(import_repair, "_MEMBER_COUNT_LIMIT", 5)

    def forbidden(*args, **kwargs):
        pytest.fail("ZIP metadata was allocated before checking the member limit")

    monkeypatch.setattr(zipfile, "ZipFile", forbidden)
    if route == "package":
        with pytest.raises(CommandRejected):
            package_archive.admit_package_archive(io.BytesIO(data))
    elif route == "plan":
        with pytest.raises(ValueError):
            plan_bundle._read_bundle(io.BytesIO(data))
    else:
        with pytest.raises(ValueError):
            import_repair._read_safe_zip(data, source_sha256="a" * 64)


def test_package_rejects_expansion_before_reading_members(monkeypatch):
    data = archive_bytes(2, b"x" * 100)
    monkeypatch.setattr(package_archive, "PACKAGE_EXPANDED_BYTE_LIMIT", 150, raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Oversized archive was decompressed")

    monkeypatch.setattr(zipfile.ZipFile, "open", forbidden)
    with pytest.raises(CommandRejected) as failure:
        package_archive.admit_package_archive(io.BytesIO(data))
    assert failure.value.code == "archive_limit"


@pytest.mark.parametrize("compression", [zipfile.ZIP_LZMA, zipfile.ZIP_BZIP2])
@pytest.mark.parametrize("route", ["package", "plan", "repair", "recovery"])
def test_unbounded_codecs_rejected_before_decompressor_creation(monkeypatch, compression, route):
    raw = io.BytesIO()
    names = sorted(plan_bundle._REQUIRED_MEMBERS) if route == "plan" else ["manifest.json"]
    with zipfile.ZipFile(raw, "w") as archive:
        for name in names:
            info = campaign_archive.fixed_zip_info(name)
            info.compress_type = compression
            archive.writestr(info, b"{}")
    data = raw.getvalue()

    def forbidden(*args, **kwargs):
        pytest.fail("Unbounded decompression was started")

    monkeypatch.setattr(zipfile.ZipFile, "open", forbidden)
    if route == "package":
        with pytest.raises(CommandRejected):
            package_archive.admit_package_archive(io.BytesIO(data))
    elif route == "plan":
        with pytest.raises(ValueError, match="compression"):
            plan_bundle._read_bundle(io.BytesIO(data))
    elif route == "repair":
        with pytest.raises(ValueError, match="compression"):
            import_repair._read_safe_zip(data, source_sha256="a" * 64)
    else:
        with pytest.raises(CommandRejected):
            campaign_archive._discover_recovery_manifest(io.BytesIO(data))


@pytest.mark.parametrize("zip64", [False, True])
def test_bounded_directory_retains_valid_comments_and_zip64(monkeypatch, zip64):
    if zip64:
        monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 1)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.comment = b"A small valid archive comment"
        archive.writestr("sample.txt", b"hello")
    preflight_zip_directory(raw, maximum_members=1)
    with zipfile.ZipFile(raw) as archive:
        assert archive.read("sample.txt") == b"hello"


def test_recovery_preflight_accepts_optional_zip64_footer(monkeypatch):
    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 1)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr(campaign_archive.fixed_zip_info("manifest.json"), b"{}")
    campaign_archive._recovery_zip_preflight(raw, len(raw.getvalue()))
    assert raw.tell() == 0


def test_recovery_counts_directory_members_even_when_footer_lies(monkeypatch):
    data = bytearray(archive_bytes(4))
    struct.pack_into("<HH", data, len(data) - 14, 1, 1)
    monkeypatch.setitem(campaign_archive.LIMITS, "member_count", 3)
    with pytest.raises(CommandRejected):
        campaign_archive._recovery_zip_preflight(io.BytesIO(data), len(data))


@pytest.mark.parametrize("depth", [400, 1600])
def test_desktop_recovery_rejects_deep_json_without_internal_error(tmp_path, depth):
    """Tiny nested manifests reject cleanly, leaving the owner ready for another command."""
    from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
    from tests.test_builder_library import LibraryProvider

    source = tmp_path / "private-recovery-name.sylvia-gm-campaign"
    manifest = b'{"campaign_id":"synthetic-probe","captured_revision":0,"nested":'
    manifest += b"[" * depth + b"0" + b"]" * depth + b"}"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(campaign_archive.fixed_zip_info("manifest.json"), manifest)
    harness = _OwnerHarness(tmp_path / "owner.sqlite3", LibraryProvider(responses=[]))
    try:
        harness.send("invalid", {"method": "recover_campaign_file", "params": {
            "recovery_id": "deep-json-probe", "source_path": str(source),
        }})
        invalid = harness.read()
        assert invalid["ok"] is False
        assert invalid["error"]["code"] == "portable_export_invalid_manifest"
        assert source.name not in invalid["error"]["message"]
        assert "RecursionError" not in invalid["error"]["message"]
        assert "hash conflict" not in invalid["error"]["message"]
        assert "Try another export" in invalid["error"]["message"]
        assert harness.request("still-usable", "campaign_index") == []
    finally:
        harness.close()


def test_desktop_plan_rejection_distinguishes_invalid_archive_from_missing_file(tmp_path):
    from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
    from tests.test_builder_library import LibraryProvider

    source = tmp_path / "private-plan-name.sylvia-gm-plan"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_BZIP2) as archive:
        for name in plan_bundle._REQUIRED_MEMBERS:
            archive.writestr(name, b"{}")
    harness = _OwnerHarness(tmp_path / "owner.sqlite3", LibraryProvider(responses=[]))
    try:
        harness.send("invalid", {"method": "install_package_file", "params": {
            "install_id": "invalid", "source_path": str(source),
        }})
        invalid = harness.read()
        assert invalid["ok"] is False
        message = invalid["error"]["message"]
        assert "archive" in message and "Store or Deflate" in message
        assert "unavailable" not in message and source.name not in message
        harness.send("missing", {"method": "install_package_file", "params": {
            "install_id": "missing", "source_path": str(tmp_path / "missing.sylvia-gm-plan"),
        }})
        missing = harness.read()
        assert missing["ok"] is False
        assert missing["error"]["message"] == "Selected package file is unavailable"
        assert harness.request("still-usable", "campaign_index") == []
    finally:
        harness.close()


def test_builder_ruleset_source_bounds_aliases_before_merge_expansion(monkeypatch):
    import yaml

    from sylvia_gm.builder import _ruleset_source

    lines = ['base: &a0 {label: small}']
    lines.extend(
        f'merged{index}: &a{index} {{<<: [*a{index - 1}, *a{index - 1}]}}'
        for index in range(1, 12)
    )
    monkeypatch.setattr(_ruleset_source, '_SOURCE_NODE_LIMIT', 1024, raising=False)

    def forbidden_expansion(*args, **kwargs):
        pytest.fail('Builder source expanded YAML merges before checking its parser budget')

    monkeypatch.setattr(yaml.constructor.SafeConstructor, 'flatten_mapping', forbidden_expansion)
    with pytest.raises(CommandRejected) as rejected:
        _ruleset_source.compile_ruleset_source('\n'.join(lines).encode())
    assert rejected.value.diagnostics[0].code == 'ruleset_source_yaml_invalid'


def test_deep_plan_json_still_returns_bounded_repair_diagnostics(tmp_path):
    """Repair inspection must not turn an already rejected Plan into an internal error."""
    source = tmp_path / 'synthetic-deep-record.sylvia-gm-plan'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('record.json', b'{"nested":' + b'[' * 1600 + b'0' + b']' * 1600 + b'}')
    result = import_repair.inspect_repair_source(source)
    assert result.kind == 'plan'
    assert result.identity is None
    assert result.diagnostics
    assert all(len(item.message) <= 500 and 'RecursionError' not in item.message
               for item in result.diagnostics)


@pytest.mark.parametrize('route', ['streamed_manifest', 'materialized_manifest', 'staging'])
def test_recovery_rejects_deep_json_inside_string_records(route):
    """Recovery also parses manifests embedded as strings in otherwise shallow records."""
    nested = '[' * 1600 + '0' + ']' * 1600
    summary = campaign_archive._AuthorityScanSummary()
    with pytest.raises(CommandRejected) as rejected:
        if route == 'streamed_manifest':
            campaign_archive._record_manifest_summary(summary, {'canonical_json': nested})
        elif route == 'materialized_manifest':
            rows = {table: [] for table in campaign_archive.TABLES_IN_ORDER}
            rows['package_manifests'] = [{'canonical_json': nested}]
            campaign_archive._validate_manifest_derived_package_graph(rows)
        else:
            row = {'install_id': 'synthetic-probe', 'status': 'validated',
                   'manifest_json': nested, 'report_json': '{}'}
            encoded = campaign_archive.canonical_authority_record('package_staging', row)
            campaign_archive._record_authority_summary(
                summary, encoded[:-1], expected_campaign_id='synthetic-probe',
                source_schema_version=campaign_archive.SOURCE_AUTHORITY_SCHEMA_VERSION,
            )
    assert rejected.value.code == 'portable_export_invalid_package_graph'
