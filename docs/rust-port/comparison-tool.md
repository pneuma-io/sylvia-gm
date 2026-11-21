# The comparison tool

`sylvia-compare` runs one scripted session against an owner executable and records everything
an outside observer can see:

1. **Replies:** every reply on the owner's command protocol.
2. **Model requests:** every request the owner sends to the model, captured by a stand-in
   model service built into the tool: method, path, the headers in the order received (the
   `Authorization` value left out; `authorised` says whether it was the stand-in key), the
   body parsed, and `body_encoding`, the Python encoding that reproduces the body's bytes
   exactly (`other` when none does, `none` for no body). No real model, key or network is
   used.
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
- `standin_narration` (session): `fixed` (default) narrates as below; `journey` answers
  "The Game Master answers: " and the player's message without its markers, as the Stop and
  resume fixtures do, so a recording shows which message each reply answered.
- `{"standin": "wait_held"}` waits until the stand-in holds one more request than earlier
  `wait_held` steps waited for: the owner is then inside that model stream. Use it between a
  background `[hold]` or `[late]` message and the Stop or read that needs it running, instead
  of polling the owner.
- `{"standin": "release"}` lets every `[late]` request being held answer.
- `{"relaunch": true}` starts the owner again on the same data folder, for reopen. It must
  follow a `shutdown` step. The step records the previous launch's exit code and the new
  launch's ready message; the database and files are dumped once, after the last launch.
