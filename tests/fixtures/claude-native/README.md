# Claude Code session-file fixtures

Session files written by Claude Code 2.1.322 during offline research against a local fake API
(synthetic prompts and replies, no model call). Local paths were replaced with
`C:\SylviaRuntime`, the OS version with a neutral value, and Claude Code's own request
bookkeeping rows (`api-request*`) reduced to their type and session ID; nothing else was changed. `tests/test_claude_native_route.py` uses them to
check how Sylvia copies the CLI's context records.

| File | What it shows |
| --- | --- |
| `first-step.jsonl` | A first step: `environment`, `model` and `date` records follow the prompt `SENTINEL_H1_USER hello`. |
| `ordinary-step.jsonl` | A forked second step: the copied history carries the context records, and nothing rendered follows the prompt `SENTINEL_H2_USER next`. |
| `date-change.jsonl` | A transcript whose second prompt `SENTINEL_DC2 today` is followed by a `date` record with `changed: true`. |
| `cli-inserted-turn.jsonl` | The CLI inserted its own `<synthetic>` "No response requested." turn before the prompt `SENTINEL_O4_USER what now`. |
