"""Reference vectors for `infrastructure/launch_handshake.py`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_handshake_vectors.py

Writes `handshake_vectors.json`:
- `derive`: `derive_owner_response` over valid secrets, secrets with the ASCII whitespace
  `bytes.fromhex` skips between pairs, upper-case digits and every rejection;
- `verify`: `LaunchHandshake.verify` outcomes for known secrets and presented responses;
- `lifecycle`: the error each misuse raises.

The two secrets lane 5 recorded in `crates/sylvia-owner/tests/vectors` are included. Reference
tooling only; retired with the snapshot.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from sylvia_gm.infrastructure import launch_handshake as h  # noqa: E402

OUT = Path(__file__).resolve().with_name("handshake_vectors.json")


def outcome(call) -> dict[str, object]:  # type: ignore[no-untyped-def]
    try:
        value = call()
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "message": str(error)}
    return {"ok": value}


def secrets_to_derive() -> list[str]:
    rng = random.Random(20261121)
    cases = [
        "00" * 32,
        "0123456789abcdef" * 4,
        "0123456789ABCDEF" * 4,
        "ff" * 32,
        "abc",
        "",
        "zz" * 32,
        "0" * 63,
        "0" * 65,
        " " + "00" * 31 + "0 ",
        "00 " * 21 + "0",
        "00\t" * 21 + "0",
        "00\n00\r00\x0b00\x0c" + "00" * 26 + "    ",
        " " * 64,
        "0 0" + "00" * 30 + "0",
        "é" * 64,
        "0" * 62 + "éé",
        "00" * 31 + "٠١",
        "00" * 31 + "\x1c0",
    ]
    for _ in range(40):
        cases.append(bytes(rng.getrandbits(8) for _ in range(32)).hex())
    return cases


def main() -> None:
    derive = [
        {"secret": secret, **outcome(lambda secret=secret: h.derive_owner_response(secret))}
        for secret in secrets_to_derive()
    ]
    known = "0123456789abcdef" * 4
    good = h.derive_owner_response(known)
    presented = [good, good.upper(), good[:-1], good + "0", "", "00" * 32, "é" * 64, "x"]
    verify = []
    for response in presented:
        handshake = h.LaunchHandshake()
        handshake._sealed = h._Sealed(secret=bytes.fromhex(known), expected_response=good)
        verify.append({"response": response, **outcome(lambda: handshake.verify(response))})

    issued_twice = h.LaunchHandshake()
    issued_twice.issue()
    consumed = h.LaunchHandshake()
    consumed.issue()
    consumed.verify(h.derive_owner_response(consumed._sealed.secret.hex()))  # type: ignore[union-attr]
    lifecycle = {
        "issue_after_issue": outcome(issued_twice.issue),
        "issue_after_verify": outcome(consumed.issue),
        "verify_after_verify": outcome(lambda: consumed.verify(good)),
        "verify_without_issue": outcome(lambda: h.LaunchHandshake().verify(good)),
        "repr": repr(h.LaunchHandshake()),
    }
    vectors = {
        "python": sys.version.split()[0],
        "transcript": h._HANDSHAKE_TRANSCRIPT.decode("ascii"),
        "derive": derive,
        "verify_secret": known,
        "verify": verify,
        "lifecycle": lifecycle,
    }
    OUT.write_bytes((json.dumps(vectors, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
