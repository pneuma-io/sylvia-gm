# Native macOS support

Status: planning only, PL-23. **Bottom priority**, as selected by Daniel. No Mac build, purchase, account or signing setup is delivered here.

## User outcome

Install and run a supported native Mac Sylvia application with GM/Builder, provider setup, content, panels and saved work. Preserve the existing product and identify genuine platform differences. This is a separate platform PR from Linux and the local WebUI.

## Current baseline

The public Alpha is a Windows x64 release. Existing Tauri/Python application source and PL-23's retained research provide a starting point; no native Mac or distribution acceptance is established by this planning review. Windows rendering and installed evidence do not establish macOS behavior.

## Proposed scope and boundaries

- Select Apple Silicon, Intel or both, and a minimum macOS version. Keep desktop and native backend/dependency targets aligned; select a universal build only if all required native parts support it.
- Use an authorized macOS build/test environment and appropriate Apple tools for the native desktop and backend. Do not treat the shipped Windows sidecar as a Mac executable.
- Package resources and the owner sidecar with correct native paths and lifecycle. A normal application bundle/DMG is a candidate; decide the supported install/update route using current [Tauri DMG guidance](https://v2.tauri.app/distribute/dmg/).
- Plan ordinary downloaded-app distribution, signing/notarization and Gatekeeper behavior before promising public installation. Record real account, signing, environment and cost decisions for later approval. This PR does not create credentials/certificates, purchase membership, install tools or bypass OS security.
- Check provider discovery when launched from Finder, native saved-connection protection, file dialogs, filesystem permissions, keyboard/clipboard, menu/window conventions and panel detach/reattach/reopen.
- Reuse normal content, campaign and storage boundaries. Coordinate app-update behavior with [PL-16/#4](https://github.com/pneuma-io/sylvia-gm/pull/4); do not copy Windows installation or portable assumptions without proving the Mac route.
- Keep the work bounded to selected Mac support. No App Store listing, all-architecture promise, UI-framework rewrite or managed hosting is implied.

## Acceptance and future tests

1. Build/package for the selected Macs and install/launch on a clean disposable supported macOS environment under normal security settings.
2. Verify native backend/resources and provider discovery from Finder; create/reopen Builder and campaign work and exercise representative GM, stop/retry and content journeys.
3. Test dialogs, keyboard/clipboard, panels, import/export/recovery and actual Mac filesystem permissions. Confirm that missing permissions and unavailable dependencies fail clearly.
4. Preserve campaigns, immutable content pins, settings and protected connections through close/reopen and the selected upgrade path.
5. Verify the selected distribution/signing/notarization path before making public-install claims, plus focused regressions for shared code changes. Do not label a Windows browser test as Mac acceptance.

## Decisions and sequencing

Keep this at the bottom of the selected queue. Before later implementation, choose target hardware/OS, package/update route, authorized native environment and public distribution arrangement. No procurement or security decision is inferred from this planning PR.

## Full specification and implementation boundary

Planning references: PL-23 — `8ee25031-0586-472a-a5bd-9746e13c1fe2`. The complete current Desk specifications and their linked original acceptance/source material remain authoritative. Retrieve all of them through authorized access before implementation; these public notes are an index and selected scope, not a replacement handoff. Daniel's latest selection here supersedes conflicting older framing. The coordinator owns Desk reconciliation.

This PR changes one proposal document only. No feature implementation, external configuration, credentials, installation, paid calls, tests of the feature, merge or release is delivered. No GitHub Actions before November 2026. Future implementation and final-byte acceptance remain separate work.
