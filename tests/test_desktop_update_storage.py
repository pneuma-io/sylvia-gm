"""Receipt publication must fit a supported final path on legacy Windows too."""

import json
from pathlib import Path

from sylvia_gm.application.desktop_update.storage import atomic_receipt


def test_recovery_ack_temporary_name_does_not_overflow_a_valid_windows_path(
    tmp_path, monkeypatch
):
    # The actual guest failed at a 203-character generation directory: its
    # 226-character ACK fitted, but the former temporary name reached 264.
    # Model that Windows prefix independently of pytest's checkout-dependent temp
    # location; the real receipt function still performs I/O in this owned directory.
    parent = tmp_path / "recovery-generation"
    parent.mkdir()
    receipt = parent / "startup-confirmed.json"

    def windows_length(path):
        return 203 + 1 + len(path.name)

    assert windows_length(receipt) == 226
    original_open = Path.open
    attempted = []

    def legacy_windows_open(path, *args, **kwargs):
        if path.parent == parent:
            attempted.append(path)
            if windows_length(path) >= 260:
                raise FileNotFoundError(2, "Legacy Windows DOS path limit", str(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", legacy_windows_open)
    atomic_receipt(receipt, {"phase": "confirmed", "nonce": "a" * 32})
    assert json.loads(receipt.read_bytes()) == {"phase": "confirmed", "nonce": "a" * 32}
    assert attempted and all(windows_length(path) < 260 for path in attempted)
    assert list(parent.iterdir()) == [receipt]
