---
type: security-layer
domain: hacking
status: locked
tags: [type/mechanic, domain/hacking, status/locked]
---
# HACKING VISIBILITY

**At a glance:** How much observable activity one hacking action creates for MONITORING; visibility affects the chance of staying quiet but does not itself cause DETECTION.

> [!note]- Full rules
> Every intrusion or discovery action has one visibility band:
> - **LOW:** limited observable activity relative to the action being attempted;
> - **STANDARD:** the action's ordinary observable activity;
> - **HIGH:** broad, repeated, conspicuous, or otherwise noisy activity.
>
> An action with no more specific authored visibility starts at STANDARD. Effects that raise or lower visibility move it through the ordered bands and stop at LOW or HIGH.
>
> When relevant MONITORING exists and whether the action is noticed is uncertain, apply the visibility band's balance modifier to the STAY QUIET difficulty. Do not add a detection check when no monitoring can observe the action or when the result is already certain from the evidence. Visibility does not itself advance DETECTION; the monitoring coverage and evidence produced determine what a defender can learn.
>
> Network visibility does not erase physical evidence at a local interface, and lower visibility never means undetectable.

## Connections
[[INTRUSION]] · [[MONITORING]] · [[DETECTION]] · [[LOCAL HACKER]] · [[NETWORKER]]
