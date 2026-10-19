---
type: procedure
domain: firearms
status: locked
tags: [type/action, domain/firearms, status/locked]
---
# RELOAD

**At a glance:** A sequence of real firearm/manipulation operations rather than one abstract button; partial reload states persist if AP runs out or the sequence is interrupted.

> [!note]- Full rules
> - Eject/drop versus retain/stow are physically different.
> - New magazine retrieval depends on INVENTORY location.
> - Insert, chamber/cycle, and other steps depend on the specific mechanism.
> - Tube-fed weapons can load shell-by-shell.
> - Macros may wrap atomic operations for convenience but cannot erase the underlying state.

## Connections
[[Firearms]] · [[AP]] · [[FIREARM MANIPULATION]] · [[MAGAZINE]] · [[CHAMBER]] · [[CYCLING]] · [[INVENTORY]]
