# The comparison tool

`sylvia-compare` runs one scripted session against an owner executable and records everything
an outside observer can see:

1. **Replies:** every reply on the owner's command protocol.
2. **Model requests:** every request the owner sends to the model, whole, captured by a
   stand-in model service built into the tool. No real model, key or network is used.
3. **Database:** every table of the SQLite database after the owner exits, plus schema and
   pragmas. Stored JSON is parsed, and the tool records which Python encoding reproduces the
   stored text exactly.
4. **Files:** every file left in the data folder. Small text files whole, others by SHA-256,
   JSON-lines logs as parsed lines.

A recording made from the frozen Python owner is the reference. The Rust owner must produce
the same recording, apart from what the session declares may differ.

Each run gets a fresh data folder and an empty user profile (`USERPROFILE`, `HOME`, `APPDATA`,
`LOCALAPPDATA` and the `XDG_*` folders point inside the work folder), so it never reads or
writes the real profile and both owners start from the same nothing.

## Commands

```
# Record a reference from the Python owner (needs the snapshot's .venv)
sylvia-compare record --session crates/sylvia-compare/sessions/startup.session.json \
    --out <folder> --owner <repo>/.venv/Scripts/python.exe \
    --arg -B --arg -m --arg sylvia_gm.interfaces.owner_process

# Check the Rust owner against a reference (exit 0 same, 1 different, 2 could not run)
sylvia-compare check --session crates/sylvia-compare/sessions/startup.session.json \
    --reference crates/sylvia-compare/recordings/windows/startup.recording.json \
    --owner target/debug/sylvia-gm-owner.exe

# Compare two recordings that already exist
sylvia-compare diff --session <session file> <reference recording> <candidate recording>
```

`--limit N` sets how many differences are printed (default 40). `--work <folder>` keeps a
check's data folder somewhere you choose; it must not exist yet. On Linux the Python owner is
`.venv/bin/python`.

Recordings are per platform, in `recordings/windows` and `recordings/linux`, because paths,
credential protection and a few platform facts differ. Record a reference on the platform it
will be checked on.

## Sessions

A session is a JSON file in `crates/sylvia-compare/sessions`:

```json
{
  "name": "ruleless-turn",
  "description": "What the session covers, in one sentence.",
  "standin": true,
  "summarise_tables": ["^package_", "^packages$"],
  "steps": [
    {"method": "model_choices", "expect_ok": true,
     "save": {"provider_id": "/result/0/provider_id"}},
    {"method": "create_guided_campaign_draft", "params": {"provider_id": "${provider_id}"}},
    {"method": "submit_session_input", "id": "turn", "background": true, "params": {}},
    {"method": "interrupt_stream", "params": {}},
    {"await": "turn"},
    {"method": "shutdown"}
  ],
  "rules": {}
}
```

- `standin` starts the stand-in model service and defines `${standin_url}`, `${standin_key}`
  and `${standin_model}`.
- `save` keeps a value from a reply (a JSON pointer into the whole reply) under a name.
  `${name}` in later params is replaced by it. A string that is exactly one placeholder takes
  the saved value's own type.
- `expect_ok` stops the session when a step's outcome is not the expected one. Leave it out
  to record whatever happens, including errors: error codes and messages are behaviour.
- `background` sends a request without waiting; a later `{"await": "<id>"}` collects its
  reply. Use it for Stop, interruption and anything else that needs two requests in flight.
- `summarise_tables` records matching tables as a row count and one hash of their rows. Use
  it for bulk content another session already records in full. `startup` records the installed
  package tables in full; other sessions summarise them.
- `timeout_s` (per step, default 300) and `ready_timeout_s` (default 600) bound the waits.
- End with `shutdown`, so the database is closed cleanly before it is dumped.

Use identities the session chooses (`operation_id`, `message_id`, `attempt_id`): Sylvia
derives campaign and session identities from them, so they come out the same on every run.

## What may differ

Two runs of the same Python owner already differ in a few places. Those, and only those, are
declared as rules. The built-in defaults cover wall-clock times, measured durations and the
random identities of diagnostics records. A session adds its own under `rules`:

```json
"rules": {
  "ignore_keys": ["elapsed_ms"],
  "alias": [{"name": "inference", "pattern": "inference-[0-9a-f]{32}",
             "keys": ["message_id", "inference_id"]}],
  "scrub": [{"pattern": "data: [A-Za-z0-9+/=]{40,}", "with": "data: <protected credential>"}],
  "volatile_files": ["^diagnostics/"]
}
```

- `ignore_keys`: the value under these keys is not compared; the key must still be present.
- `alias`: a run-specific identifier. Each distinct match becomes `<name:1>`, `<name:2>` in
  order of first appearance, so both runs must still agree on which identifier appears where.
  Always give `keys`, so an alias cannot hide a wrong hash stored under another key.
- `scrub`: text replaced by a fixed word. A session's own scrub rules are also applied when a
  recording is written, so a protected credential never reaches the repository. The work
  folder still holds the raw data; never commit a work folder.
- `volatile_files`: files that must exist but whose content is not compared.
- `ignore_pointers`: exact JSON pointers into the recording, for the rare single value.

**How to find the rules for a new session:** record it from Python twice and `diff` the two
recordings. Whatever differs between two Python runs is what a rule may cover. Add the
narrowest rule that makes Python agree with itself, and nothing more. Never add a rule to make
the Rust owner pass.

Everything else is compared exactly, including the order of object keys and whether a number
is an integer or a float.

## The stand-in model service

It speaks the OpenAI-compatible chat API like `scripts/qa/standin_model_endpoint.py`: one
model (`standin-narrator`), fixed narration, and an `update_campaign_file` tool call when
that tool is offered and the last message is not a tool result. That is enough for an opening
turn and ordinary player turns.

It does not yet script failures, held replies or tool-using mechanics turns. Sessions that
need those (Stop, Retry, a rules check, an attack) need the stand-in extended first: reply
scripts chosen by a marker in the latest player message, as `tests/stop_resume` does with
`[hold]`, `[fail]` and `[late]`. That is lane 5's first comparison-tool task, with lane 4
saying what the mechanics turns need.

## Sessions so far

| Session | Covers | Python owner |
| --- | --- | --- |
| `startup` | First start on an empty folder, bundled package install, opening reads, an unknown method, error replies | recorded on Windows; reproduces on a second run |
| `ruleless-turn` | Connect a model, No Ruleset campaign through the interview, GM opening, one player turn, reads | recorded on Windows; reproduces on a second run |

Wanted next, each by the lane that ports the behaviour: Linux recordings of both; an SRD Lite
check and an attack (lane 2); Stop, Retry and reopen (lanes 4 and 5); a Builder draft edited
and published (lanes 1 and 4); archive, restore, export and recover (lane 3); provider
settings save, remove and switch (lane 4).
