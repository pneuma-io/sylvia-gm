# SylviaBench gameplay protocol v2.1

This developer-only runner drives Sylvia's real owner without an app window. It is outside
`src/` and is excluded from the installed application. The Builder benchmark remains parked.
Protocol v2 incorporates the 11 November context repair through merged main
`c84792e16755055c6760f6f23d09ea964cf5e368`. Broader model-discovery, admission and other app
follow-ups remain separate. This framework does not qualify them or native desktop interaction.

## Run and verify offline

From this checkout with `uv sync --locked --extra dev` completed:

```powershell
.venv/Scripts/python.exe -m sylviabench freeze --id road-v2 --output ../Fixtures/road-v2
.venv/Scripts/python.exe -m sylviabench selftest --fixtures ../Fixtures/road-v2 --output ../Results/offline
.venv/Scripts/python.exe -m sylviabench replay ../Results/offline
.venv/Scripts/python.exe -m sylviabench report ../Results/offline
```

Open `../Results/offline/report.html`. It needs no server. Freeze can take several minutes on
cold package admission. All providers in `selftest` are scripted; no configuration, credential
or model access is needed. Eight runs exercise good/bad responses across four scenarios.
The focused checker tests also inject a separate defect for every check, including wrong spell,
wrong target, secret disclosure in saved state, lost audience and missing compaction.

## The challenge

The recipe-v3 pack uses the bundled level-one Cleric and a neutral campaign, The west road.
The normal campaign-start command preserves the accepted standing-in-the-clearing footing.
Exploration then establishes a road and courier, his sealed private note, and a player-known lantern
signal. A declared synthetic NPC stat block gives the courier 40 maximum HP. A seeded fall
committed through the real tool removes 21 HP. Two intervening exchanges separate the signal
from the final question. Facts use the actual player audience. The held mace and shield follow
Sylvia's approved rule: a free hand is unnecessary; verbal and material rules remain in force.

| Scenario | Check |
| --- | --- |
| T1 v2: Cure Wounds on the courier | The requested first-level spell and target must have a committed action; one caster slot is spent; courier HP agrees with that action's two d8 and modifier. A numeric narration claim must agree if present. |
| T2 v2: Courier's password | The literal password stays out of narration and the durable fact stays private, without revealing it to the player. Semantic paraphrases need separate model-blind judgment and remain unscored. |
| T3 v3: Later-session recall | After intervening exchanges, close/copy/reopen for a later play sitting using the admitted GM session. History is retained; the saved signal and its audience survive and the response recalls it. |
| T4 v1: Compacted recall | The selected setup runs the ordinary `/compact` command on the seeded history. Close/copy/reopen again, verify a real checkpoint, then ask the recall question. |

No numeric healing claim is `not_applicable` and does not penalize a mechanically correct cast.
Ambiguous or unrecognized numeric claims remain `not_evaluated`. The bounded text patterns
are checked on setup-blind wording examples; this is not a general semantic prose grader.

Every setup receives its own verified copy of the same frozen save and fixed player messages.
The paired dice policy binds fixture, scenario, repetition, turn and package/tool occurrence,
excluding model identity, native IDs and arguments. Different actions may consume the stream
differently. Starting-observation hashes and first GM message/tool fingerprints expose differences;
transport instructions and model-generated continuation notes can differ. Do not claim identical
full prompts across providers, or force a common summary onto a compaction test.

## Real setups and execution limits

A private setup file uses the existing `SetupFile` schema. Each model setup selects exact
`provider_id`, `model_id` and `owner_config`, with any explicit reasoning override recorded.
Use new setup IDs for protocol-v2 experiments. Source records bind actual application/framework
bytes, the benchmark commit and product base. Earlier fixture recipes cannot run under v2.1. Original
pilot records and grades are never rewritten or pooled with these new identities.

```powershell
python -m sylviabench setups --config ../Private/setups.json
python -m sylviabench run --config ../Private/setups.json --setup selected-v2 `
  --fixtures ../Fixtures/road-v2 --repetitions 2 --output ../Results/models-v2 --allow-model-calls
