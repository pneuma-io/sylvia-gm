"""Reusable check selections and the explicit boundary of product qualification."""

from __future__ import annotations

if __package__:
    from .platform_exclusions import platform_reason
else:
    from platform_exclusions import platform_reason


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
    **dict.fromkeys(
        (
            "tests/test_claude_native_wire.py::"
            "test_real_claude_code_requests_extend_each_other[per-step]",
            "tests/test_claude_native_wire.py::"
            "test_real_claude_code_requests_extend_each_other[persistent]",
            "tests/test_claude_native_wire.py::"
            "test_real_claude_code_fresh_start_on_player_speech_replays_the_kept_process",
        ),
        "Opt-in offline wire check with a real Claude Code binary (local fake endpoint, "
        "throwaway profile); it runs only when SYLVIA_TEST_CLAUDE_WIRE_BINARY names the binary "
        "and is run by hand, so it is never a required case.",
    ),
}


def qualification_cases(nodeids, *, include_historical=False, platform_exclusions=()):
    """Keep every collected behavioral case; report deliberate exclusions by exact ID.

    ``platform_exclusions`` are the collection's ``runs_on`` cases for other platforms. They
    cannot run here, so they are excluded from every kind of qualification, historical too.
    """
    platform = {entry["nodeid"]: entry for entry in platform_exclusions}
    unknown = sorted(set(platform) - set(nodeids))
    if unknown:
        raise ValueError(f"Platform exclusions name cases that were not collected: {unknown}")
    required, excluded = [], []
    for nodeid in nodeids:
        if nodeid in platform:
            entry = platform[nodeid]
            excluded.append({
                "nodeid": nodeid,
                "reason": platform_reason(entry["platforms"], entry["reason"]),
                "kind": "platform",
                "platforms": list(entry["platforms"]),
            })
            continue
        reason = None if include_historical else PRODUCT_CASE_EXCLUSIONS.get(nodeid)
        if reason is None:
            required.append(nodeid)
        else:
            excluded.append({"nodeid": nodeid, "reason": reason, "kind": "historical"})
    return required, excluded
