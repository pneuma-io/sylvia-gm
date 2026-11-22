//! The store reads behind a campaign's tool catalog and its knowledge bindings, from
//! `sqlite_authority.py` (`package_catalog_basis`, `compatible_package_binding`,
//! `package_lock_lineage`) and `sqlite_packages.py` (`declared_tool`,
//! `compatible_binding_entries`, `lock_lineage`).

use crate::campaigns::{current_lock_record, named_rows, parse_canonical, read_lock_record, Named};
use crate::error::{StoreError, StoreResult};
use crate::packages::{
    activation_result_from_row, compatible_migration_identity, COMPATIBLE_UPDATE_AUTHORITY,
};
use crate::pytext::py_str;
use crate::snapshots::read_state;
use crate::store::AuthorityStore;
use rusqlite::Connection;
use std::collections::HashSet;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::PackageLockEntry;
use sylvia_domain::package_library::{
    PackageLockAvailability, PackageLockRecord, StatePredicate, ToolDeclaration,
};
use sylvia_domain::pyops::py_truthy;

/// `package_catalog_basis(campaign_id)`'s answer: the current lock, the state, the current
/// turn's status, the writer, the lifecycle and every exact package-bound tool declaration.
#[derive(Debug, Clone, PartialEq)]
pub struct CatalogBasis {
    pub lock: PackageLockRecord,
    pub state: JsonMap,
    pub turn_status: String,
    pub writer: String,
    pub lifecycle: String,
    pub declarations: Vec<(PackageLockEntry, ToolDeclaration)>,
}

fn key_error(key: &str) -> StoreError {
    StoreError::Other(format!("KeyError: '{key}'"))
}

/// `sqlite_packages.declared_tool(connection, identity)`.
pub(crate) fn declared_tool(
    connection: &Connection,
    identity: (&str, &str, &str, &str),
) -> StoreResult<Option<ToolDeclaration>> {
    let (columns, rows) = named_rows(
        connection,
        "SELECT * FROM package_tools WHERE package_id = ? AND version = ? \
         AND content_sha256 = ? AND tool_name = ?",
        &[&identity.0, &identity.1, &identity.2, &identity.3],
    )?;
    let Some(values) = rows.into_iter().next() else { return Ok(None) };
    let row = Named { columns: &columns, values };
    let predicates = parse_canonical(&row.text("state_predicates_json")?)?;
    let Json::Array(predicates) = predicates else {
        return Err(StoreError::Other("TypeError: state predicates are not a list".into()));
    };
    let state_predicates = predicates
        .iter()
        .map(|item| {
            let item = item
                .as_object()
                .ok_or_else(|| StoreError::Other("TypeError: a predicate is not a mapping".into()))?;
            let field = |key: &str| item.get(key).ok_or_else(|| key_error(key));
            Ok(StatePredicate {
                namespace: sylvia_domain::pyops::py_str(field("namespace")?),
                key: sylvia_domain::pyops::py_str(field("key")?),
                equals: field("equals")?.clone(),
                negated: item.get("negated").is_some_and(py_truthy),
            })
        })
        .collect::<StoreResult<_>>()?;
    let strings = |column: &str| -> StoreResult<Vec<String>> {
        match parse_canonical(&row.text(column)?)? {
            Json::Array(items) => Ok(items.iter().map(sylvia_domain::pyops::py_str).collect()),
            _ => Err(StoreError::Other(format!("TypeError: {column} is not a list"))),
        }
    };
    let input_schema = match parse_canonical(&row.text("input_schema_json")?)? {
        Json::Object(schema) => schema,
        _ => return Err(StoreError::Other("TypeError: the input schema is not a mapping".into())),
    };
    Ok(Some(ToolDeclaration {
        name: row.text("tool_name")?,
        capability: row.text("capability")?,
        input_schema,
        state_predicates,
        turn_statuses: strings("turn_statuses_json")?,
        writers: strings("writers_json")?,
    }))
}

