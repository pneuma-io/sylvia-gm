# Local dictation components

The local speech worker uses faster-whisper 1.2.1, CTranslate2 4.8.3,
sounddevice 0.5.6, ONNX Runtime 1.31.0 and PyAV 18.0.0. The Windows build
collects their runtime assets and recursive package metadata, including supplied
wheel notices. The model is downloaded separately after an explicit user action.
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

PyAV's Python wrapper uses BSD terms. The selected Windows wheel's FFmpeg 8.1.2
libraries report LGPL version 3 or later. FFMPEG-LGPLv3.txt and GPLv3.txt retain
those original terms; they do not establish that a recipient source/relinking
delivery satisfies every applicable obligation. Native codec provenance,
corresponding source and final installed notice delivery must be checked for
the selected distributable before publication. The project's existing
release-ineligible status remains in force. This private engineering change
does not grant release or redistribution approval.

Upstream sources:

- https://github.com/NousResearch/hermes-agent/blob/0b10b9f8155a7ecadd8c540b87712c791b5c8ac9/tools/transcription_local.py
- https://github.com/SYSTRAN/faster-whisper/tree/v1.2.1
- https://github.com/OpenNMT/CTranslate2/tree/v4.8.3
- https://huggingface.co/Systran/faster-whisper-base/tree/ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66
- https://github.com/PyAV-Org/PyAV/tree/v18.0.0
- https://ffmpeg.org/legal.html
