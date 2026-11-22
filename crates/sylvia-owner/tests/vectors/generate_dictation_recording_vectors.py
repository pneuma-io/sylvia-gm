"""Generate reference vectors for a whole dictation through the worker's own recording path.

Run from the repository root, as `generate_dictation_transcribe_vectors.py` is run:

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_dictation_recording_vectors.py \
        <model folder> <speech folder> <staged LGPL PyAV folder>

For each clip, the capture callback receives the clip's samples as PortAudio's float32 blocks
(`int16 / 32768`), in blocks of `BLOCK` frames. Then `Recording.stop` writes its WAV, and
`local_dictation.transcribe` decodes it with the worker's options, under the desktop's settings
for the speech worker (`OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2`). Recorded: the WAV's SHA-256 and
the final text or error code, for English and detection. The clips are synthetic speech; no
recording of a person is used, and no audio device is opened. Reference tooling for the port,
retired with the Python source.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUTPUT = Path(__file__).with_name("dictation_recording_vectors.json")
CLIPS = ["zira-16k", "hazel-48k", "zira-44k"]
BLOCK = 480


class Blocks:
    """The `sounddevice` surface `Recording` uses, playing one clip into the callback."""

    CallbackStop = type("CallbackStop", (Exception,), {})

    def __init__(self, rate: int, samples):
        self.rate, self.samples, self._initialized = rate, samples, False
        self.default = type("Default", (), {"device": (0, -1)})()

    def _initialize(self):
        self._initialized = True

    def _terminate(self):
        self._initialized = False

    def query_devices(self):
        return [{"name": "Synthetic input", "max_input_channels": 1, "hostapi": 0,
                 "default_samplerate": float(self.rate)}]

    def query_hostapis(self):
        return [{"name": "Synthetic"}]

    def InputStream(self, device, samplerate, channels, dtype, callback):  # noqa: N802
        outer = self

        class Stream:
            def start(self):
                for at in range(0, len(outer.samples), BLOCK):
                    block = outer.samples[at:at + BLOCK].reshape(-1, 1)
                    try:
                        callback(block, len(block), None, None)
                    except outer.CallbackStop:
                        break

            def stop(self):
                pass

            def abort(self):
                pass

            def close(self):
                pass

        return Stream()


def child(model: Path, speech: Path, pyav: Path) -> None:
    sys.path.insert(0, str(pyav))
    sys.path.insert(0, str(ROOT / "src"))
    import av  # noqa: F401  (the staged LGPL binding, first on the path)
    import numpy as np

    from sylvia_gm import local_dictation

    assert Path(av.__file__).resolve().is_relative_to(pyav.resolve())
    rows = []
    for name in CLIPS:
        with wave.open(str(speech / f"{name}.wav"), "rb") as audio:
            rate = audio.getframerate()
            pcm = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
        samples = (pcm.astype(np.float32) / np.float32(32768.0)).astype(np.float32)
        recording = local_dictation.Recording("default", sound=Blocks(rate, samples))
        recording.start()
        wav = recording.stop().getvalue()
        row = {"clip": name, "rate": rate, "block": BLOCK, "wav_sha256": hashlib.sha256(wav).hexdigest()}
        for language in ("en", "auto"):
            try:
                row[language] = {"text": local_dictation.transcribe(io.BytesIO(wav), model, language)}
            except local_dictation.SpeechError as error:
                row[language] = {"error": str(error)}
        rows.append(row)
    json.dump({"omp_num_threads": os.environ.get("OMP_NUM_THREADS"), "clips": rows}, sys.stdout)


def main() -> None:
    if len(sys.argv) == 5 and sys.argv[1] == "--child":
        child(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
        return
    if len(sys.argv) != 4:
        raise SystemExit("usage: generate_dictation_recording_vectors.py <model> <speech> <LGPL PyAV>")
    model, speech, pyav = (Path(argument).resolve() for argument in sys.argv[1:])
    clips = {name: hashlib.sha256((speech / f"{name}.wav").read_bytes()).hexdigest() for name in CLIPS}
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"OPENBLAS_NUM_THREADS", "GOTO_NUM_THREADS"}}
    environment.update({"OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"})
    completed = subprocess.run([sys.executable, "-I", __file__, "--child", str(model), str(speech), str(pyav)],
                               env=environment, capture_output=True, check=True)
    vectors = {"python": sys.version.split()[0], "clip_sha256": clips, **json.loads(completed.stdout)}
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
