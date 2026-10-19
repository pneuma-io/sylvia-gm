"""Explicit Windows smoke: real local recognition of synthesized WAV, never a microphone.

Run with .venv/Scripts/python.exe tests/windows_dictation_smoke.py.
Only the production helper's input-device line is substituted in memory.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave


@unittest.skipUnless(sys.platform == "win32", "Windows desktop speech engine required")
class LocalDictationSmoke(unittest.TestCase):
    def test_installed_engine_returns_editable_text_without_microphone(self):
        script = (Path(__file__).parents[1] / "src-tauri/src/dictation.ps1").read_text(encoding="utf-8")
        executable = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")

        def launch(code):
            return subprocess.Popen(
                [executable, "-NoProfile", "-NonInteractive", "-Command", code],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
            )

        capabilities = launch(script)
        output, error = capabilities.communicate('{"mode":"capabilities"}\n', timeout=45)
        self.assertFalse(error)
        engines = json.loads(output).get("engines", [])
        if not engines:
            self.skipTest("No installed Microsoft desktop dictation language")

        with tempfile.TemporaryDirectory(prefix="sylvia-dictation-smoke-") as scratch:
            wav = Path(scratch) / "controlled.wav"
            synthesis = launch("""
                $ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Speech
                $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
                try {
                    $synth.SetOutputToWaveFile([Console]::ReadLine())
                    $synth.Speak('I walk north and open the door.')
                } finally { $synth.Dispose() }
            """)
            _, error = synthesis.communicate(str(wav) + "\n", timeout=30)
            self.assertEqual(synthesis.returncode, 0)
            self.assertFalse(error)
            with wave.open(str(wav)) as audio:
                duration = audio.getnframes() / audio.getframerate()
            self.assertEqual(script.count("engine.SetInputToDefaultAudioDevice();"), 1)
            controlled = script.replace("engine.SetInputToDefaultAudioDevice();",
                'engine.SetInputToWaveFile(@"' + str(wav).replace('"', '""') + '");')
            capture = launch(controlled)
            deadline = threading.Timer(45, capture.kill)
            deadline.start()
            try:
                capture.stdin.write(json.dumps({"mode": "start", "engine": engines[0]["id"]}) + "\n")
                capture.stdin.flush()
                self.assertEqual(json.loads(capture.stdout.readline()), {"status": "recording"})
                time.sleep(min(duration + 1, 10))
                capture.stdin.write("stop\n")
                capture.stdin.flush()
                result = json.loads(capture.stdout.readline())
                capture.wait(timeout=35)
                self.assertFalse(capture.stderr.read())
                self.assertIsInstance(result.get("text"), str)
                self.assertRegex(result["text"].lower(), r"\b(walk|door|north)\b")
                print("Controlled local recognition:", result["text"])
            finally:
                deadline.cancel()
                if capture.poll() is None:
                    capture.kill()
                    capture.wait()
                for pipe in (capture.stdin, capture.stdout, capture.stderr):
                    pipe.close()


if __name__ == "__main__":
    unittest.main()