/// `sqlite_packages.compatible_binding_entries(connection, campaign_id, source_identity)`: the
/// entries from the current lock back to the exact source, following only this campaign's
/// committed compatible transitions; none when the chain does not reach it.
pub(crate) fn compatible_binding_entries(
    connection: &Connection,
    campaign_id: &str,
    source: (&str, &str, &str),
) -> StoreResult<Vec<PackageLockEntry>> {
    let mut current = current_lock_record(connection, campaign_id)?;
    let Some(mut entry) = current.package_lock.entries().iter().find(|e| e.package_id() == source.0).cloned()
    else {
        return Ok(Vec::new());
    };
    let mut entries = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    while seen.insert(current.lock_id.clone()) {
        let identity = (entry.package_id().to_owned(), entry.version().to_owned(), entry.sha256().to_owned());
        entries.push(entry.clone());
        if (identity.0.as_str(), identity.1.as_str(), identity.2.as_str()) == source {
            return Ok(entries);
        }
        let Some(predecessor) = current.predecessor_lock_id.clone() else { return Ok(Vec::new()) };
        let previous = read_lock_record(connection, &predecessor)?;
        if previous.campaign_id != campaign_id || previous.created_revision >= current.created_revision {
            return Ok(Vec::new());
        }
        let Some(prior) =
            previous.package_lock.entries().iter().find(|e| e.package_id() == entry.package_id()).cloned()
        else {
            return Ok(Vec::new());
        };
        if prior.role() != entry.role() {
            return Ok(Vec::new());
        }
        if (prior.package_id(), prior.version(), prior.sha256())
            != (identity.0.as_str(), identity.1.as_str(), identity.2.as_str())
        {
            let (columns, rows) = named_rows(
                connection,
                "SELECT * FROM package_activations WHERE campaign_id = ? \
                 AND old_lock_id = ? AND new_lock_id = ? AND status = 'committed'",
                &[&campaign_id, &previous.lock_id, &current.lock_id],
            )?;
            let Some(values) = rows.into_iter().next() else { return Ok(Vec::new()) };
            let receipt = Named { columns: &columns, values };
            let report = parse_canonical(&receipt.text("result_json")?)?;
            let compatible = compatible_migration_identity(&receipt.text("migration_id")?)
                && matches!(&report, Json::Object(map)
                    if map.get("authority") == Some(&Json::from(COMPATIBLE_UPDATE_AUTHORITY)));
            if !compatible
                || activation_result_from_row(&receipt)?.resulting_revision != current.created_revision
            {
                return Ok(Vec::new());
            }
        }
        current = previous;
        entry = prior;
    }
    Ok(Vec::new())
}

impl AuthorityStore {
    /// `package_catalog_basis(campaign_id)`: availability and the exact package-bound
    /// declarations, in one transaction.
    pub fn package_catalog_basis(&self, campaign_id: &str) -> StoreResult<CatalogBasis> {
        let connection = self.connect()?;
        connection.execute_batch("BEGIN")?;
        let basis = catalog_basis_on(&connection, campaign_id)?;
        connection.execute_batch("COMMIT")?;
        Ok(basis)
    }

    /// `compatible_package_binding(campaign_id, identity)`: a retained binding resolved only
    /// through this campaign's compatible lineage.
    pub fn compatible_package_binding(
        &self,
        campaign_id: &str,
        identity: (&str, &str, &str),
    ) -> StoreResult<Option<PackageLockEntry>> {
        let connection = self.connect_read_only()?;
        connection.execute_batch("BEGIN")?;
        let entries = compatible_binding_entries(&connection, campaign_id, identity);
        let _ = connection.execute_batch("ROLLBACK");
        Ok(entries?.into_iter().next())
    }

    /// `package_lock_lineage(campaign_id)`: every lock the campaign has had, oldest first.
    pub fn package_lock_lineage(&self, campaign_id: &str) -> StoreResult<Vec<PackageLockRecord>> {
        crate::stored_campaigns::lock_lineage(&self.connect()?, campaign_id)
    }
}

fn catalog_basis_on(connection: &Connection, campaign_id: &str) -> StoreResult<CatalogBasis> {
    let lock = current_lock_record(connection, campaign_id)?;
    if lock.availability != PackageLockAvailability::Available {
        return Err(crate::actions::rejected("Campaign package bytes are unavailable", "command_rejected"));
    }
    let campaign = named_rows(
        connection,
        "SELECT writer, lifecycle FROM campaigns WHERE campaign_id = ?",
        &[&campaign_id],
    )?
    .1
    .into_iter()
    .next();
    // Narration-only turns share a revision; their opaque IDs do not order them. The current
    // open turn owns admission even when an older ID sorts later.
    let turn = named_rows(
        connection,
        "SELECT status FROM turns WHERE campaign_id = ? \
         ORDER BY (status = 'open') DESC, created_revision DESC, turn_id DESC LIMIT 1",
        &[&campaign_id],
    )?
    .1
    .into_iter()
    .next();
    let (Some(campaign), Some(turn)) = (campaign, turn) else {
        return Err(crate::actions::rejected("Campaign catalog basis is unavailable", "command_rejected"));
    };
    let state = read_state(connection, campaign_id)?;
    let mut declarations = Vec::new();
    for entry in lock.package_lock.entries() {
        let tools = named_rows(
            connection,
            "SELECT tool_name FROM package_tools WHERE package_id = ? AND version = ? \
             AND content_sha256 = ? ORDER BY tool_order",
            &[&entry.package_id(), &entry.version(), &entry.sha256()],
        )?
        .1;
        for row in tools {
            let name = py_str(&row[0]);
            if let Some(declaration) =
                declared_tool(connection, (entry.package_id(), entry.version(), entry.sha256(), &name))?
            {
                declarations.push((entry.clone(), declaration));
            }
        }
    }
    Ok(CatalogBasis {
        turn_status: py_str(&turn[0]),
        writer: py_str(&campaign[0]),
        lifecycle: py_str(&campaign[1]),
        lock,
        state,
        declarations,
    })
}
