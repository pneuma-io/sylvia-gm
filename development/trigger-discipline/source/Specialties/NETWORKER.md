---
type: specialty
domain: hacker
status: locked
progression_status: tertiary
tags: [type/specialty, domain/hacker, status/locked, progression/tertiary]
---
# NETWORKER

**At a glance:** HACKER specialty that maps connected topology, pivots through real LINKS, establishes FOOTHOLDS, and keeps access alive across a NETWORK.

> [!note]- Full rules
> - NETWORKER treats NODE → LINK → SEGMENT → GATEWAY → next NODE as the tactical space.
> - It cannot cross an absent physical/network path, bypass an uncompromised boundary by declaration, or reach air-gapped hardware without a route.
> - Exact AP, discovery, security-state, and persistence values remain balance parameters.
>
> **ENTRY — MAP THE LINKS**
> - After successfully GETTING IN to a networked NODE, automatically reveal the directly visible LINKS and immediate neighboring NODES that the compromised NODE itself can identify. Hidden paths, isolated segments, undisclosed gateways, and things the NODE cannot see remain unknown.
>
> **TIER 1 — Move off the first node**
> - **PIVOT:** The first GET IN attempt against an immediately connected discovered NODE through a NODE already controlled costs less AP/time. The LINK must actually exist and permit traffic.
> - **QUIET ENUMERATION:** Lower the HACKING VISIBILITY of network discovery and topology-mapping actions. Reconnaissance becomes quieter, not undetectable.
> - **CREDENTIAL SWEEP:** When a compromised NODE exposes credentials, tokens, session material, or equivalent access data, spend one HACKING action to identify which known directly reachable systems are plausible candidates for that material. The result is a candidate set based on visible scope and system identity; it does not test the credential or prove that it works.
>
> **TIER 2 — Establish the network**
> - **FOOTHOLD:** After compromising a remotely reached NODE through a valid NETWORK route, establishing a FOOTHOLD there costs less AP/time than normal, to a minimum final cost of 1 AP. The route, access prerequisites, monitoring, detection, persistence method, and remediation requirements are unchanged. Proper remediation, reset, credential change, reimage, discovery/removal, or destruction can defeat the foothold.
> - **SEGMENT MAP:** After compromising one NODE in a network SEGMENT, a single discovery action reveals a broader portion of that SEGMENT than normal. Only systems discoverable from the actual network position are revealed.
> - **TOKEN RELAY:** When authentication material genuinely valid for another discovered NODE is available, using it to establish access there requires less AP/time than a fresh intrusion. Invalid credentials remain invalid.
>
> **TIER 3 — Go through the architecture**
> - **GATEWAY PIVOT:** After compromising a GATEWAY that genuinely connects two SEGMENTS, reveal the far-side LINKS and immediate neighboring NODES that the GATEWAY itself can identify. Hidden paths and systems beyond that entry-facing topology remain unknown. The first further discovery action made from that GATEWAY costs less AP.
> - **PARALLEL PROBE:** One discovery action may probe additional directly reachable NODES or LINKS instead of examining them sequentially. Raise that action's HACKING VISIBILITY; the increase is not automatic DETECTION.
> - **ROUTE PERSISTENCE:** If one intermediate LINK in a current route is lost, switch one affected downstream connection to a previously discovered valid alternate route for less AP. Preserve downstream access only when the alternate route reaches the same compromised NODE and its required session or FOOTHOLD remains valid.
>
> **TIER 4 — Exploit the network as a system**
> - **CASCADE ACCESS:** When multiple NODES genuinely share the same authentication authority, security flaw, configuration mistake, or other exploitable common basis, successfully compromising one enables one benefit for each subsequent attempt against another affected NODE: **FASTER** reduces AP, or **EASIER** reduces DIFFICULTY. Choose one; the benefits do not stack. The shared weakness must actually exist and still apply to the target.
> - **NETWORK INTERCEPT:** Gain a PREP ACTION while controlling a NODE that actually carries relevant traffic, such as a GATEWAY. Declare a specific observable network transaction/communication condition; when matching traffic crosses the controlled NODE, immediately attempt to inspect, block, redirect, or modify it where access and protocol/security allow. Traffic taking another route is unaffected.
> - **REROUTE:** When a known LINK becomes blocked, monitored, or unavailable, move an established multi-NODE connection chain onto a discovered alternate route for less AP. Recalculate the chain through active known LINKS and retain established access only to the same compromised NODES the replacement route genuinely reaches. Unreached sessions end normally; surviving FOOTHOLDS remain available under their own rules.
>
> **TIER 5 KEYSTONE — OWN THE TOPOLOGY**
> - Store discovered NODES, LINKS, SEGMENTS, and GATEWAYS as persistent operational-map knowledge instead of tying that knowledge to one live route. Recalculate current reachability from surviving controlled NODES and valid FOOTHOLDS through active known LINKS. Pivots across already discovered reachable infrastructure cost less AP; losing one NODE or LINK does not erase topology knowledge or collapse access that another valid route or foothold still supports. Knowledge can become outdated, and air gaps, uncompromised boundaries, missing LINKS, disconnected hardware, expired sessions, and remediated FOOTHOLDS remain real limits.

## Connections
[[HACKER]] · [[NETWORKING]] · [[NODE]] · [[LINK]] · [[NETWORK]] · [[SEGMENT]] · [[GATEWAY]]
