import hashlib
import io
import json
import wave
from types import SimpleNamespace

import pytest

from sylvia_gm import local_dictation as local
from sylvia_gm import local_dictation_process as worker
from sylvia_gm.local_dictation import SpeechError, transcribe


def segment(text, probability, confidence):
    return SimpleNamespace(text=text, no_speech_prob=probability, avg_logprob=confidence)


def test_warm_recognizer_reuses_one_model_without_retaining_prior_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(local, "model_ready", lambda _: True)
    loads, inputs = [], []

    class Model:
        supported_languages = ["en"]

        def __init__(self, *args, **kwargs):
            loads.append(1)

        def transcribe(self, audio, **options):
            inputs.append(audio)
            return iter([segment(audio, 0, 0)]), None

    recognizer = local.Recognizer(tmp_path, model_factory=Model)
    assert recognizer.transcribe("first", "en") == "first"
    assert recognizer.transcribe("second", "en") == "second"
    assert loads == [1] and inputs == ["first", "second"]
    monkeypatch.setattr(local, "model_ready", lambda _: False)
    with pytest.raises(SpeechError, match="model"):
        recognizer.transcribe("modified cache", "en")


def test_warm_recognizer_discards_failed_model_and_never_returns_partial_text(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local, "model_ready", lambda _: True)
    loads = []

    class Model:
        supported_languages = ["en"]

        def __init__(self, *args, **kwargs):
            loads.append(1)

        def transcribe(self, audio, **options):
            def segments():
                if audio == "fail":
                    yield segment("partial", 0, 0)
                    raise RuntimeError("private decoder failure")
                yield segment("fresh utterance", 0, 0)
            return segments(), None

    recognizer = local.Recognizer(tmp_path, model_factory=Model)
    with pytest.raises(SpeechError, match="provider"):
        recognizer.transcribe("fail", "en")
    assert recognizer.model is None
    assert recognizer.transcribe("next", "en") == "fresh utterance"
    assert loads == [1, 1]


def test_session_eof_releases_recording_without_loading_model_or_decoding(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "model_ready", lambda _: True)

    class Capture:
        def __init__(self, device):
            pass

        def start(self):
            calls.append("open")

        def cancel(self):
            calls.append("close")

        def stop(self):
            pytest.fail("EOF must not decode")

    monkeypatch.setattr(worker, "Recording", Capture)
    source = io.StringIO('{"mode":"session"}\n{"id":"one","mode":"start","engine":"en"}\n')
    assert worker.serve(source, io.StringIO(), tmp_path) == 0
    assert calls == ["open", "close"]


def test_session_protocol_repeats_capture_closes_audio_before_reply_and_rejects_stale_id(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(worker, "model_ready", lambda _: True)
    monkeypatch.setattr(worker, "capabilities", lambda _: {"ready": True})

    class Capture:
        def __init__(self, device):
            calls.append("create")

        def start(self):
            calls.append("open")

        def stop(self):
            calls.append("close")
            return io.BytesIO(b"controlled")

        def cancel(self):
            calls.append("cancel")

    class Recognizer:
        def __init__(self, directory):
            calls.append("recognizer")

        def transcribe(self, audio, language):
            assert calls[-1] == "close"
            return "controlled words"

    monkeypatch.setattr(worker, "Recording", Capture)
    monkeypatch.setattr(worker, "Recognizer", Recognizer, raising=False)
    commands = [{"mode": "session"}, {"id": "metadata", "mode": "capabilities"},
                {"id": "one", "mode": "start", "engine": "en"},
                {"id": "old", "mode": "stop"}, {"id": "one", "mode": "stop"},
                {"id": "two", "mode": "start", "engine": "en"},
                {"id": "two", "mode": "stop"}]
    output = io.StringIO()
    source = io.StringIO("".join(json.dumps(c) + "\n" for c in commands))
    assert worker.serve(source, output, tmp_path) == 0
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[0] == {"status": "ready", "protocol": 1}
    assert replies[1] == {"id": "metadata", "ready": True, "idle": True}
    assert replies[3] == {"id": "old", "error": "unavailable", "idle": False}
    assert replies[4] == {"id": "one", "text": "controlled words", "idle": True}
    assert replies[6] == {"id": "two", "text": "controlled words", "idle": True}
    assert calls.count("open") == calls.count("close") == 2
    assert calls.count("recognizer") == 1


def test_local_decode_uses_selected_cpu_model_and_conservative_silence_gate(tmp_path, monkeypatch):
    monkeypatch.setattr("sylvia_gm.local_dictation.model_ready", lambda _: True)
    observed = {}

    class Model:
        supported_languages = ["en", "es"]

        def __init__(self, path, **options):
            observed["load"] = options

        def transcribe(self, audio, **options):
            observed["decode"] = options
            return iter(
                [
                    segment("quiet real speech", 0.9, -0.5),
                    segment("uncertain real speech", 0.1, -1.5),
                    segment("silence hallucination", 0.9, -1.5),
                ]
            ), SimpleNamespace(language="en")

    assert transcribe(object(), tmp_path, "en", model_factory=Model) == (
        "quiet real speech uncertain real speech"
    )
    assert observed["load"]["device"] == "cpu"
    assert observed["load"]["compute_type"] == "int8"
    assert observed["load"]["cpu_threads"] == 2
    assert observed["load"]["local_files_only"] is True
    assert observed["decode"] == {
        "beam_size": 5,
        "condition_on_previous_text": False,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 500},
        "no_speech_threshold": 0.6,
        "log_prob_threshold": -1.0,
        "language": "en",
    }


