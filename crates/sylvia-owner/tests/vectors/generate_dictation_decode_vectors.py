"""Generate reference vectors for sylvia_owner::dictation::audio (faster-whisper's decode_audio).

The shipped app decodes with PyAV 18.0.0 bound to the project's MSVC LGPL FFmpeg 8.1.2 DLLs,
not the MinGW FFmpeg inside the PyAV wheel. Stage that binding first, outside the repository,
with the project's own scripts:

    .venv/Scripts/python.exe src-tauri/scripts/provision_ffmpeg_lgpl.py --work-dir <work>
    .venv/Scripts/python.exe src-tauri/scripts/stage_pyav_lgpl.py --ffmpeg <work>/ffmpeg-lgpl \
        --output <pyav> --manifest licenses/dictation/PYAV-LGPL-BUILD.json

(`docs/rust-port/tools/stage_dictation_natives.sh` does both.) Then, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_dictation_decode_vectors.py <pyav>

Each case writes a 16-bit mono WAV exactly as `Recording.stop` does (the `wave` module) from
integer samples both sides generate the same way, and records `decode_audio` of it: the sample
count, the SHA-256 of the little-endian float32 output and its first and last samples' bits.
The DLLs PyAV loaded are recorded by SHA-256. The same cases are also decoded with the wheel's
own FFmpeg in a child process, and the vectors say whether each matched (information for the
review; only the LGPL result is the reference). No recording of a person is used. This script is
reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import hashlib
import io
import json
import subprocess
import sys
import wave
from pathlib import Path

OUTPUT = Path(__file__).with_name("dictation_decode_vectors.json")

CASES = [
    {"rate": 16000, "count": 16000, "seed": 1},
    {"rate": 48000, "count": 48000, "seed": 2},
    {"rate": 44100, "count": 44100, "seed": 3},
    {"rate": 8000, "count": 12000, "seed": 4},
    {"rate": 11025, "count": 11025, "seed": 5},
    {"rate": 22050, "count": 33075, "seed": 6},
    {"rate": 96000, "count": 96000, "seed": 7},
    {"rate": 48000, "count": 501760, "seed": 8},
    {"rate": 48000, "count": 501761, "seed": 9},
    {"rate": 48000, "count": 1100000, "seed": 10},
    {"rate": 44100, "count": 1, "seed": 11},
    {"rate": 48000, "count": 0, "seed": 12},
    {"rate": 16000, "count": 0, "seed": 13},
    {"rate": 4, "count": 480, "seed": 14},
]


def lcg_int16(seed: int, count: int, amplitude: int = 32767) -> list[int]:
    """state = state * 1664525 + 1013904223 (mod 2**32); sample = (state >> 16) % (2a+1) - a."""
    state, out = seed, []
    for _ in range(count):
        state = (state * 1664525 + 1013904223) % 2**32
        out.append((state >> 16) % (2 * amplitude + 1) - amplitude)
    return out


def wav(rate: int, samples: list[int]) -> bytes:
    import numpy as np

    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(np.array(samples, dtype="<i2").tobytes())
    return output.getvalue()


def decoded(case) -> dict:
    import numpy as np
    from faster_whisper.audio import decode_audio

    audio = decode_audio(io.BytesIO(wav(case["rate"], lcg_int16(case["seed"], case["count"]))))
    raw = np.ascontiguousarray(audio, dtype="<f4").tobytes()
    return {"samples": int(audio.size), "sha256": hashlib.sha256(raw).hexdigest(),
            "head": raw[:256].hex(), "tail": raw[-256:].hex()}


def loaded_dlls() -> dict:
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetModuleHandleW.restype = ctypes.wintypes.HMODULE
    kernel.GetModuleFileNameW.argtypes = [ctypes.wintypes.HMODULE, ctypes.wintypes.LPWSTR, ctypes.wintypes.DWORD]
    found = {}
    for name in ("swresample-6.dll", "avutil-60.dll"):
        buffer = ctypes.create_unicode_buffer(1024)
        kernel.GetModuleFileNameW(kernel.GetModuleHandleW(name), buffer, 1024)
        found[name] = hashlib.sha256(Path(buffer.value).read_bytes()).hexdigest()
    return found


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "--wheel":
        json.dump([decoded(case) for case in CASES], sys.stdout)
        return
    if len(sys.argv) != 2:
        raise SystemExit("usage: generate_dictation_decode_vectors.py <staged LGPL PyAV folder>")
    sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
    import av

    if av.__file__ is None or not Path(av.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve()):
        raise SystemExit("the staged LGPL PyAV was not imported")
    rows = [{**case, **decoded(case)} for case in CASES]
    wheel = json.loads(subprocess.run([sys.executable, "-I", __file__, "--wheel"], capture_output=True,
                                      check=True).stdout)
    vectors = {
        "python": sys.version.split()[0],
        "pyav": av.__version__,
        "ffmpeg": av.ffmpeg_version_info,
        "dlls": loaded_dlls(),
        "cases": rows,
        "mingw_wheel_matches": [row["sha256"] == other["sha256"] for row, other in zip(rows, wheel)],
    }
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
