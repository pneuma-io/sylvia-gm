# SNIPER DEEP AIM: stationary first playable

Owner approval: Sentinel_c9d9876937dc8191bdb39f7feca09979, answering
Sentinel_648937a10bb88191bec9ca01b10f276b on 28 October 2026. This implements
the unchanged SNIPER Entry, not a new balance seed or higher-tier ability.

Use ordinary `aim` first, investing its ordinary 1 or 2 AP. An eligible
SHARPSHOOTER with `specialty.sniper.entry.deep_aim` then invokes
`enter_deep_aim` for the same target and aim point. It spends exactly one
additional AP. It neither spends firing AP nor requires enough AP left to
fire. The map exposes the same action through its existing actor/target/point
selection, preview and queue machinery. Repeated paid stages cannot stack.

The selected Entry records an optional `sniper_preparation` when ordinary AIM
successfully completes. It binds the actual target, firearm, ordinary AIM
investment/point, shooter centre/muzzle, target centre and map revision. Older
saves with ordinary AIM but no such binding can still fire normally; perform
fresh ordinary AIM before using the new stage. Other characters gain no new
preparation/default. A fresh normal AIM replaces the binding and clears DEEP.

A valid paid stage contributes -2 to the existing difficulty calculation for
one ordinary prepared attack. Normal AIM, range, exposure/cover, recoil,
hit quality, ammunition, damage, AP and critical procedures still own firing.
The ordinary attack consumes this separate solution on commitment, including
a miss or a changed target. Burst/full-auto apply the benefit once to their
single attack calculation, not once per projectile. Rejected shots do not
spend preparation or ammunition. Durable command/sequence receipts prevent
duplicate stage or attack effects through reopen/export/recovery.

Stage eligibility and preview share the same physical/control/node/solution
checks. A real target exposure is required. Applicable incoming suppression
uses the existing authority before the stage's own spend; interruption must
not grant the stage or roll back incoming fire.

The qualified boundary is an immediate stationary shot with the same map
revision. Exact pose/identity/ordinary-solution changes prevent use; revision
changes also prevent an away-and-back or lost-and-restored scene from silently
reviving the paid benefit. This is a conservative stable-scene implementation
limit, **not** a canonical claim that every unrelated map edit is material
movement. Such edits require reassessment/fresh preparation in this version.
No material-movement distance, automatic reacquisition, interleaving cadence,
turn expiry or reaction lifetime has been selected. Existing prepared reaction
procedures do not receive a DEEP modifier from this implementation. Those wider
cases and native/input/device/installed acceptance remain separately unqualified.

No caller-supplied discount, attack modifier, preparation total, extra range,
penetration, damage or higher SNIPER node effect is accepted by this action.
