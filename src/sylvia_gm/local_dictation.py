"""Local-only multilingual base recognition, verified model cache and ephemeral input.

Decoding settings and the conservative segment gate follow Hermes Agent (MIT,
Nous Research), tools/transcription_local.py at 0b10b9f8155a7ecadd8c540b87712c791b5c8ac9.
No audio or transcript is written to disk or routine diagnostics.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import sys
import threading
import urllib.request
import wave
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MODEL_ID = "Systran/faster-whisper-base"
MODEL_REVISION = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
MODEL_FILES = {
    "config.json": (2309, "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a"),
    "model.bin": (145217532, "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9"),
    "tokenizer.json": (2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
    "vocabulary.txt": (459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
}
MAX_SECONDS = 120


class SpeechError(Exception):
    """Only a safe, renderer-known error code crosses the process boundary."""


def _verified(path: Path, expected: tuple[int, str]) -> bool:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size != expected[0]:
            return False
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == expected[1]
    except OSError:
        return False


def model_ready(directory: Path) -> bool:
    return all(_verified(directory / name, expected) for name, expected in MODEL_FILES.items())


@contextmanager
def _download_lock(directory: Path) -> Iterator[None]:
    """OS-owned lock releases on cancellation/process death, so a download can be retried."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".download.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SpeechError("busy") from None
        try:
            yield
        finally:
            lock.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def prepare_model(directory: Path) -> None:
    """Download only fixed public model files, verify their identity, then publish atomically."""
    try:
        with _download_lock(directory):
            missing = [
                (name, expected)
                for name, expected in MODEL_FILES.items()
                if not _verified(directory / name, expected)
            ]
            if (
                missing
                and shutil.disk_usage(directory).free
                < sum(e[0] for _, e in missing) * 2 + 64_000_000
            ):
                raise SpeechError("storage")
            for name, expected in missing:
                partial = directory / (name + ".part")
                if partial.is_symlink():
                    raise SpeechError("storage")
                try:
                    request = urllib.request.Request(
                        f"https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{name}?download=true",
                        headers={"User-Agent": "Sylvia-GM-local-dictation"},
                    )
                    with (
                        urllib.request.urlopen(request, timeout=30) as response,
                        partial.open("wb") as output,
                    ):
                        remaining = expected[0]
                        while remaining:
                            chunk = response.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise SpeechError("download")
                            output.write(chunk)
                            remaining -= len(chunk)
                        if response.read(1):
                            raise SpeechError("download")
                    if not _verified(partial, expected):
                        raise SpeechError("download")
                    partial.replace(directory / name)
                finally:
                    partial.unlink(missing_ok=True)
    except SpeechError:
        raise
    except (OSError, TimeoutError):
        raise SpeechError("download") from None


def transcribe(
    audio: Any, directory: Path, language: str, *, model_factory: Callable[..., Any] | None = None
) -> str:
    """Decode completely inside the guard: faster-whisper raises errors lazily on iteration."""
    if not model_ready(directory):
        raise SpeechError("model")
    try:
        if model_factory is None:
            from faster_whisper import WhisperModel

            model_factory = WhisperModel
        model = model_factory(
            str(directory),
            device="cpu",
            compute_type="int8",
            cpu_threads=2,
            num_workers=1,
            local_files_only=True,
        )
        if language != "auto" and language not in model.supported_languages:
            raise SpeechError("language")
        options = {
            "beam_size": 5,
            "condition_on_previous_text": False,
            "vad_filter": True,
            "vad_parameters": {"min_silence_duration_ms": 500},
            "no_speech_threshold": 0.6,
            "log_prob_threshold": -1.0,
        }
        if language != "auto":
            options["language"] = language
        segments, _ = model.transcribe(audio, **options)
        text = []
        for segment in segments:
            try:
                silence = float(segment.no_speech_prob) > 0.6 and float(segment.avg_logprob) < -1.0
            except (AttributeError, TypeError, ValueError):
                silence = False
            if not silence:
                text.append(segment.text.strip())
        result = " ".join(text).strip()
        if not result:
            raise SpeechError("empty")
        if len(result.encode("utf-8")) > 128_000:
            raise SpeechError("provider")
        return result
    except SpeechError:
        raise
    except Exception:
        raise SpeechError("provider") from None