def test_decode_error_while_iterating_is_safe_and_never_returns_partial_text(tmp_path, monkeypatch):
    monkeypatch.setattr("sylvia_gm.local_dictation.model_ready", lambda _: True)

    class Model:
        supported_languages = ["en"]

        def __init__(self, *args, **options):
            pass

        def transcribe(self, *args, **options):
            def lazy():
                yield segment("partial private words", 0.0, 0.0)
                raise RuntimeError("private decoder details")

            return lazy(), None

    with pytest.raises(SpeechError) as error:
        transcribe(object(), tmp_path, "en", model_factory=Model)
    assert str(error.value) == "provider"


def test_model_setup_verifies_bytes_retries_failure_and_reuses_only_complete_cache(
    tmp_path, monkeypatch
):
    payload = b"controlled model bytes"
    monkeypatch.setattr(
        local, "MODEL_FILES", {"model.bin": (len(payload), hashlib.sha256(payload).hexdigest())}
    )
    calls = []

    def fetch(request, **options):
        calls.append(request.full_url)
        assert request.full_url.startswith(
            "https://huggingface.co/Systran/faster-whisper-base/resolve/"
        )
        return io.BytesIO(b"x" * len(payload) if len(calls) == 1 else payload)

    monkeypatch.setattr(local.urllib.request, "urlopen", fetch)
    assert local.model_ready(tmp_path) is False
    with pytest.raises(SpeechError, match="download"):
        local.prepare_model(tmp_path)
    assert not (tmp_path / "model.bin").exists()
    assert not (tmp_path / "model.bin.part").exists()
    local.prepare_model(tmp_path)
    assert local.model_ready(tmp_path) is True
    local.prepare_model(tmp_path)
    assert len(calls) == 2
    (tmp_path / "model.bin").write_bytes(b"x" * len(payload))
    assert local.model_ready(tmp_path) is False


