"""Exercise concurrent saved-chat user actions against the actual JavaScript renderer."""

import subprocess
from pathlib import Path


def test_saved_session_frontend_lifecycles() -> None:
    result = subprocess.run(
        [
            "node",
            "--test",
            str(Path(__file__).with_name("frontend_saved_sessions.test.cjs")),
            str(Path(__file__).with_name("frontend_campaign_party.test.cjs")),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
