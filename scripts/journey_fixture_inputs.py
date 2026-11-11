"""Select current source fixtures while preserving explicit historical inputs."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def candidate_identity(environment):
    """Bind the selected external package bytes, including a missing-input state."""
    value = environment.get("SRD_LITE_CANDIDATE")
    if value is None:
        return {"path": None, "sha256": None}
    path = Path(value).resolve()
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
    }


def prepare_candidate(environment, root, output):
    """Materialize the actual public source once per invocation, outside the checkout."""
    if "SRD_LITE_CANDIDATE" in environment:
        return {"selected_by": "explicit", **candidate_identity(environment)}
    if not (root / "rulesets/srd-lite/package-record.json").is_file():
        # Synthetic runner harnesses may contain no Ruleset-dependent tests.
        return {"selected_by": "no source package", **candidate_identity(environment)}
    inputs = output.resolve() / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    destination = inputs / "source-srd-lite.sylvia-gm-package"
    with tempfile.NamedTemporaryFile(dir=inputs, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
    child = dict(environment)
    child["PYTHONPATH"] = os.pathsep.join([str(root / "src"), str(root)])
    program = (
        "from pathlib import Path; import sys; "
        "from sylvia_gm.application.bundled_packages import bundled_srd_archive; "
        "Path(sys.argv[1]).write_bytes(bundled_srd_archive())"
    )
    try:
        subprocess.run(
            [sys.executable, "-c", program, str(temporary)], cwd=root, env=child,
            capture_output=True, text=True, check=True,
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    environment["SRD_LITE_CANDIDATE"] = str(destination)
    return {"selected_by": "source default", **candidate_identity(environment)}
