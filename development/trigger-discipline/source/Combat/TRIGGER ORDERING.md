---
type: timing-procedure
domain: combat
status: locked
tags: [type/mechanic, domain/combat, status/locked]
---
# TRIGGER ORDERING

**At a glance:** When one event causes several prepared, suppressive, or other triggered effects at the same moment, pause at the exact physical trigger point, resolve them in a deterministic order, update the world after each resolution, then continue the interrupted action if it is still possible.

> [!note]- Full rules
> - When one movement step, exposure, attack declaration, loss of COVER, entry into contact space, or other event satisfies one or more trigger conditions, pause the interrupted action at the exact physical state where those conditions became true.
> - Gather every effect whose trigger is currently satisfied. A triggered effect is never resolved retroactively against an earlier position or state.
> - **Explicit-before priority:** an effect whose own text explicitly says it occurs **before the triggering threat or action resolves** resolves before ordinary same-moment attacks or consequences. This priority exists only when the effect actually says it acts before the threat; words such as "immediately" by themselves do not create a higher timing tier.
> - After explicit-before effects, resolve the remaining same-moment triggered effects one at a time.
> - When one controller owns several effects in the same timing tier, that controller chooses their order.
> - When opposing sides have effects in the same timing tier and no rule grants explicit priority, resolve the **non-active side** first, then the active side. The active side is the side whose TEAM TURN is currently in progress.
> - After every triggered effect resolves, immediately update position, STANCE, COVER/geometry, HP/RES, INJURY, firearm state, ammunition, RECOIL, LOS, control states, and any other changed prerequisites before resolving the next effect.
> - Before each queued effect resolves, re-check its trigger and prerequisites against the updated battlefield. If it is no longer physically or mechanically valid, it does not resolve. Its own persistence/break rules determine whether the prepared state survives; TRIGGER ORDERING never creates an AP/resource refund.
> - If resolving one triggered effect creates a new event with its own triggers, resolve that new trigger window completely using this same procedure, then return to any still-valid effects from the earlier window.
> - Once the trigger window is empty, resume the interrupted action from the character's actual current position/state if they remain capable of continuing and the action is still possible. Otherwise the interrupted action stops where the rules and resulting physical state require.
> - One effect does not resolve more than once from the same qualifying event unless that effect explicitly supports multiple responses.
>
> **Common examples**
> - Two OVERWATCH attacks trigger on the same crossing: their controller chooses which attack resolves first; the second attack then re-checks LOS, target state, COVER, and other prerequisites after the first.
> - An exposure simultaneously enters active SUPPRESSION and an OVERWATCH ZONE controlled by the same side: that controller chooses which same-tier response resolves first unless an explicit rule says otherwise.
> - INTERPOSE or another protective PREP ACTION that explicitly acts before the triggering threat resolves uses explicit-before priority. After the protective movement/geometry changes, the threatening attack re-checks its firing solution against the new state.

## Connections
[[TEAM TURN]] · [[PREP ACTION]] · [[OVERWATCH]] · [[SUPPRESSION]] · [[MOVEMENT]] · [[COVER]] · [[GUARDIAN]]
