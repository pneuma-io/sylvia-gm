# In-app self-update (PL-16)

Status: future implementation scope. This draft PR contains planning documents only. It does not deliver or qualify an updater.

Daniel selected a full in-app self-update feature on 19 October 2026. This supersedes the earlier manual-check/browser-handoff proposal. Preserve that proposal at [its original path](manual-desktop-update-checker.md) as history; opening a browser is not delivery of the selected outcome.

## User outcome

In Settings → About, a user can discover an eligible official release, read the current and available versions and change information, explicitly download it, and initiate installation from Sylvia. Show download, verification, installation and restart status, with clear failure, retry and recovery choices. A successful update reopens the intended installed or portable application and retains the user's data.

Use an explicit check initially; this scope does not select background downloads or unattended installation. Stable installations select eligible stable releases. Prerelease installations can remain on their named channel, including Alpha. Show the source, channel, architecture and distribution being selected. Reject older, equal, wrong-channel, incompatible or incomplete candidates; a recovery rollback is a separate deliberate action, not a normal downgrade offer.

## Proposed update lifecycle

1. **Check and review.** Read bounded official release metadata, compare versions and show safely rendered change information. Unavailable service, rate limits, malformed data and unsupported distribution have distinct readable outcomes. Never render release text as executable HTML or treat it as an instruction.
2. **Download.** After the user's action, download the artifact for the actual installed/portable distribution into an application-owned staging area. Show bytes/progress when known and an indeterminate state when unknown. Permit cancellation before application; an interrupted transfer can retry without touching live binaries or user data. Never execute a partial download.
3. **Verify and prepare.** Validate expected distribution, target, version, lengths and integrity/authenticity before execution or extraction. Check disk space and write access. Record a recoverable update journal containing safe version/path/status metadata, not credentials or campaign content.
4. **Reach a safe apply point.** Do not interrupt an active GM turn or Builder request, lose unsent drafts, or terminate only part of a multiwindow application. Offer deferral; when the user selects application, finish or explicitly stop/save work using existing application semantics. Checkpoint the database and preserve exact installed content/pins. Close the owner sidecar and application processes deliberately before replacing their files.
5. **Apply.** Install through the selected native route below, show status without inventing a percentage for opaque installer work, and report permission cancellation, locked files, antivirus intervention and installer failure. Do not silently switch between installed and portable layouts or overwrite another copy.
6. **Restart and confirm.** Relaunch the correct executable/data root in the same ordinary user context. Confirm application/owner versions, usable storage and the selected data root before marking success. Restore the appropriate saved UI/session state without automatically resubmitting model work or repeating committed mechanics.
7. **Recover.** An interrupted apply or failed first start remains visible on the next launch. Recover the previous compatible application and data as described below, or offer a clear repair route when automatic recovery is unsafe. Do not label a browser download or an unverified restart successful.

## Installed and portable routes

| Distribution | Proposed application route | Recovery requirement |
| --- | --- | --- |
| Existing Windows NSIS install | Reuse the verified official NSIS update artifact through a bounded native updater/installer invocation. Initiation, preparation and result handling stay in Sylvia. Use the existing install scope and ordinary user context; display any genuine OS consent requirement. Do not uninstall first or remove the data root. | Retain a known-good previous installer/application version and a consistent pre-migration data checkpoint until the new version is confirmed. Prove repair/rollback behavior of the chosen installer instead of assuming NSIS provides transactional rollback. |
| Portable ZIP | A packaged helper or launcher must stage and validate a sibling application tree, wait for all owned processes to exit, replace only the intended application files, and relaunch that exact portable copy. The proposed helper runs without system installation or elevation. It must understand the portable marker, sidecar and notices. | Keep the previous binary tree and a durable journal until startup succeeds. Preserve adjacent `isolated-data` in place. Test interruption at each replacement boundary and recovery on the next launch. Do not treat an NSIS installer download as a portable update. |

The exact helper/installer interface remains an implementation design to verify against the current bundle. It must be a small owning boundary, not a general shell-execution capability exposed to the renderer. Reuse existing single-instance, shutdown, portable identity, installer cleanup and data-location behavior.

## Integrity, authenticity and permissions

