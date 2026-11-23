"""A scripted stand-in for the Codex app-server, for `generate_chatgpt_vectors.py` (lane 5).

Python's `CodexConnection` starts `<interpreter> app-server --stdio ...` in `<home>/workspace`,
so this file is copied there as `app-server`. It answers `initialize` itself and every other
request from `<home>/standin.json` in order: `{"method": m, "result": {...}}` or
`{"method": m, "error": {...}}`, with optional `"notify"` notifications written before the
reply. It records every line it receives in `<home>/standin-log.jsonl`.
`tests/chatgpt_vectors.rs` is the same stand-in in Rust.
"""

import json
import sys
from pathlib import Path


def main() -> int:
    home = Path.cwd().parent
    steps = json.loads((home / "standin.json").read_text(encoding="utf-8"))["steps"]
    log_path = home / "standin-log.jsonl"
    position_path = home / "standin-position"
    position = int(position_path.read_text(encoding="utf-8")) if position_path.exists() else 0

    def write(message) -> None:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        message = json.loads(line)
        with log_path.open("a", encoding="utf-8", newline="\n") as log:
            log.write(json.dumps(message, sort_keys=True) + "\n")
        if "id" not in message:
            continue
        if message.get("method") == "initialize":
            write({"id": message["id"], "result": {"userAgent": "standin"}})
            continue
        step = steps[position] if position < len(steps) else {"method": None, "error": {"message": "no step"}}
        position += 1
        position_path.write_text(str(position), encoding="utf-8")
        for notification in step.get("notify", []):
            write(notification)
        if step["method"] != message.get("method"):
            write({"id": message["id"], "error": {"message": "unexpected"}})
        elif "error" in step:
            write({"id": message["id"], "error": step["error"]})
        else:
            write({"id": message["id"], "result": step["result"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
