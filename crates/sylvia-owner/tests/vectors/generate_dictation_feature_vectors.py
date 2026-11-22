"""Generate reference vectors for sylvia_owner::dictation::features from the Python snapshot.

Run with the snapshot's Windows interpreter after `uv sync --extra dev --extra dictation`, from
the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_dictation_feature_vectors.py

It records faster-whisper 1.2.1's `FeatureExtractor` (NumPy 2.5.4) on this machine: the Hann
window, the Mel filter bank, single `np.fft.rfft` frames, `np.abs` on complex64, and the full
log-Mel features of generated signals. Float values are written as the hex of their IEEE bits,
so the comparison is bit for bit. Large outputs are recorded by the SHA-256 of their
little-endian float32 bytes.

The signals are generated from integers both sides compute the same way: a 32-bit linear
congruential generator and integer square waves, scaled as faster-whisper scales decoded
16-bit audio (`int16 / 32768`). No recording of a person is used. The NumPy CPU features and
BLAS are recorded too: the matrix product and `np.abs` follow this machine's SIMD dispatch.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import numpy._core._multiarray_umath as umath
from faster_whisper.feature_extractor import FeatureExtractor

OUTPUT = Path(__file__).with_name("dictation_feature_vectors.json")


def bits(values) -> str:
    return np.ascontiguousarray(values, dtype="<f4").tobytes().hex()


def digest(values) -> str:
    return hashlib.sha256(np.ascontiguousarray(values, dtype="<f4").tobytes()).hexdigest()


def lcg_int16(seed: int, count: int, amplitude: int) -> np.ndarray:
    """state = state * 1664525 + 1013904223 (mod 2**32); sample = (state >> 16) % (2a+1) - a."""
    state, out = seed, []
    for _ in range(count):
        state = (state * 1664525 + 1013904223) % 2**32
        out.append((state >> 16) % (2 * amplitude + 1) - amplitude)
    return np.array(out, dtype=np.int16)


def square_int16(count: int, period: int, amplitude: int) -> np.ndarray:
    return np.array([amplitude if (i // (period // 2)) % 2 == 0 else -amplitude for i in range(count)],
                    dtype=np.int16)


SIGNALS = [
    {"name": "silence 1 s", "kind": "zeros", "count": 16000},
    {"name": "noise 0.5 s", "kind": "lcg", "seed": 1, "count": 8000, "amplitude": 12000},
    {"name": "quiet noise 2 s", "kind": "lcg", "seed": 7, "count": 32000, "amplitude": 40},
    {"name": "loud noise 30 s", "kind": "lcg", "seed": 42, "count": 480000, "amplitude": 32767},
    {"name": "square 440 Hz 3 s", "kind": "square", "count": 48000, "period": 36, "amplitude": 20000},
    {"name": "noise 31.7 s", "kind": "lcg", "seed": 9, "count": 507200, "amplitude": 9000},
    {"name": "one sample", "kind": "lcg", "seed": 3, "count": 1, "amplitude": 1000},
    {"name": "40 samples", "kind": "lcg", "seed": 5, "count": 40, "amplitude": 1000},
    {"name": "241 samples", "kind": "lcg", "seed": 11, "count": 241, "amplitude": 1000},
    {"name": "empty", "kind": "zeros", "count": 0},
]


def signal(spec) -> np.ndarray:
    if spec["kind"] == "zeros":
        pcm = np.zeros(spec["count"], dtype=np.int16)
    elif spec["kind"] == "lcg":
        pcm = lcg_int16(spec["seed"], spec["count"], spec["amplitude"])
    else:
        pcm = square_int16(spec["count"], spec["period"], spec["amplitude"])
    # faster_whisper.audio.decode_audio: audio.astype(np.float32) / 32768.0
    return pcm.astype(np.float32) / 32768.0


def features_of(signals) -> list:
    extractor = FeatureExtractor()
    rows = []
    for spec in signals:
        log_mel = extractor(signal(spec))
        row = {"signal": spec, "shape": list(log_mel.shape), "sha256": digest(log_mel)}
        if log_mel.size <= 4000:
            row["values"] = bits(log_mel)
        else:
            row["first_frame"] = bits(log_mel[:, 0])
            row["last_frame"] = bits(log_mel[:, -1])
        rows.append(row)
    return rows


def openblas_library() -> dict:
    """The OpenBLAS DLL NumPy loaded, by file name and SHA-256, and its build line."""
    libs = Path(np.__file__).parent.parent / "numpy.libs"
    (library,) = libs.glob("libscipy_openblas64_*.dll")
    return {"file": library.name, "sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
            "config": np.show_config(mode="dicts")["Build Dependencies"]["blas"]["openblas configuration"]}


def child(threads: str) -> None:
    """Features under one OpenBLAS thread setting (read from the environment at load)."""
    signals = SIGNALS if threads == "2" else [SIGNALS[1], SIGNALS[6]]
    json.dump({"omp_num_threads": os.environ.get("OMP_NUM_THREADS"), "features": features_of(signals)},
              sys.stdout)


def run_child(threads: str) -> dict:
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"OPENBLAS_NUM_THREADS", "GOTO_NUM_THREADS"}}
    # As the desktop starts the speech worker (src-tauri/src/dictation.rs).
    environment.update({"OMP_NUM_THREADS": threads, "MKL_NUM_THREADS": threads})
    completed = subprocess.run([sys.executable, "-I", __file__, "--child", threads], env=environment,
                               capture_output=True, check=True)
    return json.loads(completed.stdout)


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        child(sys.argv[2])
        return
    extractor = FeatureExtractor()
    window = np.hanning(401)[:-1].astype("float32")
    frames = []
    for seed in (1, 2, 3):
        frame = (lcg_int16(seed, 400, 32767).astype(np.float32) / 32768.0) * window
        spectrum = np.fft.rfft(frame)
        frames.append({"seed": seed, "input": bits(frame), "real": bits(spectrum.real),
                       "imag": bits(spectrum.imag), "abs": bits(np.abs(spectrum.astype("complex64")))})
    abs_cases = np.array([3 + 4j, 0j, 1e-30 + 1e-30j, 3e38 + 3e38j, -2.5 - 0.5j, 1 + 0j, 0 - 7j,
                          1.17549435e-38 + 2.3e-38j], dtype=np.complex64)
    vectors = {
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "cpu_dispatch": [name for name, on in umath.__cpu_features__.items() if on],
        "openblas": openblas_library(),
        "window": bits(window),
        "mel_filters": bits(extractor.mel_filters),
        "rfft_frames": frames,
        "abs": {"real": bits(abs_cases.real), "imag": bits(abs_cases.imag), "abs": bits(np.abs(abs_cases))},
        # OMP_NUM_THREADS=2 is the desktop's setting; =1 shows a threading change.
        "threads": {threads: run_child(threads) for threads in ("2", "1")},
    }
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
