"""Export the working context's model-read texts as working_context_texts.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/src/generate_working_context_texts.py

The texts are read back from the Python module, never retyped:

- `summary_prompt`: `_SUMMARY_PROMPT`;
- `summary_instruction` and `shorten_instruction`: the two system messages `prepare` sends the
  summarizer, captured from a real compaction and turned into templates by replacing the
  numbers it put in (`{summary_limit}`, `{words}`);
- `continuation_note_prefix`: the note `project` adds before the saved summary;
- `history_tool`: `HISTORY_TOOL`;
- `application_data_guidance` and `player_message_guidance`: `_request_data`'s two constants.

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _request_data as request_data  # noqa: E402
from sylvia_gm.agent import _working_context as working  # noqa: E402
from sylvia_gm.provider._base import ModelInfo, ProviderResponse  # noqa: E402
from sylvia_gm.provider._hooks import HookManager  # noqa: E402

OUTPUT = Path(__file__).with_name("working_context_texts.json")


class Recorder:
    """A summarizer that answers with a note longer than the limit, so both requests are sent."""

    def __init__(self, model: ModelInfo, limit: int) -> None:
        self.model_info = model
        self.requests: list[list[dict[str, object]]] = []
        self._limit = limit
        self.hooks = HookManager()

    async def generate(self, messages, tools=None, **_options):  # type: ignore[no-untyped-def]
        self.requests.append(messages)
        content = "n" * (self._limit + 1) if len(self.requests) == 1 else "short note"
        return ProviderResponse(content=content, tool_calls=[], finish_reason="stop",
                                usage=None)


def templates() -> dict[str, str]:
    # A window whose summary limit (capacity // 32 = 11,111) and word counts (793, 617) occur
    # nowhere else in the texts.
    capacity = 355_552
    limit = min(12_000, max(256, capacity // 32))
    words, shorten_words = max(16, limit // 14), max(12, limit // 18)
    model = ModelInfo(id="m", name="m", provider_name="p", provider_key="p", provider_mode="api",
                      context_window=capacity,
                      max_output_tokens=4096, transport="chat_completions")
    recorder = Recorder(model, limit)
    saved: list[dict[str, object]] = []

    async def save(payload: dict[str, object]) -> None:
        saved.append(payload)

    provider = working.WorkingContextProvider(recorder, save=save)  # type: ignore[arg-type]
    messages = [{"role": "system", "content": "s"}]
    for index in range(4):
        messages += [{"role": "user", "content": f"u{index}"},
                     {"role": "assistant", "content": f"a{index}"}]
    asyncio.run(provider.prepare(messages, force=True))
    assert len(recorder.requests) == 2 and saved, "both summarizer requests were sent"
    summary = recorder.requests[0][0]["content"]
    shorten = recorder.requests[1][0]["content"]
    assert isinstance(summary, str) and isinstance(shorten, str)
    prompt = working._SUMMARY_PROMPT  # noqa: SLF001
    assert summary.startswith(prompt) and shorten.startswith(prompt)

    def template(text: str, values: dict[str, int]) -> str:
        for name, value in values.items():
            assert text.count(str(value)) == 1, (name, value)
            text = text.replace(str(value), "{" + name + "}")
        return text

    note = provider.project(messages)[1]["content"]
    assert isinstance(note, str) and note.endswith("short note")
    return {
        "summary_prompt": prompt,
        "summary_instruction": template(summary[len(prompt):],
                                        {"summary_limit": limit, "words": words}),
        "shorten_instruction": template(shorten[len(prompt):],
                                        {"summary_limit": limit, "words": shorten_words}),
        "continuation_note_prefix": note[: -len("short note")],
    }


def main() -> None:
    exported = {
        **templates(),
        "history_tool": working.HISTORY_TOOL,
        "application_data_guidance": request_data.APPLICATION_DATA_GUIDANCE,
        "player_message_guidance": request_data.PLAYER_MESSAGE_GUIDANCE,
    }
    text = json.dumps(exported, ensure_ascii=True, indent=1, allow_nan=False) + chr(10)
    assert json.loads(text) == exported
    OUTPUT.write_bytes(text.encode("utf-8"))
    print(len(exported), "values,", len(text), "bytes")


if __name__ == "__main__":
    main()
