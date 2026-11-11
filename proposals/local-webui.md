# Local WebUI

Status: selected planning scope. This supersedes the restored item's cloud-hosted/browser-only service framing. No server, public exposure or hosting service is configured by this draft.

## User outcome

Start Sylvia locally and open its WebUI at a displayed local browser address, such as `http://127.0.0.1:<port>`. Use the local Sylvia backend and the user's existing data/provider configuration from the browser.

Users may later arrange their own remote host, reverse proxy or tunnel. Sylvia is not providing managed hosting, SaaS accounts or an inference subscription through this feature. No ngrok/tunnel installation, account, public binding or network exposure is selected here.

## Current baseline

The current desktop frontend's [host bridge](../src-tauri/frontend/app.js) calls `window.__TAURI__.core.invoke`; the [owner process](../src/sylvia_gm/interfaces/owner_process.py) exposes its existing structured host boundary. Serving static files or an MCP endpoint alone would not supply the browser application's complete state/actions/events and saved-session behavior. A supported WebUI needs a real browser transport and local launch/data lifecycle while reusing the existing authoritative application.

The recovered planning item previously described hosted gameplay, separately from inference access. Daniel now selects local WebUI support with hosting left to users. Mobile UI, multiplayer and the separate inference-subscription project are not silently included.

## Proposed local scope and safe defaults

- Provide an explicit local launch/stop workflow and show the actual loopback address/port and readiness/error state. Bind to `127.0.0.1` and any deliberately supported IPv6 loopback only by default, not `0.0.0.0` or a public/LAN interface.
- Serve the browser UI with a bounded transport to existing owner commands/projections/events. Preserve authoritative transactions, session/request identity, cancellation, retry and committed-action replay protection. Do not expose an arbitrary shell, filesystem path API or the full native command surface without scope checks.
- Reuse a selected local data root and existing ownership/locking. Detect another conflicting owner instead of starting competing desktop/WebUI writers against the same storage. Closing a tab must not implicitly corrupt state or replay active GM work; document process/session lifetime.
- Support the selected ordinary GM, Builder, provider/settings, content and save/reopen journeys. Identify browser equivalents or explicit limits for native dialogs, file import/export and detached panels. Do not pretend a desktop-only capability works because its control rendered.
- Protect the local browser session against cross-origin requests, cross-site WebSocket use and host-header/DNS-rebinding abuse. Select an appropriate local session/bootstrap token and request/origin validation design before implementation; never place reusable credentials in URLs or frontend bundles. Keep provider keys and protected data on the backend.
- Local file import/export must be deliberate and constrained. Do not grant a webpage arbitrary server file reads or uploads of unrelated user material. Use synthetic data for engineering checks.

## Optional user-managed remote use

Remote use must be explicit, authenticated and documented separately from the default loopback mode. State the need for protected transport, session authentication/authorization, allowed origins/hosts, CSRF protection, secret handling and a clear data/control boundary. A user's tunnel does not itself grant access to all host files or make an unauthenticated WebUI safe.

Describe the supported configuration boundary and fail clearly if its prerequisites are absent. The user remains responsible for arranging hosting/proxy/tunnel infrastructure; this plan does not configure it, create accounts/credentials, modify a firewall, enable public access or promise Internet-service operations. Do not silently weaken local protections to make a remote URL work.

## Acceptance and future tests

1. Launch on a disposable local profile, verify the shown loopback address, load the real WebUI and exercise selected saved GM/Builder/content journeys against the owning backend.
2. Reopen/reconnect and switch sessions during work; check drafts, late events, cancellation/retry and no repeated committed actions. Verify owner-conflict detection and deliberate shutdown.
3. Check native-feature/browser equivalents honestly, including intended import/export and panels, without arbitrary host-file access.
4. Verify default listening sockets remain loopback-only. Reject unauthorized origins/hosts, cross-site requests and unauthenticated control paths; confirm secrets are absent from client bundles, URLs and routine logs.
5. Use an isolated test harness for explicit authenticated remote-mode contract checks if that mode is implemented later. Do not open a real public tunnel or change user network/security settings for this planning PR.

## Decisions for implementation

Choose the minimal supported browser journey, local launch/distribution path, transport/session boundary and browser equivalents for native features. Resolve remote-mode authentication/transport requirements before claiming remote support. This is local application access with optional user-arranged hosting, not a managed hosted-service proposal.

## Full specification and implementation boundary

Planning references: local WebUI (superseding hosted-play framing) — `ceea8b8b-ea5c-4346-a68b-7b93d5de1433`. The complete current Desk specifications and their linked original acceptance/source material remain authoritative. Retrieve all of them through authorized access before implementation; these public notes are an index and selected scope, not a replacement handoff. Daniel's latest selection here supersedes conflicting older framing. The coordinator owns Desk reconciliation.

This PR changes one proposal document only. No feature implementation, external configuration, credentials, installation, paid calls, tests of the feature, merge or release is delivered. No GitHub Actions before November 2026. Future implementation and final-byte acceptance remain separate work.
