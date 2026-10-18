# Provider Context Capacity: Postmortem and Implementation Plan

> **Status:** Implementation, focused verification, and the complete installed-host proof are
> complete; Wan's acceptance of the exact installed candidate remains
>
> **Scope:** Hosted and local provider/model context capacity, request-fit admission, settings UI,
> diagnostics, and installed-host verification
>
> **Origin:** Wan's 2026-08-20 review of the misleading 32K configuration and conservative
> 33K request-fit report

This is the focused implementation specification for correcting how Sylvia GM discovers,
configures, displays, and enforces model context capacity. The active Alpha plan links here for
status. Do not reconstruct this work from chat history, Hermes documentation, or a single token
error shown by the current candidate.

## 1. Problem statement

Sylvia currently collapses several different facts into one `context_window` number:

- the model's theoretical maximum context;
- the context actually allocated by a local inference server for the loaded instance;
- a user-selected lower operating cap;
- the input budget available after reserving response tokens; and
- an unverified fallback used when discovery returned no context metadata.

That produced two misleading results in the installed flow:

1. The Models UI silently substituted `32,768` when OpenAI-compatible model discovery returned no
   context metadata. LM Studio's `/v1/models` response commonly provides model identities without
   the loaded runtime context, so the fallback looked like detected truth and was saved as if it
   were authoritative.
2. Preflight reported approximately 33K "conservative tokens required" by counting each UTF-8
   request byte as one possible token and adding the output reserve. This was a safe upper bound,
   not an actual tokenizer result, but the wording and the single context field made it easy to
   interpret as an exact model requirement.

The current source locations that own this behavior are:

- `src-tauri/frontend/app.js`, where absent discovery metadata falls back to `32768`;
- `src-tauri/frontend/app.html`, whose initial context input is `32768`;
- `src/sylvia_gm/config/provider_settings.py`, whose generic discovery reads `/models` and accepts
  context metadata only when that endpoint happens to return it;
- `src/sylvia_gm/provider/_base.py`, where `ModelInfo` stores one context value and one output cap;
  and
- `src/sylvia_gm/provider/_context_fit.py`, where the complete serialized request is admitted using
  a one-token-per-UTF-8-byte upper bound.

This is not fixed by replacing `32768` with a larger magic number. The correct value belongs to the
exact provider, model, and running server configuration.

## 2. What Hermes Agent demonstrates

Nous Research's Hermes Agent supports local inference through LM Studio, Ollama, vLLM, llama.cpp,
SGLang, LocalAI, and generic OpenAI-compatible endpoints. Its useful architectural choices are:

- `context_length` means the total input-plus-output window;
- the output limit is a separate setting;
- an explicit model or per-provider/model override has a clear configuration path;
- discovery uses several sources rather than trusting one generic `/models` response;
- context is provider-aware because one model may be served with different capacities; and
- local-provider documentation distinguishes a model's declared maximum from the context actually
  allocated by the server.

Hermes's documented resolution chain includes explicit configuration, per-provider/model
configuration, cached discovery, endpoint metadata, native provider metadata, model registries,
and finally family fallbacks. Its current LM Studio integration preserves an already-loaded
instance's context and reads the reported context after loading. Its Ollama guidance explicitly
warns that the model maximum and effective `num_ctx` can differ.

Primary references:

- [Hermes provider and context-length documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/providers.md)
- [Hermes local-model FAQ](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/faq.md)
- [Hermes context compression and token-accounting design](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/context-compression-and-caching.md)
- [Hermes context compressor implementation](https://github.com/NousResearch/hermes-agent/blob/main/agent/context_compressor.py)

Hermes is precedent, not Sylvia's specification. Sylvia must not copy these parts blindly:

- Hermes requires a 64K minimum for its own large general-agent prompt/tool surface. Sylvia's
  supported and recommended capacities must be derived from Sylvia's real requests rather than
  inherited as a magic floor.
- Hermes can fall back to model-family context guesses. Sylvia must not present such a guess as an
  observed runtime limit.
- Hermes performs lossy conversation summarization when context pressure grows and uses rough token
  estimates when provider usage is unavailable. Sylvia's Campaign Interview contract forbids
  silent compaction of its complete required transcript.

## 3. Selected design

### 3.1 Store capacity facts instead of one ambiguous number

Each configured model must be able to carry these distinct values:

| Fact | Meaning |
|---|---|
| **Declared model maximum** | The largest window claimed by model metadata or a trusted catalog. It is not proof of the loaded server allocation. |
| **Observed runtime context** | The effective window reported for the exact loaded provider/model instance. This is the strongest server-side fact. |
| **Configured operating limit** | An optional user-selected cap. It may deliberately be lower to reduce memory use. |
| **Effective context capacity** | The safe capacity Sylvia uses for admission, derived from the known runtime limit and configured cap. |
| **Maximum output tokens** | The per-response ceiling, stored and displayed separately. |

Every value retains its source and observation time. Required source labels are:

- `runtime_reported`;
- `user_configured`;
- `provider_metadata`;
- `model_catalog`;
- `legacy_unverified`; and
- `unknown`.

An explicit configured limit is authoritative as a voluntary lower cap. It is not permission to
claim more capacity than a smaller observed runtime allocation. When both are known, the effective
capacity is the smaller value and the UI explains the mismatch. When runtime capacity is unknown,
an explicit user value may be used but remains labelled **Configured**, not **Detected**.

### 3.2 Resolve capacity from the exact provider runtime

Resolution follows this order without turning lower-quality metadata into higher-quality truth:

1. Probe provider-native loaded-instance metadata when the selected preset supports it.
2. Read usable context metadata from the selected provider's standard model catalog.
3. Apply an explicit provider/model configuration supplied by the user.
4. Use trusted hosted-provider or model-catalog metadata only as the declared model maximum.
5. Otherwise report runtime capacity as **Unknown**.

The configured lower cap is then applied to the strongest known runtime capacity. A cached runtime
observation is keyed by provider identity, base URL, model ID, and relevant load identity where the
provider supplies one. It must be refreshed after model selection, server restart/reconnection, or
an observed load-configuration change. A cache is a convenience, not immutable truth.

Provider-specific work for the first vertical:

- **LM Studio:** query its native model/instance metadata in addition to `/v1/models`; retain both
  `max_context_length` and the loaded instance's configured context where available.
- **Ollama:** distinguish the model's declared maximum from the active server/model context and
  tell the user when an explicit `num_ctx`-equivalent must be configured server-side.
- **vLLM, llama.cpp, SGLang, LocalAI, and Custom OpenAI-Compatible:** consume standard metadata when
  supplied; otherwise accept a clearly labelled explicit configuration rather than inventing one.
- **Hosted presets:** retain existing audited catalog values, but label whether they are provider
  metadata or local catalog knowledge.

Sylvia continues to connect to already-running servers. It does not download models, allocate KV
cache, or silently reconfigure local inference processes in this work.

### 3.3 Make the Models UI honest

The Models settings surface must show, for the selected model:

- declared maximum context, when known;
- currently observed runtime context, when known;
- optional configured operating limit;
- effective context used by Sylvia;
- output-token limit; and
- the source/status of every displayed value.

Required behavior:

- remove every silent `32768` substitution;
- show **Unknown** when no trustworthy fact exists;
- never save a placeholder merely because an input control needs a value;
- explain that changing a Sylvia cap does not enlarge an already-loaded local server instance;
- warn when a configured value exceeds the observed runtime allocation;
- allow a lower explicit cap without calling it the model maximum; and
- refresh the projection after model discovery or an explicit re-probe.

The UI may recommend a larger capacity for full Builder/GM use, but the recommendation must be
labelled as product guidance rather than detected model truth. The initial recommendation should be
derived from installed-request measurements. Do not adopt Hermes's 64K floor or choose 128K solely
because those are familiar round numbers.

### 3.4 Separate capacity discovery from request token accounting

Request-fit reporting has four honest precision states:

| Status | Meaning | May prove fit? |
|---|---|---|
| `authoritative_count` | Produced by the exact selected model/provider tokenizer or count endpoint for the assembled request. | Yes |
| `conservative_bound` | A documented upper bound over the complete Sylvia-owned request representation. | Yes |
| `estimate` | A heuristic such as characters-per-token or calibration from prior calls. | No |
| `unavailable` | No defensible measurement exists. | No |

Provider-reported usage from a completed request is authoritative for that completed request, not an
exact preflight count for a different request. Estimates remain valuable for progress display and
diagnostics but never masquerade as exact counts.

Preflight must assemble the same neutral messages, tools, controls, options, and output reserve that
will be passed onward. It then checks:

```text
admitted input count or bound
+ output reserve
+ any separately measured provider-transformation overhead
<= effective context capacity
```

The current UTF-8-byte bound may remain as a portable conservative method, but its UI and Audit
label must say **upper bound**, display the exact request bytes separately, and avoid calling that
number the request's actual token usage. Optional exact counters belong behind provider/model
capabilities; Sylvia must not require one universal tokenizer package or download tokenizer data
silently.

If neither an authoritative count nor a conservative bound can establish fit, the existing selected
contract applies: block before sending, preserve the Draft or subflow, explain the uncertain
capacity, and offer model switching or a bounded-input reduction. Campaign Creation is never made
to fit through silent transcript truncation or summarization.

### 3.5 Preserve Sylvia-specific context behavior

This correction does not introduce a universal conversation compressor.

- **Campaign Interview:** retain the complete required Builder-question/player-answer transcript;
  never summarize or silently prune it.
- **Creation Subflow:** retain the complete required parent transcript plus subflow conversation
  required by the selected workflow.
- **GM play:** continue using the bounded current-scene/model-context projection and exact on-demand
  retrieval from the complete accepted Plan. Do not inject the entire Plan every turn and do not
  invent a second canonical Run Brief.
- **Audit:** identify the capacity source, effective limit, request bytes/hash, token precision,
  input count/bound, output reserve, and admission result.

## 4. Smallest implementation sequence

- [x] **1. Replace the ambiguous model-capacity record.**

  Add the distinct capacity facts and provenance to the provider/model value object and owner-local
  configuration projection. Keep the change focused; do not introduce a provider registry service
  or generic telemetry subsystem.

- [x] **2. Add native local-runtime discovery where it materially improves truth.**

  Implement LM Studio loaded-instance discovery first, then Ollama runtime distinction. Reuse the
  existing bounded provider-discovery command and do not scan the LAN. Standard-compatible servers
  continue through the generic path.

- [x] **3. Remove UI fallbacks and expose the resolved facts.**

  Replace the single defaulted field with the declared/runtime/configured/effective projection and
  separate output cap. Preserve manual model IDs and saved-provider behavior.

- [x] **4. Introduce token-accounting precision explicitly.**

  Replace the current implicit method string with a typed result carrying precision, source,
  request layer, count/bound, reserve, and admission decision. Keep the UTF-8 bound as the initial
  portable fallback unless a more useful equally safe bound is proven.

- [x] **5. Connect effective capacity to every live provider-call path.**

  Builder first inference, tool continuations, first GM inference, player GM turns, and narration
  Retry must all use the same capacity resolver and Audit vocabulary. Do not create path-specific
  fallbacks.

- [x] **6. Migrate old saved values honestly.**

  Existing records without provenance, including historical `32768` values, become
  `legacy_unverified`. Do not silently erase a value that a user may have entered deliberately, and
  do not call it detected until re-probed or confirmed.

- [x] **7. Verify the changed boundaries proportionately.**

  Complete the automated and installed-host evidence below before marking this plan implemented.

## 5. Authentic acceptance tests

### 5.1 Focused automated tests

The owning tests must prove:

1. absent `/v1/models` context metadata results in **Unknown**, never `32768`;
2. a model maximum of 131K with a loaded runtime of 8K displays both and admits against 8K;
3. a configured cap below the observed runtime becomes the effective capacity;
4. a configured value above the observed runtime produces a visible mismatch and cannot enlarge the
   effective capacity;
5. reconnect/re-probe updates a changed loaded-instance context;
6. a generic OpenAI-compatible endpoint remains usable through explicit configuration when native
   discovery is unavailable;
7. output reserve is separate from total context;
8. exact counts, conservative bounds, estimates, and unavailable values are never relabelled;
9. rejected Campaign Creation inference preserves the complete Draft and Transcript;
10. successful provider usage is captured as authoritative only for the completed request; and
11. every actual Builder/GM provider path uses the same resolver rather than an old fallback.

### 5.2 Live host test

After implementation, build and normally install one exact Windows candidate on Wan's host—not the
VM—and test against an already-running configured local provider. The proof sequence must capture:

1. Models settings before discovery, showing no invented 32K value;
2. selected local provider and discovered model identity with private endpoint/credentials omitted;
3. declared maximum, loaded runtime, configured cap, effective capacity, and output limit;
4. a deliberate lower configured cap and its effective result;
5. a deliberate configured/runtime mismatch and its warning;
6. re-probe after changing the loaded runtime context;
7. a Campaign Creation request that is rejected before send with the Draft preserved;
8. the same request succeeding after selecting or loading sufficient context;
9. Admin Full Audit showing request bytes, count precision, reserve, effective capacity, and provider
   usage without claiming an estimate is exact; and
10. close/reopen proving the saved configuration and provenance restore correctly.

Screenshots must show every player-visible step and be posted in the active task. Machine-private
headers, credentials, and unrelated campaign data must not appear in repository evidence.

### 5.3 Current verification record

The exact normally installed Windows candidate exercised on Wan's host is:

- product version: `0.3.0-alpha.1`;
- installer: `src-tauri/target/release/bundle/nsis/Sylvia GM_0.3.0-alpha.1_x64-setup.exe`,
  SHA-256 `198B0D9DD871A0C0AD7112344653027B69CF1317CA31033A7FF490594C10BEC2`;
- installed GUI: `%USERPROFILE%/AppData/Local/Sylvia GM/sylvia-gm.exe`,
  SHA-256 `CEE09313A60A7644FB2DDBF62A2648826D627B55FCDA48752F0F6F0DFF3F4088`; and
- installed owner: `%USERPROFILE%/AppData/Local/Sylvia GM/sylvia-gm-owner.exe`,
  SHA-256 `32788172553668B4EB69F5D9870762AA41A166FF5A8DAB132BA6A9B606A92375`.

Focused verification on 2026-08-20 passed:

- `153` focused Python tests covering capacity resolution, Audit, context integrity, live provider
  assembly, the canonical frontend, and owner transport;
- `node --check src-tauri/frontend/app.js`;
- `cargo check` for `src-tauri`;
- `bash scripts/check-docs-index.sh`; and
- `git diff --check`.

The installed host proof is stored outside repository evidence at
`tmp/context-capacity-host-proof-2026-08-20/`. It covers Unknown-before-discovery, an 8K loaded
runtime against a 131K model maximum, a voluntary 4K cap, a cap-above-runtime warning, re-probe to
65,536, every visible New Campaign step, rejection before send with the Ready Draft preserved, the
same campaign starting after the capacity increase, a real GM opening/retry result, and restored
65,536-token capacity/provenance after closing and reopening the installed app. Admin Full Audit was
then explicitly enabled for one controlled Builder exchange. Its captured records prove:

- admitted effective capacity: `65,536` tokens from `runtime_reported`;
- exact provider-client request representation: `8,420` UTF-8 bytes with SHA-256;
- preflight precision: `conservative_bound`, with an `8,420` input upper bound, `4,096` output
  reserve, and `12,516` total required upper bound; and
- completed-request provider usage: `1,711` input and `6` output tokens, distinctly labelled
  `authoritative_count`, `provider_reported_completed_request`, and `completed_request_only`.

The matching screenshots are
`21a-final-admin-audit-capacity-measurement.png`,
`21b-final-admin-audit-effective-capacity.png`, and
`21c-final-admin-audit-provider-usage.png` in the same host-proof directory.

## 6. Completion boundary

This correction is complete only when all of the following are true:

- no live path silently substitutes 32K or another guessed context value;
- model maximum, runtime allocation, configured cap, effective capacity, and output reserve are
  distinct in storage and UI;
- LM Studio reports the loaded runtime context when its native interface supplies it;
- generic and other local providers have an honest explicit-configuration path;
- preflight and Audit identify exact counts, conservative bounds, estimates, and unavailable data
  correctly;
- Campaign Creation still never compacts required history;
- all affected provider-call paths share one resolver;
- focused tests pass;
- the normally installed host candidate completes the screenshot sequence; and
- Wan accepts the exact installed result.

No passing unit-test count, provider mock, model catalog value, or Hermes comparison substitutes for
the installed host proof.