- Use HTTPS and fixed official release origins; validate redirects and final artifact targets. Reject malformed metadata, unexpected executables, path traversal, symlinks/reparse escapes and ZIP entries outside the staging root. Verify the final bytes before applying them.
- Plan a pinned public verification key and signed update artifacts/metadata binding version, target and distribution to the selected payload. SHA-256 from the same mutable release alone does not establish publisher authenticity. A missing/invalid signature must fail closed. Do not silently fall back to unsigned installation.
- The existing Tauri 2 updater is a candidate native primitive, not an already installed dependency. Its documented update-signature verification is mandatory; Windows uses signed MSI/NSIS update artifacts. Its download/install APIs can support in-app progress. Sylvia's custom portable ZIP still needs its own verified route. See [Tauri updater documentation](https://v2.tauri.app/plugin/updater/).
- Keep update signing separate from Windows Authenticode/reputation. Choosing public-key custody, signing/release ownership, rotation/recovery and the authenticated manifest format remains prerequisite work before releasing an updater. No key, credential, account, purchase or signing configuration is created by this planning revision.
- Require only narrowly scoped native capabilities for the approved update operation. No arbitrary URL execution, command strings from metadata, broad filesystem access or persistent privileged service. Do not weaken CSP, TLS checks, Windows protections or repository settings.
- A per-user installation or writable portable folder can have an ordinary-user update route. A machine-wide/protected folder, denied ACL or denied OS consent is a real permission constraint: report it and retain the old working app. Do not bypass the restriction or run Sylvia permanently elevated. Tauri exposes Windows installer modes with different interaction/elevation limits; choose and verify the appropriate mode rather than hiding consent. See [Tauri Windows installer guidance](https://v2.tauri.app/distribute/windows-installer/).

## Data preservation and rollback

Preserve campaigns, exact content snapshots and pins, settings, saved Builder/GM history, layouts, unsent drafts, provider configuration and Windows-protected credential bindings. Do not export credentials to update logs or staging metadata. Updating application binaries must not perform campaign-content adoption; PL-01 remains separate.

Take a consistent local data checkpoint before any schema migration, and retain its app/schema identity with the previous binaries. If the new version has already written data in an incompatible schema, swapping an old executable is not a safe rollback. Detect that condition and offer an explicit restore of the matching checkpoint or a compatible repair; explain any post-checkpoint data that would be lost. Never silently discard new progress. Test same-user credential usability after update and retain the existing account-bound protection.

Cleanup may remove only update-owned staging/retention files after success or explicit user action. It must not delete backups, independent installations, imported packages or campaign data.

## Current constraints to resolve before delivery

- Current Alpha has no updater implementation or installed trust configuration. Existing releases cannot acquire this feature retroactively: the first updater-capable version requires a normal approved installation/bootstrap. Subsequent supported upgrades must complete within the app.
- Existing release bytes are unsigned Windows downloads, and current Cargo/configuration does not configure the Tauri updater. A trusted signing/release arrangement and updater-capable artifacts/manifest must be selected and implemented before authentic self-update can ship.
- Portable replacement, recovery-launcher packaging, Windows file locks, installer exit behavior, app/sidecar coordination, storage migrations, disk exhaustion and protected install locations need real final-artifact proof. An API success or mocked browser test does not establish these outcomes.
- Windows 11 is the currently tested platform. Other Windows versions and native Linux/macOS delivery retain their existing qualifications and PL-22/23 ownership.
- No security warning bypass, elevation, software installation, key creation, credential access or release publication is part of this planning pass.

## Acceptance and tests when implemented

1. Use signed disposable release fixtures for eligible current/new/old/wrong-channel/wrong-architecture/distribution cases; show truthful current/new version and inert notes. Check unavailable service, rate limits, invalid metadata and bounded retry.
2. Exercise explicit download, progress with/without content length, cancellation, interrupted transfer, disk-full and retry. Assert no live binary or user-data mutation before verification/application.
3. Reject wrong-key/missing/tampered signatures, truncated or swapped payloads, version/target mismatches, unsafe redirects and extraction escapes before execution. Include a valid signed artifact accepted by the same path.
4. Keep active GM/Builder work, unsent drafts and extra windows safe through defer, shutdown cancellation and retry. Confirm no action replay or duplicate provider submission.
5. On disposable ordinary Windows profiles, upgrade an installed NSIS copy from one real version to another, restart and verify app/owner identity, data, pins, layouts and protected provider configuration. Exercise installer failure, locked files and refused permissions; the old app remains usable.
6. Upgrade an actual writable portable ZIP layout through its helper. Verify the marker, sidecar and notices, preservation of `isolated-data`, exact relaunch target, independent-copy isolation, and interruption/recovery at each replacement phase.
7. Exercise failed startup, schema-compatible rollback and incompatible-schema recovery with a consistent checkpoint. Explain and obtain explicit choice before restoring older data. Confirm success only after the new app opens the intended data.
8. Reuse the existing portable identity/release recipe and NSIS cleanup tests at their owning boundaries, plus focused updater lifecycle and UI tests. Native installed/portable proof is required before claiming those outcomes; no feature tests or installations run in this planning PR.

## Scope and retained history

PL-01/#5 owns campaign-specific package adoption. Full SRD/#3 and Siren/#2 remain separate content work. This PR does not change their implementation, source licences or current shipped selection.

The earlier manual-check plan and its implementation experiment remain preserved in the branch history, including the planning checkpoint `47718537d43011a9694756c29c4f2a040b133cd9`. This revision adds the selected full outcome without rebasing or removing that history. The old document is explicitly historical.

This pass authorizes planning-document and PR metadata reconciliation only. No feature implementation, merge, tag, release, purchase or unrelated publication is selected. No GitHub Actions before November 2026.

