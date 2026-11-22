# Sylvia GM Rust port

Sylvia GM is being ported from Python to Rust. This folder is the working agreement for
everyone doing that port: who owns what, the rules, and how a change is checked.

- **Reference:** the Python source in `src/sylvia_gm` at the 0.4.1 release commit,
  `c19eee455b35679703a574a4db51767d32f1af1b` (tagged `v0.4.1` when published). It is frozen:
  nothing under `src/sylvia_gm`, `tests` or `scripts` changes on this branch except release
  fixes the manager merges in from Python `main`. It is the definition of correct behaviour
  until the port is finished.
- **Reference interpreters.** Behaviour is recorded from the Python each platform's 0.4.1 build
  runs. Windows: CPython 3.12.11 from uv, SQLite 3.49.1. Linux: Ubuntu 24.04's system CPython
  3.12.3 (what `scripts/setup_linux_dev.sh` and the Linux release build use), SQLite 3.45.1.
  Both are Unicode 15.0. The Rust build bundles SQLite 3.49.1 on both platforms; lane 3 showed
  that every schema identity, migration digest and open/upgrade outcome is the same under
  3.45.1, and Rust opens databases written by either. The Windows reference environment is
  `uv sync --extra dev --extra dictation --python 3.12`: the `dictation` extra (faster-whisper,
  CTranslate2, sounddevice, ONNX Runtime, PyAV) is what the dictation vector generators read.
- **Release fixes merged in so far:** `rust-rewrite` branched from `0da1010`. The release
  commit then moved to `c19eee4` for issue #240, which changed only the frontend (owner
  refusals shown without the desktop host's code) and two frontend tests; it was merged into
  `rust-rewrite`. `src/sylvia_gm` is identical at both commits, so the ledgers, site lists,
  vectors and recordings that name `0da1010` stay valid. The owner keeps rejecting with
  `code: message` through the desktop host, as Python does.
- **Goal:** the supported product builds, tests and runs with no Python interpreter.
- **Integration branch:** `rust-rewrite`. Lane workers open pull requests against it. `main`
  receives the port once, at cutover.
- **Manager:** the "Sylvia | Rust Rewrite Manager" thread reviews and merges every pull
  request, owns `crates/sylvia-base`, the workspace `Cargo.toml` and this folder's rules, and
  keeps Dot Desk current. Workers report to that thread.

The full plan and its reasons are in the deliverable
`Sylvia-GM/Rust Rewrite/Rewrite plan.md`. This file is the part you need while working.

## Lanes

Five lanes, one worker thread each. A lane owns its crates and its ledger file. Nobody edits
another lane's crate; ask that lane's worker or the manager.

| Lane | Crates | Python it retires | Ledger |
| --- | --- | --- | --- |
| 1. Formats and content | `sylvia-domain`, content modules of `sylvia-app` | `domain`, `state`, `campaign`, `planning`, `builder`, content and library services | `ledger/lane-1-formats-and-content.md` |
| 2. Game engine | `sylvia-engine`, mechanics modules of `sylvia-app` | `runtime`, `ruleset`, `spatial`, `dice.py`, mechanics-facing services | `ledger/lane-2-game-engine.md` |
| 3. Storage and authority | `sylvia-store`, lifecycle modules of `sylvia-app` | `infrastructure`, `application/authority.py`, lifecycle services | `ledger/lane-3-storage-and-authority.md` |
| 4. Providers and agents | `sylvia-provider`, `sylvia-agent` | `provider`, `config`, `session`, `tools`, `agent`, character chat | `ledger/lane-4-providers-and-agents.md` |
| 5. Owner, clients and release | `sylvia-owner`, `sylvia-compare` upkeep | `interfaces`, desktop update, package root, packaging | `ledger/lane-5-owner-clients-and-release.md` |

Dependencies run one way: `sylvia-base` ← `sylvia-domain` ← `sylvia-engine` ← `sylvia-store`
← `sylvia-app` ← `sylvia-agent` ← `sylvia-owner`; `sylvia-provider` depends on `sylvia-base`
and `sylvia-domain` only. `sylvia-app` has one module per Python service file, and the ledger
row names the lane that owns it.

### Command handlers

Python's command handlers are `interfaces/commands/<topic>.py`: thin functions that read the
request's parameters and call a service. The Rust handlers mirror them one to one in
`crates/sylvia-owner/src/commands/<topic>.rs` and take the owner's dispatch context, which
lives in `sylvia-owner` and holds a handle to each service (the store, the provider
resolver, the session loop, the storage root).

