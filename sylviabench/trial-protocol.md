# First live comparison

Selected on 10 November 2026. This is a developer pilot on the three implemented gameplay
scenarios, followed by a decision about the next OCG sample. It does not complete the larger
scenario, adventure or player-calibration roadmap.

## Models and connections

Use the latest generally available member of each named OpenAI and Anthropic family. The current
public catalogs list:

| Family | Candidate ID | Intended Sylvia connection |
| --- | --- | --- |
| Astra | `gpt-6-astra` | ChatGPT account |
| Sol | `gpt-6.1-sol` | ChatGPT account |
| Terra | `gpt-6.1-terra` | ChatGPT account |
| Luna | `gpt-6-luna` | ChatGPT account |
| Fable | `claude-fable-5-2` | Anthropic API using included subscription credits |
| Opus | `claude-opus-5-5` | Anthropic API using included subscription credits |
| Sonnet | `claude-sonnet-5-5` | Anthropic API using included subscription credits |
| Haiku | `claude-haiku-5-5` | Anthropic API using included subscription credits |

Catalog presence is not proof of account access. Record the actual advertised model, its exact
canonical Sylvia ID, connection and transport before execution. Mythos 5.2 has restricted access
through Anthropic's verification programs; check account availability and record the result rather
than silently treating Fable as a Mythos result. If a required family is unavailable, bring the
access choice to Daniel. Do not silently replace Sylvia with an external agent harness.

Start the OCG pilot with GLM-5.3-Flash, DeepSeek V4.1 Flash and Qwen3.8 Flash. This samples three
families and two native API formats. If account-region settings block DeepSeek, record that access
failure before considering MiMo-V2.6-Flash as a separately named pilot choice. Do not change account
privacy settings to make it pass. Any model through OCG is a separate setup from its direct route.

Use Sylvia's GM defaults for each model. Record the effective request controls, context and output
allowances. Equal reasoning labels are not assumed to mean equal computation across families.
Fast mode is off. No prompts or checks are tuned for an individual model.

## Fixed experiment

1. Qualify the supported account/API route using the synthetic fixture. Qualification observations
   stay separate from comparison scores.
2. Freeze one `gm-trial-v1` pack and verify it before each run. Keep this exact artifact for later
   batches. Use the same application/content bytes, scenario versions and paired-dice policy.
3. Run T1 healing, T2 secret and T3 save/reopen recall twice per setup: six runs per model. Use at
   most two manual retries and sixteen Sylvia provider calls per run. Preserve unsuccessful first
   attempts and any transport-level retry qualification.
4. Compare starting-observation, player-message and first provider-input hashes. Inspect differences
   before interpreting setups as equal challenges. Model-generated continuations can diverge.
5. Keep incomplete or unattributed failures explicit. Do not replace missing data, redistribute
   untested story weight or invent a headline rank. Two repetitions are a feasibility sample.

The full general-access family sample plus three OCG choices would mean 66 scored runs. Following
Daniel's funding selection, the first funded batch is four ChatGPT models, three Go flash models
and Haiku through Go: eight setups and 48 planned scored runs, subject to inference qualification.
Fable, Opus and Sonnet stay pending testing credits; a later direct Haiku run would be a separate
route comparison. Mythos access remains separately restricted. Stop on exhausted allowance; retain
missing matched runs as untested and resume when the supported allowance renews. Do not fill in
another model.

The current Sylvia discovery parser misses the newer Go catalog's nested `limit` metadata. This
pilot enters each published 1,000,000-token context size through Advanced settings and records
`provider_metadata` as its source. All four Go setups retain the existing 4,096-token output
fallback. Their reasoning control is unrecognized by this Sylvia version and remains unset.
Report truncation or unknown effort as a harness limitation; do not infer a model's general
capability from it. Improving discovery or changing the output allowance needs a new setup/batch.

## Spending and privacy

Additional spending is zero. ChatGPT must use Sylvia's managed account connection. Claude requires
a supported API key and verified included-credit funding with no paid fallback; a Claude app login
alone does not establish that route. Daniel deferred the direct Claude runs until testing credits
are available; retain those families as pending. Haiku through Go is a separately identified setup.
For OCG, disable Extra Usage or verify that no paid balance and no automatic top-up can fund it.
Daniel confirmed a zero credit balance and auto top-up off for this pilot. The benchmark request
bound is not a monetary spending cap.

Keep keys, account configuration and raw Full Audit traces out of Git and the comparison report.
Enable Full Audit for the selected synthetic campaign before tested actions. Retain only the small
necessary results and reproducible fixture artifact with the delivery.

## OCG decision after the pilot

Report supported route/tool behavior, checker outcomes, interruptions, waits, and measured usage
where available. Separate access/transport failures from model behavior. Decide whether the next
OCG work should widen the model set, increase repetitions, deepen gameplay scenarios or repair a
specific integration. These three scenarios cannot support an overall player recommendation.

## Catalog and funding sources

Checked 10 November 2026:

- [OpenAI model catalog](https://developers.openai.com/api/docs/models)
- [Anthropic model overview](https://platform.claude.com/docs/en/models/overview)
- [Fable and Mythos 5.2 availability](https://www.anthropic.com/claude-fable-and-mythos-5-2)
- [Claude API credits for subscribers](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers)
- [OpenCode Go models, endpoints and Extra Usage](https://dev.opencode.ai/docs/console/go/)

Public catalogs must be rechecked if execution occurs after this selection date. Saved local
catalogs and prices do not establish live entitlement or an available balance.
