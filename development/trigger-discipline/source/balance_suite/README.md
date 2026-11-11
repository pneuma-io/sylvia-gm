# TD balance instrumentation

This lab is being reconciled with the qualified TD 0.1.30 integration baseline.
Its parameter values remain experimental. Structural coverage, exact arithmetic,
runtime parity and tactical-policy qualification are separate results.

Run it from the Sylvia checkout with `python scripts/trigger_balance.py`. It uses
this checkout's shared spatial engine. The private rules vault stays outside the
public checkout; select it with `TD_BALANCE_NOTES_DIR` or the global
`--notes-directory` option. Source validation checks the compiler's bound note
identities, including normalized line endings. A branch name alone is insufficient.

Useful commands, after selecting the private vault:

```powershell
python scripts/trigger_balance.py validate --structural-only --output ../evidence/validation.json
python scripts/trigger_balance.py coverage --structural-only --output ../evidence/coverage.json
python scripts/trigger_balance.py exact --output ../evidence/exact.json
python scripts/trigger_balance.py overwatch-parity --package TD_PACKAGE --fixtures tests/fixtures/overwatch-lab-parity-v1.json --output ../evidence/lanes
```

Use a fresh output directory for each parity run. Coverage output can contain
private note excerpts and belongs outside the public repository. The public
compiler's existing private-content boundary remains in place.

`--structural-only` audits the authored ability inventory and counterfactuals
without using tactical policies as evidence. It does not qualify numerical
balance or current-runtime composition. The historical angle-based Overwatch
probe is explicitly labelled legacy; shooter-origin coverage is checked through
the package/lab differential fixtures.

Saved duel and team results carry a hash of the lab, retained compiler inputs
and shared spatial code. Replay and objective-result reuse reject missing or
different execution identities. Preserve historical outputs as historical data.

Current limits include tactical-policy failures, older policy-dependent objective
assertions and SENTINEL's combined AIM/Overwatch interface still using authored
fixed zones. Ordinary lane setup must not silently apply the combined ability.
The reference team fixture declares its lane dimensions explicitly; those
dimensions grant no player or class capability. Wider class coverage, new
penalties and intermediate progression schedules need their own design decisions.