- A lower crate cannot name a type from `sylvia-owner`, so service crates do not export
  command handlers. They export **typed service operations** (the Rust counterparts of the
  Python functions a handler calls) and the handle type the dispatch context holds.
- Lane 5 owns the handler files, the parameter parsing with Python's exact messages, and the
  dispatch. When a lane lands a service operation it tells the manager which commands that
  makes answerable.
- The one shared area: the lane that ports a service **may** add the handler for its own
  command in `crates/sylvia-owner/src/commands/<topic>.rs` in the same pull request, touching
  only that handler, its status in `commands/table.rs` and its tick in `commands.md`. Say so in
  the pull request; lane 5 is told and may adjust it afterwards.

`src-tauri` (the desktop shell) stays outside the Cargo workspace until lane 5 moves
packaging off PyInstaller. The release scripts pin its own lock file and target folder.

## Rules

1. **Port behaviour as it is.** No redesign, no new features, no clean-ups that change
   behaviour. Restructure only where Python's shape cannot be expressed in Rust.
2. **A Python bug is ported, then reported.** Tell the manager. If a fix is agreed, the
   difference goes in `differences.md` with its reason and its test. Nothing differs silently.
3. **Text the model or the player reads is data.** Prompts, tool names, tool descriptions,
   tool schemas and error messages are copied from the snapshot byte for byte. Long texts go
   in data files that Rust loads; short messages are string literals copied exactly. Never
   reword one. A reworded error changes model behaviour and breaks prompt caching.
4. **Bytes that are hashed or stored must match exactly.** Use `sylvia_base::pyjson` for
   every JSON encoding, never `serde_json::to_string`. `sites/json-encodings.md` names the
   options for each Python call. "Parses to the same value" is not enough.
5. **Prove it against Python.** Library code gets reference vectors generated from the
   snapshot (see `crates/sylvia-base/tests/vectors` for the pattern). Owner commands get a
   comparison session (see `comparison-tool.md`). A port without one of the two is not done.
6. **Every pull request says** which ledger rows it retires, which commands it ticks in
   `commands.md`, which rows it ticks in `sites/`, and which vectors or sessions now pass.
   Once it is open, do not rebase it or push to it unless the manager sends it back. The
   manager resolves conflicts with the moving branch at merge time.
7. **New dependencies go in the workspace `Cargo.toml`** in the pull request that needs them,
   with one line on why. One library per job across the workspace.
8. **Python is reference tooling only.** A generator script that reads the snapshot to write
   vectors may be Python and lives beside its output. Nothing the product or its tests need at
   run time may call Python.
