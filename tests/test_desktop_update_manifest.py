"""Release policy and public-key verification; metadata fixtures use a labelled verifier stub.

The RFC public vector exercises real verification without creating or importing a private key.
Signed installer/portable artifact acceptance remains separate from these protocol fixtures.
"""

import base64
import hashlib
from copy import deepcopy

import pytest

from sylvia_gm.application.desktop_update.manifest import (
    ARTIFACT_LIMIT,
    MANIFEST_LIMIT,
    KeyRing,
    UpdateError,
    Version,
    bounded_json,
    canonical_bytes,
    parse_manifest,
    payload_path,
    release_url,
)


def manifest_fixture(distribution="portable"):
    from sylvia_gm.portable_artifact import LEGAL_FILES, REQUIRED_PAYLOADS

    names = (
        REQUIRED_PAYLOADS | {"ENTRY-HASHES.json", "BUILD-IDENTITY.json", "SOURCE-INPUTS.json"}
        if distribution == "portable"
        else {
            "sylvia-gm.exe",
            "sylvia-gm-owner.exe",
            *(f"licenses/{name}" for name in LEGAL_FILES),
            "licenses/SRD-5.2.1.md",
            "licenses/Severed-Lands.md",
            "licenses/dependency-notices.txt",
        }
    )
    files = []
    for name in sorted(names - {"sylvia-portable.json"}):
        value = ("fixture:" + name).encode()
        files.append(
            {"path": name, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        )
    if distribution == "portable":
        value = b'{"version":1}'
        files.append(
            {
                "path": "sylvia-portable.json",
                "size": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
        )
    suffix = "-portable.zip" if distribution == "portable" else "-setup.exe"
    return {
        "format_version": 1,
        "version": "0.3.0-alpha.2",
        "owner_version": "0.3.0-alpha.2",
        "minimum_app_version": "0.3.0-alpha.1",
        "channel": "alpha",
        "target": "windows-x86_64",
        "distribution": distribution,
        "notes": "A complete update.\nKeep your campaign.",
        "artifact": {
            "url": "https://github.com/pneuma-io/sylvia-gm/releases/download/"
            "v0.3.0-alpha.2/sylvia-gm-0.3.0-alpha.2-windows-x86_64" + suffix,
            "size": 100,
            "sha256": "a" * 64,
        },
        "files": files,
    }


def envelope(signed):
    return canonical_bytes(
        {
            "signed": signed,
            "key_id": "protocol-fixture",
            "signature": base64.b64encode(bytes(64)).decode(),
        }
    )


def accept_stub(key_id, message, signature):
    assert key_id == "protocol-fixture" and isinstance(message, bytes) and len(signature) == 64


def parse(signed, distribution="portable", verify=accept_stub):
    return parse_manifest(
        envelope(signed),
        current=Version.parse("0.3.0-alpha.1"),
        target="windows-x86_64",
        distribution=distribution,
        verify=verify,
    )


def test_semver_precedence_and_build_metadata_do_not_offer_equal_versions():
    values = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
    ]
    parsed = [Version.parse(value) for value in values]
    assert all(left < right for left, right in zip(parsed, parsed[1:], strict=False))
    assert Version.parse("1.0.0+first") == Version.parse("1.0.0+second")
    assert len({Version.parse("1.0.0+first"), Version.parse("1.0.0+second")}) == 1


@pytest.mark.parametrize(
    "value",
    [
        "",
        "v1.0.0",
        "1.0",
        "01.0.0",
        "1.0.0-alpha.01",
        "1.0.0-alpha..1",
        "1.0.0-",
        "1.0.0+foo..bar",
        True,
    ],
)
def test_invalid_release_versions_reject(value):
    with pytest.raises(UpdateError, match="SemVer"):
        Version.parse(value)


def test_oversized_portable_marker_is_rejected_before_download_or_extraction():
    signed = manifest_fixture()
    marker = next(row for row in signed["files"] if row["path"] == "sylvia-portable.json")
    marker["size"] = 129
    with pytest.raises(UpdateError) as rejected:
        parse(signed)
    assert rejected.value.code == "invalid_metadata"


@pytest.mark.parametrize("distribution", ["installed", "portable"])
def test_protocol_fixture_retains_target_distribution_and_inert_notes(distribution):
    signed = manifest_fixture(distribution)
    signed["notes"] = "<script>do not execute this</script>"
    result = parse(signed, distribution)
    assert result.version.text == signed["version"]
    assert result.distribution == distribution and result.notes == signed["notes"]
    assert result.signed_bytes == canonical_bytes(signed)
    assert result.artifact_sha256 == signed["artifact"]["sha256"]


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("version", "0.3.0-alpha.1", "not_newer"),
        ("version", "0.2.0-alpha.99", "not_newer"),
        ("minimum_app_version", "0.3.0-alpha.2", "bootstrap_required"),
        ("owner_version", "0.3.0-alpha.3", "wrong_component_version"),
        ("channel", "beta", "wrong_channel"),
        ("target", "windows-aarch64", "wrong_target"),
        ("distribution", "installed", "wrong_target"),
        ("format_version", True, "invalid_metadata"),
    ],
)
def test_signed_protocol_rejects_ineligible_identity(field, value, code):
    signed = manifest_fixture()
    signed[field] = value
    with pytest.raises(UpdateError) as caught:
        parse(signed)
    assert caught.value.code == code


