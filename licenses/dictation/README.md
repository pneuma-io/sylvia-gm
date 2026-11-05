# Local dictation components

The local speech worker uses faster-whisper 1.2.1, CTranslate2 4.8.3,
sounddevice 0.5.6, ONNX Runtime 1.31.0 and PyAV 18.0.0. The Windows build
collects their runtime assets and recursive package metadata, including supplied
wheel notices. Whisper transcription weights are downloaded separately after an
explicit user action.

Faster-whisper also includes the 1,245,151-byte Silero VAD6 ONNX graph used to
find speech segments. The existing dictation path enables this detector; it is
already bundled and is not a Whisper transcription model. Its original Silero
Team MIT notice is retained in FASTER-WHISPER-MIT.txt alongside SYSTRAN's notice.
Three small ONNX Runtime example graphs remain unchanged wheel assets.
The audio hook selects only `libportaudio64bit.dll` for the Windows x64 worker;
ASIO and other-platform binaries are excluded. The wheel's supplier declares
PortAudio v19.7.0. PORTAUDIO-MIT.txt preserves that upstream licence text.

The multilingual base model is `Systran/faster-whisper-base`, revision
`ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`. Its files are checked against fixed
sizes and SHA256 hashes before loading. OpenAI's Whisper MIT licence is preserved
in WHISPER-MIT.txt. The model is approximately 148 MB.

The decoding options and conservative silence filter follow Nous Research's
Hermes Agent, `tools/transcription_local.py` at commit
`0b10b9f8155a7ecadd8c540b87712c791b5c8ac9`. Its original MIT notice is in
HERMES-MIT.txt. Faster-whisper and CTranslate2 have their own preserved MIT texts.

PyAV's Python wrapper uses BSD terms. The upstream Windows wheel includes
x264/x265-linked FFmpeg libraries; their patched LGPL runtime label does not
establish downstream redistribution rights. FFMPEG-LGPLv3.txt and GPLv3.txt
preserve the previously collected original texts.

The Windows release packaging instead uses FFmpeg 8.1.2 built from the official
source with GPL, nonfree, version-3 and external auto-detected components
disabled. The seven shared libraries and the rebinding of the same PyAV 18.0.0
extension objects are pinned in PYAV-LGPL-BUILD.json. No stock av.libs DLL is
selected. FFMPEG-LGPLv2.1.txt supplies the selected LGPL-2.1-or-later terms.
REBUILD-CODECS.md describes source delivery and recipient recombination.

Microphone capture and the speech-model implementation remain the merged-main
implementations. This packaging change supplies a different native dependency
build; it is not native microphone or model-transcription acceptance. Persistent
installed notices and final package bytes still require their actual checks.

Upstream sources:

- https://github.com/NousResearch/hermes-agent/blob/0b10b9f8155a7ecadd8c540b87712c791b5c8ac9/tools/transcription_local.py
- https://github.com/SYSTRAN/faster-whisper/tree/v1.2.1
- https://github.com/OpenNMT/CTranslate2/tree/v4.8.3
- https://huggingface.co/Systran/faster-whisper-base/tree/ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66
- https://github.com/PyAV-Org/PyAV/tree/v18.0.0
- https://ffmpeg.org/legal.html
