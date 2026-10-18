# Codex model selection and routing

Research checked on 10 September 2026. Supporting evidence for Wan's personal
workflow discussion, not Sylvia product or implementation authority. No model
settings, agents, plugins, or scheduled tasks were changed.

The evidence supports matching models to work, but does not establish a community
consensus or a measured savings forecast for Sylvia. Wan requested recommendations
drawn from other people's comparisons, without assigning him a benchmarking exercise.

Theo and Ben's [4 September Astra review](https://nerdsnipe.substack.com/p/openais-best-model-doesnt-know-when)
calls Astra their new benchmark for coding, computer use, and multimodal work.
Their published episode description also identifies UI, stopping behavior, and
agent reliability as sources of friction. This supports a capability preference;
it does not establish a recommendation to use Astra for every task.

Theo's earlier [11 July Sol advice](https://x.com/theo/status/2076078865060151465)
was accessible through a [readable mirror](https://www.techtwitter.com/articles/gpt-5-6-sol-without-hitting-limits).
The original X page could not be independently retrieved. The mirrored text says
he mostly used Sol, occasionally Terra for quick feedback, and considered Luna
useful for programmatic work and subagents. He preferred medium/high effort over
routine Extra High and emphasized controlling delegation. His July warnings about
Ultra bugs are historical, not verified defects in today's app.

His [28 August coding-model discussion](https://nerdsnipe.substack.com/p/ox-alpha-revealed-openais-latest)
links the original tier list. Full rankings could not be verified against the
original media, so secondary reports of its letter grades are excluded here.

[Matt Shumer's 3 September review](https://somethingbig.ai/astra-review) makes
Astra his everyday default, generally at Medium reasoning. He especially praises
backend engineering and computer use. His elaborate multi-agent experiments used
large token volumes; he says ordinary work does not need that setup.

[Simon Willison's 4 September comparison](https://simonwillison.net/2026/Sep/4/astra-pelicans/)
preferred Astra Low to every Sol result on an SVG drawing task. He observed that
Astra's lower token use narrowed the cost difference. That is a narrow creative
coding example, not a repository-maintenance benchmark. His quoted Sol prices
also differ from the current official promotional rates.

[Daniel McAteer's Sol Advisor](https://github.com/DannyMac180/sol-advisor) is a
concrete community implementation, with about 2,600 GitHub stars when checked.
Its current README keeps Sol High responsible for decisions and acceptance,
defaults to solo work, and optionally assigns bounded implementation to Luna Max
or judgment-heavy work to Terra High. A separate Sol review is a selected route.
This demonstrates model-specific delegation, not guaranteed savings or automatic
replacement of the primary model. It was inspected as evidence, not installed.

The current [Codex credit rate card](https://learn.chatgpt.com/docs/pricing#token-rates)
lists these standard rates per million tokens:

| Model | Input | Cached input | Output |
| --- | ---: | ---: | ---: |
| Astra | 250 | 25 | 1,250 |
| Sol | 100 | 10 | 500 |
| Terra | 50 | 5 | 300 |
| Luna | 5 | 0.5 | 30 |

For identical token counts, Sol costs 40% of Astra's credits; Terra 20–24%; Luna
2–2.4%, depending on the input/output mix. These calculations are not forecasts
of subscription-window savings. Context, reasoning, tools, caching, and retries
affect actual consumption. Sol's promotional pricing is documented through at
least 21 November 2026.

[Fast mode](https://learn.chatgpt.com/docs/agent-configuration/speed) increases
Astra and GPT-5.6 credit consumption by 2.5 times. Spark is a separate model with
its own preview usage limits, not another name for Fast mode.

Local inspection found Codex CLI 0.153.4 and these saved settings in
`%USERPROFILE%/.codex/config.toml`: `model = "gpt-6-astra"`,
`model_reasoning_effort = "xhigh"`, and `service_tier = "default"`. Only those
allowlisted setting lines were returned. Saved defaults may be overridden by an
individual task.

[Official model guidance](https://learn.chatgpt.com/docs/models) documents the
composer's model/reasoning control, the CLI `/model` command, and the shared local
configuration file. Higher effort uses more tokens. The published model roles
place Astra on the hardest work, Sol on complex general work, Terra on everyday
work, and Luna on clear repeatable tasks. Spark targets rapid text-only coding.

[Native custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
can specify their own model and reasoning effort. Without a configured or
explicit choice, they inherit the parent's settings. This can automate which
model handles a delegated job while the primary conversation stays on its chosen
model. For Sylvia, preserve one writer: helpers should read and report unless
writing ownership is explicitly transferred. A strong parent should receive
distilled findings instead of repeating the helper's entire investigation.

The [Codex app server](https://learn.chatgpt.com/docs/app-server) accepts model and
effort overrides on `turn/start`; these persist for subsequent turns. Its
`turn/steer` method does not accept a model override. The installed CLI's generated
protocol schemas independently confirmed this. CLI help also exposes model
selection on `exec` and `queue`. These are mechanisms for a custom router that
selects the next turn's model, not a built-in task-classification policy.

No native automatic primary-model router was established by the inspected model,
configuration, or [hook documentation](https://learn.chatgpt.com/docs/hooks).
An instruction to choose a cheaper model cannot by itself change the active
inference model. A custom client or dispatcher would need to make the actual
selection. Such a router has not been implemented or tested here.

Further research found direct comparisons of model and reasoning choices.

[Nori's 5 September experiment](https://zenn.dev/nnakapa/articles/lab-38-gpt6-astra-gpt56-sol-qcd)
ran 240 trials across three small Python/database tasks and four effort levels.
Astra Low and Sol High passed all checks. Summed task medians were 123.5 seconds
and $0.555 for Astra Low, versus 178.1 seconds and $0.381 for Sol High. Astra Extra
High took 260 seconds and $1.008 without a higher pass rate. Costs are API-price
estimates from Codex usage. This was a requested-model comparison; actual returned
model identity was not independently confirmed.

[Shinsuke Kagawa's 5 September Galley comparison](https://dev.to/shinpr/switching-from-gpt-56-sol-to-gpt-6-astra-start-with-medium-effort-25ao)
supports Medium as a practical Astra default while documenting useful High-only
fixes. It is one repository case study with model-assisted evaluation. The author
kept High's implementation. Reported costs exclude subsequent repairs and are
API equivalents, not subscription deductions.

Kagawa's earlier [5 August phase comparison](https://dev.to/shinpr/reasoning-effort-is-not-a-quality-setting-5aoe)
used seven configurations across design, design review, implementation, and code
review. Sol High improved on Sol Medium across the four phases, with larger
implementation gains than review gains. Luna Max implemented ordinary behavior
cheaply but introduced boundary regressions. One run per configuration and phase,
LLM grading, and a disclosed design-scoring flaw limit generalization. The
overengineering example in the article concerns Opus; it is not evidence that
raising Sol's effort generally worsens design.

[CursorBench 3.2](https://cursor.com/cursorbench) reports Luna High at 56.8%,
Luna Max at 61.1%, Sol Low at 52.6%, Sol Medium at 60.0%, and Sol High at 63.5%.
Luna Max used 87,973 tokens and 61 steps versus Sol Medium's 9,747 and 27.
Higher-effort Luna can compete on these tasks while doing more work. Cursor's
private evaluation uses its own agent workflow; small score differences may be
noise. Its prices must not be presented as Codex quota measurements.

[Instavar's corrected July Codex study](https://instavar.com/research/agents/gpt-5-6-codex-models-reasoning-levels-benchmark-2026)
retains 294 runs, but only 210 enter the corrected common comparison. Medium,
High, Extra High, and Max each accepted 42/42 valid common rows. Medium used less
time and fewer input tokens. The tasks mostly reached a ceiling, and the public
reproduction package is unpublished. This supports avoiding unnecessary reasoning
on bounded work, not equal capability on difficult work.

[ARC Prize's 3 September study](https://arcprize.org/blog/astra) supplies a
counterexample: Astra Max cost less overall than Medium and scored higher in
the Standard harness because it solved games in fewer actions. These are abstract
interactive puzzles, not ordinary repository work. It establishes that lower
effort need not lower total task cost, not that Max should be the coding default.

The firsthand subscription reports disagree. [rafwu](https://www.reddit.com/r/codex/comments/1w9erx3/comment/p89wrwa/)
reported Astra Low completing a task in 40 minutes versus Sol High's 70, with an
LLM judge preferring Astra; no quota delta was published.
[Budget-Walk6857](https://www.reddit.com/r/codex/comments/1w7tiqk/comment/p81y5we/)
reported a Java review exhausting their allowance on Astra Medium, while a
subjectively similar Sol Medium task used half as much. These September comments
are uncontrolled anecdotes, with incomplete task, plan, and context information.

[gorgono95's monorepo experience](https://www.reddit.com/r/codex/comments/1uz7pua/sol_vs_terra_vs_luna_what_actually_worked_for_me/)
favored Sol High for serious implementation and Luna High for documentation,
navigation, and evidence gathering. They found that higher reasoning did not
eliminate cheaper models' operational omissions. This is useful workflow
experience but has no published runs or measured error rates.

My recommendation from this evidence is Astra Medium for normal substantial
Sylvia work; Astra High for interacting lifecycle, retry, ownership, concurrency,
and persistence changes; Sol Medium/High for defined work when conserving usage
is the priority; and Luna High for bounded helper work, with Max available for
harder self-contained implementation. Extra High and Max should be selected for
difficult reasoning, not applied to every stage automatically.

The evidence does not justify a promise that Astra Low always beats Sol High,
that high-effort Luna replaces Sol's engineering judgment, or that more agents
always reduce consumption. Any later routing setup should preserve one writer
and avoid duplicating implementation and verification at every model tier.