@pytest.mark.parametrize(
    "value",
    [
        "http://github.com/pneuma-io/sylvia-gm/releases/download/v0.3.0-alpha.2/a-portable.zip",
        "https://github.com/another/repo/releases/download/v0.3.0-alpha.2/a-portable.zip",
        "https://github.com.evil.test/pneuma-io/sylvia-gm/releases/download/v0.3.0-alpha.2/a-portable.zip",
        "https://user@github.com/pneuma-io/sylvia-gm/releases/download/v0.3.0-alpha.2/a-portable.zip",
        "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.3.0-alpha.2/../a-portable.zip",
        "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.3.0-alpha.2/a-portable.zip?token=x",
        "https://[invalid",
        "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.3.0-alpha.2/a%2F..%2Fa-portable.zip",
        "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.3.0-alpha.2/a\\b-portable.zip",
    ],
)
def test_wrong_release_origin_or_redirect_target_cannot_be_selected(value):
    with pytest.raises(UpdateError):
        release_url(value, "0.3.0-alpha.2", "portable")


def test_official_encoded_asset_names_are_supported_without_decoded_path_escapes():
    value = "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.3.0-alpha.2/Sylvia%20GM-setup.exe"
    assert release_url(value, "0.3.0-alpha.2", "installed") == value


@pytest.mark.parametrize(
    "value",
    [
        "../x.exe",
        "/x.exe",
        "C:/x.exe",
        "x:stream",
        "a\\b.exe",
        "a/./b",
        "CON.txt",
        "CON .txt",
        "a/LPT1.txt",
        "trailing.",
        "trailing ",
        "isolated-data/authority.sqlite3",
        "participants/item.json",
        "config.yaml",
        "webview/data",
    ],
)
def test_payload_paths_cannot_escape_or_replace_mutable_data(value):
    with pytest.raises(UpdateError):
        payload_path(value)


def test_required_components_marker_and_case_aliases_are_bound_before_download():
    original = manifest_fixture()
    for mutation in ("missing_owner", "missing_marker", "case_alias"):
        signed = deepcopy(original)
        if mutation == "missing_owner":
            signed["files"] = [
                row for row in signed["files"] if row["path"] != "sylvia-gm-owner.exe"
            ]
        elif mutation == "missing_marker":
            signed["files"].pop()
        else:
            signed["files"].append(
                {**signed["files"][0], "path": signed["files"][0]["path"].upper()}
            )
        with pytest.raises(UpdateError):
            parse(signed)


@pytest.mark.parametrize("size", [True, 0, -1, ARTIFACT_LIMIT + 1, 1.5])
def test_download_lengths_are_bounded(size):
    signed = manifest_fixture()
    signed["artifact"]["size"] = size
    with pytest.raises(UpdateError):
        parse(signed)


@pytest.mark.parametrize(
    "raw", [b'{"a":1,"a":2}', b'{"x":NaN}', b"{}" * 10, b"[" * 100 + b"0" + b"]" * 100]
)
def test_invalid_metadata_is_bounded_and_does_not_reach_a_verifier(raw):
    with pytest.raises(UpdateError):
        bounded_json(raw)
    with pytest.raises(UpdateError):
        bounded_json(b"x" * (MANIFEST_LIMIT + 1))


def test_missing_trust_fails_closed_even_for_an_otherwise_valid_protocol_fixture():
    with pytest.raises(UpdateError) as caught:
        parse(manifest_fixture(), verify=KeyRing({}).verify)
    assert caught.value.code == "trust_not_configured"


def test_rfc8032_public_verification_vector_and_tamper_rejection():
    # RFC 8032 section 7.1 TEST 1: public key/signature only, never its private seed.
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    ring = KeyRing({"rfc8032": public})
    ring.verify("rfc8032", b"", signature)
    for key_id, message, changed in [
        ("wrong", b"", signature),
        ("rfc8032", b"x", signature),
        ("rfc8032", b"", signature[:-1] + b"\x00"),
    ]:
        with pytest.raises(UpdateError) as caught:
            ring.verify(key_id, message, changed)
        assert caught.value.code == "invalid_signature"