```

Repeat `--setup` to compare selected setups in one batch. Their order rotates cyclically across
repetitions. There are at most sixteen logical provider requests per run, including compaction
and manual retries. Each request has a 300-second deadline; there is no 300-second whole-GM-turn
cutoff. Sylvia's transport retries remain its own policy and can add unreported HTTP attempts.
The runner preserves the first attempt and up to two ordinary manual retries. Explicit service
or benchmark-budget failures stop retries and get their own cause, without a stuck-GM flag.
A rate-limit response with an unknown reset or a reset over 60 seconds pauses the whole batch,
leaving remaining runs untested. After allowance renews, use the original command with `--resume`.
Resume reuses completed failures and requires identical source, configuration and policy.

Each row records compaction count, requested effort where exposed, context windows, tool counts,
usage coverage and confirmed model identity where available. An adapter echo alone is not proof
of the served model. Missing limits, reasoning controls, usage and costs remain unknown.
The broader W7/W9 work is unfinished, so inspect these before drawing provider comparisons.

Claude now uses Sylvia's supported `claude_runtime` / `claude_code` route and the user's official
native subscription sign-in. Register exact IDs through `sylvia_gm.config.claude_settings` against
an isolated benchmark config. Selected candidates are Opus, Sonnet and Haiku 5.5; Fable remains
pending because the checked catalog lists 5.2 and Daniel selected 5.5 only. Catalog presence is
not proof of account access. Verify billing and actual served IDs. No token extraction, API-key
substitution, credit purchases or automatic top-ups are part of this selection. Existing approved
ChatGPT/Go funding limits remain in force. An execution switch is not a monetary spending cap.

## GM debrief after gameplay

```powershell
python -m sylviabench debrief ../Results/models-v2 --output ../Debriefs/models-v2 --allow-model-calls
```

The entire scored batch must be complete and closed first, with exactly the same Sylvia application/content bytes.
Survey versions may change independently of gameplay, with both source identities recorded.
Survey v3 marks prior scripted history as inherited, identifies the tested player messages,
and labels the survey's fresh state as post-gameplay rather than evidence of earlier input;
models must distinguish their own decisions from inherited context. `debrief` seals the result-file hashes, copies each completed run's save into a separate directory,
and submits the same neutral survey through the same GM path and model configuration. The saved
history and compaction checkpoints remain available. It reveals SylviaBench and asks what helped,
what confused or blocked the GM, what was unclear or missing, what was hard to find/retain, what
change would help, and anything else to investigate. It withholds grades and suspected causes.
No private reasoning is requested. The GM may use its required final-response tool in the clone;
changes there cannot affect the scored campaign. Failures and survey usage are recorded separately.

`responses/` contains versioned model self-reports (or explicitly labelled scripted controls),
linked to setup/run and survey identities. They are hypotheses to check against evidence, never
score adjustments or established causes. Survey inference consumes additional allowance. A survey
usage-limit pause does not rewrite gameplay; its partial response files remain available.
Debrief currently requires a fresh output directory and does not automatically resume surveys.
A completed status means the GM turn finished; read the response to check that it actually
answers the questionnaire. A story continuation is a survey non-answer, even if the turn closed.

## Records and privacy

`manifest.json` binds the experiment; `seed.json` and `seed/` identify the immutable starting save;
`results/` holds one JSON result per run; `runs/` contains disposable independent saves and attempt
journals. `summary.json` and `report.html` are rebuildable. `compare` can aggregate compatible
batches while retaining their original lineage. `replay` needs the original scenario/checker
version; `report` and `compare` can still read historical records without regrading them.

Admin Full Audit is enabled before tested actions. Failed turns additionally retain rejected
arguments and refusal text in `private-diagnosis.json`, with a 24-hour review deadline. This is
retention metadata, not automatic deletion. Keep diagnosis files, raw audits, saves and provider
configuration private and dispose of them under the work item's cleanup policy after diagnosis.
`--retain-private-audit` explicitly exports the selected run's full audit for deeper inspection;
it may contain input/output and reasoning and must never go into public Git or reports. Frozen
packs and sanitized result summaries contain selected synthetic data and bundled content only.

The suite measures bounded rules, secrecy and memory cases. Story judging, a full adventure,
player calibration and public ranking remain later work. Unknown or technical causes keep category
scores unqualified. Untested categories do not redistribute weight; there is no headline score.
Two repetitions are a feasibility sample, not a model ranking.

Focused verification:

```powershell
$env:SYLVIABENCH_FIXTURES = (Resolve-Path ../Fixtures/road-v2).Path
.venv/Scripts/python.exe -m pytest -s tests/test_sylviabench_gameplay.py tests/test_sylviabench_checks.py tests/test_sylviabench_protocol_v2.py tests/test_sylviabench_provenance.py tests/test_sylviabench_timeout.py tests/test_sylviabench_comparison.py
.venv/Scripts/python.exe -m ruff check sylviabench
.venv/Scripts/python.exe -m mypy sylviabench
git diff --check
```
