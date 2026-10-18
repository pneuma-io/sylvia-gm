# Manual desktop update checker (PL-16)

Status: future work. This planning-only draft does not deliver an updater. The proposed feature
is not implemented or acceptance-tested at this PR head. Installation and restart remain manual.

## Proposed scope

Add a user-initiated check in Settings → About against official Sylvia GM GitHub releases.
Show the current and available versions, safely rendered release notes, and an explicit button
to the official download page. Let users defer and retry without interrupting their work.

Stable installations would see stable releases. Prerelease installations would also see their
current named channel, such as Alpha. Keep the release source and channel visible, and explain
any limit on how many recent releases are checked. Run no background checks.

The download handoff would keep Sylvia running. Before manual installation, remind users to
finish GM turns and Builder work, save or copy unsent drafts, and back up campaigns. Retain the
existing installer and portable guidance, including preserving portable isolated-data and
verifying the release checksums. Discovery must not change campaigns, content pins, settings
or credentials.

## Acceptance to verify when implemented

- Discover a newer disposable release fixture and inspect its version and plain-text notes.
  Equal/older versions and releases outside the selected channel must not offer an update.
- Defer, close About, and retry while GM/Builder work and unsent drafts remain intact.
- Reject malformed release/package metadata and unsafe links. An unavailable service, timed-out
  or interrupted check must show a clear failure and permit an explicit retry.
- Open only the official release page on an explicit action. If no browser opens, provide the
  fixed official page for manual use. Never download, execute, install or restart automatically.
- Preserve existing installed/portable data and upgrade guidance. A check or handoff must not
  alter selected campaigns, content or settings.

## Test plan

Use mocked public-release responses for SemVer ordering, channel selection, missing or invalid
metadata, unsafe URLs, rate limits, service failures, interrupted reads and retry. Use disposable
data only. Browser checks should exercise the shipped About controls, inert HTML-like release
notes, deferral, handoff failure/retry and active GM/Builder draft preservation.

Reuse focused release-artifact and portable data-preservation regressions. Before claiming native
acceptance, exercise the selected installed and portable manual-upgrade journeys on disposable
Windows profiles. Browser fixtures and source checks cannot establish native installed acceptance.
No feature acceptance is claimed by this planning PR.

## Boundaries and dependencies

Existing version/signing display, unsigned Windows release downloads and artifact verification
are starting points to reuse. Secure automatic installation needs a separately selected signing,
verification and recovery design; it is outside this proposal. Do not add keys, relax verification
or change host security for this work.

PL-01 owns campaign-specific Ruleset/content adoption. PL-22 and PL-23 own native Linux/macOS
delivery. Existing Full SRD drafts #1/#3 and Siren Protocol draft #2 do not cover this app-update
scope. No release, merge, workflow execution or signing procurement is selected here.
