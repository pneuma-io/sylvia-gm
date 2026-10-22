---
type: security-state
domain: hacking
status: locked
tags: [type/state, domain/hacking, status/locked]
---
# DETECTION

**At a glance:** Defender awareness that suspicious or hostile network activity exists; not a CYBER HP bar and not automatically the end of an intrusion.

> [!note]- Full rules
> DETECTION is evidence-based and may hold one of these states for a particular defender/security system:
> - **UNDETECTED:** no relevant evidence has been noticed.
> - **SUSPICIOUS:** an anomaly has been noticed, but its affected route/session is not yet identified.
> - **LOCATED:** the affected NODE, LINK, account, credential, or session has been identified.
> - **CONFIRMED:** a hostile intrusion has been established.
>
> These are not a mandatory ladder. Evidence can jump directly to LOCATED or CONFIRMED, and different defenders can hold different awareness. Success is not automatically quiet and failure is not automatically detected; MONITORING and the evidence actually produced determine the transition.
>
> Detected defenders can revoke credentials, kill sessions, isolate segments, shut down gateways, restore endpoints, or hunt footholds. Defensive actions change real network state and can deliberately sacrifice capability to deny the attacker.

## Connections
[[MONITORING]] · [[INTRUSION]] · [[CREDENTIAL]] · [[FOOTHOLD]] · [[NETWORK SEGMENT]] · [[GATEWAY]]