- `timeout_s` (per step, default 300) and `ready_timeout_s` (default 600) bound the waits.
- `seed_files` (session): files or folders written under the work folder before the first launch (`{"path": "data/...", "text"|"json"|"directory": ...}`; `json` is written in Python's canonical encoding). `${work}`, `${data_root}` and `${app_root}` (an empty `app` folder beside the data folder) may be used.
- `launch` (session): extra launch-envelope fields, such as the desktop's `desktop_update_context`. A `relaunch` step may give its own `launch` and its own `seed`, written before the owner starts again.
- A launch the owner refuses (its first reply is not `ok`, or it exits without one) is recorded with that reply (or `null`) and the owner's exit code; only a `relaunch` may follow it. A `shutdown` the owner refuses does not close the session.
- End with `shutdown`, so the database is closed cleanly before it is dumped.

Use identities the session chooses (`operation_id`, `message_id`, `attempt_id`): Sylvia
derives campaign and session identities from them, so they come out the same on every run.

## What may differ

Two runs of the same Python owner already differ in a few places. Those, and only those, are
declared as rules. The built-in defaults cover wall-clock times, measured durations, the
random identities of diagnostics records and the stand-in's port in a model request's `Host`
header. A session adds its own under `rules`:

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
  `parent/key` limits it to a `key` directly inside an object stored under `parent`, so
  `activity_timing/revision` ignores a stopped turn's progress counter without hiding
  campaign revisions.
- `alias`: a run-specific identifier. Each distinct match becomes `<name:1>`, `<name:2>` in
  order of first appearance, so both runs must still agree on which identifier appears where.
  Always give `keys`, so an alias cannot hide a wrong hash stored under another key.
- `scrub`: text replaced by a fixed word. A session's own scrub rules are also applied when a
  recording is written, so a protected credential never reaches the repository. The work
  folder still holds the raw data; never commit a work folder.
- `volatile_files`: files that must exist but whose content is not compared.
- `ignore_pointers`: exact JSON pointers into the recording, for the rare single value.

Local diagnostics aggregates matching outcomes per wall-clock minute, so two error replies
share one `diagnostic_id` only when both land in the same minute. A run that crosses a minute
boundary between them gives the later reply a fresh identity, in Python too. A session that
repeats an error outcome therefore declares `diagnostic_id` in `ignore_keys` (presence is still
compared); `sylvia-owner`'s diagnostics tests cover the sharing itself.

**How to find the rules for a new session:** record it from Python twice and `diff` the two
recordings. Whatever differs between two Python runs is what a rule may cover. Add the
narrowest rule that makes Python agree with itself, and nothing more. Never add a rule to make
the Rust owner pass. Then `check` the reference against the Python owner at least five more
times on each platform before calling it reproducible: a race that hits one run in three
(how far a stopped reply got) is easily missed by two. `crates/sylvia-compare/reproduce.sh 5
<session>...` does that from the checkout root after `cargo build -p sylvia-compare`.

Everything else is compared exactly, including the order of object keys and whether a number
is an integer or a float.

## WebUI host sessions

A session with `"webui": true` tests the WebUI host instead of the owner. `--owner` names the host
program (`target/debug/sylvia-gm`, or Python's `-m sylvia_gm.main`), which the tool starts with
`--webui --data-root <data> --port 0`; the host starts its own owner. `"webui_remote": {"origin",
"key"}` starts it in private HTTPS mode instead, with a key file and a fixed free port.

Each step is an `http` request, sent on its own connection by a small HTTP/1.1 client, so a
session can send what a browser never would:

```json
{"http": {"method": "POST", "path": "/api/command",
          "headers": [["Origin", "${origin}"], ["X-Sylvia-WebUI", "1"], ["Cookie", "${cookie}"]],
          "json": {"id": "a", "method": "application_info"}}}
```

- `headers` go out in order, after `Host` (unless named, or `no_host` is set). `Content-Length`
  or chunked framing, and `Connection: close`, are added.
- The body is one of `json`, `text`, `repeat` (that many `a` bytes) or `multipart` (parts with
  `name`, `filename`, `headers`, `content` or `repeat`). `chunked: N` sends it in pieces of N bytes.
- `${origin}` and `${authority}` are what the browser sends as Origin and Host; `${address}` is
  where the host listens. `"save_cookie": true` keeps the cookies a response sets as `${cookie}`.

A response is recorded as status, reason, headers (lowercased names, sorted; the hop-by-hop
`Connection` and `Keep-Alive` are left out) and body: JSON parsed with the encoding that
reproduces it, small text whole, anything else by size and SHA-256. The data folder and database
are dumped once the host has exited after `/api/stop`.

## The stand-in model service

It speaks the OpenAI-compatible chat API like `scripts/qa/standin_model_endpoint.py`: one
model (`standin-narrator`), fixed narration, and an `update_campaign_file` tool call when
that tool is offered and the last message is not a tool result. That is enough for an opening
turn and ordinary player turns.

A marker in the latest player message (the newest user message without a `[host-seal ...]`
prefix, as `latest_player_message` in `tests/gm_context_contract.py` finds it) scripts the
reply, following `JourneyGM` in `tests/stop_resume_owner_fixture.py`:

| Marker | The stand-in | The owner sees |
| --- | --- | --- |
| `[hold]` | streams the role chunk and "Partial narration that Stop must discard.", then holds the connection until the owner closes it | a reply in progress, until Stop |
| `[fail]` | streams the same partial chunk and drops the connection mid-body, the first time it sees that player message; later requests with it are answered | a lost provider connection (`provider_error`), then a Retry that succeeds |
| `[late]` | holds before answering until a `release` step, then answers | a slow reply |

It is a small HTTP/1.1 server on `std::net`, because holding a stream open and dropping one
mid-body need control of the socket. It does not yet script tool-using mechanics turns (a
rules check, an attack); lane 4 says what those need.

## Sessions so far

| Session | Covers | Python owner |
| --- | --- | --- |
| `startup` | First start on an empty folder, bundled package install, opening reads, an unknown method, error replies | recorded on Windows and Linux; reproduces on a second run |
| `ruleless-turn` | Connect a model, No Ruleset campaign through the interview, GM opening, one player turn, reads | recorded on Windows and Linux; reproduces on a second run |
| `stop-redirect` | Stop a held reply, the Retry offer and history, a redirecting message, a `[late]` reply read while running and then released | recorded on Windows and Linux; reproduces on a second run |
| `fail-retry` | A reply lost mid-stream, the Retry offer, plain and redirecting input refused, Manual Retry and its replay | recorded on Windows and Linux; reproduces on a second run |
| `reopen` | Stop and a failed reply, each followed by a restart on the same data folder; the offer survives, a redirect and a Manual Retry answer | recorded on Windows and Linux; reproduces on a second run |
| `webui-guard` | The local WebUI host's boundary, request by request: cookie, Host, Origin, Sec-Fetch, the same-origin header, command checks and idempotency, renamed arguments, asset path tricks, downloads, upload names and size limits, unknown routes, Stop | recorded on Windows and Linux; five reproductions on each |
| `webui-remote` | Private HTTPS mode: the sign-in page, the remote Host only, access-key sign-in, the remote cookie, sign-out, the sign-in budget, Stop | recorded on Windows and Linux; five reproductions on each |
| `builder-chat-edit` | Renaming and deleting Builder chats before any exists: missing parameters, display names, identities, the exact deletion confirmation, an unknown chat | recorded on Windows and Linux; five reproductions on each |
| `desktop-update` | The desktop updater as 0.4.1 ships it (no trusted key): the unavailable state, every refusal, the request and host checks, the reservation, abort and shutdown rules, both update gates, and the startup check with seeded receipts and launch fields | recorded on Windows and Linux; five reproductions on each |
| `desktop-update-recovery` | The recovery-only owner: an update interrupted mid-apply met by the desktop's update context, its bounded recovery pipe, the refused methods and shutdown, and launches without a reply or with the ordinary refusal | recorded on Windows and Linux; five reproductions on each |
| `provider-settings` | Provider connections on an empty folder: saving a custom service (identity, headers, sealed key) and a local runtime, the refusals for a mismatched transport, a bad header and an unknown preset, the default model, new-chat defaults with their effort and speed checks, removal, and the resolver reloading after each change | recorded on Windows and Linux; reproduces on a second run |

`stop-redirect`, `fail-retry` and `reopen` follow the Stop, Retry and reopen contract fixtures in
`tests/stop_resume/contract.cjs`, with the session's snake_case parameters where the desktop
sends camelCase ones.

Wanted next, each by the lane that ports the behaviour: an SRD Lite check and an attack
(lane 2); a Builder draft edited and published (lanes 1 and 4); archive, restore, export and
recover (lane 3); a chat's model switch (lane 4).
