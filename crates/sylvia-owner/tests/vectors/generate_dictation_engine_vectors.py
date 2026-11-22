"""Generate reference vectors for the dictation engine: CTranslate2's Whisper across the C++ boundary.

The first engine check is the boundary itself: the shipped ctranslate2 4.8.3 wheel's
`ctranslate2.models.Whisper`, built as faster-whisper 1.2.1's `WhisperModel` builds it (CPU, int8,
`intra_threads=2`, `inter_threads=1`), given the log-Mel features of a test clip. It records:
- `encode`: the encoder output's shape and the SHA-256 of its float32 bytes, plus its first values;
- `detect_language`: the first ten languages with their probabilities;
- `generate`: one beam-search call with faster-whisper's own default options and its English
  `sot_sequence` prompt: the token ids, the score and `no_speech_prob`.
Floats are recorded by their IEEE bits.

Run from the repository root after `uv sync --extra dev --extra dictation`, with the pinned model
(as `prepare_model` downloads it) and the SAPI test speech (`docs/rust-port/tools/sapi_speech.ps1`):

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_dictation_engine_vectors.py \
        <model folder> <speech folder>

The engine runs in a child process under the desktop's settings for the speech worker
(`OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2`), read when the libraries load. The clips are synthetic
speech; no recording of a person is used. This script is reference tooling for the port and is
retired with the Python source.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

OUTPUT = Path(__file__).with_name("dictation_engine_vectors.json")
CLIPS = ["zira-16k"]


def bits(values) -> str:
    import numpy as np

    return np.ascontiguousarray(values, dtype="<f4").tobytes().hex()


def clip_audio(path: Path):
    """The clip's 16-bit mono samples as faster-whisper scales decoded audio (16 kHz only here)."""
    import numpy as np

    with wave.open(str(path), "rb") as audio:
        assert audio.getnchannels() == 1 and audio.getsampwidth() == 2 and audio.getframerate() == 16000
        pcm = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
    return pcm.astype(np.float32) / 32768.0


def child(model: Path, speech: Path) -> None:
    import ctranslate2
    import numpy as np
    from faster_whisper.audio import pad_or_trim
    from faster_whisper.feature_extractor import FeatureExtractor
    from faster_whisper.tokenizer import Tokenizer
    from faster_whisper.transcribe import get_ctranslate2_storage, get_suppressed_tokens
    import tokenizers

    whisper = ctranslate2.models.Whisper(str(model), device="cpu", device_index=0, compute_type="int8",
                                         intra_threads=2, inter_threads=1)
    tokenizer = Tokenizer(tokenizers.Tokenizer.from_file(str(model / "tokenizer.json")),
                          whisper.is_multilingual, task="transcribe", language="en")
    suppress = list(get_suppressed_tokens(tokenizer, [-1]))
    rows = []
    for name in CLIPS:
        features = FeatureExtractor()(clip_audio(speech / f"{name}.wav"))
        segment = pad_or_trim(features[:, :3000])
        encoder_output = whisper.encode(get_ctranslate2_storage(np.expand_dims(segment, 0)), to_cpu=False)
        encoded = np.array(encoder_output)
        languages = whisper.detect_language(encoder_output)[0][:10]
        prompt = list(tokenizer.sot_sequence)
        result = whisper.generate(
            encoder_output, [prompt], length_penalty=1, repetition_penalty=1, no_repeat_ngram_size=0,
            max_length=448, return_scores=True, return_no_speech_prob=True, suppress_blank=True,
            suppress_tokens=suppress, max_initial_timestamp_index=50, beam_size=5, patience=1,
        )[0]
        rows.append({
            "clip": name,
            "features_sha256": hashlib.sha256(np.ascontiguousarray(segment, dtype="<f4").tobytes()).hexdigest(),
            "encoder_shape": list(encoded.shape),
            "encoder_sha256": hashlib.sha256(np.ascontiguousarray(encoded, dtype="<f4").tobytes()).hexdigest(),
            "encoder_head": bits(encoded.reshape(-1)[:64]),
            # CTranslate2's own printout of the output (first and last values, shape).
            "encoder_text": str(encoder_output),
            "languages": [[token, bits([probability])] for token, probability in languages],
            "prompt": prompt,
            "suppress_tokens": suppress,
            "sequences_ids": result.sequences_ids,
            "scores": bits(result.scores),
            "no_speech_prob": bits([result.no_speech_prob]),
        })
    library = Path(ctranslate2.__file__).parent / "ctranslate2.dll"
    json.dump({"ctranslate2": ctranslate2.__version__,
               "dll_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
               "omp_num_threads": os.environ.get("OMP_NUM_THREADS"), "clips": rows}, sys.stdout)


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        child(Path(sys.argv[2]), Path(sys.argv[3]))
        return
    if len(sys.argv) != 3:
        raise SystemExit("usage: generate_dictation_engine_vectors.py <model folder> <speech folder>")
    model, speech = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    clips = {name: hashlib.sha256((speech / f"{name}.wav").read_bytes()).hexdigest() for name in CLIPS}
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"OPENBLAS_NUM_THREADS", "GOTO_NUM_THREADS"}}
    # As the desktop starts the speech worker (src-tauri/src/dictation.rs).
    environment.update({"OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"})
    completed = subprocess.run([sys.executable, "-I", __file__, "--child", str(model), str(speech)],
                               env=environment, capture_output=True, check=True)
    vectors = {"python": sys.version.split()[0], "clip_sha256": clips, **json.loads(completed.stdout)}
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
