from __future__ import annotations


# Approved hacking visibility and topology procedures leave no rejected
# specialty parameter rows. Keep the empty audit surface so callers can prove
# that the optimizer has no silently discarded specialty binding.
REJECTED_SPECIALTY_PARAMETER_ROWS: frozenset[tuple[str, str, str]] = frozenset(
    {
    }
)


# Approved representation and exclusive-branch decisions have eliminated the
# former non-cumulative rows.  Keep the empty audit surface so callers can prove
# there is no deferred optimizer binding.
DEFERRED_NON_CUMULATIVE_PARAMETER_ROWS: frozenset[
    tuple[str, str, str]
] = frozenset(
    {
    }
)


AUTHORED_ALTERNATIVE_NODES: frozenset[tuple[str, str]] = frozenset(
    {
        ("ARSENAL", "ROLE PACKAGE"),
        ("AUTODRONES", "LOCAL RESPONSE"),
        ("BREAKER", "CONTROLLED BREACH"),
        ("BREAKER", "NO FIXED ENTRY"),
        ("FIELD ENGINEER", "EMPLACE"),
        ("FIELD ENGINEER", "FIELD MODIFICATION"),
        ("FIELD ENGINEER", "OVERDRIVE"),
        ("FIELD ENGINEER", "PATCH JOB"),
        ("FIELD ENGINEER", "REDUNDANT SYSTEM"),
        ("FIELD ENGINEER", "TECHNICAL OBSTACLE"),
        ("FIELD MEDIC", "BATTLEFIELD RECOVERY"),
        ("FPV DRONES", "EVASIVE PILOTING"),
        ("GUNNER", "SUPPRESSION DISCIPLINE"),
        ("HANDLER", "FAVOR LEDGER"),
        ("LOCAL HACKER", "HARD LOCKOUT"),
        ("NETWORKER", "CASCADE ACCESS"),
        ("RESUSCITATOR", "BRIDGE TO TRAUMA"),
        ("SHIELD BEARER", "IMPACT CONTROL"),
        ("SNIPER", "PERFECT SOLUTION"),
        ("TRAUMA", "RECOVERY PLAN"),
        ("TRAUMA", "TRAUMA CARE"),
    }
)


# Exact audit findings for nodes whose canonical direction is clear but whose
# magnitude, branch selection, lifecycle, or subsystem procedure is incomplete.
# The approved hacking procedures currently leave this surface empty.
SPECIALTY_MECHANICAL_GAPS: dict[tuple[str, str], str] = {
}
