"""A scripted stand-in for the Codex app-server's turns, for `generate_chatgpt_provider_vectors.py`
(lane 5, PR 6b).

Python's `CodexConnection` starts `<interpreter> app-server --stdio ...` in `<home>/workspace`, so
this file is copied there as `app-server`. It answers `initialize` itself. Every other line it
receives consumes the next step of `<home>/standin.json`, in order, across the processes the
provider starts (`<home>/standin-position`):

  {"method": m, "result": {...}}   a request; replied with the result
  {"method": m, "error": {...}}    a request; replied with the error
  {"response": true}               the provider's answer to one of the stand-in's server requests

A step may carry `"before"` (lines written before the reply) and `"after"` (written after it):
notifications `{"method", "params"}` and server requests `{"id", "method", "params"}`. A line
that does not match its step is answered with an error and recorded. Every line received is
recorded in `<home>/standin-log.jsonl`. `tests/chatgpt_provider_vectors.rs` is the same stand-in in
Rust.
"""

import json
import sys
from pathlib import Path


def main() -> int:
    home = Path.cwd().parent
    steps = json.loads((home / "standin.json").read_text(encoding="utf-8"))["steps"]
    position_path = home / "standin-position"
    position = int(position_path.read_text(encoding="utf-8")) if position_path.exists() else 0

    def write(message) -> None:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    def log(entry) -> None:
        with (home / "standin-log.jsonl").open("a", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(entry, sort_keys=True) + "\n")

    for line in sys.stdin:
        message = json.loads(line)
        log(message)
        if "id" not in message:
            continue
        if message.get("method") == "initialize":
            write({"id": message["id"], "result": {"userAgent": "standin"}})
            continue
        step = steps[position] if position < len(steps) else {"method": None, "error": {"message": "no step"}}
        position += 1
        position_path.write_text(str(position), encoding="utf-8")
        for early in step.get("before", []):
            write(early)
        if "method" in message:
            if step.get("method") != message["method"]:
                log({"unexpected": message["method"], "step": position - 1})
                write({"id": message["id"], "error": {"message": "unexpected"}})
            elif "error" in step:
                write({"id": message["id"], "error": step["error"]})
            else:
                write({"id": message["id"], "result": step["result"]})
        elif not step.get("response"):
            log({"unexpected": "response", "step": position - 1})
        for late in step.get("after", []):
            write(late)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
