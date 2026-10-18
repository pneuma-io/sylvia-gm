# Campaign rules updates

Status: Draft for future work. This PR contains planning only. The feature is not
implemented or qualified by this PR; its regression, browser and native checks
remain to be completed.

## Player outcome

A player can preview an explicitly selected rules update for an existing campaign,
then adopt it when compatible. Campaign progress and the saved panel arrangement
survive. Installing or publishing updated content leaves existing campaign pins
unchanged until the player chooses to adopt it.

## Proposed scope

- Select an existing immutable local snapshot eligible under the campaign's current
  Ruleset and Campaign Setting ownership and compatibility rules.
- Preview changed content and explain conflicts before any apply. Block incompatible
  changes and removals; this scope does not choose destructive conflict resolutions.
- Require explicit confirmation at an idle completed-TURN checkpoint. Recheck the
  reviewed campaign revision and exact snapshot before applying through the existing
  activation transaction. Failure keeps the old version authoritative.
- Preserve saved state, existing windows, panel arrangement and applicable view
  preferences. Add new panels once in the sidebar.
- Retain previous exact versions for recovery. Returning to one requires a fresh
  compatibility review and must preserve progress made after the earlier adoption.
- Keep reusable source content and other campaigns unchanged.

Reuse the existing package activation, immutable pin/lineage and panel workspace
handling. Campaign sharing/archive writes, remote discovery, automatic updates and
automatic state repair are outside this scope.

## Acceptance and test plan

1. Create disposable campaigns with known state and a customized multi-window layout.
   Preview and cancel an update; verify that state, pins and source content are unchanged.
2. Adopt a compatible snapshot. Check progress, windows, views and exactly one copy
   of each new sidebar panel. Save and reopen to verify persistence and campaign isolation.
3. Explain and reject incompatible state/contracts and removed content or panels.
   Verify no partial writes on rejection or failure.
4. Reject stale previews and active work, including an open TURN after an earlier
   completed TURN. Check duplicate requests and lost acknowledgements against the
   existing activation receipt and rollback behavior.
5. Exercise recovery after further play without restoring stale progress. Cover the
   actual Lite package and a neutral synthetic Ruleset. Capture rendered UI evidence;
   qualify native multi-window behavior separately in the selected Windows environment.
