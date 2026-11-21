"""Generate reference vectors for sylvia_provider::config from the frozen Python reference.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-provider/tests/vectors/generate_config_vectors.py

Writes `config_vectors.json` and `credential_vectors.json`:

- `load_config` (as every Sylvia caller runs it: no `.env`, credentials left sealed) over user
  settings files and environments: the model it builds, or the error, and the settings file
  afterwards (legacy credentials are migrated on read). Then `provider_credentials` for each
  provider.
- Platform protection is replaced by a deterministic fake seal so that envelopes are exact;
  every key and header here is a disposable test value. No real settings file or credential is
  read. Platform-specific messages are written as tokens.
- AES-256-GCM bytes for fixed keys and nonces, as the Linux key file seals them.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sylvia_gm.config import _credential_protection as protection
from sylvia_gm.config import _user_settings as user_settings
from sylvia_gm.config._resolver import ConfigError
from sylvia_gm.config.loader import load_config, provider_credentials

HERE = Path(__file__).parent

TOKENS = {
    protection.UNAVAILABLE_MESSAGE: "<UNAVAILABLE>",
    protection._VERIFY_FAILED: "<VERIFY_FAILED>",
    user_settings._MIGRATION_RETRY: "<MIGRATION_RETRY>",
    protection._PROTECTION: "<PROTECTION>",
}


def tokens(text: str) -> str:
    for value, token in TOKENS.items():
        text = text.replace(value, token)
    return text


def fake_seal(value: bytes, *, decrypt: bool) -> bytes:
    """The test seal: b"FAKE" + the bytes reversed. Opening anything else fails."""
    if decrypt:
        if not value.startswith(b"FAKE"):
            raise ConfigError(protection.UNAVAILABLE_MESSAGE)
        return value[4:][::-1]
    return b"FAKE" + value[::-1]


protection._platform_seal = fake_seal

B = "\\"
SEALED_KEY = protection.protect_credentials("sk-disposable-1", {"X-Test": "v1"})


def envelope_yaml(envelope: dict) -> str:
    return "{" + ", ".join(f"{k}: {json.dumps(v)}" for k, v in envelope.items()) + "}"


SEALED = envelope_yaml(SEALED_KEY)
SEALED_REF = envelope_yaml(protection.protect_credentials("${TEST_SECRET_REF}", {"X-Ref": "${TEST_SECRET_REF}"}))

CASES: list[tuple[str, str | None, dict[str, str]]] = [
    ("no-user-file", None, {}),
    ("empty-file", "", {}),
    ("null-file", "null\n", {}),
    ("list-file", "- 1\n", {}),
    ("broken-yaml", "a: [\n", {}),
    ("constructor-value-error", "a: !!int x\n", {}),
    ("plain-key-migrates", "providers:\n  openai:\n    enabled: true\n    api_key: sk-disposable-2\n", {}),
    ("plain-headers-migrate", "providers:\n  mine:\n    base_url: http://localhost:1/v1\n    headers: {X-A: one, X-B: two}\n", {}),
    ("empty-key-stays", "providers:\n  openai:\n    api_key: ''\n", {}),
    ("conflict", "providers:\n  x:\n    api_key: k\n    protected_credentials: " + SEALED + "\n", {}),
    ("non-text-key", "providers:\n  x:\n    api_key: 123\n", {}),
    ("non-text-header", "providers:\n  x:\n    headers: {X: 1}\n", {}),
    ("sealed", "providers:\n  x:\n    enabled: true\n    base_url: http://h/v1\n    protected_credentials: " + SEALED + "\n", {}),
    ("sealed-with-env-key", "providers:\n  x:\n    protected_credentials: " + SEALED + "\n",
     {"SYLVIA_GM_PROVIDERS__X__API_KEY": "sk-from-env"}),
    ("sealed-with-env-headers", "providers:\n  x:\n    protected_credentials: " + SEALED + "\n",
     {"SYLVIA_GM_PROVIDERS__X__HEADERS": '{"X-Env": "e"}'}),
    ("sealed-with-bad-env", "providers:\n  x:\n    protected_credentials: " + SEALED + "\n",
     {"SYLVIA_GM_PROVIDERS__X__API_KEY": "[1]"}),
    ("sealed-references", "providers:\n  x:\n    protected_credentials: " + SEALED_REF + "\n", {"TEST_SECRET_REF": "resolved"}),
    ("sealed-reference-unset", "providers:\n  x:\n    protected_credentials: " + SEALED_REF + "\n", {}),
    ("sealed-garbage", "providers:\n  x:\n    protected_credentials: {version: 1, protection: other, data: abc}\n", {}),
    ("sealed-not-json", "providers:\n  x:\n    protected_credentials: {when: 2026-01-01}\n", {}),
    ("sealed-bad-base64", "providers:\n  x:\n    protected_credentials: {version: 1, protection: "
     + json.dumps(protection._PROTECTION) + ", data: 'not base64!'}\n", {}),
    ("enabled-yes", "providers:\n  openai:\n    enabled: 'yes'\n", {}),
    ("enabled-one", "providers:\n  openai:\n    enabled: 1\n", {}),
    ("enabled-float", "providers:\n  openai:\n    enabled: 1.0\n", {}),
    ("enabled-two", "providers:\n  openai:\n    enabled: 2\n", {}),
    ("enabled-maybe", "providers:\n  openai:\n    enabled: maybe\n", {}),
    ("enabled-off", "providers:\n  openai:\n    enabled: off\n", {}),
    ("name-number", "providers:\n  openai:\n    name: 5\n", {}),
    ("name-binary", "providers:\n  openai:\n    name: !!binary T3BlbkFJ\n", {}),
    ("models", "providers:\n  x:\n    models:\n    - {id: m, name: M, context_window: 8192}\n    - {id: n, when: 2026-01-01}\n", {}),
    ("models-not-dicts", "providers:\n  x:\n    models: [1]\n", {}),
    ("models-int-keys", "providers:\n  x:\n    models: [{1: 2}]\n", {}),
    ("models-not-list", "providers:\n  x:\n    models: {a: 1}\n", {}),
    ("headers-plain-text-values", "providers:\n  x:\n    headers: {X: 'a'}\n", {}),
    ("providers-not-mapping", "providers: x\n", {}),
    ("provider-not-mapping", "providers:\n  x: 5\n", {}),
    ("non-text-top-key", "1: a\n", {}),
    ("app-null-keeps-default", "app: null\n", {}),
    ("app-override", "app: {name: Mine, extra: 1}\n", {}),
    ("unknown-top-level", "custom: {x: 1}\n", {}),
    ("var-set", "app: {name: '${TEST_NAME_VAR}'}\n", {"TEST_NAME_VAR": "Named"}),
    ("var-unset", "app: {name: '${TEST_UNSET_VAR}'}\n", {}),
    ("var-escaped", "app: {name: 'a $${TEST_NAME_VAR} b'}\n", {"TEST_NAME_VAR": "Named"}),
    ("var-in-list", "providers:\n  x:\n    models: [{id: '${TEST_NAME_VAR}'}]\n", {"TEST_NAME_VAR": "m"}),
    ("var-unset-in-list", "providers:\n  x:\n    models: [{id: '${TEST_UNSET_VAR}'}]\n", {}),
    ("var-bad-name", "app: {name: '${1X} ${ X} $X ${}'}\n", {}),
    ("env-app-name", None, {"SYLVIA_GM_APP__NAME": "From env"}),
    ("env-bool", None, {"SYLVIA_GM_PROVIDERS__OPENAI__ENABLED": "TRUE"}),
    ("env-bool-other", None, {"SYLVIA_GM_PROVIDERS__OPENAI__ENABLED": "on"}),
    ("env-json-object", None, {"SYLVIA_GM_TTS__VOICES": '{"dm": "x"}'}),
    ("env-json-broken", None, {"SYLVIA_GM_TTS__VOICE": "[1,"}),
    ("env-json-list", None, {"SYLVIA_GM_PROVIDERS__OPENAI__MODELS": ' [{"id": "m"}]'}),
    ("env-int-bad", "extra: {count: 3}\n", {"SYLVIA_GM_EXTRA__COUNT": "abc"}),
    ("env-int-good", "extra: {count: 3}\n", {"SYLVIA_GM_EXTRA__COUNT": " 7 "}),
    ("env-float", "extra: {ratio: 1.5}\n", {"SYLVIA_GM_EXTRA__RATIO": "2.5e1"}),
    ("env-new-path", None, {"SYLVIA_GM_PROVIDERS__NEWONE__BASE_URL": "http://new/v1"}),
    ("env-through-scalar", "extra: 5\n", {"SYLVIA_GM_EXTRA__INNER": "x"}),
    ("env-empty-path", None, {"SYLVIA_GM_": "x"}),
    ("env-mixed-case", None, {"SYLVIA_GM_App__Name": "Mixed"}),
    ("crlf-file", "providers:\r\n  openai:\r\n    enabled: true\r\n", {}),
    ("unicode", "app: {name: 'Sylvia \u00e9 \U0001F3B2'}\n", {}),
    ("deep-merge-lists-replace", "providers:\n  openai:\n    models: [{id: a}]\n", {}),
    ("deep-merge-null-removes", "providers:\n  openai: null\n", {}),
    ("tts-voices-bad", "tts: {voices: {dm: 1}}\n", {}),
    ("credential-error-field", "providers:\n  x:\n    credential_error: blocked\n", {}),
    ("credential-overrides-plain", "providers:\n  x:\n    credential_overrides: '{\"api_key\": \"o\"}'\n", {}),
]


def data_record(value):
    """Python values with their types."""
    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        return ["float", repr(value)]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    if isinstance(value, datetime.datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, datetime.date):
        return ["date", value.isoformat()]
    if isinstance(value, (list, tuple)):
        return ["list", [data_record(item) for item in value]]
    if isinstance(value, dict):
        return ["dict", [[data_record(k), data_record(v)] for k, v in value.items()]]
    raise TypeError(type(value))


def secret(value):
    return None if value is None else tokens(value.get_secret_value())


def config_record(config) -> dict:
    return {
        "app": config.app.model_dump(),
        "logging": config.logging.model_dump(),
        "ruleset": config.ruleset.model_dump(),
        "tts": {**config.tts.model_dump(), "voices": list(config.tts.voices.items())},
        "providers": [
            [provider_id, {
                "enabled": p.enabled, "preset_id": p.preset_id, "mode": p.mode, "name": p.name,
                "base_url": p.base_url, "api_key": secret(p.api_key),
                "headers": [[k, secret(v)] for k, v in p.headers.items()],
                "protected_credentials": secret(p.protected_credentials),
                "credential_overrides": secret(p.credential_overrides),
                "credential_error": None if p.credential_error is None else tokens(p.credential_error),
                "models": [data_record(row) for row in p.models],
            }]
            for provider_id, p in config.providers.items()
        ],
    }


def error_record(error: BaseException) -> dict:
    return {"class": type(error).__name__, "message": tokens(str(error))}


def run_case(folder: Path, user_text: str | None, env: dict[str, str]) -> dict:
    for name in list(os.environ):
        if name.upper().startswith("SYLVIA_GM_") or name.upper().startswith("TEST_"):
            del os.environ[name]
    os.environ.update(env)
    user_path = folder / "config.yaml"
    if user_path.exists():
        user_path.unlink()
    if user_text is not None:
        user_path.write_bytes(user_text.encode("utf-8"))
    out: dict = {}
    try:
        config = load_config(user_path, load_dotenv_file=False, decrypt_credentials=False)
        out["config"] = config_record(config)
        credentials = []
        for provider_id, definition in config.providers.items():
            try:
                key, headers = provider_credentials(definition)
                credentials.append([provider_id, {"key": key, "headers": list(headers.items())}])
            except Exception as error:  # noqa: BLE001
                credentials.append([provider_id, {"error": error_record(error)}])
        out["credentials"] = credentials
    except Exception as error:  # noqa: BLE001
        out["error"] = error_record(error)
    out["file_after"] = tokens(user_path.read_bytes().decode("utf-8")) if user_path.exists() else None
    for name in env:
        os.environ.pop(name, None)
    return out


def aes_gcm_vectors() -> list:
    out = []
    for index in range(6):
        key = bytes((index * 31 + i) % 256 for i in range(32))
        nonce = bytes((index * 7 + i) % 256 for i in range(12))
        plaintext = json.dumps({"api_key": f"sk-disposable-{index}", "headers": {"X-T": "v" * index}}, ensure_ascii=False).encode()
        sealed = nonce + AESGCM(key).encrypt(nonce, plaintext, b"sylvia-gm:linux-user-key-file:v1")
        out.append({"key": key.hex(), "nonce": nonce.hex(), "plaintext": plaintext.hex(), "sealed": sealed.hex()})
    return out


def main() -> int:
    cases = []
    with tempfile.TemporaryDirectory() as folder:
        for name, user_text, env in CASES:
            user = None if user_text is None else tokens(user_text)
            cases.append({"name": name, "user": user, "env": env, **run_case(Path(folder), user_text, env)})
    document = {"python": sys.version.split()[0], "platform": sys.platform, "cases": cases}
    (HERE / "config_vectors.json").write_bytes((json.dumps(document, ensure_ascii=True, indent=1) + "\n").encode("ascii"))
    credentials = {"aes_gcm": aes_gcm_vectors()}
    (HERE / "credential_vectors.json").write_bytes((json.dumps(credentials, indent=1) + "\n").encode("ascii"))
    print(f"{len(cases)} load_config cases -> {HERE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
