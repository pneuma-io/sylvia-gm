# Official hosted provider specifications

Checked 16 October 2026. Research covers all **18 hosted presets** in IMPLEMENTED-PRESETS.json, within the existing 25-preset roster and five implemented transports. Local servers, custom endpoint server policy, and the ChatGPT subscription runtime are assigned separately. This note compares the preserved Build2082 source with current public documentation; it does not review a later repaired candidate.

The provider verification scope permits documented conformance plus meaningful synthetic contracts for providers without live subscriptions. Live provider behaviour remains **untested**. No inference, key, credential, account, installation, model download, subscription or legal-agreement operation was performed. the separate final native-input check is unchanged.

The machine-readable companion is [OFFICIAL-HOSTED-PROVIDER-CLAIMS.json](OFFICIAL-HOSTED-PROVIDER-CLAIMS.json). It records 65 primary source pages, 49 claims, provider prerequisites, documentation gaps and baseline comparison findings. All source access dates are 2026-10-16; that is an access date, not a claim that every page was published that day.

## Selected routes

C = Chat Completions; R = Responses; A = Anthropic Messages; G = native Gemini generateContent. OpenCode uses model-specific routes, so its saved discovery base is intentionally shown separately from inference.

| Existing preset | Selected protocol | Documented base / discovery base | Authentication |
|---|---|---|---|
| OpenAI API | responses | `https://api.openai.com/v1` | Bearer API key; application/json |
| OpenCode Go | model-specific C/R/A/G | `discovery: https://opencode.ai/inference/go/v1` | Bearer OpenCode service key; application/json |
| OpenCode Zen / Console | model-specific C/R/A/G | `discovery: https://opencode.ai/inference/v1` | Bearer OpenCode service key; application/json |
| OpenRouter | chat_completions | `https://openrouter.ai/api/v1` | Bearer API key; application/json |
| Featherless | chat_completions | `https://api.featherless.ai/v1` | Bearer API key; application/json |
| Groq | chat_completions | `https://api.groq.com/openai/v1` | Bearer API key; application/json |
| Together AI | chat_completions | `https://api.together.ai/v1` | Bearer API key; application/json |
| DeepSeek ordinary API | chat_completions | `https://api.deepseek.com` | Bearer DeepSeek API key; application/json |
| Anthropic / Claude API | anthropic_messages | `https://api.anthropic.com/v1` | Bearer or supported x-api-key fallback; anthropic-version; application/json |
| Google Gemini | chat_completions; native G through OpenCode | `https://generativelanguage.googleapis.com/v1beta/openai` | Bearer Gemini API key for compatibility; gateway Bearer for OpenCode native |
| xAI | responses | `https://api.x.ai/v1` | Bearer xAI API key; application/json |
| Mistral | chat_completions | `https://api.mistral.ai/v1` | Bearer API key; application/json |
| MiniMax Anthropic compatibility | anthropic_messages | `https://api.minimax.io/anthropic/v1` | Anthropic SDK API key; documented direct example also uses Bearer; application/json |
| Kimi / Moonshot ordinary API | chat_completions | `https://api.moonshot.ai/v1` | Bearer platform API key; application/json |
| Z.AI ordinary API | chat_completions | `https://api.z.ai/api/paas/v4` | Bearer platform API key; application/json |
| Alibaba Model Studio regional compatibility | chat_completions | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1 (Singapore default)` | Bearer regional/workspace key; application/json |
| Fireworks AI | chat_completions | `https://api.fireworks.ai/inference/v1` | Bearer API key; application/json |
| Cerebras | chat_completions | `https://api.cerebras.ai/v1` | Bearer API key; application/json |

## Shared wire contracts

### C: chat_completions

