# SylviaBench gameplay framework

SylviaBench runs short gameplay scenarios through Sylvia's real owner process without opening
the desktop app. Its supplied self-test uses scripted providers. It measures whether the framework
and its checks work; it supplies no model rankings or calibrated player recommendations.

The developer package lives outside `src/` and is excluded from the installed app. The earlier
offline Builder framework remains a separate, parked track.

## Run the offline example

From a Sylvia source checkout with its Python development dependencies installed:

```powershell
.venv/Scripts/python.exe -m sylviabench selftest --output .scratch/bench-example
.venv/Scripts/python.exe -m sylviabench replay .scratch/bench-example
.venv/Scripts/python.exe -m sylviabench report .scratch/bench-example
```

Open `.scratch/bench-example/report.html`. It is a standalone file with no server, scripts or
external assets. The equivalent script entry point is `python scripts/sylviabench.py`.

`selftest` builds one synthetic campaign through the normal campaign interview and Character
Builder, using the bundled Cleric and a compatible level-one Plan. A scripted GM establishes a
minor fall injury, a player-known signal and a private password. Each run gets an independent copy
of that closed save. No installed profile, personal campaign, provider configuration or credentials
are required. Package admission can take several minutes on a fresh process; progress is printed.

The command succeeds only when all three good cases pass and each bad case fails its intended
check. It records six runs, not six real-model observations.

## Reuse the same challenge across batches

Create a named fixture pack once, then select it for every comparison:

```powershell
python -m sylviabench freeze --id gm-trial-v1 --output ../Fixtures/gm-trial-v1
python -m sylviabench verify-fixtures ../Fixtures/gm-trial-v1
python -m sylviabench selftest --fixtures ../Fixtures/gm-trial-v1 --output ../Results/selftest
```

The pack contains a closed synthetic save, its campaign and participant files, starting observations,
and file hashes. It excludes provider settings and raw audit traces. Keep it locally with the comparison
deliverables, outside Git. The runner verifies every file before use. A pack requires the same Sylvia
application, dependency-lock and content bytes; a benchmark-only commit can reuse it. Its generation
commit remains recorded. Changing the starting injury or history requires a new pack identity.

Dice use the benchmark's `package-tool-occurrence-v1` policy. Within the same fixture, scenario,
repetition and player turn, the same numbered invocation of a package mechanic starts with the same
random stream. Model names, native call IDs and parameter values do not change that seed. Models can
still choose different actions or consume different numbers of dice. The policy records each tool,
occurrence, parameter hash and seed; it does not force equal outcomes for different actions. It runs
only inside this developer runner and leaves the shipped game's normal dice policy unchanged.

Records also contain hashes of the initial observations, player messages and provider-boundary
messages/tools, plus the selected context/output/reasoning metadata. The report compares the first
GM input in matched runs, separately from preparation or compaction requests. Per-message and
per-tool fingerprints locate differences without copying their content. Differences need inspection
before making an equivalent-challenge
claim. Inputs after the first model response can naturally diverge. Transport-specific serialization
and hidden service instructions are outside this equality check.

## Other commands

```powershell
python -m sylviabench setups
python -m sylviabench scenarios
python -m sylviabench run --setup good --scenario T3 --repetitions 2 --output .scratch/recall
python -m sylviabench run --setup recover --scenario T3 --output .scratch/recovery
python -m sylviabench run --setup stuck --scenario T3 --output .scratch/interruption
```

`setups` lists the explicit benchmark configuration. It does not discover models, contact services
or read installed credentials. `--config sylviabench/example-setups.json` selects a configuration
file; each setup ID must be unique. Omit `--scenario` to run the whole supplied set. Repeat
`--setup` or `--scenario` to select several. Repetitions apply equally to the selected setups.

`recover` deliberately interrupts the first attempt. `stuck` interrupts three attempts. These
are framework probes, not model behavior. The runner uses Sylvia's manual Retry command at most
twice (`--retries 0`, `1` or `2`), preserving every attempt's outcome and measured wait.

## What the checks cover

| Scenario | Required observations | Known-bad case |
| --- | --- | --- |
| T1: Healing | A slot is spent; HP gain matches the committed healing dice, modifier and HP cap; the narrated amount matches the receipt. | The GM states a healing amount one higher than the committed amount. |
| T2: Secret | The declared password phrase stays out of narration and its durable fact stays GM-private. | The GM states the password. |
| T3: Recall | The signal fact survives closing, copying and reopening the save, and the answer recalls the blue lantern at the west gate. | The GM answers with a red flag at the east gate. |

The secret is synthetic campaign memory with an explicit private visibility. This first fixture
does not validate a Plan secret's reveal conditions or detect semantic paraphrases. The narration
checks use bounded text patterns, not an LLM judge. Story judging, long-context and compaction
scenarios, a simulated adventure player and player-experience calibration remain later work.

The owner records mechanics and TALK narration. Checks read those committed records rather than
trusting the scripted driver's proposed actions. Admin Full Audit is enabled for the synthetic
run; raw audit traces and model reasoning are not copied into result records or reports.

## Records, resume and replay

A results directory contains:

- `manifest.json`: exact source, framework, scenario and setup identities, repetitions and retry policy.
- `seed.json` and `seed/`: the frozen synthetic campaign, its starting observations and database hash.
- `results/`: one validated JSON result per setup, scenario and repetition.
- `runs/`: independent saves and attempt journals, including unfinished process attempts.
- `summary.json` and `report.html`: rebuildable summaries and the readable report.

Use the original command with `--resume` after stopping. Resume requires the same source commit,
relevant working changes, framework bytes, scenarios, setups and run policy. Completed runs are
read back and reused, including failures. A process interrupted before its result was saved gets
a fresh independent save; its old directory and attempt journal remain available. Resume happens
between whole runs, not in the middle of a partially executed player turn.

Only one process may write a results directory. If a process is killed and leaves `run.lock`, verify
that it has stopped before removing that file. A partially built seed needs a fresh results
directory. The runner never replaces an existing save.

`replay` reruns the checks over saved observations without opening an owner or constructing a
provider. `report` validates records and regenerates the HTML. Neither command executes model
work. Keep the corresponding source checkout when replaying an older suite. Edited or mismatched
manifests and mixed source/scenario/setup records are rejected.

Results retain requested provider/model/reasoning separately from observed identity. Unknown
identity, token use and cost stay unknown. Scripted runs have no model usage. Error attribution
requires evidence; unclassified failures remain unknown. Missing or interrupted checks never pass.
An unchanged history cannot pass a turn by reusing its previous narration: a new committed
player-visible response is required. Technical or unclassified failures leave their category unscored.

Category scores average the share of passed runs within each scenario, then average scenarios
within the category. The planned weights are Rules 30, Memory 25, Fair play 25 and Story 20. Story
is untested in this suite, so there is no headline score. Missing categories do not redistribute
their weight. Reports show sample counts, interruptions and measured waits; with at least two
runs per observed scenario they also show bootstrap bands for this fixed scenario set. These
bands do not establish player-experience validity. No reference-relative verdict thresholds are set.

## Connecting a model later

The runner has an explicitly enabled route to Sylvia's configured provider resolver. A `model`
setup names an exact `provider_id`, `model_id` and `owner_config` path. It cannot execute unless
`run` also receives `--allow-model-calls` and a verified `--fixtures` pack. No supplied setup enables
this route. For example, after creating a private setup file:

```powershell
python -m sylviabench run --config ../Private/setups.json --setup selected-model `
  --fixtures ../Fixtures/gm-trial-v1 --repetitions 2 --output ../Results/first-model `
  --allow-model-calls
```

Each run allows at most 16 calls at Sylvia's provider boundary, including calls made during manual
Retry. Sylvia's transport-level retries retain their product policy and can make additional HTTP
attempts. The manifest records this bound. Provider adapter identity is recorded as adapter evidence;
it is not automatically treated as an independently confirmed service identity.

That code path has no live acceptance claim. Headless ChatGPT account authentication, provider
usage extraction, cost limits, service-failure rerun policy and natural-language checker calibration
must be qualified before using it for comparisons. Select the models, connection access and
spending limits first. The framework does not enforce a monetary spending cap or infer subscription
quota from elapsed time. The explicit opt-in is an execution switch, not evidence of a budget.

Provider configuration stays at the explicitly selected local path and is never copied into the
results directory. Keep that path and any benchmark result containing private material out of
commits. The built-in fixtures contain only synthetic facts and bundled content.

For a qualification run needing exact input inspection, `--retain-private-audit` exports the enabled
owner's Full Audit into each run's `private-audit.json` before shutdown. It can contain raw model
input, output and reasoning. Keep it private; do not commit it or include it in a shared delivery.
Normal result records contain fingerprints and selected metadata. Remove retained raw audits after
the relevant discrepancy has been diagnosed.

## Add a scenario

1. Add an entry to `scenarios.json` with a stable ID/version, category, exact player messages,
   mandatory check IDs and the failure its bad driver must demonstrate.
2. Add its scripted good/bad tool sequence in `fixtures.script` and its evidence checks in
   `checks.evaluate`. Use owner-authoritative mechanics and campaign records.
3. Extend the offline acceptance test to prove the good case passes and the bad case fails for
   its intended reason. Missing evidence must remain `not_evaluated`.
4. Increment scenario or suite identities when the behavior or scoring rules change. Do not pool
   results across those identities or tune one model's scenario privately.

The framework uses closed Pydantic records for setup, scenario, attempt, check and result data.
`owner.py` owns transport and stream attachment; `fixtures.py` owns synthetic campaign preparation;
`packs.py` owns reusable fixture verification; `randomness.py` owns the paired dice override;
`provenance.py` observes the unchanged provider boundary;
`runner.py` owns isolation, retry and resume; `checks.py` owns evidence interpretation; `report.py`
owns aggregation and HTML output. Additional checkers should stay at that same boundary.

Run the focused verification locally:

```powershell
.venv/Scripts/python.exe -m pytest -s tests/test_sylviabench_gameplay.py
.venv/Scripts/ruff.exe check sylviabench scripts/sylviabench.py tests/test_sylviabench_gameplay.py
.venv/Scripts/mypy.exe --follow-imports silent sylviabench
git diff --check
```
