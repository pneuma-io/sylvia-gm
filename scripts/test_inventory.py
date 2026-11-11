"""Tests awaiting local-gate triage. Remove entries; assign new tests directly to a group."""

# These browser checks receive real fixture backends from their Python test owners.
JAVASCRIPT_HARNESSES = {
    'tests/webui_expiry_recovery.cjs': 'tests/test_webui_private_proxy.py',
    'tests/webui_logout_loss.cjs': 'tests/test_webui_private_proxy.py',
    'tests/webui_private_proxy.cjs': 'tests/test_webui_private_proxy.py',
    'tests/weekend_candidate_lite.cjs': 'tests/test_weekend_candidate.py',
}

UNASSIGNED_PYTHON = ()

UNASSIGNED_JAVASCRIPT = ()
