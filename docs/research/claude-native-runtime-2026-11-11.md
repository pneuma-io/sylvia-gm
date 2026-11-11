# Claude native runtime connection

Checked 11 November 2026 for the selected "Connect user-owned Claude runtime" work item.
This implements the user's installed, unmodified Claude Code executable as a separate Sylvia
provider. It uses the executable's stdio SDK control channel, without adding the Agent SDK package
or implementing Anthropic OAuth in Sylvia.

## Authentication and billing

Anthropic's [legal and compliance page](https://code.claude.com/docs/en/legal-and-compliance)
expressly describes end users signing into an unmodified Claude Code binary with their own
subscription, including when another platform runs it. The same page prohibits third-party
Claude.ai login and credential intermediation. Those distinctions determine this implementation:
only the official executable handles authentication and uses its existing native account.

The [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) separately directs
third-party products offering Claude.ai login or plan limits to API-key authentication unless
previously approved. This connection follows the native-binary route selected in the work item;
it does not offer a Sylvia OAuth client or use extracted subscription tokens as API credentials.
Shipping terms and any vendor agreement remain separate from this source PR.

The [authentication guide](https://code.claude.com/docs/en/authentication) describes subscription
and Console accounts. Sylvia invokes `claude auth login` for subscription sign-in. An existing
Console sign-in remains usable; the native `claude auth login --console` flow selects API billing.
The owner command also accepts `options.account_type="api"`. No logout action is exposed because
logging out the shared native account could affect unrelated Claude sessions. Cancel terminates
only Sylvia's unfinished sign-in process.

Sylvia projects only allowlisted native account-status fields. It does not read credential files,
accept pasted OAuth tokens, forward inherited API keys or OAuth environment variables, or expose
native sign-in output. Claude bills the end user's chosen account directly. The existing Anthropic
API-key connection remains separate, with no automatic fallback between the two routes.

## Runtime and tool boundary

The implementation requires the official native executable, version 2.1.322 or later. Windows
2.1.322 is the version exercised here; macOS and Linux are not qualified by this PR.
Sylvia neither bundles nor installs or updates Claude Code. An absolute executable path is
optional; otherwise it searches PATH and the native `~/.local/bin` installation.

The [CLI reference](https://code.claude.com/docs/en/cli-reference) documents native sign-in/status,
safe mode, empty built-in tool selection, strict MCP configuration, no session persistence and
resuming from a transcript path. The [programmatic guide](https://code.claude.com/docs/en/headless)
documents streamed JSON output. `--bare` is unsuitable here because it excludes native subscription
authentication. Safe mode preserves native authentication while disabling user customizations.
Native managed security policy remains in force.

Each Sylvia provider step starts a fresh process in a disposable Sylvia directory. Built-in tools,
slash commands, Chrome integration, user setting sources and unrelated MCP servers are disabled.
Only the current Sylvia tool schemas are advertised through the SDK MCP channel. The native
initialization must report exactly that tool set. No permission-bypass mode is enabled.

A model's proposed call is not enough to execute an action. The adapter waits for Claude Code's
actual MCP `tools/call` request, correlates it with the completed streamed call, then stops and
collects the native process. It sends no MCP execution result back through that child. The existing
Sylvia session loop validates and commits the returned operation. A new step receives that saved
result and the newly discovered tool surface. Speculative additional native calls are discarded.
Native permission denials never become an executable Sylvia call.

Canonical user/assistant roles and typed tool-call/result pairs are reconstructed in a temporary
native transcript. Signed thinking stays in Sylvia's existing private continuation channel, with
its signature unchanged. Tool-name prefixes are transport encoding. Native transcripts and system
prompt files are deleted after child collection, including on errors or cancellation.

Claude's stdin prompt normalizer does not accept a fresh typed tool result as a replacement for
native history. After a tool-result tail, the adapter therefore uses a labelled,
`isSynthetic=true` `SYLVIA_GM_RUNTIME_CONTINUE` invocation. It never becomes a saved player message.
Claude Code can add its own resume framing, including `No response requested.` before the next
invocation. Audit records the transcript, current input, tools and controls at the native runtime
boundary; it explicitly reports downstream model input as not exposed. It does not claim that
the native runtime sends an otherwise identical direct-API prompt.

Output length and native reasoning are runtime-managed. An optional context allowance guides
Sylvia's working-context planning; final request admission belongs to Claude Code. Model aliases
are saved without inference and do not prove account availability. Only native-reported token
counts are projected. CLI list-price estimates are discarded; quota and monetary cost are not
invented.

The local T3 reference was inspected at its installed release: it uses Agent SDK 0.3.321, native
executable selection, SDK MCP servers, native sessions and explicit policy controls. Its coding
agent permissions and persistent-session policy are not Sylvia's gameplay policy. No T3 source
or release bundle is included in this repository.

## Setup and SylviaBench

Desktop: Settings > Accounts > Claude, then check the installation, sign in through the binary,
and save an official alias or model ID. Choose the saved model for a new or idle GM/Builder session
through the existing model selector. Saving does not change an existing chat or the global default.
Browser-only clients show the desktop requirement for native sign-in.

Headless users can register the same provider in an explicitly selected owner directory:

```powershell
python -m sylvia_gm.config.claude_settings --config D:/SelectedOwner/config.yaml
python -m sylvia_gm.config.claude_settings --config D:/SelectedOwner/config.yaml --model opus
```

The first command checks native status; the second saves `claude_runtime` / `claude-runtime/opus`.
Use `--executable` for a specific native installation and `--context-window` for a planning allowance.
Both commands leave sign-in to Claude Code. They do not make a model request or change the default.

The gameplay framework in [PR 35](https://github.com/pneuma-io/sylvia-gm/pull/35) already resolves
an explicitly selected owner configuration. Once both changes are available together, a private
setup file can contain:

```json
{
  "schema_version": 1,
  "setups": [{
    "id": "claude-opus",
    "name": "Claude Opus through native runtime",
    "kind": "model",
    "provider_id": "claude_runtime",
    "model_id": "claude-runtime/opus",
    "owner_config": "D:/SelectedOwner/config.yaml",
    "reasoning": null
  }]
}
```

The benchmark still requires its verified fixture pack and explicit `--allow-model-calls` switch.
This PR does not import the unmerged framework, run a comparison, alter its protocol, infer remaining
subscription quota or remove its execution guard. Its own pending fixture/protocol work remains.
Native Claude framing is part of the adapter provenance for any later comparison.

## Verification limits

`test_claude_native_protocol.py` drives the installed, unmodified Windows executable against a
local scripted Messages endpoint with a synthetic API key and a disposable native profile. It
proves the tool handoff, fresh-process continuation, restricted tool exposure and native streaming.
It does not sign into a real account or consume subscription allowance.

`test_claude_runtime.py` covers setup, safe errors, signed continuation, child ownership and the
production game/session loop. Its synthetic mechanic commits once, survives interrupted narration
and remains present after reopening SQLite. Browser tests cover the Accounts controls with synthetic
status data. Rust command checking excludes the absent packaged sidecar, so it is not an installer
or installed-app acceptance result.

A separate bounded smoke used the owner's existing native subscription sign-in and the `sonnet`
alias. Its first attempt received a rejected native usage-limit event, which Sylvia surfaced without
API fallback. After the owner confirmed that allowance had reset, the same smoke passed against
Claude Code 2.1.322 and the reported model `claude-sonnet-5-5`. It admitted one synthetic counter
operation, executed it once, and returned the expected reply after receiving canonical history and
the tool result in a fresh provider process. Both owned processes were collected and no temporary
turn directories remained. The two steps reported 1,152 and 1,386 total tokens. This verifies live
subscription transport and continuation; it is not a benchmark comparison or a packaged-app journey.

The merge with the current command-router refactor registers `claude_connection` in the shared
catalog and its provider handler. Native sign-in remains excluded from browser-client commands;
local diagnostics recognize the operation without recording account details.
