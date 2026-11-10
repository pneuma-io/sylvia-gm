# Native Linux support

Status: selected planning scope, PL-22. Separate from macOS and local WebUI. No Linux build or installation is delivered here.

## User outcome

Install and run a supported native Linux Sylvia application, configure a compatible provider, use GM and Builder, manage content and retain saved work across restarts and supported upgrades. State the actual supported architectures, dependency baseline and desktop/distribution scope before advertising Linux support.

## Current baseline

The public Alpha release and current NSIS packaging target Windows x64, tested on Windows 11. Existing [Linux feasibility research](../docs/research/linux-feasibility-2026-09-14.md) and platform-neutral Python/Tauri source are starting points, not Linux release qualification. Read the complete PL-22/AH-08 references and their known file-operation/window-placement limitations before implementation.

## Proposed scope and boundaries

- Choose one bounded initial native path: architecture, build/runtime dependency baseline, representative Linux desktop/distribution and download format. AppImage and a native package are candidates, not commitments to every distribution.
- Build and package both the native desktop and a compatible backend/sidecar on an appropriate Linux build environment. A Linux wrapper around a Windows backend is not a delivered Linux application.
- Reuse the existing application, Rulesets and content. Adapt platform-specific paths, safe file operations, provider discovery from an application-menu launch, credential storage, clipboard/dialogs and panels at their actual owning boundaries.
- Inspect WebKitGTK rendering and compositor/window behavior. Record supported docking/detaching/positioning behavior honestly, including any native Wayland constraints, rather than weakening the global panel contract silently.
- Choose install and upgrade/data-location behavior. Coordinate application-update integration with [PL-16/#4](https://github.com/pneuma-io/sylvia-gm/pull/4); a Windows updater design does not automatically qualify Linux.
- Follow the selected dependency/build baseline using current [Tauri AppImage guidance](https://v2.tauri.app/distribute/appimage/). Do not introduce a distribution matrix, package service or new toolchain purchase before the bounded path is chosen.
- No operating-system setup, privileged package installation, downloaded executable, signing configuration, repository workflow change or environment procurement occurs in this planning pass. User-managed WebUI access is a separate feature, not native Linux acceptance.

## Acceptance and future tests

1. On a dedicated disposable selected Linux environment, install the actual packaged app and verify backend startup, content discovery and provider configuration from an application-menu launch.
2. Prepare storage; create/reopen Builder and campaign work; perform a representative GM turn/stop/retry and content action. Attribute provider/model limits separately.
3. Exercise file dialogs, import/export/recovery, backup/migration, keyboard/clipboard and the selected panel behavior with real Linux filesystem semantics.
4. Close/reopen and perform a supported upgrade while preserving campaigns, exact content pins, settings and protected connection data. Reject unsafe/unavailable file operations clearly.
5. Retain focused regressions for any changed shared Windows boundary. A WSLg run, source import or browser fixture alone is not a supported native Linux release claim.

## Decisions for implementation

Select the initial distribution/desktop, architecture, dependency baseline, format and authorized build/test environment. Resolve the known safe-file and native-window differences in that bounded slice. Record actual support and remaining gaps instead of assuming exhaustive Linux compatibility.

## Full specification and implementation boundary

Planning references: PL-22 — `2edd61f2-3e5b-4781-a838-23b6539f89e1`. The complete current Desk specifications and their linked original acceptance/source material remain authoritative. Retrieve all of them through authorized access before implementation; these public notes are an index and selected scope, not a replacement handoff. Daniel's latest selection here supersedes conflicting older framing. The coordinator owns Desk reconciliation.

This PR changes one proposal document only. No feature implementation, external configuration, credentials, installation, paid calls, tests of the feature, merge or release is delivered. No GitHub Actions before November 2026. Future implementation and final-byte acceptance remain separate work.