Request model/messages and nested tools[].function{name,parameters}; assistant tool_calls contain IDs and JSON argument strings; role:tool results reference tool_call_id. Stream choices[].delta may contain empty content, multiple indexed tool fragments, usage-only frames and a final marker. Preserve errors and distinguish absent usage from measured zero. [OpenRouter API reference](https://openrouter.ai/docs/api_reference/overview), [Kimi streaming output](https://platform.kimi.ai/docs/guide/utilize-the-streaming-output-feature-of-kimi-api)

### R: responses

Use input items and flat function tool definitions. Return function_call_output linked by call_id. Parse typed output items to preserve content beyond output_text. SSE has text/argument deltas, output_item.done, completed, failed, incomplete and error. Preserve final native reasoning items; an in-progress encrypted item may be incomplete. [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling), [OpenAI Responses streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events)

### A: anthropic_messages

Use top-level system, user/assistant content arrays, max_tokens, native tool input_schema and linked tool_use/tool_result blocks. Reassemble indexed JSON/thinking/signature deltas and require message_stop. Preserve entire assistant blocks unchanged for continuation. [Claude streaming Messages](https://platform.claude.com/docs/en/build-with-claude/streaming), [Claude thinking in tool workflows](https://platform.claude.com/docs/en/build-with-claude/thinking-tool-workflows), [OpenCode inference API](https://opencode.ai/v2/docs/console/api/inference/)

### G: gemini_generate_content

Use model/method in the path and native contents/parts JSON. Tools use functionDeclarations, object args and functionResponse. Preserve Part metadata and opaque thoughtSignature values. Read native candidates, finishReason and usageMetadata; do not impose Chat delta envelopes. [Gemini generateContent reference](https://ai.google.dev/api/generate-content)

Responses function selection uses `auto` by default, `required` for one or more calls, `none`, or a flat named function object. Preserve intentional `strict:false`. Direct OpenAI now tries schema normalization when strict is omitted and falls back to best effort if normalization is impossible; omission is not an unconditional rejection of optional properties. Explicit strict schemas require every property in required and additionalProperties:false on each object. Responses structured text uses text.format, while Chat uses response_format. [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling), [OpenAI structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

The selected provider and model determine tools, structured output, sampling and reasoning support. A model-list row or compatible URL does not prove those capabilities. Account access, regional keys, loaded/gated models and deployed targets remain prerequisites; synthetic protocol checks cannot establish them.

## Hosted provider requirements

### OpenAI API

Use POST /v1/responses. Organization/project headers are conditional for multi-organization or legacy-key selection, not universal API requirements. [OpenAI API overview](https://developers.openai.com/api/reference/overview)

Replay native output items, or use previous_response_id. With store:false or ZDR, encrypted reasoning is returned by default; the legacy include value reasoning.encrypted_content remains accepted. Preserve opaque fields, ordering and identities. [OpenAI reasoning models](https://developers.openai.com/api/docs/guides/reasoning)

429 may mean temporary rate limiting, exhausted credits, or spend/usage limits. Inspect error.code. Rate errors and 503 overload require honoring Retry-After when present; retrying billing errors does not restore access. [OpenAI error codes](https://developers.openai.com/api/docs/guides/error-codes)

### OpenCode Go

OpenCode inference explicitly documents Authorization: Bearer for all API families, including Anthropic and native Gemini. The inference guide does not document x-api-key or x-goog-api-key aliases. /v1/models is a discovery path, not the documented inference base. [OpenCode inference API](https://opencode.ai/v2/docs/console/api/inference/)

Go inference uses /inference/go/openai/v1/chat/completions or /responses, /inference/go/anthropic/v1/messages, and /inference/go/google/v1beta/models/<model>:generateContent (streamGenerateContent for streaming). The current Go table includes a Gemini contributor model on the native Google family; MiniMax and Qwen use Messages. [OpenCode Go](https://opencode.ai/v2/docs/console/go/)

Documentation limits: No gateway-specific strict omission, encrypted reasoning include default, cancellation guarantee, or full retry/error policy is stated in these public endpoint pages. Apply the documented family shape and keep those gateway details qualified.

### OpenCode Zen / Console

Console inference uses /inference/openai/v1/chat/completions or /responses, /inference/anthropic/v1/messages, and /inference/google/v1beta/models/<model>:generateContent. Its table routes Gemini to Google, GPT/Grok/Muse Spark to Responses, Claude and most Qwen to Messages, Qwen3.8 Max and the listed open models to Chat Completions. Use exact exceptions before prefixes. [OpenCode Console model endpoints](https://opencode.ai/v2/docs/console/models/)

See OC01 for gateway authentication. The existing Sylvia preset name is retained; the setup URL now redirects to Console documentation.

Documentation limits: The three Sylvia-specific x-opencode metadata headers are implementation evidence. The inspected official pages do not establish whether they are required, optional, or sufficient for all models. No live claim is made.

### OpenRouter

POST /api/v1/chat/completions normalizes message/delta choices. HTTP-Referer and X-OpenRouter-Title are optional attribution headers; their absence is not a protocol failure. [OpenRouter API reference](https://openrouter.ai/docs/api_reference/overview)

Preserve complete, ordered reasoning_details for encrypted/summary blocks. Plain reasoning can be replayed as reasoning; reasoning_content is a documented alias here. [OpenRouter reasoning tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)

Ignore SSE comments. A stream may fail after HTTP 200 through a top-level error and finish_reason:error, including as its only event. The final usage frame repeats the terminal finish_reason with a content-free choice; it is not a second generation. [OpenRouter streaming](https://openrouter.ai/docs/api_reference/streaming)

### Featherless

The documented OpenAI client and direct HTTP route use /v1/chat/completions. HTTP-Referer and X-Title are requested application attribution and explicitly shown as optional, not authentication. [Featherless quickstart](https://featherless.ai/docs/quickstart-guide), [Featherless completions](https://featherless.ai/docs/completions)

Tool syntax is standard nested OpenAI function definitions, JSON argument strings and linked tool result IDs. The tool guide lists native Kimi-K2-Instruct and Qwen 3 family support; catalogue presence alone does not establish tool capability. [Featherless tool calling](https://featherless.ai/docs/tool-calling)

A 400 can mean a cold/loading model; 403 can mean gated-model access rather than a malformed key. 503 can mean model capacity exhaustion; the error guide recommends retrying the same request and reconsidering availability if it persists beyond three retries. [Featherless error codes](https://featherless.ai/docs/api-reference-error-codes)

Documentation limits: The inspected chat reference does not fully specify streaming tool-event or opaque reasoning/signature variants. Use shared Chat fixtures, with capability-dependent reasoning left unknown unless separately documented.

### Groq

Use the documented OpenAI-compatible base and Chat Completions. Groq rejects logprobs, logit_bias, top_logprobs and messages[].name, and only supports n=1. Newer Responses support does not change Sylvia's selected Chat route. [Groq OpenAI compatibility](https://console.groq.com/docs/openai)

Tool support is model-specific. Parsed reasoning uses message.reasoning; raw mode uses think tags and cannot be explicitly combined with tools or JSON mode. include_reasoning and reasoning_format are mutually exclusive. GPT-OSS effort supports low/medium/high; other families have their own values. [Groq tool use](https://console.groq.com/docs/tool-use/overview), [Groq reasoning](https://console.groq.com/docs/reasoning)

HTTP error bodies contain error.message/type. 429 is rate limiting; 500/502/503 can warrant delayed retry. The current error table also distinguishes 413 payload, 422 semantic/tool errors, and 498 Flex capacity. [Groq error codes](https://console.groq.com/docs/errors)

### Together AI

Use /v1/chat/completions and exact provider/model IDs. Errors use an OpenAI-shaped envelope with Together-owned type/code values. Cached token usage can be nested in prompt_tokens_details or flat in usage.cached_tokens. [Together OpenAI compatibility](https://docs.together.ai/docs/inference/openai-compatibility)

Chat streaming returns incremental deltas; tool patterns include multiple, parallel and sequential calls. Tool eligibility comes from the selected serverless/dedicated model, not the provider name. [Together chat completions](https://docs.together.ai/docs/inference/chat/overview), [Together function calling](https://docs.together.ai/docs/inference/function-calling/overview)

Reasoning may be in reasoning or reasoning_content; replay it under the same key. Preserved-thinking blocks must retain the original sequence. Hybrid controls and allowed effort values vary; DeepSeek V3.1 tools require non-reasoning mode. [Together reasoning](https://docs.together.ai/docs/inference/chat/reasoning)

### DeepSeek ordinary API

Official SDK examples use the root base URL, Chat Completions, streaming reasoning_content/content, nested function tools and role:tool results linked by tool_call_id. Thinking defaults on in the current guide. With tools present, replay reasoning_content from every earlier assistant turn, including turns without tool calls, or the API can return 400. [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/)

Thinking toggles are provider extensions passed through extra_body with the OpenAI SDK; an accepted sampling parameter is not proof it has an effect.

Documentation limits: Public reads of the quickstart/error-code pages timed out or were inaccessible. Their fresh error taxonomy and retry details remain a documentation gap in this note; no remembered legacy table is promoted to current evidence.

### Anthropic / Claude API

POST /v1/messages is the native API. anthropic-version and content-type are required. Current docs prefer Bearer credentials and explicitly retain x-api-key fallback; anthropic-workspace-id is required for multi-workspace keys and optional for ordinary single-workspace keys. [Claude API overview](https://platform.claude.com/docs/en/api/overview)

The stream consists of message_start, indexed content block start/delta/stop, message_delta and message_stop, with possible ping/error events. Tool input_json_delta is partial JSON. Thinking signatures arrive as signature_delta. Echo complete assistant content unchanged before the user tool_result. [Claude streaming Messages](https://platform.claude.com/docs/en/build-with-claude/streaming), [Claude thinking in tool workflows](https://platform.claude.com/docs/en/build-with-claude/thinking-tool-workflows)

Manual thinking requires budget_tokens >=1024 and normally less than max_tokens; newer models require adaptive thinking instead. Error handling distinguishes 429 rate/spend limits, 500, 504 and 529 overload; SSE errors can follow HTTP 200. [Claude extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking), [Claude API errors](https://platform.claude.com/docs/en/api/errors)

### Google Gemini

The direct preset correctly selects v1beta/openai/chat/completions, Bearer authentication, standard tools and streaming deltas. Google-specific thinking configuration lives in its documented extra_body.google extension; compatibility remains beta. [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)

Native generateContent uses user/model contents[].parts, systemInstruction, functionDeclarations, functionCall object arguments and functionResponse objects. If a functionCall supplies an id, return the matching id. Part.thoughtSignature is an opaque base64 value for reuse. streamGenerateContent uses native candidate/parts responses, not Chat choices. [Gemini generateContent reference](https://ai.google.dev/api/generate-content)

OpenCode native authentication is governed by OC01. The newer Gemini Thinking guide primarily documents Interactions; that newer API is outside the selected generateContent route.

Documentation limits: Model-specific signature strictness and compatibility extra_content variants must follow the selected model's current documentation. The native API also documents CONTINUATION/continuationToken; Sylvia's present parser only accepts STOP/MAX_TOKENS, so it must fail clearly if that distinct finish state occurs.

### xAI

The current quickstart uses /v1/responses. Responses uses typed output items and function_call_output with call_id; stateful tool examples use previous_response_id. store defaults true; include requests encrypted reasoning. Direct OpenAI's automatic encrypted include guarantee is not stated for xAI. [xAI API quickstart](https://docs.x.ai/developers/quickstart), [xAI Responses comparison](https://docs.x.ai/developers/model-capabilities/text/comparison), [xAI function calling](https://docs.x.ai/developers/tools/function-calling)

The official Responses example sends text.format with type:json_schema, name, schema and strict:true. The Chat response_format envelope must be translated for this selected transport. Exact strict-omission fallback is not documented as identical to OpenAI. [xAI structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs)

The error guide separates authentication/permission, not-found, invalid body/media and 429 rate limiting. Do not retry invalid input as a connection failure. [xAI debugging errors](https://docs.x.ai/developers/debugging)

### Mistral

POST /v1/chat/completions accepts model/messages/tools; streaming is data-only SSE ending in [DONE]. Preserve tool-call identity across assistant/tool turns; model support still governs available features. [Mistral Chat API](https://docs.mistral.ai/api/endpoint/chat)

When enabled, current reasoning returns content as ThinkChunk/TextChunk lists. Streaming can produce a thinking list, a mixed thinking/text list, then a plain string. Replay the complete assistant message including thinking chunks. [Mistral reasoning](https://docs.mistral.ai/studio/conversations/reasoning)

Documentation limits: The attempted standalone error-handling URL was inaccessible. The Chat reference does not establish a fresh provider-specific retry policy; do not label a generic policy as an exact Mistral requirement.

### MiniMax Anthropic compatibility

Official SDK setup uses ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic with ANTHROPIC_API_KEY, so removing the saved /v1 before SDK construction is correct. It requires complete response.content replay, including thinking/text/tool_use. max_tokens includes thinking; undersized caps can end before text. [MiniMax Anthropic SDK compatibility](https://platform.minimax.io/docs/api-reference/text-anthropic-api)

The compatibility table restricts this interface to the listed MiniMax text models. M3 supports disabled/adaptive thinking; M2.x cannot actually disable thinking. Do not apply M3 controls to every MiniMax model.

MiniMax's general error table separates retry-later conditions such as 1000/1001/1002/1024 from invalid credentials 1004, insufficient balance 1008 and invalid parameters 2013. [MiniMax error codes](https://platform.minimax.io/docs/api-reference/errorcode)

Documentation limits: The general MiniMax error table does not fully map business codes to HTTP status or Anthropic SSE error payloads. Treat that mapping as unknown. New model availability under a restricted plan is a prerequisite, not a request to obtain access.

### Kimi / Moonshot ordinary API

The ordinary platform documents /v1/chat/completions and standard tool-call/result loops. Its availability of additional API families does not require expanding this preset or switching to Kimi Code. [Kimi quickstart](https://platform.kimi.ai/docs/overview), [Kimi tool calls](https://platform.kimi.ai/docs/guide/use-kimi-api-to-complete-tool-calls)

Kimi K3 and K2.7 Code always preserve thinking; replay every historical assistant message including reasoning_content unchanged. K2.6 preservation is optional. Effort/toggle fields differ by model; K3 uses top-level reasoning_effort and no thinking parameter. [Kimi thinking models](https://platform.kimi.ai/docs/guide/use-thinking-models)

The official streaming guide explicitly requires data:[DONE]; finish_reason:stop alone is insufficient. Tool fragments accumulate by index. include_usage sends a final empty-choices accounting frame; an interrupted stream can lack final usage. [Kimi streaming output](https://platform.kimi.ai/docs/guide/utilize-the-streaming-output-feature-of-kimi-api)

### Z.AI ordinary API

The standard endpoint is /api/paas/v4/chat/completions. Coding Plan uses a separate endpoint. Accept-Language configures response language and has a default; it is not an additional credential. [Z.AI quickstart](https://docs.z.ai/guides/overview/quick-start), [Z.AI chat completion](https://docs.z.ai/api-reference/llm/chat-completion)

Preserved Thinking is enabled using thinking.clear_thinking:false in extra_body. Replay the complete unmodified reasoning_content sequence, including prior assistant tool turns. [Z.AI thinking mode](https://docs.z.ai/guides/capabilities/thinking-mode)

Interpret both HTTP status and business error.code. 429 includes rate overload (1302/1305) but also exhausted balance (1113) and plan/access problems; retrying every 429 as temporary throttling misstates the cause. [Z.AI errors](https://docs.z.ai/api-reference/api-code)

### Alibaba Model Studio regional compatibility

The Singapore shared base in Sylvia is documented. Beijing, Virginia, Hong Kong and workspace-specific domains have distinct bases; the key must match the selected region/workspace. Keep endpoint editability. Token/Coding Plan endpoints are separate services. [Alibaba Model Studio base URLs](https://www.alibabacloud.com/help/en/model-studio/base-url)

OpenAI-compatible HTTP requests use Chat Completions messages and standard error envelopes. SSE carries choices/delta, terminal finish_reason and [DONE]. Streaming tool arguments are fragments and the name arrives in the first tool fragment; thinking/tool constraints depend on model. [Alibaba OpenAI compatibility](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope), [Alibaba function calling](https://www.alibabacloud.com/help/en/model-studio/qwen-function-calling)

Documentation limits: The compatibility page's Beijing example currently repeats a Singapore workspace hostname. Prefer the dedicated base-URL table for regional authority; do not copy that contradictory example. A fresh complete retry policy was not established from these selected pages.

### Fireworks AI

Use exact full Fireworks model/deployment identifiers. Streaming usage is included on the terminal finish_reason frame. Default context-overflow behaviour lowers max_tokens to fit; context_length_exceeded_behavior:error requests an error instead. [Fireworks OpenAI compatibility](https://docs.fireworks.ai/tools-sdks/openai-compatibility)

Streaming tool calls require accumulating all indexed argument fragments. Reasoning uses reasoning_content, with model-specific allowed effort values; GPT-OSS rejects none and integer effort, while MiniMax M3 has its own adaptive option. [Fireworks tool calling](https://docs.fireworks.ai/guides/function-calling), [Fireworks Chat Completion API](https://docs.fireworks.ai/api-reference/post-chatcompletions)

Fireworks recommends backoff for 429/500/502/503/504 and fixing 400/401/404/422. Error-code documentation additionally distinguishes 402 payment, 408 timeout and 520 unknown errors. Dedicated 429 means deployment capacity; serverless 429 means rate/TPM limits. [Fireworks reliability](https://docs.fireworks.ai/guides/reliability), [Fireworks inference errors](https://docs.fireworks.ai/guides/inference-error-codes)

Documentation limits: Two official pages differ on 500 guidance: reliability treats it as transient, while the error table describes a likely engineering fault. A bounded retry followed by a clear error preserves that uncertainty.

### Cerebras

Use exact shared model IDs or a dedicated endpoint ID in model. Standard stream:true is supported; do not send tool_stream. n=1 only. Tools plus response_format is model-dependent and rejected by GPT-OSS-120B. [Cerebras OpenAI compatibility](https://inference-docs.cerebras.ai/resources/openai), [Cerebras Chat Completions](https://inference-docs.cerebras.ai/api-reference/chat-completions)

Function tools support linked results, parallel calls and strict schemas on documented models. Strictness has model-specific schema limits; Qwen rejects pattern/minLength/maxLength in strict tool schemas. Streaming emits Chat delta content. [Cerebras tool calling](https://inference-docs.cerebras.ai/capabilities/tool-use), [Cerebras streaming](https://inference-docs.cerebras.ai/capabilities/streaming)

The SDK documents retrying connection failures, 408, 429 and >=500 with bounded exponential backoff. This is SDK policy, not a requirement to reproduce exactly two retries. Auth/permission/invalid-body errors remain distinct. [Cerebras errors](https://inference-docs.cerebras.ai/support/error)

Documentation limits: Reasoning formats and clear_thinking depend on model. A customer-trial model being documented does not mean it is generally available to the user's account.

## Native continuation is provider-specific

Returning a scalar reasoning field is not evidence that a service accepts that field in assistant history. Keep capture separate from replay. Do not forward arbitrary provider extensions to every compatible host.

| Provider / model scope | Documented replay contract |
|---|---|
| deepseek | All earlier assistant turns when tools present; no-tool accepted but ignored. [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/) |
| moonshot | K3/K2.7 Code all historical assistant turns, even without tools; K2.6 keep:'all' optional. [Kimi thinking models](https://platform.kimi.ai/docs/guide/use-thinking-models) |
| openrouter | Ordered full reasoning_details for opaque state; scalar for plain reasoning. [OpenRouter reasoning tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) |
| together | Same key as output; preserve original sequence for preserved thinking/tool continuation. [Together reasoning](https://docs.together.ai/docs/inference/chat/reasoning) |
| zai | Exact sequence under thinking.clear_thinking:false. [Z.AI thinking mode](https://docs.z.ai/guides/capabilities/thinking-mode) |
| mistral | Replay complete assistant content including ThinkChunk. [Mistral reasoning](https://docs.mistral.ai/studio/conversations/reasoning) |
| fireworks | Input allowed; reasoning_history controls disabled/interleaved/preserved and model defaults. [Fireworks Chat Completion API](https://docs.fireworks.ai/api-reference/post-chatcompletions) |
| cerebras | qwen-3.8-27b clear_thinking false/omitted preserves supplied history; do not invent aliases. [Cerebras Chat Completions](https://inference-docs.cerebras.ai/api-reference/chat-completions) |
| alibaba | Only documented preserve_thinking-capable model modes; absence allowed. [Alibaba deep thinking](https://www.alibabacloud.com/help/en/model-studio/deep-thinking) |
| groq, featherless | General input replay not established by inspected docs; retain privately for inspection without globally forwarding. [Groq reasoning](https://console.groq.com/docs/reasoning), [Groq API reference](https://console.groq.com/docs/api-reference), [Featherless completions](https://featherless.ai/docs/completions) |
| opencode_go, opencode_zen | Documented protocol family; do not infer all upstream scalar aliases work on gateway. [OpenCode inference API](https://opencode.ai/v2/docs/console/api/inference/) |
| openai, xai | Native output replay or previous_response_id, provider-specific encrypted include defaults. [OpenAI reasoning models](https://developers.openai.com/api/docs/guides/reasoning), [xAI Responses comparison](https://docs.x.ai/developers/model-capabilities/text/comparison) |

## Baseline comparison findings and useful contracts

These are source/documentation comparisons, not live reproductions or final acceptance decisions. Line references below are relative to the frozen root the retained private integration source. The implementation owner must reconcile them with the repaired candidate and actual SDK request/response fixtures.

### H01: OpenCode inference path mismatch

Inference clients use discovery bases unchanged; the documented per-family path segments are missing. Existing aliases may work, but current docs do not establish them. [OpenCode inference API](https://opencode.ai/v2/docs/console/api/inference/), [OpenCode Go](https://opencode.ai/v2/docs/console/go/), [OpenCode Console model endpoints](https://opencode.ai/v2/docs/console/models/)

Source: `src/sylvia_gm/config/provider_settings.py:130`, `src/sylvia_gm/provider/_openai_compat.py:706`, `src/sylvia_gm/provider/_anthropic.py:525`, `src/sylvia_gm/provider/_gemini.py:160`. Contract: Assert exact URL per preset and protocol while retaining discovery /models bases.

### H02: OpenCode gateway authentication mismatch

Messages rotates SDK api_key and native Gemini sends x-goog-api-key. The gateway's explicit requirement is Bearer; alternate header acceptance is undocumented. [OpenCode inference API](https://opencode.ai/v2/docs/console/api/inference/)

Source: `src/sylvia_gm/provider/_anthropic.py:536`, `src/sylvia_gm/provider/_gemini.py:155`. Contract: Capture exact headers at actual HTTP boundary for all gateway families and retained session metadata.

### H03: Go native Gemini routing gap

Go lacks a Gemini family rule although the official current table lists a Google-native Gemini contributor route. Discovery-supplied protocol metadata can override this, so impact depends on metadata. [OpenCode Go](https://opencode.ai/v2/docs/console/go/)

Source: `src/sylvia_gm/provider/_protocol_routes.py:25`. Contract: Check sparse/manual model setup plus explicit provider protocol metadata without guessing upstream URLs.

### H04: Responses native continuation lost

The adapter rebuilds text/functions and does not retain complete output items or a previous-response chain, losing opaque reasoning and provider fields. [OpenAI reasoning models](https://developers.openai.com/api/docs/guides/reasoning), [xAI Responses comparison](https://docs.x.ai/developers/model-capabilities/text/comparison)

Source: `src/sylvia_gm/provider/_openai_compat.py:383`, `src/sylvia_gm/provider/_openai_compat.py:475`, `src/sylvia_gm/agent/_provider_session.py:5207`. Contract: Round-trip completed reasoning+function items, preserve ordering/identities, and reject required missing metadata.

### H05: Responses terminal failures normalized as length

response.failed and all incomplete reasons become length; explicit error events are ignored and EOF is accepted. Server failures are distinct from max_output_tokens. [OpenAI Responses streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events)

Source: `src/sylvia_gm/provider/_openai_compat.py:563`, `src/sylvia_gm/provider/_openai_compat.py:995`. Contract: Fixture failed/server_error, incomplete/max_output_tokens, refusal/filter reason, explicit error, and missing terminal.

### H06: Reasoning replay limited by model-name heuristic

Only DeepSeek/Kimi names with tools trigger scalar reasoning replay, and the original field key is lost. This misses documented preserved-thinking cases, including Kimi without tools and Together reasoning-key retention. [Together reasoning](https://docs.together.ai/docs/inference/chat/reasoning), [Kimi thinking models](https://platform.kimi.ai/docs/guide/use-thinking-models), [Z.AI thinking mode](https://docs.z.ai/guides/capabilities/thinking-mode), [Mistral reasoning](https://docs.mistral.ai/studio/conversations/reasoning)

Source: `src/sylvia_gm/agent/_native_continuation.py:6`, `src/sylvia_gm/agent/_provider_session.py:5195`, `src/sylvia_gm/provider/_openai_compat.py:162`. Contract: Test native reasoning key and whole-message preservation across tool and ordinary assistant turns; retain strict provider/model identity checks.

### H07: Mistral typed content unsupported

Current parsing assumes string delta content and passes nonstream lists through without separating thinking. Mistral documents ThinkChunk/TextChunk lists and mixed transition chunks. [Mistral reasoning](https://docs.mistral.ai/studio/conversations/reasoning)

Source: `src/sylvia_gm/provider/_openai_compat.py:269`, `src/sylvia_gm/provider/_openai_compat.py:1018`, `src/sylvia_gm/provider/_openai_compat.py:1092`. Contract: Feed text, thinking, mixed transition and tool parts through actual HTTP/SDK parsing and keep private reasoning out of visible narration.

### H08: Cache accounting ignores Together flat field

Only nested cached_tokens is read; Together documents usage.cached_tokens on some models. [Together OpenAI compatibility](https://docs.together.ai/docs/inference/openai-compatibility)

Source: `src/sylvia_gm/provider/_openai_compat.py:212`, `src/sylvia_gm/provider/_openai_compat.py:1138`. Contract: Exercise nested and flat cache usage in streamed and complete responses without inventing missing usage.

### H09: Streaming transport completion needs raw boundary proof

The SDK iterator's EOF currently counts as completion. Kimi requires [DONE]; OpenRouter's midstream error can be the only frame. SDK event handling may hide markers or raise errors, so prove this at HTTP/SSE level. [Kimi streaming output](https://platform.kimi.ai/docs/guide/utilize-the-streaming-output-feature-of-kimi-api), [OpenRouter streaming](https://openrouter.ai/docs/api_reference/streaming)

Source: `src/sylvia_gm/provider/_openai_compat.py:995`. Contract: Test stop-then-EOF without DONE, full DONE, final usage repetition, and error-only HTTP200.

### H10: Retry-After and permanent 429 classification

Server Retry-After is capped by max_delay and retained only for 429. Current OpenAI docs require respecting it for 503 too; billing/spend 429 differs from temporary rate limits. [OpenAI error codes](https://developers.openai.com/api/docs/guides/error-codes), [Claude API errors](https://platform.claude.com/docs/en/api/errors), [Z.AI errors](https://docs.z.ai/api-reference/api-code)

Source: `src/sylvia_gm/provider/_failover.py:40`, `src/sylvia_gm/provider/_openai_compat.py:339`. Contract: Test long numeric/HTTP-date Retry-After without real sleeping, cancellation while waiting, 503 hints and nonrecoverable billing codes.

### H11: Responses tool options need translation/preservation

Function conversion drops strict; kwargs passthrough does not translate nested Chat named tool_choice or response_format to Responses text.format. A deliberate strict:false can therefore change meaning. [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling), [OpenAI structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [xAI structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs)

Source: `src/sylvia_gm/provider/_openai_compat.py:432`, `src/sylvia_gm/provider/_openai_compat.py:468`. Contract: Fixture required/none/named choice, strict:false retention, and text.format translation. Gateway strict-default details remain undocumented.

## Evidence boundaries and unresolved details

No live integration is accepted here. Endpoint reachability, credential validity, model availability, account entitlements, latency, actual tool choice and provider billing remain unverified. This research supplies the documented contract and flags gaps; the implementation owner's meaningful synthetic HTTP/SSE and owner-session tests supply local conformance evidence.

Keep the existing good continuation boundaries: Anthropic preserves native content blocks and signatures; Chat stores opaque reasoning_details and tool signature extensions outside public history; Gemini preserves parts and native IDs. Do not discard those fields when repairing common format handling. In particular, the native Gemini parser does preserve absent finishReason as None at _gemini.py:232-234. An earlier partial read suggested a false terminal bug; the full expression withdraws that suspicion. Native truncation remains a verification case, not a confirmed baseline defect.

Streaming cancellation should close the active HTTP stream and retain an incomplete result if no terminal proof was received. Closing a client connection is not evidence that every upstream provider stopped generation or billing. OpenRouter documents provider-dependent cancellation behaviour. [OpenRouter streaming](https://openrouter.ai/docs/api_reference/streaming)

The inspected OpenCode endpoint guide does not define a complete gateway error taxonomy, strict omission default, encrypted reasoning include default, or required status for Sylvia's x-opencode-session/client metadata. Bearer authentication and the protocol-specific paths are explicit. Do not copy an upstream authentication convention over that gateway requirement.

DeepSeek quickstart/error-code reads and the attempted Mistral standalone retry URL were inaccessible; the readable protocol and reasoning sources remain useful. MiniMax's general business-code table does not fully define compatibility HTTP/SSE mappings. Alibaba's compatibility page has a conflicting Beijing workspace example, so its dedicated regional base-URL table is the better source for that choice. Fireworks has conflicting 500 guidance across two primary pages; bounded retry and a clear terminal error are defensible, while an exact uniform retry promise is not.

Synthetic cases should exercise actual serialized SDK/HTTP requests, fragmented tool arguments, required native state on replay, usage-only frames, provider-specific content blocks, long Retry-After, permanent access errors, failed/incomplete streams and cancellation before completion. Preserve distinct “unknown”, “unsupported”, “not available to this account”, and “passed synthetic contract” statuses. Run the selected GM and Builder consumers so setup cannot pass while one consumer drops continuation data. No new provider roster or all-model qualification programme is selected by this note.

