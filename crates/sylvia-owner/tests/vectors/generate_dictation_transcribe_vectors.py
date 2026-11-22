"""Generate reference vectors for the dictation engine's whole transcription: faster-whisper 1.2.1.

Run from the repository root after `uv sync --extra dev --extra dictation`, with the pinned model
(as `prepare_model` downloads it), the SAPI test speech (`docs/rust-port/tools/sapi_speech.ps1`)
and PyAV bound to the shipped LGPL FFmpeg (`docs/rust-port/tools/stage_dictation_natives.sh`):

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_dictation_transcribe_vectors.py \
        <model folder> <speech folder> <staged LGPL PyAV folder>

Each clip is first rewritten as the worker's own recording (`Recording.stop`'s 16-bit mono WAV at
the clip's rate), the bytes the worker hands to `transcribe`. Then, in a child process under the
desktop's settings for the speech worker (`OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2`):
- the Silero VAD's speech probabilities (count, SHA-256 of the float32 bytes) and speech chunks
  with the worker's `min_silence_duration_ms=500`;
- the SHA-256 of the log-Mel features of the audio the VAD kept;
- `WhisperModel(...).transcribe(...)` with the worker's options, English and detected: every
  segment's text, tokens, times, temperature and, by their IEEE bits, `avg_logprob`,
  `no_speech_prob` and the compression ratio, and the detected language;
- `local_dictation.transcribe`'s final text, or its error code;
- `VARIANTS`: English transcriptions under other thresholds, with CTranslate2's sampling seeded,
  for the temperature fallback, the all-failed choice and the no-speech skip.
The clips are synthetic speech, a silence and generated noise; no recording of a person is used.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUTPUT = Path(__file__).with_name("dictation_transcribe_vectors.json")
CLIPS = ["zira-16k", "hazel-48k", "zira-44k", "zira-long-16k", "hazel-windows-16k"]
# (label, clip, CTranslate2 seed, thresholds in place of the worker's)
VARIANTS = [
    ("every-temperature-fails-log-prob", "zira-16k", 1234,
     {"log_prob_threshold": 0.0, "no_speech_threshold": None}),
    ("every-temperature-fails-compression", "zira-16k", 99, {"compression_ratio_threshold": 0.5}),
    ("fallback-log-prob-per-window", "hazel-windows-16k", 7,
     {"log_prob_threshold": -0.2, "no_speech_threshold": None}),
    ("fallback-compression-per-window", "hazel-windows-16k", 11, {"compression_ratio_threshold": 1.65}),
    ("no-speech-skip", "hazel-windows-16k", 5, {"log_prob_threshold": -0.25, "no_speech_threshold": 0.0}),
    ("checks-off", "zira-long-16k", 3,
     {"compression_ratio_threshold": None, "log_prob_threshold": None, "no_speech_threshold": None}),
]


def lcg_int16(seed: int, count: int, amplitude: int) -> list[int]:
    state, out = seed, []
    for _ in range(count):
        state = (state * 1664525 + 1013904223) % 2**32
        out.append((state >> 16) % (2 * amplitude + 1) - amplitude)
    return out


def worker_wav(rate: int, pcm: bytes) -> bytes:
    """`Recording.stop`'s file: `wave` with one channel, two-byte samples, the device rate."""
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(pcm)
    return output.getvalue()


def recordings(speech: Path) -> list[tuple[str, int, bytes]]:
    rows = []
    for name in CLIPS:
        with wave.open(str(speech / f"{name}.wav"), "rb") as audio:
            assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
            rows.append((name, audio.getframerate(), audio.readframes(audio.getnframes())))
    rows.append(("silence-48k", 48000, b"\x00\x00" * 96000))
    rows.append(("noise-16k", 16000, struct.pack(f"<{48000}h", *lcg_int16(77, 48000, 3000))))
    return rows


def f64(value: float) -> str:
    return struct.pack("<d", float(value)).hex()


def f32_digest(values) -> str:
    import numpy as np

    return hashlib.sha256(np.ascontiguousarray(values, dtype="<f4").tobytes()).hexdigest()


