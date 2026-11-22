"""Generate reference vectors for sylvia_owner::dictation from the frozen Python snapshot.

Run with the snapshot's own Windows interpreter (desktop dictation is Windows-only), from the
repository root, after `uv sync --extra dev --extra dictation`:

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_dictation_vectors.py

Each scenario runs the real `local_dictation_process.serve` and `local_dictation` code with
scripted stand-ins for what they import: `sounddevice` (PortAudio's device list and input
streams), `faster_whisper` (the model and its language table), `urllib.request.urlopen` and
`shutil.disk_usage`. Input arrives as the desktop writes it, one line per pipe read, through
the same `TextIOWrapper` set-up as Windows `sys.stdin` (universal newlines); replies go through
a `TextIOWrapper` that translates newlines as Windows `sys.stdout` does. Recorded: the reply
bytes, the exit code, every call the code made on its stand-ins in order, and the files left in
the model folder.

`processes` also runs the real worker as a child process (`python -m
sylvia_gm.local_dictation_process`) on inputs that never reach the speech runtime.

No audio of a person is used: recordings are the scripted sample values below. This script is
reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import hashlib
import io
import json
import msvcrt
import os
import shutil
import subprocess
import sys
import tempfile
import types
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
from faster_whisper.tokenizer import _LANGUAGE_CODES  # noqa: E402

from sylvia_gm import local_dictation as local  # noqa: E402
from sylvia_gm import local_dictation_process as worker  # noqa: E402

OUTPUT = Path(__file__).with_name("dictation_vectors.json")
LANGUAGE_CODES = list(_LANGUAGE_CODES)


class Calls(list):
    pass


class FakeStream:
    def __init__(self, sound, options):
        self.sound = sound
        self.callback = options["callback"]

    def _maybe_fail(self, name):
        failure = self.sound.state["fail"].get(name)
        if failure == "permission":
            raise PermissionError("scripted")
        if failure:
            raise RuntimeError("scripted")

    def start(self):
        self.sound.calls.append(["start"])
        self._maybe_fail("start")
        for delivery in self.sound.state["deliveries"]:
            if "values" in delivery:
                values = [float(v) if isinstance(v, str) else v for v in delivery["values"]]
                data = np.array(values, dtype="float32").reshape(-1, 1)
            else:
                data = np.zeros((delivery["frames"], 1), dtype="float32")
            status = True if delivery.get("status") else None
            self.sound.calls.append(["deliver", len(data), bool(status)])
            try:
                self.callback(data, len(data), None, status)
            except self.sound.CallbackStop:
                self.sound.calls.append(["callback_stop"])
                break

    def stop(self):
        self.sound.calls.append(["stop"])
        self._maybe_fail("stop")

    def abort(self):
        self.sound.calls.append(["abort"])
        self._maybe_fail("abort")

    def close(self):
        self.sound.calls.append(["close"])
        self._maybe_fail("close")


class FakeSound(types.ModuleType):
    """The parts of `sounddevice` that local_dictation.py uses."""

    def __init__(self, spec, calls):
        super().__init__("sounddevice")
        self.calls = calls
        self.state = {
            "rows": spec.get("rows", []),
            "hosts": spec.get("hosts", []),
            "default": spec.get("default", 0),
            "fail_initialize": spec.get("fail_initialize", False),
            "fail": dict(spec.get("fail", {})),
            "deliveries": spec.get("deliveries", []),
        }
        self._initialized = spec.get("initialized", 1)
        self.CallbackStop = type("CallbackStop", (Exception,), {})

    def apply(self, mutation):
        for key, value in mutation.items():
            if key == "fail":
                self.state["fail"] = dict(value)
            elif key in self.state:
                self.state[key] = value

    def _terminate(self):
        self.calls.append(["terminate"])
        self._initialized = 0

    def _initialize(self):
        self.calls.append(["initialize"])
        if self.state["fail_initialize"]:
            raise RuntimeError("scripted")
        self._initialized = 1

    def query_devices(self):
        return [dict(row) for row in self.state["rows"]]

    def query_hostapis(self):
        return [{"name": name} for name in self.state["hosts"]]

    @property
    def default(self):
        return SimpleNamespace(device=[self.state["default"], -1])

    def InputStream(self, **options):  # noqa: N802 (sounddevice's name)
        self.calls.append(
            ["open", options["device"], options["samplerate"], options["channels"], options["dtype"]]
        )
        failure = self.state["fail"].get("open")
        if failure == "permission":
            raise PermissionError("scripted")
        if failure:
            raise RuntimeError("scripted")
        return FakeStream(self, options)


def whisper_module(spec, calls, directory):
    """`faster_whisper` with a scripted `WhisperModel`, or without one (import fails)."""
    module = types.ModuleType("faster_whisper")
    if spec is None:
        return module
    state = {"fail_loads": spec.get("fail_loads", 0), "decodes": list(spec.get("decodes", []))}

    class WhisperModel:
        def __init__(self, path, **options):
            calls.append(["load", path == str(directory), options])
            if state["fail_loads"]:
                state["fail_loads"] -= 1
                raise RuntimeError("scripted")
            self.supported_languages = spec.get("supported", LANGUAGE_CODES)

        def transcribe(self, audio, **options):
            data = audio.getvalue()
            calls.append(["transcribe", hashlib.sha256(data).hexdigest(), len(data), options])
            decode = state["decodes"].pop(0) if state["decodes"] else {"segments": []}
            if decode.get("raise_call"):
                raise RuntimeError("scripted")

            def segments():
                for index, row in enumerate(decode.get("segments", [])):
                    if decode.get("raise_at") == index:
                        raise RuntimeError("scripted")
                    row = dict(row)
                    if "repeat" in row:
                        row["text"] = row.pop("repeat") * row.pop("times") + row.pop("suffix")
                    yield SimpleNamespace(**row)
                if decode.get("raise_at") == len(decode.get("segments", [])):
                    raise RuntimeError("scripted")

            return segments(), None

    module.WhisperModel = WhisperModel
    return module


class LineReads(io.RawIOBase):
    """A pipe the desktop writes one line at a time: each read returns at most one line."""

    def __init__(self, data):
        self.data = data
        self.at = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        end = self.data.find(b"\n", self.at)
        end = len(self.data) if end < 0 else end + 1
        chunk = self.data[self.at : min(end, self.at + len(buffer))]
        buffer[: len(chunk)] = chunk
        self.at += len(chunk)
        return len(chunk)


class Source:
    def __init__(self, data, mutations, sound, directory):
        self.text = io.TextIOWrapper(
            io.BufferedReader(LineReads(data)), encoding="utf-8", errors="strict", newline=None
        )
        self.mutations = mutations
        self.sound = sound
        self.directory = directory
        self.count = 0

    def readline(self, limit):
        mutation = self.mutations.get(str(self.count), {})
        self.count += 1
        if mutation.get("delete_model"):
            for name in list(local.MODEL_FILES):
                (self.directory / name).unlink(missing_ok=True)
        if self.sound is not None:
            self.sound.apply({k: v for k, v in mutation.items() if k != "delete_model"})
        return self.text.readline(limit)


def folder_listing(directory):
    return sorted(
        [path.relative_to(directory).as_posix(), path.stat().st_size]
        for path in directory.rglob("*")
        if path.is_file()
    )


def run(scenario):
    calls = Calls()
    directory = Path(tempfile.mkdtemp(prefix="dictation-vectors-")) / "model"
    directory.mkdir()
    files = {name: text.encode("utf-8") for name, text in scenario.get("model", {}).items()}
    spec = {name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in files.items()}
    for name, content in scenario.get("present", {}).items():
        (directory / name).write_bytes(content.encode("utf-8"))
    local.MODEL_FILES = spec
    worker.MODEL_FILES = spec

    sound = None if scenario.get("sound") is None else FakeSound(scenario["sound"], calls)
    sys.modules["sounddevice"] = sound
    tokenizer = types.ModuleType("faster_whisper.tokenizer")
    tokenizer._LANGUAGE_CODES = LANGUAGE_CODES
    sys.modules["faster_whisper"] = whisper_module(scenario.get("whisper"), calls, directory)
    sys.modules["faster_whisper.tokenizer"] = tokenizer if scenario.get("languages", True) else None

    responses = list(scenario.get("fetch", []))

    def urlopen(request, timeout=None):
        calls.append(["fetch", request.full_url, request.get_header("User-agent"), timeout])
        response = responses.pop(0)
        error = response.get("error")
        if error == "os":
            raise OSError("scripted")
        if error == "timeout":
            raise TimeoutError("scripted")
        if error == "other":
            raise RuntimeError("scripted")
        return io.BytesIO(response["body"].encode("utf-8"))

    def disk_usage(path):
        calls.append(["disk_usage", Path(path) == directory])
        if scenario.get("free") == "os":
            raise OSError("scripted")
        return SimpleNamespace(free=scenario.get("free", 10**12))

    urllib.request.urlopen = urlopen
    shutil.disk_usage = disk_usage
    held = None
    if scenario.get("locked"):
        held = (directory / ".download.lock").open("a+b")
        held.write(b"0")
        held.flush()
        held.seek(0)
        msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)

    data = scenario["input"].encode("utf-8") if "input" in scenario else bytes.fromhex(scenario["input_hex"])
    source = Source(data, scenario.get("mutations", {}), sound, directory)
    output = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict", newline=None)
    try:
        code = worker.serve(source, output, directory)
    except Exception as error:  # an exception escaping serve is behaviour too
        code = f"raised {type(error).__name__}"
    output.flush()
    if held is not None:
        held.seek(0)
        msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
        held.close()
    written = output.buffer.getvalue()
    # A reply too long to keep readable is recorded by its digest.
    shown = {"output": written.decode("utf-8")} if len(written) < 20_000 else {
        "output_sha256": hashlib.sha256(written).hexdigest(), "output_bytes": len(written)}
    result = {
        "exit": code,
        **shown,
        "calls": calls,
        "files": folder_listing(directory),
    }
    shutil.rmtree(directory.parent)
    return result


def line(value):
    return json.dumps(value) + "\n"


MIC = {"name": "Scripted microphone", "max_input_channels": 1, "hostapi": 0, "default_samplerate": 48000}
SLOW = {"name": "Slow input", "max_input_channels": 2, "hostapi": 1, "default_samplerate": 4}
SPEAKER = {"name": "Scripted speakers", "max_input_channels": 0, "hostapi": 0, "default_samplerate": 48000}
HOSTS = ["MME", "Windows WASAPI"]
MODEL = {"config.json": "{}", "model.bin": "controlled model bytes"}


def sound(**changes):
    base = {"rows": [SPEAKER, MIC, SLOW], "hosts": HOSTS, "default": 1, "deliveries": [{"frames": 480}]}
    base.update(changes)
    return base


def segment(text, no_speech=0.0, logprob=0.0):
    return {"text": text, "no_speech_prob": no_speech, "avg_logprob": logprob}


def device_id(index, row):
    return local._device_id(index, row, HOSTS[row["hostapi"]])


SPOKEN = {"decodes": [{"segments": [segment(" controlled words ")]}]}
SAMPLES = [0.0, 0.25, -0.25, 0.5, -0.5, 1.0, -1.0, 1.5, -1.5, 0.999, -0.999, 1e-05, -1e-05,
           0.333333, -0.333333, 3.0517578125e-05, "nan", "inf", "-inf"]


def scenarios():
    mic = device_id(1, MIC)
    slow = device_id(2, SLOW)
    ready = {"model": MODEL, "present": MODEL}
    start = line({"mode": "start", "engine": "en"})
    yield "capabilities, no model", {"input": line({"mode": "capabilities"}), "sound": sound(), "model": MODEL}
    yield "capabilities, model ready", {"input": line({"mode": "capabilities"}), "sound": sound(
        rows=[dict(MIC, default_samplerate=44100.0), dict(SLOW, hostapi=-1)], default=0), **ready}
    yield "capabilities, a corrupt model file", {"input": line({"mode": "capabilities"}), "sound": sound(),
        "model": MODEL, "present": {"config.json": "{}", "model.bin": "controlled model bytez"}}
    yield "capabilities, PortAudio not initialized", {"input": line({"mode": "capabilities"}),
        "sound": sound(initialized=0)}
    yield "capabilities, no sounddevice", {"input": line({"mode": "capabilities"}), "sound": None}
    yield "capabilities, no language table", {"input": line({"mode": "capabilities"}), "sound": sound(),
        "languages": False}
    yield "capabilities, initialization fails", {"input": line({"mode": "capabilities"}),
        "sound": sound(fail_initialize=True)}
    yield "capabilities, default unavailable", {"input": line({"mode": "capabilities"}),
        "sound": sound(default=0)}
    yield "capabilities, default -1", {"input": line({"mode": "capabilities"}), "sound": sound(default=-1)}
    yield "capabilities, a host index out of range", {"input": line({"mode": "capabilities"}),
        "sound": sound(rows=[dict(MIC, hostapi=7)])}
    yield "capabilities, CRLF", {"input": '{"mode":"capabilities"}\r\n', "sound": sound()}
    yield "capabilities, CR", {"input": '{"mode":"capabilities"}\r', "sound": sound()}
    yield "capabilities, duplicate keys", {"input": '{"mode":"start","mode":"capabilities"}\n', "sound": sound()}
    yield "unknown mode", {"input": line({"mode": "listen"}), "sound": sound()}
    yield "mode not a string", {"input": line({"mode": 1}), "sound": sound()}
    yield "no mode", {"input": line({}), "sound": sound()}
    yield "request is a list", {"input": line(["capabilities"]), "sound": sound()}
    yield "request is a string", {"input": line("capabilities"), "sound": sound()}
    yield "invalid JSON", {"input": "{mode}\n", "sound": sound()}
    yield "empty input", {"input": "", "sound": sound()}
    yield "no newline", {"input": '{"mode":"capabilities"}', "sound": sound()}
    yield "blank line", {"input": "\n", "sound": sound()}
    yield "8192 bytes", {"input": '{"mode":"capabilities","pad":"' + "x" * 8159 + '"}\n', "sound": sound()}
    yield "8193 bytes", {"input": '{"mode":"capabilities","pad":"' + "x" * 8160 + '"}\n', "sound": sound()}
    yield "8193 characters", {"input": '{"mode":"capabilities","pad":"' + "x" * 8200 + '"}\n', "sound": sound()}
    yield "multibyte over 8192 bytes", {"input": '{"mode":"capabilities","pad":"' + "é" * 4100 + '"}\n',
        "sound": sound()}
    yield "invalid UTF-8", {"input_hex": b'{"mode":"capabilities","x":"\xff"}\n'.hex(), "sound": sound()}
    yield "truncated UTF-8 at the end", {"input_hex": b'{"mode":"capabilities"}\xc3'.hex(), "sound": sound()}

    yield "start, no model", {"input": start, "sound": sound(), "model": MODEL}
    yield "start, engine missing", {"input": line({"mode": "start"}), "sound": sound(), **ready}
    yield "start, device not a string", {"input": line({"mode": "start", "engine": "en", "device": 3}),
        "sound": sound(), **ready}
    yield "start, device null", {"input": line({"mode": "start", "engine": "en", "device": None}),
        "sound": sound(), **ready}
    yield "start, unknown language", {"input": line({"mode": "start", "engine": "xx"}), "sound": sound(), **ready}
    yield "start, the hundredth language", {"input": line({"mode": "start", "engine": "yue"}),
        "sound": sound(), **ready}
    yield "start, no language table", {"input": start, "sound": sound(), "languages": False, **ready}
    yield "start, no sounddevice", {"input": start, "sound": None, **ready}
    yield "start, unknown device", {"input": line({"mode": "start", "engine": "en", "device": "d-9-x"}),
        "sound": sound(), **ready}
    yield "start, default unavailable", {"input": start, "sound": sound(default=0), **ready}
    yield "start, then end of input", {"input": start, "sound": sound(), **ready}
    yield "start, then another action", {"input": start + "stopx\n", "sound": sound(), **ready}
    yield "start, then a long action", {"input": start + "stop and keep going\n", "sound": sound(), **ready}
    yield "start, stop, text", {"input": start + "stop\n", "whisper": {"decodes": [{"segments": [
        segment(" quiet real speech ", 0.9, -0.5), segment("\x1cuncertain real speech　", 0.1, -1.5),
        segment("silence hallucination", 0.9, -1.5), segment("  ")]}]},
        "sound": sound(deliveries=[{"values": SAMPLES}, {"frames": 7}]), **ready}
    yield "start, stop, chosen slow device and its cap", {"input": line({"mode": "start", "engine": "auto",
        "device": slow}) + "stop\n", "whisper": SPOKEN, "sound": sound(deliveries=[
            {"frames": 300}, {"values": [0.5] * 250}, {"frames": 10}]), **ready}
    yield "start, stop, exactly at the cap", {"input": line({"mode": "start", "engine": "en",
        "device": slow}) + "stop\n", "whisper": SPOKEN, "sound": sound(deliveries=[
            {"frames": 480}, {"frames": 1}]), **ready}
    yield "start, stop, nothing recorded", {"input": start + "stop\n", "whisper": SPOKEN,
        "sound": sound(deliveries=[]), **ready}
    yield "start, stop, an empty block", {"input": start + "stop\n", "whisper": SPOKEN,
        "sound": sound(deliveries=[{"frames": 0}]), **ready}
    yield "start, stop, input overflow", {"input": start + "stop\n", "whisper": SPOKEN,
        "sound": sound(deliveries=[{"frames": 5}, {"frames": 5, "status": True}]), **ready}
    yield "start, open refused", {"input": start, "sound": sound(fail={"open": "permission"}), **ready}
    yield "start, open fails", {"input": start, "sound": sound(fail={"open": "error"}), **ready}
    yield "start, start refused", {"input": start, "sound": sound(fail={"start": "permission"}), **ready}
    yield "start, start fails and abort fails", {"input": start, "sound": sound(
        fail={"start": "error", "abort": "error"}), **ready}
    yield "start, stop fails", {"input": start + "stop\n", "whisper": SPOKEN,
        "sound": sound(fail={"stop": "error"}), **ready}
    yield "start, close fails", {"input": start + "stop\n", "whisper": SPOKEN,
        "sound": sound(fail={"close": "error"}), **ready}
    yield "start, stop, no WhisperModel", {"input": start + "stop\n", "whisper": None, "sound": sound(), **ready}
    yield "start, stop, load fails", {"input": start + "stop\n", "whisper": {"fail_loads": 1},
        "sound": sound(), **ready}
    yield "start, stop, language the model lacks", {"input": start + "stop\n", "whisper": {"supported": ["de"]},
        "sound": sound(), **ready}
    yield "start, stop, decode call fails", {"input": start + "stop\n", "whisper": {"decodes": [
        {"raise_call": True}]}, "sound": sound(), **ready}
    yield "start, stop, fails mid-segments", {"input": start + "stop\n", "whisper": {"decodes": [
        {"segments": [segment("partial private words")], "raise_at": 1}]}, "sound": sound(), **ready}
    yield "start, stop, only silence", {"input": start + "stop\n", "whisper": {"decodes": [
        {"segments": [segment("hallucination", 0.61, -1.01)]}]}, "sound": sound(), **ready}
    yield "start, stop, no segments", {"input": start + "stop\n", "whisper": {"decodes": [{"segments": []}]},
        "sound": sound(), **ready}
    yield "start, stop, 128000 bytes", {"input": start + "stop\n", "whisper": {"decodes": [
        {"segments": [dict(segment(""), repeat="é", times=64000, suffix="")]}]}, "sound": sound(), **ready}
    yield "start, stop, over 128000 bytes", {"input": start + "stop\n", "whisper": {"decodes": [
        {"segments": [dict(segment(""), repeat="é", times=64000, suffix="x")]}]}, "sound": sound(), **ready}

    prepare = line({"mode": "prepare"})
    yield "prepare, downloads what is missing", {"input": prepare, "model": MODEL,
        "present": {"config.json": "{}"}, "fetch": [{"body": "controlled model bytes"}]}
    yield "prepare, replaces a corrupt file", {"input": prepare, "model": MODEL,
        "present": {"config.json": "{]", "model.bin": "x"}, "fetch": [{"body": "{}"},
            {"body": "controlled model bytes"}]}
    yield "prepare, nothing missing", {"input": prepare, **ready}
    yield "prepare, wrong bytes", {"input": prepare, "model": MODEL, "present": {"config.json": "{}"},
        "fetch": [{"body": "controlled model bytez"}]}
    yield "prepare, short body", {"input": prepare, "model": MODEL, "present": {"config.json": "{}"},
        "fetch": [{"body": "controlled"}]}
    yield "prepare, extra bytes", {"input": prepare, "model": MODEL, "present": {"config.json": "{}"},
        "fetch": [{"body": "controlled model bytes!"}]}
    yield "prepare, network error", {"input": prepare, "model": MODEL, "fetch": [{"error": "os"}]}
    yield "prepare, timeout", {"input": prepare, "model": MODEL, "fetch": [{"error": "timeout"}]}
    yield "prepare, other error", {"input": prepare, "model": MODEL, "fetch": [{"error": "other"}]}
    yield "prepare, low storage", {"input": prepare, "model": MODEL, "free": 64_000_047}
    yield "prepare, just enough storage", {"input": prepare, "model": MODEL, "free": 64_000_048,
        "fetch": [{"body": "{}"}, {"body": "controlled model bytes"}]}
    yield "prepare, free space unreadable", {"input": prepare, "model": MODEL, "free": "os"}
    yield "prepare, busy", {"input": prepare, "model": MODEL, "locked": True}
    yield "prepare, an existing lock file", {"input": prepare, "model": MODEL,
        "present": {".download.lock": "7", "config.json": "{}", "model.bin": "controlled model bytes"}}

    session = line({"mode": "session"})

    def ask(identity, mode, **fields):
        return line({"id": identity, "mode": mode, **fields})

    yield "session, a full exchange", {"input": session + ask("metadata", "capabilities")
        + ask("one", "start", engine="en") + ask("during", "capabilities") + ask("overlap", "start", engine="en")
        + ask("old", "stop") + ask("one", "stop") + ask("changed", "capabilities")
        + ask("two", "start", engine="auto", device=mic) + ask("two", "stop"),
        "mutations": {"7": {"rows": [dict(MIC, name="Replacement input")], "default": 0}},
        "whisper": {"decodes": [{"segments": [segment("first words")]}, {"segments": [segment("second words")]}]},
        "sound": sound(), **ready}
    yield "session, end of input while recording", {"input": session + ask("one", "start", engine="en"),
        "sound": sound(), **ready}
    yield "session, end of input at once", {"input": session, "sound": sound()}
    yield "session, stop with nothing running", {"input": session + ask("a", "stop"), "sound": sound()}
    yield "session, unknown mode", {"input": session + ask("a", "prepare"), "sound": sound()}
    yield "session, an id too long", {"input": session + ask("a" * 65, "capabilities"), "sound": sound()}
    yield "session, an id of 64", {"input": session + ask("A-" * 32, "capabilities"), "sound": sound()}
    yield "session, an empty id", {"input": session + ask("", "capabilities"), "sound": sound()}
    yield "session, a non-ASCII id", {"input": session + ask("été", "capabilities"), "sound": sound()}
    yield "session, an id with a space", {"input": session + ask("a b", "capabilities"), "sound": sound()}
    yield "session, a numeric id", {"input": session + line({"id": 7, "mode": "capabilities"}), "sound": sound()}
    yield "session, invalid JSON", {"input": session + "{\n", "sound": sound()}
    yield "session, a list", {"input": session + "[]\n", "sound": sound()}
    yield "session, invalid JSON while recording", {"input": session + ask("one", "start", engine="en") + "{\n",
        "sound": sound(), **ready}
    yield "session, a line too long", {"input": session + ask("a", "capabilities", pad="x" * 8200),
        "sound": sound()}
    yield "session, no newline", {"input": session + '{"id":"a","mode":"capabilities"}', "sound": sound()}
    yield "session, invalid UTF-8", {"input_hex": (session.encode() + b'{"id":"a","mode":"\xfe"}\n').hex(),
        "sound": sound()}
    yield "session, CR-separated lines", {"input": '{"mode":"session"}\r{"id":"a","mode":"capabilities"}\r\n'
        + '{"id":"b","mode":"stop"}\r', "sound": sound()}
    yield "session, no model", {"input": session + ask("one", "start", engine="en"), "sound": sound(), "model": MODEL}
    yield "session, bad language", {"input": session + ask("one", "start", engine="xx", device=4)
        + ask("two", "start") + ask("three", "start", engine="yue"), "sound": sound(), **ready}
    yield "session, bad device", {"input": session + ask("one", "start", engine="en", device=None)
        + ask("two", "start", engine="en", device="d-1-0000000000000000"), "sound": sound(), **ready}
    yield "session, no language table", {"input": session + ask("one", "start", engine="en"),
        "sound": sound(), "languages": False, **ready}
    yield "session, no sounddevice", {"input": session + ask("one", "start", engine="en")
        + ask("meta", "capabilities"), "sound": None, **ready}
    yield "session, start refused then allowed", {"input": session + ask("one", "start", engine="en")
        + ask("two", "start", engine="en") + ask("two", "stop"),
        "mutations": {"2": {"fail": {}}}, "whisper": SPOKEN, "sound": sound(fail={"start": "permission"}), **ready}
    yield "session, start fails, abort fails", {"input": session + ask("one", "start", engine="en"),
        "sound": sound(fail={"start": "error", "abort": "error"}), **ready}
    yield "session, model kept, then dropped", {"input": session + ask("one", "start", engine="en")
        + ask("one", "stop") + ask("two", "start", engine="en") + ask("two", "stop")
        + ask("three", "start", engine="en") + ask("three", "stop") + ask("four", "start", engine="en")
        + ask("four", "stop") + ask("five", "start", engine="en") + ask("five", "stop"),
        "whisper": {"decodes": [{"segments": [segment("one")]}, {"segments": []}, {"raise_call": True},
            {"segments": [segment("four")]}, {"segments": [segment("five")]}]},
        "sound": sound(), **ready}
    yield "session, load fails then succeeds", {"input": session + ask("one", "start", engine="en")
        + ask("one", "stop") + ask("two", "start", engine="en") + ask("two", "stop"),
        "whisper": {"fail_loads": 1, "decodes": [{"segments": [segment("two")]}]}, "sound": sound(), **ready}
    yield "session, a language the model lacks keeps it", {"input": session + ask("one", "start", engine="de")
        + ask("one", "stop") + ask("two", "start", engine="en") + ask("two", "stop"),
        "whisper": {"supported": ["en"], "decodes": [{"segments": [segment("two")]}]}, "sound": sound(), **ready}
    yield "session, model removed between attempts", {"input": session + ask("one", "start", engine="en")
        + ask("one", "stop") + ask("two", "start", engine="en") + ask("two", "stop"),
        "mutations": {"3": {"delete_model": True}}, "whisper": {"decodes": [{"segments": [segment("one")]}]},
        "sound": sound(), **ready}
    yield "session, model removed while recording", {"input": session + ask("one", "start", engine="en")
        + ask("one", "stop") + ask("again", "capabilities"),
        "mutations": {"2": {"delete_model": True}}, "whisper": SPOKEN, "sound": sound(), **ready}
    yield "session, nothing recorded", {"input": session + ask("one", "start", engine="en") + ask("one", "stop"),
        "whisper": SPOKEN, "sound": sound(deliveries=[]), **ready}
    yield "session, stop fails", {"input": session + ask("one", "start", engine="en") + ask("one", "stop")
        + ask("two", "start", engine="en"), "whisper": SPOKEN, "sound": sound(fail={"stop": "error"}), **ready}
    yield "session, close fails", {"input": session + ask("one", "start", engine="en") + ask("one", "stop")
        + ask("two", "start", engine="en"), "whisper": SPOKEN, "sound": sound(fail={"close": "error"}), **ready}
    yield "session, device gone before start", {"input": session + ask("meta", "capabilities")
        + ask("one", "start", engine="en", device=mic), "mutations": {"2": {"rows": [SLOW], "default": 0}},
        "sound": sound(), **ready}
    yield "session, refresh fails then recovers", {"input": session + ask("one", "capabilities")
        + ask("two", "capabilities"), "mutations": {"1": {"fail_initialize": True},
            "2": {"fail_initialize": False}}, "sound": sound()}


def processes():
    """The real worker as a child process, on inputs that never reach the speech runtime."""
    environment = {key: os.environ[key] for key in ["SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "LOCALAPPDATA"]
                   if key in os.environ}
    cases = [
        ("no folder", [], ""),
        ("two folders", ["{model}", "{model}"], ""),
        ("start without a model", ["{model}"], line({"mode": "start", "engine": "en", "device": "default"})),
        ("unknown mode", ["{model}"], line({"mode": "listen"})),
        ("empty input", ["{model}"], ""),
        ("session, then end of input", ["{model}"], line({"mode": "session"})),
        ("session, a stop", ["{model}"], line({"mode": "session"}) + line({"id": "a", "mode": "stop"})),
        ("session, CRLF and CR", ["{model}"], '{"mode":"session"}\r\n{"id":"a","mode":"stop"}\r'
            + '{"id":"b","mode":"stop"}\n'),
        ("session, a bad id", ["{model}"], line({"mode": "session"}) + line({"id": "", "mode": "stop"})),
        ("session, invalid JSON", ["{model}"], line({"mode": "session"}) + "{\n"),
    ]
    results = []
    for name, arguments, text in cases:
        folder = Path(tempfile.mkdtemp(prefix="dictation-process-"))
        model = folder / "model"
        completed = subprocess.run(
            [sys.executable, "-I", "-m", "sylvia_gm.local_dictation_process",
             *[argument.format(model=model) for argument in arguments]],
            input=text.encode("utf-8"), capture_output=True, env=environment, cwd=ROOT / "src",
            timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        results.append({"name": name, "arguments": arguments, "input": text, "exit": completed.returncode,
                        "stdout": completed.stdout.decode("utf-8"), "stderr": completed.stderr.decode("utf-8"),
                        "files": folder_listing(folder)})
        shutil.rmtree(folder)
    return results


def main():
    rows = []
    for name, scenario in scenarios():
        rows.append({"name": name, **scenario, "expect": run(scenario)})
    vectors = {
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "language_codes": LANGUAGE_CODES,
        "device_ids": [
            {"index": index, "row": row, "host": host, "id": local._device_id(index, row, host)}
            for index, row, host in [
                (0, MIC, "MME"), (3, dict(MIC, default_samplerate=44100.0), "Windows WASAPI"),
                (12, dict(MIC, name="Micrófono é中", default_samplerate=22050.5), "Windows WDM-KS"),
                (1, dict(MIC, default_samplerate=1e16), "MME"),
            ]
        ],
        "scenarios": rows,
        "processes": processes(),
    }
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
