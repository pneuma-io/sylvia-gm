"""Reusable check selections and the explicit boundary of product qualification."""

from __future__ import annotations


HISTORICAL_CHECKS = {
    # The original stage7 checkpoint: whole modules, not a permanent case-count cap.
    "gm-turn-checkpoint": {
        "turn-provider-contracts": [
            "tests/test_alpha_f4_provider_session_adapter.py",
            "tests/test_alpha_f4_generic_sessions.py",
        ],
        "campaign-play": [
            "tests/test_campaign_rules_update.py",
            "tests/test_campaign_rules_update_bindings.py",
            "tests/test_campaign_rules_update_knowledge.py",
            "tests/test_action_sequences.py",
            "tests/test_action_sequence_provider.py",
            "tests/test_td_native_panel_repairs.py",
            "tests/test_fc_campaign_journey.py",
            "tests/test_fc_party_combat.py",
            "tests/test_fc_campaign_progress.py",
            "tests/test_migrated_plan_lifecycle.py",
            "tests/test_riichi_quick_test_plan.py",
            "tests/test_no_rules_interview.py",
        ],
    },
}

PRODUCT_CASE_EXCLUSIONS = {
    "tests/test_alpha_f4_generic_sessions.py::"
    "test_probe_25_durable_map_labels_all_frozen_probes_and_parent_owned_93_gate":
        "Historical milestone-label assertions; retained for the historical checkpoint, "
        "excluded from product qualification by the independent CCO review.",
}


def qualification_cases(nodeids, *, include_historical=False):
    """Keep every collected behavioral case; report deliberate historical exclusions."""
    required, excluded = [], []
    for nodeid in nodeids:
        reason = None if include_historical else PRODUCT_CASE_EXCLUSIONS.get(nodeid)
        if reason is None:
            required.append(nodeid)
        else:
            excluded.append({"nodeid": nodeid, "reason": reason})
    return required, excluded