def test_low_storage_never_starts_download(tmp_path, monkeypatch):
    monkeypatch.setattr(local.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    monkeypatch.setattr(
        local.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network must not start")
    )
    with pytest.raises(SpeechError, match="storage"):
        local.prepare_model(tmp_path)


def test_model_metadata_and_missing_model_never_start_audio_or_network(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "input_devices", lambda _: [])
    monkeypatch.setattr(worker, "Recording", lambda _: pytest.fail("metadata must not capture"))
    monkeypatch.setattr(
        worker, "prepare_model", lambda _: pytest.fail("metadata must not download")
    )
    output = io.StringIO()
    assert worker.serve(io.StringIO('{"mode":"capabilities"}\n'), output, tmp_path) == 0
    assert json.loads(output.getvalue())["ready"] is False
    output = io.StringIO()
    assert worker.serve(io.StringIO('{"mode":"start","engine":"en"}\n'), output, tmp_path) == 1
    assert json.loads(output.getvalue()) == {"error": "model"}


def sound_fixture():
    calls = []
    sound = SimpleNamespace(
        default=SimpleNamespace(device=[0, 1]),
        query_devices=lambda: [
            {
                "name": "Chosen input",
                "max_input_channels": 1,
                "hostapi": 0,
                "default_samplerate": 48000,
            }
        ],
        query_hostapis=lambda: [{"name": "Controlled host"}],
        CallbackStop=RuntimeError,
    )

    class Stream:
        def __init__(self, **options):
            calls.append(("open", options))
            self.callback = options["callback"]

        def start(self):
            import numpy as np

            self.callback(np.zeros((480, 1), dtype="float32"), 480, None, None)
            calls.append(("start",))

        def stop(self):
            calls.append(("stop",))

        def abort(self):
            calls.append(("abort",))

        def close(self):
            calls.append(("close",))

    sound.InputStream = Stream
    sound._initialized = 1
    sound._terminate = lambda: setattr(sound, "_initialized", 0)
    sound._initialize = lambda: setattr(sound, "_initialized", 1)
    return sound, calls


def cached_sound_fixture():
    """PortAudio-style inventory changes become visible only after reinitialization."""
    sound, calls = sound_fixture()
    state = {"rows": sound.query_devices(), "default": 0, "active": False,
             "fail_initialize": False, "permission": False, "refreshes": 0}
    cached = {"rows": list(state["rows"]), "default": 0}
    sound._initialized = 1

    def terminate():
        assert not state["active"], "must never terminate PortAudio with an open stream"
        sound._initialized -= 1

    def initialize():
        assert not state["active"], "must never initialize PortAudio with an open stream"
        state["refreshes"] += 1
        if state["fail_initialize"]:
            raise RuntimeError("controlled initialization failure")
        sound._initialized += 1
        cached.update(rows=list(state["rows"]), default=state["default"])

    class Defaults:
        @property
        def device(self):
            return [cached["default"], -1]

    original_stream = sound.InputStream

    def stream(**options):
        if state["permission"]:
            raise PermissionError("controlled permission failure")
        result = original_stream(**options)
        close = result.close
        state["active"] = True

        def closed():
            state["active"] = False
            close()

        result.close = closed
        return result

    sound._terminate, sound._initialize = terminate, initialize
    sound.query_devices = lambda: cached["rows"]
    sound.default = Defaults()
    sound.InputStream = stream
    return sound, calls, state


def test_warm_device_inventory_refreshes_add_remove_and_default_changes_without_capture():
    sound, calls, state = cached_sound_fixture()
    original = local.input_devices(sound)[1]["id"]
    added = dict(state["rows"][0], name="Newly connected input", default_samplerate=44100)
    state.update(rows=[*state["rows"], added], default=1)
    options = local.input_devices(sound)
    assert len(options) == 3
    assert "Newly connected input" in options[0]["label"]
    recording = local.Recording("default", sound=sound)
    assert recording.device == 1 and recording.rate == 44100
    state.update(rows=[added], default=0)
    with pytest.raises(SpeechError, match="device"):
        local.Recording(original, sound=sound)
    assert calls == [] and not state["active"]


def test_warm_device_initialization_failure_and_permission_recover_on_next_attempt():
    sound, calls, state = cached_sound_fixture()
    state["fail_initialize"] = True
    with pytest.raises(SpeechError, match="device"):
        local.input_devices(sound)
    assert sound._initialized == 0 and calls == []
    state.update(fail_initialize=False, permission=True)
    with pytest.raises(SpeechError, match="permission"):
        local.Recording("default", sound=sound).start()
    state["permission"] = False
    recording = local.Recording("default", sound=sound)
    recording.start()
    recording.cancel()
    assert sound._initialized == 1 and not state["active"]
    assert calls[-2:] == [("abort",), ("close",)]


def test_session_refreshes_only_idle_devices_and_preserves_the_loaded_model(tmp_path, monkeypatch):
    import sys

    sound, calls, state = cached_sound_fixture()
    monkeypatch.setitem(sys.modules, "sounddevice", sound)
    monkeypatch.setattr(local, "model_ready", lambda _: True)
    monkeypatch.setattr(worker, "model_ready", lambda _: True)
    loads = []

    class Model:
        supported_languages = ["en"]

        def __init__(self, *args, **kwargs):
            assert not state["active"]
            loads.append(1)

        def transcribe(self, *args, **kwargs):
            assert not state["active"]
            return iter([segment("controlled words", 0, 0)]), None

    monkeypatch.setattr(
        worker, "Recognizer", lambda path: local.Recognizer(path, model_factory=Model)
    )
    commands = iter([
        {"mode": "session"},
        {"id": "initial", "mode": "capabilities"},
        {"id": "one", "mode": "start", "engine": "en"},
        {"id": "during", "mode": "capabilities"},
        {"id": "overlap", "mode": "start", "engine": "en"},
        {"id": "one", "mode": "stop"},
        {"id": "changed", "mode": "capabilities"},
        {"id": "two", "mode": "start", "engine": "en"},
        {"id": "two", "mode": "stop"},
    ])

    class Source:
        def readline(self, limit):
            command = next(commands, None)
            if command is None:
                return ""
            if command.get("id") == "changed":
                state.update(rows=[dict(state["rows"][0], name="Replacement input")], default=0)
            return json.dumps(command) + "\n"

    output = io.StringIO()
    assert worker.serve(Source(), output, tmp_path) == 0
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[3] == {"id": "during", "error": "unavailable", "idle": False}
    assert replies[4] == {"id": "overlap", "error": "unavailable", "idle": False}
    assert "Replacement input" in replies[6]["devices"][0]["label"]
    assert [row["text"] for row in replies if "text" in row] == ["controlled words"] * 2
    assert state["refreshes"] == 4 and loads == [1]
    assert len([call for call in calls if call[0] == "open"]) == 2
    assert not state["active"]


def test_input_selection_is_metadata_only_and_recording_keeps_native_rate_in_memory():
    sound, calls = sound_fixture()
    choices = local.input_devices(sound)
    assert calls == []
    recording = local.Recording(choices[1]["id"], sound=sound)
    assert calls == []
    recording.start()
    assert calls[0][1]["device"] == 0 and calls[0][1]["samplerate"] == 48000
    audio = recording.stop()
    with wave.open(audio, "rb") as pcm:
        assert pcm.getframerate() == 48000
        assert pcm.getnframes() == 480
    assert sound.default.device == [0, 1]
    assert recording.chunks == [] and recording.stream is None


def test_changed_input_identity_is_rejected_instead_of_using_a_different_microphone():
    sound, calls = sound_fixture()
    selected = local.input_devices(sound)[1]["id"]
    sound.query_devices = lambda: [
        {
            "name": "Different input",
            "max_input_channels": 1,
            "hostapi": 0,
            "default_samplerate": 48000,
        }
    ]
    with pytest.raises(SpeechError, match="device"):
        local.Recording(selected, sound=sound)
    assert calls == []


def test_permission_failure_is_safe_and_cancel_releases_the_stream_and_memory():
    sound, calls = sound_fixture()
    recording = local.Recording("default", sound=sound)
    recording.start()
    assert recording.frames > 0
    recording.cancel()
    assert recording.stream is None and recording.chunks == [] and recording.frames == 0
    assert calls[-2:] == [("abort",), ("close",)]
    sound.InputStream = lambda **options: (_ for _ in ()).throw(
        PermissionError("private OS details")
    )
    with pytest.raises(SpeechError) as error:
        local.Recording("default", sound=sound).start()
    assert str(error.value) == "permission"


def test_eof_cancels_capture_without_transcription_or_sending_text(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "model_ready", lambda _: True)

    class Capture:
        def __init__(self, device):
            calls.append(("device", device))

        def start(self):
            calls.append(("start",))

        def cancel(self):
            calls.append(("cancel",))

        def stop(self):
            pytest.fail("EOF must not decode")

    monkeypatch.setattr(worker, "Recording", Capture)
    output = io.StringIO()
    assert (
        worker.serve(
            io.StringIO('{"mode":"start","engine":"en","device":"default"}\n'), output, tmp_path
        )
        == 0
    )
    assert json.loads(output.getvalue()) == {"status": "recording"}
    assert calls == [("device", "default"), ("start",), ("cancel",)]


def test_recording_is_bounded_and_overflow_never_returns_corrupted_audio(monkeypatch):
    import numpy as np

    monkeypatch.setattr(local, "MAX_SECONDS", 1)
    sound, _ = sound_fixture()
    recording = local.Recording("default", sound=sound)
    recording.start()
    with pytest.raises(sound.CallbackStop):
        recording.stream.callback(np.zeros((96000, 1), dtype="float32"), 96000, None, None)
    assert recording.frames == 48000
    recording.cancel()
    recording = local.Recording("default", sound=sound)
    recording.start()
    recording.stream.callback(np.zeros((100, 1), dtype="float32"), 100, None, True)
    with pytest.raises(SpeechError, match="device"):
        recording.stop()
    assert recording.chunks == [] and recording.stream is None


def test_unsupported_language_never_falls_back_to_a_different_language(tmp_path, monkeypatch):
    monkeypatch.setattr(local, "model_ready", lambda _: True)

    class Model:
        supported_languages = ["en"]

        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            pytest.fail("unsupported choice must not decode")

    with pytest.raises(SpeechError, match="language"):
        transcribe(object(), tmp_path, "invalid", model_factory=Model)


def test_packaging_collects_pinned_runtime_assets_and_only_declared_notices(tmp_path, monkeypatch):
    import importlib.util
    import os
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "dictation_builder", root / "src-tauri/scripts/build_owner_sidecar.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    monkeypatch.setattr(builder, "version", builder._DICTATION_VERSIONS.__getitem__)
    hook = tmp_path / "src-tauri/scripts/hooks/hook-sounddevice.py"
    hook.parent.mkdir(parents=True)
    hook.write_text("# synthetic hook", encoding="utf-8")
    notices = tmp_path / "licenses/dictation"
    notices.mkdir(parents=True)
    for name in builder._DICTATION_NOTICES:
        (notices / name).write_text("Synthetic notice", encoding="utf-8")
    (notices / "private-note.txt").write_text("Must not ship", encoding="utf-8")
    arguments = builder.dictation_build_arguments(tmp_path)
    pairs = list(zip(arguments[::2], arguments[1::2], strict=True))
    assert {value for flag, value in pairs if flag == "--collect-all"} == {
        "faster_whisper",
        "ctranslate2",
        "sounddevice",
        "onnxruntime",
        "av",
    }
    assert ("--additional-hooks-dir", str(hook.parent)) in pairs
    assert {
        Path(value.rsplit(os.pathsep, 1)[0]).name for flag, value in pairs if flag == "--add-data"
    } == set(builder._DICTATION_NOTICES)
    monkeypatch.setattr(builder, "version", lambda _: "0.0.invalid")
    with pytest.raises(RuntimeError, match="locked dictation extra"):
        builder.dictation_build_arguments(tmp_path)


def test_worker_runtime_cache_is_scoped_to_its_selected_profile(tmp_path, monkeypatch):
    import os
    import sys

    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))
    observed = []
    monkeypatch.setattr(
        worker, "serve", lambda source, output, directory: observed.append(directory) or 0
    )
    for key in [
        "HF_HOME",
        "HF_XET_CACHE",
        "HF_HUB_OFFLINE",
        "HF_HUB_DISABLE_TELEMETRY",
        "HF_HUB_DISABLE_XET",
        "TOKENIZERS_PARALLELISM",
    ]:
        monkeypatch.delenv(key, raising=False)
    assert worker.main([str(tmp_path)]) == 0
    assert observed == [tmp_path]
    assert os.environ["HF_HOME"] == str(tmp_path / ".runtime")
    assert os.environ["HF_XET_CACHE"] == str(tmp_path / ".runtime/xet")
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert os.environ["HF_HUB_DISABLE_XET"] == "1"


def test_worker_bootstrap_needs_no_home_or_owner_imports(tmp_path):
    import os
    import subprocess
    import sys

    script = """
import sys
from sylvia_gm import local_dictation_process as worker
assert 'sylvia_gm.interfaces' not in sys.modules
raise SystemExit(worker.main([sys.argv[1]]))
"""
    environment = {
        key: os.environ[key]
        for key in ["SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "LOCALAPPDATA"]
        if key in os.environ
    }
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        input='{"mode":"start","engine":"en","device":"default"}\n',
        text=True,
        capture_output=True,
        env=environment,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 1
    assert result.stderr == ""
    assert json.loads(result.stdout) == {"error": "model"}
