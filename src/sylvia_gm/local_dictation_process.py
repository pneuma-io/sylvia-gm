"""Bounded local speech-worker protocol. Only an explicit Start can open an input stream."""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import suppress
from io import TextIOWrapper
from pathlib import Path
from typing import Any, TextIO, cast

from sylvia_gm.local_dictation import (
    MODEL_FILES,
    MODEL_ID,
    MODEL_REVISION,
    Recognizer,
    Recording,
    SpeechError,
    input_devices,
    model_ready,
    prepare_model,
    transcribe,
)


def capabilities(directory: Path) -> dict[str, Any]:
    try:
        import sounddevice
        from faster_whisper.tokenizer import _LANGUAGE_CODES

        devices = input_devices(sounddevice)
    except SpeechError:
        devices = []
    except Exception:
        raise SpeechError("runtime") from None
    return {
        "engines": [
            {"id": "auto", "language": "auto", "label": "Detect language automatically"},
            *({"id": code, "language": code, "label": code} for code in _LANGUAGE_CODES[:99]),
        ],
        "devices": [{"id": d["id"], "label": d["label"]} for d in devices],
        "ready": model_ready(directory),
        "model": {
            "id": MODEL_ID,
            "revision": MODEL_REVISION,
            "download_bytes": sum(size for size, _ in MODEL_FILES.values()),
            "device": "cpu",
            "compute_type": "int8",
        },
    }


def serve(source: TextIO, output: TextIO, directory: Path) -> int:
    def reply(value: dict[str, Any]) -> None:
        output.write(json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n")
        output.flush()

    recording = None
    try:
        line = source.readline(8193)
        if not line.endswith("\n") or len(line.encode("utf-8")) > 8192:
            raise SpeechError("unavailable")
        request = json.loads(line)
        mode = request.get("mode")
        if mode == "session":
            return serve_session(source, reply, directory)
        if mode == "capabilities":
            reply(capabilities(directory))
        elif mode == "prepare":
            prepare_model(directory)
            reply({"status": "ready"})
        elif mode == "start":
            if not model_ready(directory):
                raise SpeechError("model")
            language, device = request.get("engine"), request.get("device", "default")
            if not isinstance(language, str) or not isinstance(device, str):
                raise SpeechError("unavailable")
            from faster_whisper.tokenizer import _LANGUAGE_CODES

            if language != "auto" and language not in _LANGUAGE_CODES[:99]:
                raise SpeechError("language")
            recording = Recording(device)
            recording.start()
            reply({"status": "recording"})
            action = source.readline(16)
            if action == "stop\n":
                audio = recording.stop()
                try:
                    reply({"text": transcribe(audio, directory, language)})
                finally:
                    audio.close()
        else:
            raise SpeechError("unavailable")
        return 0
    except SpeechError as error:
        reply({"error": str(error)})
    except Exception:
        reply({"error": "provider"})
    finally:
        if recording is not None:
            with suppress(Exception):
                recording.cancel()
    return 1


def serve_session(source: TextIO, reply: Any, directory: Path) -> int:
    """Serialize bounded, identified attempts; keep only the model while the microphone is idle."""
    recognizer = Recognizer(directory)
    recording = None
    active_id = None
    language = "auto"
    reply({"status": "ready", "protocol": 1})
    try:
        while line := source.readline(8193):
            if not line.endswith("\n") or len(line.encode("utf-8")) > 8192:
                return 1
            request = json.loads(line)
            identity = request.get("id")
            if not isinstance(identity, str) or not 0 < len(identity) <= 64 or not all(
                c.isascii() and (c.isalnum() or c == "-") for c in identity
            ):
                return 1
            result: dict[str, Any]
            try:
                mode = request.get("mode")
                if mode == "capabilities" and recording is None:
                    result = capabilities(directory)
                elif mode == "start" and recording is None:
                    if not model_ready(directory):
                        raise SpeechError("model")
                    selected_language = request.get("engine")
                    device = request.get("device", "default")
                    from faster_whisper.tokenizer import _LANGUAGE_CODES

                    if not isinstance(selected_language, str) or (
                        selected_language != "auto"
                        and selected_language not in _LANGUAGE_CODES[:99]
                    ):
                        raise SpeechError("language")
                    if not isinstance(device, str):
                        raise SpeechError("device")
                    candidate = Recording(device)
                    try:
                        candidate.start()
                    except Exception:
                        candidate.cancel()
                        raise
                    recording, active_id, language = candidate, identity, selected_language
                    result = {"status": "recording"}
                elif mode == "stop" and recording is not None and identity == active_id:
                    capture, recording, active_id = recording, None, None
                    audio = None
                    try:
                        audio = capture.stop()  # close the stream before model loading or decode
                        result = {"text": recognizer.transcribe(audio, language)}
                    finally:
                        capture.cancel()
                        if audio is not None:
                            audio.close()
                else:
                    raise SpeechError("unavailable")
            except SpeechError as error:
                result = {"error": str(error)}
            except Exception:
                result = {"error": "provider"}
            reply({"id": identity, **result, "idle": recording is None})
            result.clear()  # the model may stay warm; the previous transcript must not
        return 0
    finally:
        if recording is not None:
            with suppress(Exception):
                recording.cancel()


def main(args: list[str] | None = None) -> int:
    args = sys.argv[1:] if args is None else args
    if len(args) != 1:
        return 2
    # Third-party debug logs may include input text. The protocol exposes safe codes only.
    logging.disable(logging.CRITICAL)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HOME"] = str(Path(args[0]) / ".runtime")
    os.environ["HF_XET_CACHE"] = str(Path(args[0]) / ".runtime/xet")
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    cast(TextIOWrapper, sys.stdin).reconfigure(encoding="utf-8", errors="strict")
    cast(TextIOWrapper, sys.stdout).reconfigure(encoding="utf-8", errors="strict")
    return serve(sys.stdin, sys.stdout, Path(args[0]))


if __name__ == "__main__":
    raise SystemExit(main())