9. **`unsafe` only where Python itself calls the operating system directly**, and only with the
   manager's approval for that module. The code goes in one named module per platform with
   `#![allow(unsafe_code)]` scoped to it. Each foreign call is wrapped in a small safe function
   with a `SAFETY:` comment, and has a test on the platform it runs on. Everything else stays
   under the workspace's `unsafe_code = "deny"`. Windows calls use `windows-sys` 0.61 (the
   desktop shell's version); Linux calls use `rustix`. Approved so far:
   `sylvia-store/src/safe_file/windows.rs` (`GetDriveTypeW`, `SetFileInformationByHandle`
   with `FileDispositionInfo`, and `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING |
   MOVEFILE_WRITE_THROUGH`), mirroring `infrastructure/safe_file.py`'s ctypes calls;
   `sylvia-provider/src/config/protection/windows.rs` (`CryptProtectData`,
   `CryptUnprotectData`, `LocalFree`), mirroring `config/_credential_protection.py`'s DPAPI calls;
   and `sylvia-provider/src/config/host_browser/windows.rs` (`ShellExecuteW` with no verb,
   parameters or folder, `SW_SHOWNORMAL`), mirroring `os.startfile` as `webbrowser` calls it. It
   only ever receives a shipped preset's setup address.
   `sylvia-owner/src/desktop_update/windows.rs` (`GetDiskFreeSpaceExW` for
   `shutil.disk_usage`; the process barrier's `GetProcessId`, `GetProcessTimes` and
   `WaitForSingleObject` on handles adopted into `OwnedHandle` only after they verify;
   `SetHandleInformation` clearing inheritance on the helper's standard and adopted handles;
   `TaskDialogIndirect` found at run time with `LoadLibraryExW` and `GetProcAddress`, never
   freed, plus `SendMessageW` for the dialog's fixed messages), mirroring `desktop_update`'s
   ctypes calls; and `sylvia-base/src/winreg.rs` (`RegOpenKeyExW`, `RegQueryValueExW`,
   `RegCloseKey`; read-only, HKCU, closed on drop), CPython's `winreg.QueryValueEx` with
   `Reg2Py`'s string rules, shared by the desktop update and the HTTP client's proxy read.
   Native libraries the Python build ships, loaded so that results are theirs (each behind
   sylvia-owner's `dictation` feature, Windows only, loaded by full path with
   `LoadLibraryExW(..., LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32)`,
   never freed, each `GetProcAddress` cast to its typed function pointer beside its lookup with
   the C prototype quoted; listed with file hashes in `third-party-ports.md`):
   `sylvia-owner/src/dictation/swresample/windows.rs` (libswresample 6.x as PyAV uses it:
   `swresample_version` gate, `swr_alloc_set_opts2`, `swr_init`, `swr_get_out_samples`,
   `swr_convert`, `swr_free` on an owned context, a `#[repr(C)]` `AVChannelLayout` with size and
   alignment assertions) and `sylvia-owner/src/dictation/openblas/windows.rs` (NumPy's
   scipy-openblas: `scipy_openblas_get_config64_` version gate, `scipy_cblas_sgemm64_`,
   `scipy_cblas_sgemv64_`; no thread-count call, the library reads `OMP_NUM_THREADS` itself
   as it does under Python).

## Concurrency

One model for the whole port; do not introduce another without the manager.

- One tokio runtime in the owner, for provider streams, child processes, the command loop
  and timers.
- SQLite runs on one dedicated thread that owns the connection and takes requests over a
  channel. That keeps the single-writer behaviour the Python store relies on.
- `sylvia-domain` and `sylvia-engine` are synchronous and have no runtime dependency, so
  tools and SylviaBench can call them directly.
- Python's worker threads become tasks. Python's locks become ownership by one task, or a
  `tokio::sync::Mutex` held across an await only when Python held the lock across the same
  work.

## Where Python and Rust differ without telling you

The lists in `sites/` give every occurrence by lane. A row's Done cell names the vector or
session that proves it. Otherwise it says why no proof is needed:
- `retired` when the code holding the site is retired;
- `not reached: <reason>` when the site is in kept code that no live path reaches;
- `not needed: <reason>` when the site is reached but has no observable effect.

The traps behind them:

| Trap | Python | Rust | What to do |
| --- | --- | --- | --- |
| Float text | `repr`: `1e+16`, `1e-05`, half-to-even on ties | `Display`: `10000000000000000`, half-up | `pyjson::float_repr` |
| Integer versus float | `1` and `1.0` are different and encode differently | same in `serde_json`, lost by `as f64` | keep `Json` numbers as they arrive |
| Big integers | unlimited | 64-bit | `pyjson::loads` refuses them; see `differences.md` |
| Integer overflow | cannot happen | `+`, `-`, `*` panic in debug builds and wrap in release | on any value from content, the model or the player, use `checked_*` and refuse on overflow with a declared message; never store a clamped value |
| Dict order | insertion order | `HashMap` is random | `JsonMap` (ordered) or `IndexMap`; `BTreeMap` only where Python sorted |
| String length | `len(str)` counts code points | `str::len` counts bytes | `chars().count()` |
| Slicing | by code point | by byte | slice by `char_indices` |
| `str.strip()` | strips `\x1c`–`\x1f` too | `trim` does not | port the exact set where input is untrusted |
| Case and identity | `casefold`, `unicodedata` on Unicode 15.0 | crate tables | pin crates to Unicode 15.0 |
| Regular expressions | lookaround, backreferences, `$` before final newline | `regex` has none of these | `fancy-regex`; test each pattern |
| Sorting | stable; tuples compare element-wise | `sort` is stable, `sort_unstable` is not | `sort_by` with the same key |
| Rounding | `round()` is half-to-even | `f64::round` is half-away-from-zero | port `round` explicitly |
| Float powers | `x ** y` on floats calls the C library's `pow` (the Microsoft runtime on Windows, glibc on Linux), which can differ in the last bit | `f64::powf` calls the same platform `pow`, so it agrees with Python on each platform | port CPython's special cases around `pow` (`float_pow`); where a result depends on `pow`, record reference vectors per platform (`expression_recorded_vectors.<platform>.json`) |
| Integer division | `//` and `%` floor toward negative infinity | `/` and `%` truncate toward zero | `div_euclid` / `rem_euclid`, checking the sign cases |
| Random numbers | `random.Random(seed)`: MT19937, seeded by `init_by_array`, `randrange` by rejection | no crate matches | port CPython's algorithm; compare whole roll sequences |
| YAML | PyYAML, YAML 1.1 (`yes`, `no`, dates, octal) | YAML 1.2 crates | test every shipped file |
| Exceptions | `str(error)` is the message; `ValueError` is caught by the owner | no exceptions | `CodedError` with the same code and message |

## Checks

Run these before opening a pull request, on Linux first (boat.dev) and on Windows for
anything platform-specific.

**Toolchain.** `rust-toolchain.toml` pins Rust 1.100.0 with clippy and rustfmt, so format and
lint give the same verdict on every machine. `rustup` installs it on first use. On Windows,
rustup on this PC resolves a bare version to the GNU toolchain, which cannot build the bundled
SQLite. The MSVC toolchain is already installed on this PC, so in each Windows checkout run
only:

```
rustup override set 1.100.0-x86_64-pc-windows-msvc
```

The override applies to that folder only. Do not run `rustup toolchain install`, `rustup
update` or anything else that changes the shared rustup installation: several sessions share
`~/.cargo/bin`, and on 21 Nov 2026 two concurrent installs broke its proxies for a while. If
a toolchain is missing, ask the manager, who installs it once for everyone. A toolchain change
is its own pull request, with the fixes for any new lints in the same change.

```
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo build -p sylvia-owner -p sylvia-compare
target/debug/sylvia-compare check --session crates/sylvia-compare/sessions/<name>.session.json \
    --reference crates/sylvia-compare/recordings/<platform>/<name>.recording.json \
    --owner target/debug/sylvia-gm-owner
```

`sylvia-compare check` exits 1 while the Rust owner still differs from the Python recording,
and prints where. Report the count of remaining differences per session in the pull request;
it should only go down.

No GitHub Actions run for this branch. State in the pull request what you ran and where.

**Dictation (Windows, the `dictation` feature).** `check.sh` never builds the `dictation`
feature or loads its native libraries. Changes to `sylvia-owner/src/dictation` are checked
separately on Windows. Stage the libraries the product ships once, then run the dictation tests
with an absolute path:

```
bash docs/rust-port/tools/stage_dictation_natives.sh <empty folder outside the checkout>
powershell -NoProfile -ExecutionPolicy Bypass -File docs/rust-port/tools/sapi_speech.ps1 <speech folder>
export LIBRARY_PATH=<that folder>/ct2-lib SYLVIA_DICTATION_NATIVE_DIR=<that folder>/native
export SYLVIA_DICTATION_MODEL_DIR=<the pinned model, as prepare_model downloads it>
export SYLVIA_DICTATION_SPEECH_DIR=<speech folder>
cargo test -p sylvia-owner --features dictation --test dictation_native
cargo test -p sylvia-owner --features dictation --test dictation_engine
cargo test -p sylvia-owner --features dictation --test dictation_transcribe
cargo test -p sylvia-owner --features dictation --lib dictation -- --include-ignored
cargo clippy -p sylvia-owner --features dictation --all-targets -- -D warnings
```

`dictation_native` runs itself again under `OMP_NUM_THREADS=2` (the desktop's setting for the
speech worker) and `OMP_NUM_THREADS=1`; `dictation_engine` and `dictation_transcribe` under
`OMP_NUM_THREADS=2` with the native folder first on `PATH`. OpenBLAS reads the variable when it
loads and its threads decide the bits, so do not set `OPENBLAS_NUM_THREADS` in the shell.
`dictation_transcribe` compares whole transcriptions with faster-whisper's, segment by segment,
and the worker's final text; the SAPI clips' hashes are in its vectors, so a different Windows
voice build fails as a clip mismatch. It takes about ten minutes.

## Files in this folder

| File | What it is |
| --- | --- |
| `ledger/lane-N-*.md` | One row per Python file with its Rust target and status |
| `commands.md` | The 137 owner commands and which the Rust owner answers |
| `comparison-tool.md` | How to record and check sessions, and how to write one |
| `differences.md` | Every agreed difference between the Python and Rust behaviour |
| `third-party-ports.md` | Third-party code the port translates, with its licence; add a row when you translate one |
| `sites/*.md` | Hash, JSON encoding, regex, Unicode, length-limit and YAML call sites by lane |
| `tools/*.py` | Reference tooling that reads the Python snapshot; retired with it |