def child(model: Path, speech: Path, pyav: Path) -> None:
    sys.path.insert(0, str(pyav))
    sys.path.insert(0, str(ROOT / "src"))
    import av  # noqa: F401  (the staged LGPL binding, first on the path)
    import numpy as np
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio
    from faster_whisper.feature_extractor import FeatureExtractor
    from faster_whisper.vad import VadOptions, collect_chunks, get_speech_timestamps, get_vad_model

    from sylvia_gm import local_dictation

    assert Path(av.__file__).resolve().is_relative_to(pyav.resolve())
    whisper = WhisperModel(str(model), device="cpu", compute_type="int8", cpu_threads=2, num_workers=1,
                           local_files_only=True)
    options = dict(beam_size=5, condition_on_previous_text=False, vad_filter=True,
                   vad_parameters={"min_silence_duration_ms": 500}, no_speech_threshold=0.6, log_prob_threshold=-1.0)
    rows = []
    for name, rate, pcm in recordings(speech):
        wav = worker_wav(rate, pcm)
        audio = decode_audio(io.BytesIO(wav))
        vad = VadOptions(min_silence_duration_ms=500)
        padded = np.pad(audio, (0, 512 - audio.shape[0] % 512))
        probabilities = get_vad_model()(padded)
        chunks = get_speech_timestamps(audio, vad)
        kept = np.concatenate(collect_chunks(audio, chunks)[0], axis=0)
        row = {
            "clip": name, "rate": rate, "wav_sha256": hashlib.sha256(wav).hexdigest(), "wav_bytes": len(wav),
            "audio_samples": int(audio.shape[0]), "audio_sha256": f32_digest(audio),
            "vad_probabilities": {"shape": list(probabilities.shape), "sha256": f32_digest(probabilities)},
            "speech_chunks": [[chunk["start"], chunk["end"]] for chunk in chunks],
            "kept_samples": int(kept.shape[0]), "features_sha256": f32_digest(FeatureExtractor()(kept)),
            "transcriptions": {},
        }
        for language in ("en", None):
            segments, info = whisper.transcribe(io.BytesIO(wav), language=language, **options)
            row["transcriptions"]["auto" if language is None else language] = {
                "language": info.language, "language_probability": f64(info.language_probability),
                "segments": [{"id": s.id, "seek": s.seek, "start": f64(s.start), "end": f64(s.end), "text": s.text,
                              "tokens": list(s.tokens), "temperature": f64(s.temperature),
                              "avg_logprob": f64(s.avg_logprob), "compression_ratio": f64(s.compression_ratio),
                              "no_speech_prob": f64(s.no_speech_prob)} for s in segments],
            }
            try:
                final = {"text": local_dictation.transcribe(io.BytesIO(wav), model, language or "auto")}
            except local_dictation.SpeechError as error:
                final = {"error": str(error)}
            row["transcriptions"]["auto" if language is None else language]["worker"] = final
        rows.append(row)
    import ctranslate2
    import onnxruntime

    # The worker's thresholds never fall back on these clips, so other thresholds reach the
    # temperature fallback, the all-failed choice and the no-speech skip. Sampling draws from
    # CTranslate2's per-thread generator, seeded from `random_device` unless a seed is set: each
    # variant sets one and loads a fresh model, whose new worker thread seeds from it.
    wavs = {name: worker_wav(rate, pcm) for name, rate, pcm in recordings(speech)}
    variants = []
    for label, clip, seed, thresholds in VARIANTS:
        ctranslate2.set_random_seed(seed)
        model_for_variant = WhisperModel(str(model), device="cpu", compute_type="int8", cpu_threads=2,
                                         num_workers=1, local_files_only=True)
        segments, info = model_for_variant.transcribe(io.BytesIO(wavs[clip]), language="en",
                                                      **{**options, **thresholds})
        variants.append({
            "variant": label, "clip": clip, "seed": seed, "thresholds": thresholds,
            "segments": [{"id": s.id, "seek": s.seek, "start": f64(s.start), "end": f64(s.end), "text": s.text,
                          "tokens": list(s.tokens), "temperature": f64(s.temperature),
                          "avg_logprob": f64(s.avg_logprob), "compression_ratio": f64(s.compression_ratio),
                          "no_speech_prob": f64(s.no_speech_prob)} for s in segments],
        })
        del model_for_variant
    json.dump({"ctranslate2": ctranslate2.__version__, "onnxruntime": onnxruntime.__version__,
               "omp_num_threads": os.environ.get("OMP_NUM_THREADS"), "clips": rows, "variants": variants},
              sys.stdout)


def main() -> None:
    if len(sys.argv) == 5 and sys.argv[1] == "--child":
        child(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
        return
    if len(sys.argv) != 4:
        raise SystemExit("usage: generate_dictation_transcribe_vectors.py <model> <speech> <LGPL PyAV>")
    model, speech, pyav = (Path(argument).resolve() for argument in sys.argv[1:])
    clips = {name: hashlib.sha256((speech / f"{name}.wav").read_bytes()).hexdigest() for name in CLIPS}
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"OPENBLAS_NUM_THREADS", "GOTO_NUM_THREADS"}}
    # As the desktop starts the speech worker (src-tauri/src/dictation.rs).
    environment.update({"OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"})
    completed = subprocess.run([sys.executable, "-I", __file__, "--child", str(model), str(speech), str(pyav)],
                               env=environment, capture_output=True, check=True)
    vectors = {"python": sys.version.split()[0], "clip_sha256": clips, **json.loads(completed.stdout)}
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
