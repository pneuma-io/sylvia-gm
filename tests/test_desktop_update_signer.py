"""Signer failure paths never generate/import a key; no positive signing call is run here."""

import importlib.util
import json
from pathlib import Path

import pytest


def signer():
    source = Path(__file__).resolve().parents[1] / "scripts/sign_desktop_update.py"
    spec = importlib.util.spec_from_file_location("updater_signer_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_import_and_help_never_create_files_or_enter_a_signing_action(tmp_path, monkeypatch):
    module = signer()
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exited:
        module.main(["--help"])
    assert exited.value.code == 0
    assert list(tmp_path.iterdir()) == []


def test_generation_rejects_existing_destination_before_any_key_generation(
    tmp_path, capsys, monkeypatch
):
    module = signer()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    monkeypatch.setattr(
        Ed25519PrivateKey, "generate", lambda: pytest.fail("key generation is not approved")
    )
    existing = tmp_path / "existing.dpapi"
    existing.write_bytes(b"existing synthetic fixture")
    assert (
        module.main(
            [
                "generate",
                "--purpose",
                "fixture",
                "--store",
                str(existing),
                "--public-output",
                str(tmp_path / "public.json"),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().err)["code"] == "signer_destination"
    assert existing.read_bytes() == b"existing synthetic fixture"
    assert not (tmp_path / "public.json").exists()


def test_production_requires_backup_before_creating_or_importing_a_key(tmp_path, capsys):
    module = signer()
    assert (
        module.main(
            [
                "generate",
                "--purpose",
                "production",
                "--store",
                str(tmp_path / "key.dpapi"),
                "--public-output",
                str(tmp_path / "public.json"),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().err)["code"] == "backup_required"
    assert list(tmp_path.iterdir()) == []


def test_bad_public_manifest_is_rejected_before_key_store_access(tmp_path, capsys, monkeypatch):
    module = signer()
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b"[]")
    monkeypatch.setattr(module, "_load", lambda *_: pytest.fail("must not open signing store"))
    assert (
        module.main(
            [
                "sign",
                "--store",
                str(tmp_path / "key.dpapi"),
                "--manifest",
                str(manifest),
                "--validation-current-version",
                "0.3.0-alpha.1",
                "--output",
                str(tmp_path / "signed.json"),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().err)["code"] == "invalid_metadata"
    assert not (tmp_path / "signed.json").exists()


def test_signing_failure_never_echoes_private_error_details(tmp_path, capsys, monkeypatch):
    module = signer()
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b"{}")

    def fail(_path):
        raise OSError("PRIVATE PATH AND KEY MATERIAL")

    monkeypatch.setattr(module, "_load", fail)
    assert (
        module.main(
            [
                "sign",
                "--store",
                str(tmp_path / "key.dpapi"),
                "--manifest",
                str(manifest),
                "--validation-current-version",
                "0.3.0-alpha.1",
                "--output",
                str(tmp_path / "signed.json"),
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert "PRIVATE" not in output.out + output.err
    assert json.loads(output.err)["code"] == "signing_failed"
    assert not (tmp_path / "signed.json").exists()
