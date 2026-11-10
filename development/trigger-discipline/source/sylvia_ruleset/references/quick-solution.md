# QUICK SOLUTION ownership

The existing SNAPSHOT Entry is an owning engine effect, not a GM-only choice.
Its canonical note and registered parameter are unchanged. Generated unsupported
coverage labels describe missing implementation; they are not design authority.
Other SNAPSHOT nodes are not implemented by this change.

Ordinary AIM still accepts a nominal investment of 1 or 2 AP. A legal SHARPSHOOTER
with the selected Entry pays `max(1, nominal investment - registered discount)`
for the first performed AIM against each target not already attacked in the
current TEAM TURN. The registered seed is 1, with its existing bounds 1–2.
AIM investment and SHARPSHOOTER/DEEP AIM preparation remain independent of the AP
paid. No AP0 client request, local refund or shot prepayment is introduced.

The optional actor marker `quick_solution_turn` holds distinct aimed and attacked
target IDs. A first AIM uses eligibility even when the minimum price saves no AP.
Actual single/burst/full-auto and prepared firearm attacks record the target even
on a miss. A manually controlled FPV fragmentation attack records its operator's
selected target once the valid attack run performs its placement check, including
failed placement. Offensive CQC (TRIP, DISARM, BODY CONTROL, WEAPON CONTROL, SHOVE
and THROW) records its attacker and target at the performed check, including a
failed maneuver. Defensive ESCAPE is not an attack. A raw blast snapshot does not
declare a performed attack or invent targets, and an autonomous drone's own shot
does not become its operator's attack. Rejected, no-effect and interrupted actions
do not invent a performed action. The existing pending-choice/transaction and
receipt paths own adoption.
Returning to an actor or target does not reset history. Real TEAM TURN side changes
clear the recorded histories through the existing roster helper.

History is also recorded for SHARPSHOOTER actors without this Entry, so selecting
it does not erase an already tracked action. Older state may omit the marker;
reads and previews do not create it, and performed actions initialize it lazily.
No history from actions executed by an older package is reconstructed or claimed.
Actual side transition establishes a new empty turn for every recorded actor.

The existing combined SENTINEL sightline records its performed ordinary AIM. Its
explicit final combined cost remains the existing contract; this Entry does not
apply a second discount to that adjudicated total. Preparing watch alone is not AIM.
Prepared firearm attacks use the same attack history as ordinary firearm actions.

Owning execution and player controls share the price calculations. The map's
existing AIM1 control is unchanged; an AIM2 investment variant calls the same
ordinary AIM tool. Both previews and queued intentions show the actual AP price.
The variant is a presentation of an existing parameter, not a new ability.

This is deterministic source/player/queue support. Native, installed, physical
device, provider and final-balance/owner-feel acceptance require their own evidence.
