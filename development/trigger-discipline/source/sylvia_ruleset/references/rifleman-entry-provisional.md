# Provisional RIFLEMAN entries

Daniel approved these exact first-playable rules through the current thumbs-up on
Sentinel_de771604e5ac81918159f49261ce26fd, verified by the coordinator on 28 October
2026 at 03:00:36 UTC and recorded in TDH-09 revision 58. This is provisional
implementation approval, not final balance, native acceptance or starting-profile approval.
The preserved tertiary source and all other unresolved specialty values remain unchanged.

SUSTAINED FIRE DISCIPLINE reduces each qualifying new firing action's additional
recoil by 1, to a minimum of 0 added. It never removes existing recoil or ammunition
costs. The first observed firing action starts an actor-owned chain bound to the
actual firearm ID. The chain survives reload, partial recovery and team-turn
changes. Full recoil recovery or a successful switch to another firearm ends it.
Actual committed suppression fire uses this same gain calculation; later dispatch
of its already-committed rounds does not add another gain or advance the chain.
Prepared shots that really consume a new round use the ordinary chain calculation.
No periodic attack or additional trigger cadence is introduced.

Older state without `gunner_chain_weapon_id` has no inferred firing history: its
next real firing action starts the chain. This optional execution marker supplies
no starting benefit and is not a weapon profile or ownership field. It persists
with the actor's saved state and is not transferred with a firearm.

WEAPON MANIPULATION reduces an ordinary stored reload, cycle/chamber or one-shell
loading cost by 1 AP, to a minimum of 1 AP. It changes the effective operation cost,
not the weapon's authored price or physical mechanism. Magazine removal/retention,
replacement, loose-shell counts, chambering and mandatory cycling still happen.
Unavailable or zero-cost automatic cycling does not become a manual capability.

`handle_weapon`, `switch_weapon` and other explicitly adjudicated handling ports
already receive a final total. The GM accounts for the approved benefit once when
deciding that total; the runtime charges the supplied total unchanged. It does not
subtract the entry benefit a second time or invent a universal retrieval schedule.
Higher-tier overlapping operations are not implemented by this Entry rule.

The two registered reduction parameters are pinned to the approved provisional
value 1 during package generation. Other balance seeds do not become player canon,
and a later seed edit cannot silently alter these approved playable values.
