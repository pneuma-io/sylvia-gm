# ChatGPT adapter lifecycle timing

The existing provider request hooks receive one `sylvia/provider_lifecycle` record per
response after its owned runtime is closed. Full Audit associates this metadata with
the same application exchange. The record contains no prompts, history, tool arguments,
account content, process identifiers, paths or credentials.

All interval endpoints are integer nanoseconds relative to one `perf_counter_ns` origin
in the owner process. `started_utc` is a separate approximate wall-clock marker; it is
not used to calculate durations. The origin follows acquisition of the provider lock.
Receipt emission is excluded from `elapsed_ns`.

`runtime_start` includes scheduling and the nested `process_boot` and `initialize`
observations. Boot covers runtime resolution, isolated environment/workspace preparation,
process creation and reader startup. Initialize covers its RPC and initialized message.
An injected connection without the observer interface reports only `runtime_start` and
marks detail unavailable. The observer cannot execute an owner tool.

The other spans cover account/read, thread/start, history injection, turn/start, event
streaming and shutdown. RPC durations include local scheduling, serialization and any
remote work behind that RPC. Account/read timing does not prove a network token refresh.
Turn streaming includes event handling, existing audit hooks and stream-consumer waits;
it is not pure model service time. Internal queue, prefill, reasoning, token generation
and cache behavior remain unknown unless separately reported by the runtime.

`covered_ns` is the union of all intervals. Nested boot/initialize spans must not be
added to their runtime-start parent. `unclassified_ns` retains preparation, audit hooks,
notification draining and other time outside measured intervals. Shutdown can have
multiple spans because the existing adapter closes before yielding a tool, after a
response and in its final cleanup. These calls remain unchanged and are timed separately.

Records are bounded to 24 fixed-name spans. Completed, error and cancelled outcomes retain
the same cancellation, owner-only tool execution, ephemeral-thread and profile controls.
This instrumentation changes no runtime lifecycle policy or model/reasoning setting.
Closing a stream after receiving its terminal `finish_reason` preserves completed status;
closing or cancelling before that terminal output records cancelled and still closes the child.

Inference cancellation signals that connection to reject later admission checks and wakes
its response queue. Startup or an RPC already past a check can still begin after the signal;
this is not an atomic no-start guarantee. The required behavior is to withhold stopped output
and collect the retained child before close returns. Cleanup survives repeated cancellation.
The next response still constructs a fresh connection. Ordinary account connection `close()`
does not set the stop signal and retains its existing restart behavior. Intentional stop uses
a typed internal exception translated to public cancellation and cancelled lifecycle status;
an actual connection failure retains its error classification.

The subsequent account-read optimization uses `refreshToken: false`, matching Settings
account status. Account type is still checked on every response and missing/non-ChatGPT
accounts are rejected before thread creation. This avoids explicitly requesting a
proactive refresh on every tool round; it does not disable the runtime's authentication
handling, cache account results, change credentials or reuse a thread. The
[upstream parameter contract](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/typescript/v2/GetAccountParams.ts)
documents the proactive-refresh flag. Timing that RPC alone does not establish which
internal refresh or network operations actually occurred.

Offline fixtures cover account revocation between responses, an account RPC rejection and
a turn reporting an authentication failure after partial output. They verify safe failure,
redaction, unchanged input history and explicit retry on a fresh connection. They do not
prove automatic refresh of a real expired token or real revocation recovery; the runtime
owns that behavior, and no real credentials are changed by these tests.

SylviaBench can map these records to request/event spans by converting nanoseconds to
milliseconds, retaining the runtime-start parent and observed clock domain. An adapter
must leave model compute/queue splits absent. The Bench scaffold is maintained separately;
this change does not edit or embed it.