class Recognizer:
    """Keep one verified CPU model, never an utterance, inside one profile's worker."""

    def __init__(self, directory: Path, *, model_factory: Callable[..., Any] | None = None):
        self.directory = directory
        self.factory = model_factory
        self.model: Any = None

    def transcribe(self, audio: Any, language: str) -> str:
        def load(*args: Any, **options: Any) -> Any:
            if self.model is None:
                factory = self.factory
                if factory is None:
                    from faster_whisper import WhisperModel

                    factory = WhisperModel
                self.model = factory(*args, **options)
            return self.model

        try:
            # Retain the complete byte verification and conservative decoding guards per use.
            return transcribe(audio, self.directory, language, model_factory=load)
        except SpeechError as error:
            if str(error) in {"model", "provider"}:
                self.model = None
            raise


def _device_id(index: int, row: Any, host: str) -> str:
    identity = hashlib.sha256(
        f"{host}\n{row['name']}\n{row['default_samplerate']}".encode()
    ).hexdigest()[:16]
    return f"d-{index}-{identity}"


def input_devices(sound: Any) -> list[dict[str, Any]]:
    """Refresh idle PortAudio metadata; callers must own no open recording stream."""
    try:
        # The pinned runtime freezes devices/defaults at initialization. This worker owns
        # PortAudio exclusively; serialized idle discovery can refresh it without
        # discarding the Whisper model or changing the user's system defaults.
        if sound._initialized:
            sound._terminate()
        sound._initialize()
        rows, hosts = sound.query_devices(), sound.query_hostapis()
        default = int(sound.default.device[0])
        devices = []
        for index, row in enumerate(rows):
            if row["max_input_channels"] > 0:
                host = hosts[row["hostapi"]]["name"]
                devices.append(
                    {
                        "id": _device_id(index, row, host),
                        "label": f"{row['name']} ({host})",
                        "index": index,
                        "rate": int(row["default_samplerate"]),
                    }
                )
        selected = next((d for d in devices if d["index"] == default), None)
        label = (
            f"System default: {selected['label']}"
            if selected
            else "System default input (unavailable)"
        )
        return [
            {
                "id": "default",
                "label": label,
                "index": default,
                "rate": selected["rate"] if selected else 0,
            },
            *devices,
        ]
    except Exception:
        raise SpeechError("device") from None


class Recording:
    """Own a bounded, in-memory mono recording until explicit Stop or cancellation."""

    def __init__(self, device: str, *, sound: Any = None) -> None:
        if sound is None:
            import sounddevice

            sound = sounddevice
        self.sound = sound
        self.chunks: list[Any] = []
        self.frames = 0
        self.failed = False
        self.lock = threading.Lock()
        self.stream: Any = None
        options = input_devices(sound)
        selected = next((d for d in options if d["id"] == device), None)
        if not selected or selected["index"] < 0 or selected["rate"] <= 0:
            raise SpeechError("device")
        self.rate = selected["rate"]
        self.device = selected["index"]

    def start(self) -> None:
        def capture(data: Any, frames: int, _time: Any, status: Any) -> None:
            with self.lock:
                if status:
                    self.failed = True
                available = MAX_SECONDS * self.rate - self.frames
                if available > 0:
                    chunk = data[:available].copy()
                    self.chunks.append(chunk)
                    self.frames += len(chunk)
                if self.frames >= MAX_SECONDS * self.rate:
                    raise self.sound.CallbackStop

        try:
            self.stream = self.sound.InputStream(
                device=self.device,
                samplerate=self.rate,
                channels=1,
                dtype="float32",
                callback=capture,
            )
            self.stream.start()
        except PermissionError:
            self.cancel()
            raise SpeechError("permission") from None
        except Exception:
            self.cancel()
            raise SpeechError("device") from None

    def cancel(self) -> None:
        stream, self.stream = self.stream, None
        try:
            if stream is not None:
                try:
                    stream.abort()
                finally:
                    stream.close()
        finally:
            with self.lock:
                self.chunks.clear()
                self.frames = 0

    def stop(self) -> io.BytesIO:
        import numpy as np

        if self.stream is None:
            raise SpeechError("device")
        try:
            self.stream.stop()
        finally:
            self.stream.close()
            self.stream = None
        with self.lock:
            chunks, self.chunks = self.chunks, []
            self.frames = 0
        if self.failed:
            raise SpeechError("device")
        if not chunks:
            raise SpeechError("empty")
        pcm = (np.clip(np.concatenate(chunks).reshape(-1), -1, 1) * 32767).astype("<i2")
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(self.rate)
            audio.writeframes(pcm.tobytes())
        output.seek(0)
        return output
