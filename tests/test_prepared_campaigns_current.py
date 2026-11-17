"""Prepared parties stay bound to the exact Plans and packages this build ships.

This is deliberately fast and lives with the bundled-content checks, which run whenever
`campaign-plans/`, `campaign-settings/` or `rulesets/` change. A rebind of the bundled Plans
that forgets `scripts/build_ready_campaigns.py --write` makes every prepared campaign
unavailable to players; the slower prepared-campaign journeys only run in full qualification.
"""

from sylvia_gm.application.ready_campaigns import ready_campaign_catalog

PREPARED = {"a-debt-in-iron", "last-cart-through", "the-cartographers-wake", "rift-awakening"}


def test_prepared_campaigns_load_against_the_shipped_plans():
    # The catalog raises for a prepared party whose Plan hash or package bindings are stale.
    catalog = ready_campaign_catalog()

    assert {row["id"] for row in catalog} == PREPARED
    assert all(row["party"] for row in catalog)
