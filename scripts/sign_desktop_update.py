"""Offline release operator tool. Private keys never enter the updater, Git or artifact metadata.

Key creation/import requires the release owner's separate approval. Merely importing this
module or requesting --help never creates a key, prompts for a password or changes storage.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sylvia_gm.application.desktop_update.manifest import (
    MANIFEST_LIMIT,
    KeyRing,
    UpdateError,
    Version,
    bounded_json,
    canonical_bytes,
    parse_manifest,
)
from sylvia_gm.application.desktop_update.storage import checked_file, directory_identity


def _publish_new(path: Path, raw: bytes) -> None:
    """Explicit operator-selected new files only; never overwrite or follow an existing link."""
    directory_identity(path.parent)
    if not path.is_absolute() or path.exists() or path.is_symlink():
        raise UpdateError("signer_destination", "Signing output must be a new absolute local file.")
    with path.open("xb") as output:
        if output.write(raw) != len(raw):
            raise UpdateError(
                "signer_write_failed", "Signing output could not be written completely."
            )
        output.flush()
        os.fsync(output.fileno())


def _public(key: object) -> dict[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not isinstance(key, Ed25519PrivateKey):
        raise UpdateError("wrong_signing_key", "This signing key is not Ed25519.")
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {
        "key_id": "ed25519-" + hashlib.sha256(raw).hexdigest()[:24],
        "public_key_base64": base64.b64encode(raw).decode("ascii"),
    }


def _protected(key: object) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from sylvia_gm.config._credential_protection import _dpapi

    if not isinstance(key, Ed25519PrivateKey):
        raise UpdateError("wrong_signing_key", "This signing key is not Ed25519.")
    raw = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    # The existing primitive uses current-user protection and forbids native prompts.
    protected = _dpapi(raw, decrypt=False)
    if not isinstance(protected, bytes):
        raise UpdateError("protection_failed", "The signing key could not be protected.")
    return protected


def _load(path: Path) -> Ed25519PrivateKey:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from sylvia_gm.config._credential_protection import _dpapi

    protected = checked_file(path, maximum=16 * 1024)
    return Ed25519PrivateKey.from_private_bytes(_dpapi(protected, decrypt=True))


def _password(*, confirm: bool) -> bytes:
    if not sys.stdin.isatty():
        raise UpdateError(
            "interactive_password_required",
            "Enter the recovery password directly in a local terminal.",
        )
    password = getpass.getpass("Recovery backup password: ")
    if len(password) < 16:
        raise UpdateError(
            "recovery_password_short", "Use a recovery password of at least sixteen characters."
        )
    if confirm and password != getpass.getpass("Confirm recovery backup password: "):
        raise UpdateError("recovery_password_mismatch", "The recovery passwords did not match.")
    return password.encode("utf-8")


def _check_outputs(paths: list[Path]) -> None:
    if len(set(paths)) != len(paths):
        raise UpdateError("signer_destination", "Each signing output needs a distinct new file.")
    for path in paths:
        directory_identity(path.parent)
        if not path.is_absolute() or path.exists() or path.is_symlink():
            raise UpdateError(
                "signer_destination", "Signing output must be a new absolute local file."
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    generate = actions.add_parser(
        "generate", help="Create a separately approved current-user-protected key"
    )
    generate.add_argument("--purpose", choices=("fixture", "production"), required=True)
    generate.add_argument("--store", type=Path, required=True)
    generate.add_argument("--public-output", type=Path, required=True)
    generate.add_argument("--encrypted-backup", type=Path)
    recover = actions.add_parser(
        "recover", help="Import a separately approved encrypted recovery key"
    )
    recover.add_argument("--encrypted-backup", type=Path, required=True)
    recover.add_argument("--store", type=Path, required=True)
    recover.add_argument("--public-output", type=Path, required=True)
    sign = actions.add_parser(
        "sign", help="Sign one bounded release manifest using an approved key"
    )
    sign.add_argument("--store", type=Path, required=True)
    sign.add_argument("--manifest", type=Path, required=True)
    sign.add_argument("--validation-current-version", required=True)
    sign.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if sys.platform != "win32":
            raise UpdateError(
                "windows_required", "This signing store uses current-user Windows protection."
            )
        if args.action == "generate":
            if (args.purpose == "production") != (args.encrypted_backup is not None):
                raise UpdateError(
                    "backup_required",
                    "Production needs an encrypted recovery backup; disposable fixtures have none.",
                )
            outputs = [args.store, args.public_output]
            if args.encrypted_backup is not None:
                outputs.append(args.encrypted_backup)
            _check_outputs(outputs)
            password = _password(confirm=True) if args.encrypted_backup is not None else None
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            key = Ed25519PrivateKey.generate()
            if password is not None:
                backup = key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.BestAvailableEncryption(password),
                )
                _publish_new(args.encrypted_backup, backup)
            _publish_new(args.store, _protected(key))
            public = _public(key)
            _publish_new(args.public_output, canonical_bytes({"purpose": args.purpose, **public}))
        elif args.action == "recover":
            _check_outputs([args.store, args.public_output])
            from cryptography.hazmat.primitives.serialization import load_pem_private_key

            key = load_pem_private_key(
                checked_file(args.encrypted_backup, maximum=16 * 1024), _password(confirm=False)
            )
            public = _public(key)
            _publish_new(args.store, _protected(key))
            _publish_new(args.public_output, canonical_bytes(public))
        else:
            _check_outputs([args.output])
            raw = checked_file(args.manifest, maximum=MANIFEST_LIMIT)
            signed = bounded_json(raw)
            if not isinstance(signed, dict):
                raise UpdateError(
                    "invalid_metadata", "The unsigned release manifest must be an object."
                )
            key = _load(args.store)
            public = _public(key)
            envelope = canonical_bytes(
                {
                    "signed": signed,
                    "key_id": public["key_id"],
                    "signature": base64.b64encode(key.sign(canonical_bytes(signed))).decode(
                        "ascii"
                    ),
                }
            )
            # Reuse the shipped client's verifier and full policy, including signed file identities.
            parse_manifest(
                envelope,
                current=Version.parse(args.validation_current_version),
                target=str(signed.get("target")),
                distribution=str(signed.get("distribution")),
                verify=KeyRing(
                    {public["key_id"]: base64.b64decode(public["public_key_base64"])}
                ).verify,
            )
            _publish_new(args.output, envelope)
        print(json.dumps({"status": "complete", **public}))
        return 0
    except Exception as error:
        # Keep private inputs and OS/crypto error details out of output.
        code = error.code if isinstance(error, UpdateError) else "signing_failed"
        print(json.dumps({"status": "error", "code": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
