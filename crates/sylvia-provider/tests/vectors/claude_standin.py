"""A scripted stand-in for Claude Code, for `generate_claude_provider_vectors.py` (lane 5).

It runs in the step's workspace, `<home>/workspace`, and plays the run of
`<home>/standin.json` whose position is the number of processes started before it
(`<home>/standin-count`). It records what it was started with and every stdin line in
`<home>/standin-log.jsonl`. `tests/claude_provider_vectors.rs` is the same stand-in in Rust,
so both providers meet the same process.

A run is a list of items:
  {"await": n}     read stdin lines until n have arrived (stop at end of input)
  {"emit": event}  write one stdout line
  {"session": rows} append rows to `<CLAUDE_CONFIG_DIR>/projects/standin/<session id>.jsonl`;
                   the row "$prompt" is the last stdin user message as a user record
  {"exit": code}   exit now
After the last item it reads stdin to its end, then exits 0.
"""

import json
import os
import sys
from pathlib import Path


def main(arguments: list[str]) -> int:
    if arguments == ["--version"]:
        sys.stdout.write("2.1.322 (Claude Code)\n")
        return 0
    home = Path.cwd().parent
    runs = json.loads((home / "standin.json").read_text(encoding="utf-8"))["runs"]
    counter = home / "standin-count"
    run = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    counter.write_text(str(run + 1), encoding="utf-8")

    def log(entry: dict) -> None:
        with (home / "standin-log.jsonl").open("a", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps({"run": run, **entry}, sort_keys=True) + "\n")

    log({"argv": arguments})
    for flag, key in (("--system-prompt-file", "system"), ("--resume", "history")):
        if flag in arguments:
            path = Path(arguments[arguments.index(flag) + 1])
            log({key: path.read_bytes().decode("utf-8")})
    log({"env": {"ENABLE_TOOL_SEARCH": os.environ.get("ENABLE_TOOL_SEARCH")}})
    session = arguments[arguments.index("--session-id") + 1] if "--session-id" in arguments else None
    received = 0
    prompt = None
    stdin = sys.stdin.buffer

    def read_line() -> bool:
        nonlocal received, prompt
        line = stdin.readline()
        if not line:
            return False
        message = json.loads(line.decode("utf-8"))
        received += 1
        log({"stdin": message})
        if isinstance(message, dict) and message.get("type") == "user":
            prompt = message.get("message")
        return True

    for item in runs[run] if run < len(runs) else []:
        if "await" in item:
            while received < item["await"]:
                if not read_line():
                    break
        elif "emit" in item:
            sys.stdout.write(json.dumps(item["emit"]) + "\n")
            sys.stdout.flush()
        elif "session" in item:
            folder = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "standin"
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / f"{session}.jsonl").open("a", encoding="utf-8", newline="\n") as file:
                for row in item["session"]:
                    row = {"type": "user", "message": prompt} if row == "$prompt" else row
                    file.write(json.dumps(row) + "\n")
        elif "exit" in item:
            sys.stdout.flush()
            return item["exit"]
    while read_line():
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
