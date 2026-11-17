# Sylvia's own copy of Claude Code

Checked 12 November 2026 for the version 0.5 requirement recorded on the Dot Desk item
"Pre-launch: automatic setup for every provider and model": Sylvia setup provides a dedicated,
unmodified Claude Code installation, profile and sign-in, without a separate manual install and
without touching a Claude Code the person already uses.

This replaces the executable selection and shared sign-in described in
[the native runtime note of 11 November](claude-native-runtime-2026-11-11.md). That note's
transport, tool boundary, audit and billing descriptions still apply.

## What Sylvia does now

Settings > Accounts > Claude installs Claude Code for Sylvia, signs in, and saves a model.
Sylvia never looks for a Claude Code on `PATH` or in `~/.local/bin`, and no setting can point it
at one. A `runtime_executable` value saved by the earlier version is ignored and dropped on the
next save.

Everything lives under the owner data directory (the folder holding `config.yaml`):

| Path under `claude-runtime/` | Contents |
| --- | --- |
| `install/<version>/claude.exe` | The binary exactly as Anthropic publishes it |
| `profile/` | Claude Code's settings, sessions and sign-in (`CLAUDE_CONFIG_DIR`) |
| `anthropic/` | Console sign-ins that Claude Code keeps outside its profile (`ANTHROPIC_CONFIG_DIR`) |
| `temp/` | Claude Code's temporary files (`CLAUDE_CODE_TMPDIR`) |
| `turn-*/` | Sylvia's disposable transcript for one provider step, removed afterwards |

Each owner data directory has its own copy and its own sign-in, matching the ChatGPT runtime
home. Removing the `claude-runtime` folder after signing out removes all of it.

## Where the binary comes from

Sylvia downloads from `https://downloads.claude.ai/claude-code-releases`, the release channel
Anthropic's own installer script (`https://claude.ai/install.ps1`) reads:

1. `/stable` names the stable release. Sylvia installs that release, or its own minimum
   supported version (2.1.322) when stable is older. On 12 November stable was 2.1.311, so
   2.1.322 was installed.
2. `/<version>/manifest.json` gives the size and SHA-256 checksum for each platform.
3. `/<version>/win32-x64/claude.exe` (or `win32-arm64`) is the binary.

The download is kept only when all of these hold; otherwise it is deleted and setup reports a
plain failure:

- its size and SHA-256 match the manifest;
- Windows reports a valid code signature whose signer is `Anthropic, PBC`, using the
  `Get-AuthenticodeSignature` check that Anthropic's
  [setup page](https://code.claude.com/docs/en/setup) documents;
- the installed file reports the expected version.

Sylvia does not bundle or redistribute Claude Code, does not run `claude install` or
`claude update`, and does not change `PATH`. Updating happens only when the person presses
"Check for a newer version"; the previous copy is removed after the new one passes its checks.
The manifest's GPG signature is not checked; the Windows code signature serves that purpose.
Automatic setup is Windows only in this version. Other platforms get a clear refusal.

## Anthropic's terms

Anthropic's [legal and compliance page](https://code.claude.com/docs/en/legal-and-compliance)
answers "Can customers offer Claude Code in their products?". Preinstalling or running Claude
Code in a product requires agreeing to Anthropic's Commercial Terms of Service and meeting these
conditions:

- **Unmodified binary, installed and run as published.** Met by the download and checks above.
- **No authentication method removed, disabled or restricted.** Settings offers the binary's
  subscription sign-in and its Console sign-in. Sylvia still does not forward inherited API-key or
  cloud-provider environment variables to the binary, a boundary kept from the earlier version.
- **No paying for, reselling or intermediating usage.** Each person signs in through Anthropic's
  page with their own account and is billed directly. Sylvia never reads or stores the sign-in.
- **Name and logo.** Sylvia says in plain text that it runs Claude Code and uses no Anthropic
  logo or product naming.

Agreeing to the Commercial Terms, and judging whether the environment-variable boundary meets
the second condition, are owner decisions before a release that ships this. They are not settled
by this source change.

## Keeping the person's own Claude Code untouched

The binary runs with these documented variables:

| Variable | Effect |
| --- | --- |
| `CLAUDE_CONFIG_DIR` | Settings, session history, plugins and `.credentials.json` go to Sylvia's `profile/`. Anthropic documents this for running separate accounts side by side. |
| `ANTHROPIC_CONFIG_DIR` | Console sign-ins made without an API key go to Sylvia's `anthropic/` instead of `%APPDATA%\Anthropic`, which separate profiles otherwise share. |
| `CLAUDE_CODE_TMPDIR` | Temporary files go to Sylvia's `temp/`. |
| `DISABLE_AUTOUPDATER`, `DISABLE_UPDATES` | Claude Code never updates itself. Its updater writes to the shared per-user installation in `~/.local`. |
| `DISABLE_INSTALLATION_CHECKS` | No warnings about a location Claude Code's installer did not choose. |

Sign out, added in Settings, runs `claude auth logout` against Sylvia's profile only.

## Headless setup, including SylviaBench

```powershell
python -m sylvia_gm.config.claude_settings --config D:/SelectedOwner/config.yaml --install
python -m sylvia_gm.config.claude_settings --config D:/SelectedOwner/config.yaml --login
python -m sylvia_gm.config.claude_settings --config D:/SelectedOwner/config.yaml --model opus
```

`--install` downloads or updates the copy for that owner directory. `--login` opens Anthropic's
sign-in page and waits; add `--console` for a Console account. `--logout` signs that copy out.
With no action the command prints the current status. `--executable` no longer exists.

A benchmark comparison pinned to a commit before this change keeps that commit's behaviour.

## Verification and limits

- `tests/test_claude_install.py` runs setup against a local stand-in for the release channel:
  version selection, placement, replacement of an older copy, cancel, and deletion on a bad
  checksum, signature, version or channel reply.
- `tests/test_claude_native_protocol.py` copies a published binary into Sylvia's layout, runs a
  tool request and a continuation against a local scripted endpoint with Sylvia's environment,
  and asserts that a fresh copy is signed out and that nothing is written to a substitute user
  home. It also checks that the signature test accepts Anthropic's binary and rejects a
  Microsoft-signed program and an unsigned file.
- `tests/test_claude_runtime.py` covers the environment, the refusal to use another Claude
  Code, sign-in, cancel and sign-out staying in Sylvia's profile, and setup being required first.
- `tests/frontend_claude_connection.browser.cjs` covers the Settings controls with synthetic
  status data.
- A real install on Windows 11 on 12 November 2026 downloaded 2.1.322 (346,496,160 bytes) from
  Anthropic, passed the checksum and signature checks, and reported signed out while the same
  Windows user's own Claude Code stayed signed in and unchanged.

- A real sign-in the same day used the owner action behind the Settings button, started
  headlessly. Claude Code opened Anthropic's page in the default browser, the owner approved it
  with a Claude subscription, and Sylvia's copy reported subscription billing with its credentials
  file inside `profile/`. A bounded two-step check through that copy reported
  `claude-sonnet-5-5`, admitted one synthetic counter operation, and returned the expected reply
  from a fresh process (1,092 and 1,289 total tokens, 5.64 seconds). No turn directory or process
  remained. Sign-out then removed the credentials file and status reported signed out. The
  owner's own Claude Code files were unchanged and the owner's own sessions stayed signed in.

Not checked: macOS and Linux, a packaged or installed Sylvia build, proxies that intercept TLS,
and the Console sign-in path with a real Console account.
