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
- `seed_files` (session): files or folders written under the work folder before the first launch (`{"path": "data/...", "text"|"json"|"copy_from"|"directory": ...}`; `copy_from` copies a checkout file, relative to the checkout root, byte for byte, and `"base64_into": "<name>"` with it keeps the bytes as `${<name>}` in standard base64, for a step that sends an archive inline; `json` is written in Python's canonical encoding). `${work}`, `${data_root}` and `${app_root}` (an empty `app` folder beside the data folder) may be used.
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
- Files are listed by path. After the rules are applied they are ordered again by the
  normalised path, so an aliased identifier in a path (a new campaign's folder) cannot reorder
  them between runs.
- `ignore_pointers`: exact JSON pointers into the recording, for the rare single value. A
  pointer may name an object member or an array item (one header's value is
  `/model_requests/<n>/headers/<i>/1`).
- `ignore_headers_row_8`: the one rule that covers a Rust difference rather than a Python run
  difference, because `differences.md` row 8 agrees it: the HTTP client's own request headers
  (Python's OpenAI SDK and httpx send `User-Agent: AsyncOpenAI/Python ...` or `python-httpx/...`,
  `Accept-Encoding`, `Connection` and `X-Stainless-*`; the Rust client sends
  `User-Agent: SylviaGM/<version>` and none of the others). List exactly row 8's names:
  `"ignore_headers_row_8": ["User-Agent", "Accept-Encoding", "Connection", "X-Stainless-*"]`.
  Each listed name is dropped, name and value, from every `model_requests` entry's `headers`
  on both recordings; names compare case-insensitively and a trailing `*` is a prefix. Every
  other header is still compared, in order. `check` and `diff` print
  `rules: ignore_headers_row_8 dropped N header(s) from M model request(s)` whenever it drops
  any. Never list a header row 8 does not name, and never use it as a blanket header ignore: a
  new header difference must still show, and goes to the manager as a new row first.
- `header_name_case_row_40`: `true` for differences row 40 (header name case). The Rust
  client writes request header names in lowercase (hyper's HTTP/1.1 writer), and Python writes
  them as given; HTTP names are case-insensitive (RFC 9110 §5.1). Every `model_requests`
  entry's header *names* are lowercased on both recordings. Values and order are still
  compared exactly. `check` and `diff` print
  `rules: header_name_case_row_40 compared N header name(s) in M model request(s) without case`
  whenever it changes any.

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
- A part with `"content_from_step": "<step id>"` carries the body of that earlier step's
  response, byte for byte, so a download can go back as an upload (`webui-exports` recovers
  the campaign archive it downloaded).
- A part with `"content_from_file": "<checkout path>"` carries that checkout file, byte for
  byte (`webui-package-upload` uploads the repository's Riichi archives).
- `"save": {"name": "/json/result/..."}` keeps a value from the recorded response, as an owner
  step's `save` does; the pointer is into the recorded response (`status`, `headers`, `json`).
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
| `[call:<suffix> <json>]` | calls the first offered tool (in request order) named `<suffix>` or ending `_<suffix>`, with `<json>` as its arguments byte for byte | a GM tool call, such as a rules check or an attack |

`[call:...]` scripts a mechanics turn. The suffix is ASCII letters, digits and underscores; one
space and one JSON value follow, then `]`, and anything else is left as plain text. A message
may carry several markers: they are called in order, one a round, each after the previous
one's result. Once the last marker's result is the last message, or the next marker's tool is
not offered, the stand-in answers as it would without markers: the Campaign File update, then
the narration. A marker call's id is `standin-mechanic-<n>`, where `<n>` is one more than the
number of assistant tool-call messages in the request, so every platform records the same id;
the Campaign File call keeps `standin-call-1`. `journey` narration echoes the message without
its markers, as it does `[hold]`, `[fail]` and `[late]`. A message without `[call:...]` gets
the same replies as before the marker existed.

It is a small HTTP/1.1 server on `std::net`, because holding a stream open and dropping one
mid-body need control of the socket.

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
| `webui-exports` | The campaign export and recovery through the WebUI host: a campaign made through the interview (stand-in model), exported and downloaded (byte-identical between the hosts), the refused export identities and an unknown campaign, an export under the same identity again, the download uploaded back as a recovery (refused while the campaign exists, recovered after trash and purge, refused under the wrong suffix), an unknown download, Stop. `etag` and `last-modified` follow the download file's modification time and are not compared | recorded on Windows and Linux; five reproductions on each |
| `admin-commands` | Owner commands with no turn behind them, on a No Ruleset campaign made through the interview: the catalog before and after the campaign exists, database backups and their refused identities, the provider setup page's refusals (a shipped preset would open a browser, so none is opened), and Full Audit: status, the record and flag refusals, switching it on and off, and exports (refused destinations, a fresh file, the same file again, one while it is off). The backup and both export files are compared | recorded on Windows and Linux; five reproductions on each |
| `admin-srd` | The same commands on an SRD Lite campaign made from the bundled A Debt in Iron plan, before its GM opening: the catalog with the Ruleset's 148 tools (before and after Full Audit is used), a backup of a database holding a mechanics campaign, and Full Audit switched on, read, exported and switched off. Nothing is captured: traces come from GM turns, which wait for the Rust turn loop | recorded on Windows and Linux; five reproductions on each |
| `package-install` | Installing packages through the owner on a fresh profile: `install_package`'s refusals (missing archive, not base64, non-ASCII, an archive that is not a package, a missing install id), the Riichi 0.1.0 archive inline and again under the same install id; `install_package_file`'s refusals (missing path, a missing file, a folder, a missing install id) and the Riichi quick-test setting package from a file. A failed file install is left out (Python's import-repair offer is `rust_port_pending`) | recorded on Windows and Linux; five reproductions on each |
| `webui-package-upload` | Package uploads through the WebUI host: no file selected, the Riichi 0.1.0 archive and the quick-test setting package installed from uploads, an upload without an install id, Stop. A failed install is left out, as in `package-install` | recorded on Windows and Linux; five reproductions on each |
| `builder-chat-edit` | Renaming and deleting Builder chats before any exists: missing parameters, display names, identities, the exact deletion confirmation, an unknown chat | recorded on Windows and Linux; five reproductions on each |
| `builder-chat-create` | Standalone Builder chats created through the owner: the refusals before a default model exists, the stand-in model made the default, a chat from it and its replay (a changed display name is an idempotency conflict), chats with an explicit model and start settings (an accepted speed, a refused effort and speed, a model the catalog does not list), refused display names, the index, a rename, an inexact and an exact delete, a deleted chat's operation run again, and `resume_agent_session`'s refusals | recorded on Windows and Linux; five reproductions on each |
| `session-switches` | A Builder chat's reasoning and speed switches and steering with no live run (the stand-in model advertises no efforts or speeds): the reasoning refusals in Python's order, the special values accepted for the chat and the next reply and cleared, the speed refusals with `standard` and `null` saved, and steering's refusals, including the loop's own with no live run | recorded on Windows and Linux; five reproductions on each |
| `session-reads` | Session reads and the narration stream commands before any turn runs, on a No Ruleset campaign's DM session and a Builder chat: session_context and session_history_page (a limit, a cursor, every refused limit and cursor, unknown sessions, the reasoning and speed state after a saved next-reply effort and speed), and each stream command's refusals with no live attempt | recorded on Windows and Linux; five reproductions on each |
| `submit-refusals` | submit_session_input before any turn runs, on a No Ruleset campaign's DM session and a Builder chat: the owner's reservation refusing a missing session, every check of the handler in Python's order, question answers combined or misplaced, the /model command (picker, refused forms, by name and id, next reply only, Settings too), the role and turn checks, the GM branch up to the run, a player action's response basis (an unknown action, also automatic, and on the chat), and switch_session_model's refusals and modes. A submit that runs a turn or compaction waits for lane 4's loop (T5a) | recorded on Windows and Linux; five reproductions on each |
| `turn-refusals` | The turn commands' refusals before the session loop runs, on a No Ruleset campaign's DM session and a Builder chat: start_dm_session's missing identities and every mismatch (another campaign, a Builder chat, another TURN, an unknown session); manual_retry_session up to the retry; stop_agent_session's missing and unknown session. Starting, retrying and stopping for real wait for lane 4's loop (T5) | recorded on Windows and Linux; five reproductions on each |
| `package-reads` | Reading installed package candidates on a fresh profile: package_candidates with the bundled Riichi and SRD Lite candidates and again after Riichi 0.1.0 is installed; inspect_package_candidate's refusals and both Riichi candidates; package_candidate_text's missing fields, the export-only check before the candidate is read, an unknown candidate and package, prompts, JSON, notices, a missing file, an unsafe path and a runtime over the text limit; compare_package_candidates' refusals and four comparisons | recorded on Windows and Linux; five reproductions on each |
| `campaign-snapshots` | snapshot and display_snapshot on an SRD Lite campaign with a party and a Riichi Mahjong campaign whose package declares engine-private state (its state is still empty before a match starts), and each command's missing and unknown campaign | recorded on Windows and Linux; five reproductions on each |
| `action-commands` | The direct action and TURN commands on an SRD Lite campaign with no turn run: execute_action's argument refusals in Python's order and its authority refusals, a d20_test check, its replay and both idempotency conflicts; resolve_mechanics_choice with no pending choice; retryable_interruption with no interruption; open_turn and complete_turn, including acting in a second TURN; begin_campaign_construction's refusals | recorded on Windows and Linux; five reproductions on each |
| `player-action-refusals` | A player's direct action on an SRD Lite campaign after one d20_test check: execute_player_action and preview_player_action refuse non-object sources and selections, every field in Python's order, an unknown campaign or session, a stale revision, and each source form malformed, out of range or naming nothing saved; a mechanics-choice source on a carded action is Python's `KeyError` (internal_error). Shipped result cards carry no player controls | recorded on Windows and Linux; five reproductions on each |
| `campaign-construction` | confirm_new_campaign (refusals in Python's order, a confirmation with no packages, its replay and conflict, one with SRD Lite's candidate), begin_campaign_construction's success, replay and conflict, and complete_campaign_construction (refusals, completion, replay, conflict, unknown campaign). Python's confirmation makes uuid4 campaign identities: campaign and lock identities are aliases, and six hash cells over payloads holding them are ignored by exact pointer; those columns are proven byte for byte with fixed identities in `campaign-snapshots` and `action-commands` | recorded on Windows and Linux; five reproductions on each |
| `desktop-update` | The desktop updater as 0.4.1 ships it (no trusted key): the unavailable state, every refusal, the request and host checks, the reservation, abort and shutdown rules, both update gates, and the startup check with seeded receipts and launch fields | recorded on Windows and Linux; five reproductions on each |
| `desktop-update-recovery` | The recovery-only owner: an update interrupted mid-apply met by the desktop's update context, its bounded recovery pipe, the refused methods and shutdown, and launches without a reply or with the ordinary refusal | recorded on Windows and Linux; five reproductions on each |
| `ready-campaigns` | The prepared campaigns: the catalog; each of the four started as a local copy, one start replayed; a changed and an unknown template refused as not created, and the same start again stopped by its fence; a start stopped at the model guidance warning, then continued with the override; editable copies of a prepared party and a changed customization refused; a start without its name; the index and a snapshot. The Plan copies in each campaign folder are compared | recorded on Windows and Linux; reproduces on a second run |
| `srd-mechanics-turn` | An SRD Lite campaign from the bundled A Debt in Iron plan with its fixed character, the GM opening, and one player turn in which the GM makes a skill check (`[call:d20_test ...]`, not spatial) and then updates the Campaign File; reads | recorded on Windows and Linux; five reproductions on each |
| `provider-settings` | Provider connections on an empty folder: saving a custom service (identity, headers, sealed key) and a local runtime, the refusals for a mismatched transport, a bad header and an unknown preset, the default model, new-chat defaults with their effort and speed checks, removal, and the resolver reloading after each change | recorded on Windows and Linux; reproduces on a second run |
| `account-connections` | The Claude Code connection on an empty folder, offline: its status before Sylvia's copy is installed, the refused actions and options, sign-in, model registration and sign-out refused until the copy exists, and cancel; then the ChatGPT connection's refused actions (unknown, a number, none), refused before any Codex runtime is looked for | recorded on Windows and Linux; reproduces on a second run |
| `compact` | `/compact` on a No Ruleset campaign's DM session after its opening and one player turn: the working-context checkpoint written from the saved history, and the request and terminal evidence around it | recorded on Windows and Linux; reproduces on a second run |
| `opening-retry` | A GM opening that fails (the campaign's name carries the stand-in's `[fail-opening]` marker), the Retry offer and history, then a Manual Retry of the opening, its replay and the read-backs | recorded on Windows and Linux; reproduces on a second run |

`srd-mechanics-turn` declares more rules than the others, because a mechanic is random on
every Python run:
- **Ids:** its `action_id` (`action-<hex>`) and its provider idempotency key
  (`provider-<hex>`) are fresh `uuid4` values, so they are aliases.
- **Hashes:** the `payload_hash` of the `actions` and `idempotency_records` rows hashes them,
  so it is ignored.
- **Dice:** the tool's dice seed hashes the `action_id`, so the roll differs every run. The
  session's check has target number 1, so the outcome does not. Only the roll's own values are
  ignored: `_d20_test`/`d20_test` `results` and `total`, the adjusted total, the result card's
  `equation` roll and total and its `details` value. The `scrub` rules replace the same values
  inside the JSON text of tool results and narration facts. The roll's digits also change the
  size of the request that carries them, so that one request's `Content-Length` value is an
  `ignore_pointers` entry; its body is still compared. Everything else about the check must
  match: the outcome, the modifiers, the card and the resulting state.

`ready-campaigns` creates campaigns through `confirm_new_campaign`, whose campaign identity is
a fresh `uuid4` on every run:
- **Ids:** the campaign identity, and the identities derived from its digest (the Game Master
  session, TURN, attempt, handoff, construction, evidence and Plan identities, the lock
  identities and the initial continuity entries), are aliases. A TURN identity is also an object
  key in a snapshot's `turns`, so that one pattern is also an alias without keys.
- **Hashes:** the stored payload, content and canonical-representation digests in database rows
  hash those identities, so they are ignored. Their formulas are proven by lane 3's
  `campaign_creation` and `construction_handoff` vectors instead.
- **Timing:** the start's `timing_ms` values (`resolve_party`, `local_start`) are ignored.

`stop-redirect`, `fail-retry` and `reopen` follow the Stop, Retry and reopen contract fixtures in
`tests/stop_resume/contract.cjs`, with the session's snake_case parameters where the desktop
sends camelCase ones.

Wanted next, each by the lane that ports the behaviour: an SRD Lite check and an attack
(lane 2); a Builder draft edited and published (lanes 1 and 4); archive, restore, export and
recover (lane 3); a chat's model switch (lane 4).
