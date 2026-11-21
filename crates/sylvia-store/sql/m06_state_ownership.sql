
CREATE TABLE campaign_state_namespace_owners (
    campaign_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    owner_package_id TEXT,
    PRIMARY KEY (campaign_id, namespace),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE
);
CREATE TRIGGER campaign_state_owner_matches_current_lock
BEFORE INSERT ON campaign_state_namespace_owners
BEGIN
    SELECT CASE WHEN
        (
            NEW.owner_package_id IS NULL
            AND EXISTS (
                SELECT 1 FROM campaigns AS campaign
                JOIN campaign_lock_memberships AS membership
                  ON membership.lock_id = campaign.current_lock_id
                WHERE campaign.campaign_id = NEW.campaign_id
            )
        )
        OR (
            NEW.owner_package_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM campaigns AS campaign
                JOIN campaign_lock_memberships AS membership
                  ON membership.lock_id = campaign.current_lock_id
                WHERE campaign.campaign_id = NEW.campaign_id
                  AND membership.package_id = NEW.owner_package_id
            )
        )
        OR EXISTS (
            SELECT 1 FROM campaigns AS campaign
            JOIN campaign_lock_memberships AS membership
              ON membership.lock_id = campaign.current_lock_id
            WHERE campaign.campaign_id = NEW.campaign_id
              AND NEW.namespace LIKE membership.package_id || '.%'
              AND membership.package_id <> NEW.owner_package_id
        )
        THEN RAISE(ABORT, 'campaign state namespace owner conflicts with current lock')
    END;
END;
CREATE TRIGGER campaign_state_owner_required_insert
BEFORE INSERT ON campaign_state
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM campaign_state_namespace_owners AS owner
        WHERE owner.campaign_id = NEW.campaign_id
          AND owner.namespace = NEW.namespace
    ) THEN RAISE(ABORT, 'campaign state namespace owner is required') END;
END;
CREATE TRIGGER campaign_state_owner_required_update
BEFORE UPDATE OF campaign_id, namespace ON campaign_state
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM campaign_state_namespace_owners AS owner
        WHERE owner.campaign_id = NEW.campaign_id
          AND owner.namespace = NEW.namespace
    ) THEN RAISE(ABORT, 'campaign state namespace owner is required') END;
END;
CREATE TRIGGER campaign_state_owner_immutable
BEFORE UPDATE ON campaign_state_namespace_owners
BEGIN
    SELECT RAISE(ABORT, 'campaign state namespace owner is immutable');
END;
CREATE TRIGGER campaign_state_owner_delete_requires_empty
BEFORE DELETE ON campaign_state_namespace_owners
WHEN EXISTS (
    SELECT 1 FROM campaign_state AS state
    WHERE state.campaign_id = OLD.campaign_id
      AND state.namespace = OLD.namespace
)
BEGIN
    SELECT RAISE(ABORT, 'campaign state namespace owner still has state');
END;
CREATE TRIGGER mutation_requires_matching_namespace_owner
BEFORE INSERT ON mutations
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM actions AS action
        JOIN campaign_state_namespace_owners AS owner
          ON owner.campaign_id = NEW.campaign_id
         AND owner.namespace = NEW.namespace
        WHERE action.campaign_id = NEW.campaign_id
          AND action.action_id = NEW.action_id
          AND owner.owner_package_id IS NOT NULL
          AND owner.owner_package_id = action.package_id
    ) THEN RAISE(ABORT, 'mutation package does not own state namespace') END;
END;
