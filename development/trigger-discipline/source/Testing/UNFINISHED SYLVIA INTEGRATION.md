# Trigger Discipline implementation in progress

This branch retains the local ruleset continuation missing from main, including authored-campaign and character guidance, stance and cover geometry, panel and actor bindings, weapon handling and transfer, ground weapons, medical behavior, character eligibility and network actions/state/security. The owning regression sources are retained beside their modules.

This is unfinished development work. Existing historical qualification reports do not qualify this source against the current Sylvia host. Rebuild the generated draft from this branch rather than using the older draft/output files inherited from main:

```powershell
python -m sylvia_ruleset build --allow-worktree-sources
```

The resulting `ruleset-draft` has a standalone primary Ruleset with no dependencies. Its separate conformance Setting is only a test fixture. Sylvia PR #22 contains Siren Protocol, which takes the primary draft explicitly:

```powershell
# From the Sylvia PR #22 checkout, after installing its Python dependencies:
python scripts/build_siren_protocol_package.py --ruleset-draft "../trigger-discipline/ruleset-draft" --output "../siren-protocol.sylvia-gm-package"
```

Clone this branch as `trigger-discipline` beside the Sylvia checkout. The output path must be new. Siren cast starting-state choices and installed gameplay acceptance remain unfinished. Canonical atomic Markdown owns rules; this file is a development resumption note. GitHub Actions remain disabled during October.
