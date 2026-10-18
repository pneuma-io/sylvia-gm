# Sylvia testing without Computer Use

> Status: advisory research and proposed qualification task; not a replacement testing policy.
> Date: 2026-09-24. Source inspected at `5c613a6077897dadb2da4fc335d6f792580a33b5`
> with existing uncommitted changes. No installed-app automation was run for this assessment.

Subsequent implementation and qualification are recorded in the
[active plan](../plans/alpha-rebuild-readiness-plan.md#1-current-work). The research below remains
the original feasibility assessment, not current runner status.

Wan wants agents to test the real installed Sylvia application without requiring exclusive use
of his computer. The proposed route is external automation of Sylvia's actual WebView2 interface,
with Windows-specific checks in an isolated Windows test environment. The behavioural goal remains
paused; this assessment does not start AR-01 or change ticket acceptance status.

## Feasibility and current evidence

Playwright documents attaching to a running WebView2 application through
`chromium.connectOverCDP`. Its example supplies a debugging port through
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` and isolates browser storage with
`WEBVIEW2_USER_DATA_FOLDER`. This is a suitable first experiment against Sylvia's installed
executable. [Playwright WebView2 documentation](https://playwright.dev/docs/webview2)

That connection is not yet qualified on Sylvia. Playwright explicitly describes CDP attachment as
lower fidelity than its own browser protocol. Input, screenshots, asynchronous updates and all
Sylvia window types need a small installed-app pilot before relying on this route.
[Playwright CDP limitations](https://playwright.dev/docs/api/class-browsertype#browser-type-connect-over-cdp)

Local source supports keeping the real application chain intact:

| Finding | Source and implication |
|---|---|
| Existing browser tests replace parts of the application connection. | [Shell browser test](../../tests/frontend_shell.browser.cjs) rewrites initialization and installs a fake bridge; [panel startup test](../../tests/frontend_panel_startup.browser.cjs) supplies fake Tauri calls. These remain useful renderer tests, but cannot establish installed-app acceptance. |
| The release desktop launches its packaged Python owner. | [Desktop startup and OwnerBridge](../../src-tauri/src/lib.rs) select the adjacent owner executable in release builds. A new runner should launch the installed desktop and retain that path. |
| Backend and frontend storage need separate isolation. | `SYLVIA_GM_AUTHORITY_DATABASE` selects the database in [desktop startup](../../src-tauri/src/lib.rs); WebView2's user-data directory is separate. A different database does not prevent Sylvia's single-instance handler from redirecting a second launch to the existing app. |
| Some actions explicitly focus native windows. | [Desktop startup and Audit window](../../src-tauri/src/lib.rs) and [panel windows](../../src-tauri/src/panel_windows.rs) show, unminimize or focus windows. Browser automation alone cannot promise that same-desktop runs never interrupt Wan. |
| Outside-window dragging uses Windows input. | [Panel drag implementation](../../src-tauri/src/panel_drag.rs) observes Windows mouse hooks and button state. A browser-only drag or direct command invocation does not verify that path. |

Agents can still conduct exploratory play through this runner: read the rendered conversation,
choose the next player action, submit it through the real controls, and inspect the selected
test's Full Audit. It need not be limited to replaying a fixed script. Backend inspection remains
supporting evidence, not a substitute for observing the player-facing result.

## Proposed smallest implementation

1. Select an isolated Windows guest or dedicated test machine. Install the exact Sylvia candidate
   there and record the installer, desktop and packaged-owner identities. Use synthetic/public
   campaign data and separate application/browser storage. Do not inherit an owner-executable
   override or unrelated credentials from the development environment.
2. Add one small runner that launches the installed executable, attaches Playwright, discovers
   its main, Audit and panel views, performs actions through rendered controls, and captures
   relevant screenshots and failures. Reuse Playwright already used by this repository; pin the
   tested runner version. Preserve the normal Rust commands, owner, database and package runtime.
3. Qualify one short journey: enable Full Audit before tested actions; create a Builder or campaign
   session through the UI; send a message; observe a persisted result; open and close a detached
   panel and Audit; close Sylvia cleanly; reopen and verify persistence. Confirm expected behavior
   when the tested view is not foreground and that host input is unaffected.
4. Add only the native checks needed by the selected tickets, using automated Windows input inside
   the guest. Keep unsupported cases visibly pending. Then update the shared procedure and resume
   the selected testing work under its existing scope.

The debugging endpoint should be temporary and local to the test environment. Verify its binding,
retain the real CSP and command permissions, and close the owned processes on completion or failure.
Do not add a permanent application server, ship relaxed security settings, replace `window.__TAURI__`,
or turn direct backend calls into claimed UI coverage.

Identify views by their observed identity/URL rather than assuming `pages()[0]`. Playwright provides
page discovery and creation events, but that is not a guarantee that every native Tauri window
appears in one browser context. Multiwindow discovery is a qualification requirement.
[Playwright page events](https://playwright.dev/docs/api/class-browsercontext#browser-context-event-page)

If CDP fails a required capability, the supported fallback is external `tauri-driver` with a matching
Microsoft Edge WebDriver, aimed at the installed application. Do not build two runners in advance.
Tauri's current default WDIO setup uses an embedded plugin; explicitly select its external route
to avoid changing the application merely to test it.
[Tauri manual setup](https://v2.tauri.app/develop/tests/webdriver/manual-setup/),
[Tauri WebDriver overview](https://v2.tauri.app/develop/tests/webdriver/)

## What this does and does not prove

| Evidence | Valid claim |
|---|---|
| Source or browser tests with fixtures | The selected component behavior works under those fixtures. |
| Installed UI automation with a scripted provider | The real desktop, owner and storage handle the tested mechanics, UI and recovery flow. |
| Installed UI automation with the selected real provider | The observed real model, tools and application journey work together; assess content and continuity from actual replies and Full Audit. |
| Native Windows automation inside the guest | The selected native gesture, dialog or window behavior works under the recorded guest conditions. |

Provider choice is a separate dimension from automation method. A scripted provider is useful for
repeatable interruption tests, but cannot satisfy a real-provider or campaign-quality claim.
Existing provider authorization and spending boundaries still apply.

Microsoft states that Edge WebDriver does not operate native Windows UI, and that its version must
match the application's WebView2 runtime. File dialogs, installer interaction, outside-window drops
and window-manager behavior therefore need another driver or a small retained manual check. Selecting
a file programmatically does not prove its native picker worked.
[Microsoft WebView2 automation](https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/webdriver)

Playwright can operate browser pages without bringing them to the front. That does not suppress
Sylvia's native focus requests. A Windows VM separates that desktop from Wan's working desktop;
ordinary same-host attachment must first demonstrate its own noninterference.
[Playwright page behavior](https://playwright.dev/docs/pages#multiple-pages)

Native tests still need a usable interactive guest desktop. Microsoft warns that desktop UI tests
need an interactive process and that an RDP disconnection can lock the session. Do not promise
reliability on a locked desktop or equate this proposal with running the Tauri app headlessly.
[Microsoft Windows UI testing guidance](https://learn.microsoft.com/en-us/azure/devops/pipelines/test/ui-testing-considerations?view=azure-devops)

Read-only host inspection found Windows 11 Home, about 32 GB RAM and 16 logical processors.
No Hyper-V management service was found; no VM readiness or guest availability was established.
Microsoft excludes Windows 11 Home from the supported Hyper-V host editions, so select an appropriate
existing guest, compatible VM host or separate machine before assuming that setup path.
[Microsoft Hyper-V requirements](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/get-started/install-hyper-v)

For AR-24, keep a genuinely fresh Windows environment: installing developer or test dependencies
must not hide a missing Sylvia runtime dependency. For AR-23, test effective CSP through realistic
untrusted content; privileged automation evaluation itself is not proof of a CSP bypass. For AR-01,
the blind player's observations must stay limited to player-visible UI; evaluator access to Audit
and the plan must not leak into player decisions.

## Proposed documentation changes after qualification

| Document | Targeted change |
|---|---|
| [Development Process](../operations/quality-preserving-development-process.md), sections 4-5 | Own the testing policy: qualified installed-app automation is the default; distinguish component, installed, native and real-provider evidence. Preserve Full Audit, artifact identity and visual acceptance. |
| [AGENTS.md](../../AGENTS.md) | Add a short default and point to the process: do not take over Wan's desktop or automatically invoke Computer Use for Sylvia testing. Report unsupported native cases explicitly. |
| `%USERPROFILE%/.codex/skills/sylvia-testing/SKILL.md` | Describe the qualified runner's launch, attach, observation and recovery procedure. Preserve Full Audit before actions and after restarts, selected-data limits and real-provider requirements. |
| `%USERPROFILE%/.codex/skills/live-playtesting/SKILL.md` | Clarify that driving a real app through its automation protocol is live testing; retain screenshots and recordings where the claim requires them. Keep Sylvia-specific setup in its extension. |
| [AR-01](../../.scratch/alpha-readiness/issues/01-verify-long-form-campaign-continuity.md), section 1 step 1 | Replace the explicit requirement to load the computer-use skill. Preserve the six-act, blind-player, detour, natural-compaction and reopen acceptance criteria. |
| [Alpha Completion Plan](../plans/alpha-rebuild-readiness-plan.md) and [ticket index](../../.scratch/INDEX.md) | Record runner qualification and the native/provider cases still pending. Do not close existing tickets just because the testing method changed. |
| AR-21 through AR-24 and PES-146 | Update only affected execution instructions and evidence labels. Keep native drag, clean-machine, recovery and security requirements intact. |

The [product contract](../product/alpha-launch-contract.md) already allows automated acceptance.
The [architecture contract](../architecture/alpha-architecture-contract.md), section 16, requires
the actual desktop and supervised owner; external UI automation can preserve that requirement.
Neither needs broad rewriting. Any later application-level test API would need its own architecture
decision; this proposal does not require one. Leave the bundled Computer Use skill untouched.

This note is supporting evidence in [the documentation index](../INDEX.md). The existing Development
Process remains the sole testing-policy authority; no additional runbook or competing plan is proposed.
