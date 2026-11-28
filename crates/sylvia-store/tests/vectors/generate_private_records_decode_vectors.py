"""`private_records_archive.decode` on hand-made members: the encodings `json.loads(bytes)`
detects (UTF-8 with and without its byte order mark, UTF-16 and UTF-32 with and without theirs),
empty data, line endings, and the refusals.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_private_records_decode_vectors.py

It writes `private_records_decode.json`: each case's member bytes (hex) and its rows or refusal;
`tests/private_records_decode.rs` decodes the same bytes. A lone surrogate, which a Python string
can hold, is `docs/rust-port/differences.md` row 3 and is not a case here. Reference tooling only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

from sylvia_gm.infrastructure import private_records_archive as archive  # noqa: E402

SESSIONS = {"session-a"}


def row(order: int = 0, attempt: str = "attempt-a", status: str = "completed") -> dict[str, object]:
    return {"session_id": "session-a", "attempt_id": attempt, "response_id": f"response-{order}",
            "turn_id": "turn-a", "basis_message_id": "message-a", "source_order": order, "provider_id": "standin",
            "model_id": "standin/standin-narrator", "kind": "thinking", "payload": "Weighing the door.",
            "status": status}


def text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


LINE = text(row())
SECOND = text(row(1))
CASES: list[tuple[str, bytes]] = [
    ("empty", b""),
    ("utf-8", (LINE + "\n" + SECOND + "\n").encode("utf-8")),
    ("utf-8 non-ascii", (text({**row(), "payload": "Le café brûle."}) + "\n").encode("utf-8")),
    ("utf-8 bom", b"\xef\xbb\xbf" + (LINE + "\n").encode("utf-8")),
    ("crlf", (LINE + "\r\n" + SECOND + "\r\n").encode("utf-8")),
    ("utf-16-be", (LINE + "\n").encode("utf-16-be")),
    ("utf-16 bom be", b"\xfe\xff" + (LINE + "\n").encode("utf-16-be")),
    ("utf-32-be", (LINE + "\n").encode("utf-32-be")),
    ("utf-32 bom be", b"\x00\x00\xfe\xff" + (LINE + "\n").encode("utf-32-be")),
    ("utf-16-le odd tail", (LINE).encode("utf-16-le") + b"\n"),
    ("utf-16 bom le odd tail", b"\xff\xfe" + LINE.encode("utf-16-le") + b"\n"),
    ("mixed encodings", (LINE + "\n").encode("utf-8") + (SECOND + "\n").encode("utf-16-be")),
    ("no final newline", LINE.encode("utf-8")),
    ("invalid utf-8", b"\xff" + (LINE + "\n").encode("utf-8")),
    ("two boms", b"\xef\xbb\xbf\xef\xbb\xbf" + (LINE + "\n").encode("utf-8")),
    ("duplicate key", (LINE[:-1] + ', "status": "completed"}\n').encode("utf-8")),
    ("not an object", b"[1, 2]\n"),
    ("blank line", (LINE + "\n\n").encode("utf-8")),
    ("foreign session", (text({**row(), "session_id": "session-b"}) + "\n").encode("utf-8")),
    ("nan", (LINE[:-1] + ', "extra": NaN}\n').encode("utf-8")),
]


def outcome(data: bytes) -> dict[str, object]:
    try:
        return {"rows": list(archive.decode(data, session_ids=set(SESSIONS)))}
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}


def main() -> None:
    record = {"python": sys.version.split()[0], "session_ids": sorted(SESSIONS),
              "cases": [{"name": name, "hex": data.hex(), "outcome": outcome(data)} for name, data in CASES]}
    (HERE / "private_records_decode.json").write_bytes(
        (json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
