"""Fresh local checks select the current source package and bind explicit input bytes."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "tests/fixtures/SRD-Lite-117-QA-Legacy-Contract-Clean.sylvia-gm-package"


def _fresh_defaults(output, candidate=None):
    environment = dict(os.environ)
    environment.pop("SRD_LITE_CANDIDATE", None)
    environment["SYLVIA_QA_FIXTURE_ROOT"] = str(output)
    environment["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
    if candidate is not None:
        environment["SRD_LITE_CANDIDATE"] = str(candidate)
    command = """import hashlib,json,os,runpy,zipfile
from pathlib import Path
runpy.run_path('conftest.py')
path = Path(os.environ['SRD_LITE_CANDIDATE'])
with zipfile.ZipFile(path) as archive:
    records = json.loads(archive.read('manifest.json'))['packages']
    record = next(row for row in records if row['id'] == 'sylvia.srd-lite.bg3')
print(json.dumps({
    'path':str(path.resolve()),'version':record['version'],
    'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", command], cwd=ROOT, env=environment,
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def test_fresh_plain_pytest_defaults_use_current_committed_lite(tmp_path):
    selected = _fresh_defaults(tmp_path / "fresh")
    current = json.loads((ROOT / "rulesets/srd-lite/package-record.json").read_text())
    assert selected["version"] == current["version"]
    assert Path(selected["path"]).is_relative_to(tmp_path)


def test_explicit_historical_candidate_is_preserved(tmp_path):
    before = hashlib.sha256(LEGACY.read_bytes()).hexdigest()
    selected = _fresh_defaults(tmp_path / "explicit", LEGACY)
    assert Path(selected["path"]) == LEGACY.resolve()
    assert selected["sha256"] == before
    assert hashlib.sha256(LEGACY.read_bytes()).hexdigest() == before


def test_runner_source_guard_includes_selected_external_candidate(tmp_path, monkeypatch):
    from scripts import check_supported_journeys as gate

    source = tmp_path / "checkout"
    source.mkdir()
    candidate = tmp_path / "selected.sylvia-gm-package"
    candidate.write_bytes(b"first selected input")
    monkeypatch.setattr(gate, "ROOT", source)
    monkeypatch.delenv("SYLVIA_EXPECTED_REVISION", raising=False)
    monkeypatch.setenv("SRD_LITE_CANDIDATE", str(candidate))
    before = gate.source_identity()
    candidate.write_bytes(b"changed selected input")
    assert gate.source_identity()["sha256"] != before["sha256"]
