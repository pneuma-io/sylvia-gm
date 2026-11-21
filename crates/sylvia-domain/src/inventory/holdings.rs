//! `validate_holdings`, `transfer_holding` and `player_inventory`: rules-neutral holding
//! validation and transfers; persistence stays with campaign authority.

use crate::authority::validate_authority_identity_value;
use crate::error::{DomainError, DomainResult};
use crate::pyops::{py_eq, py_item, py_str, py_truthy};
use serde_json::json;
use std::collections::BTreeSet;
use sylvia_base::pystr;
use sylvia_base::{Json, JsonMap};

const FIELDS: [&str; 10] = [
    "holding_id",
    "item_id",
    "name",
    "kind",
    "stackable",
    "quantity",
    "owner_id",
    "holder_id",
    "visibility",
    "known_by",
];
const SAFE_INTEGER: i64 = (1 << 53) - 1;

fn value(message: &str) -> DomainError {
    DomainError::value(message)
}

/// `type(value) is int` (not `bool`, not `float`), as an `i64`.
fn exact_int(value: &Json) -> Option<i64> {
    match value {
        Json::Number(number) if number.is_i64() || number.is_u64() => {
            Some(number.as_i64().unwrap_or(i64::MAX))
        }
        _ => None,
    }
}

fn is_one_of(value: &Json, options: &[&str]) -> bool {
    options.iter().any(|option| py_eq(value, &Json::from(*option)))
}

/// `validate_holdings(holdings, entity_ids=...)`: one complete inventory, without inferring
/// ownership or merging distinct items.
pub fn validate_holdings(holdings: &[Json], entity_ids: &BTreeSet<String>) -> DomainResult<Vec<JsonMap>> {
    let mut result = Vec::with_capacity(holdings.len());
    let mut identities: Vec<String> = Vec::new();
    let mut item_shapes: Vec<(String, (String, String, bool))> = Vec::new();
    let mut item_totals: Vec<(String, i128)> = Vec::new();
    let mut stacks: Vec<(String, String, String)> = Vec::new();
    for holding in holdings {
        let Some(holding) = holding
            .as_object()
            .filter(|h| h.len() == FIELDS.len() && FIELDS.iter().all(|f| h.contains_key(*f)))
        else {
            return Err(value("Inventory holding fields are invalid"));
        };
        for field in ["holding_id", "item_id", "owner_id", "holder_id"] {
            validate_authority_identity_value(&holding[field], &format!("Inventory {field}"), false)?;
        }
        let text = |field: &str| holding[field].as_str().expect("validated as an identity").to_owned();
        let identity = text("holding_id");
        if identities.contains(&identity) {
            return Err(value("Inventory holding identity is duplicated"));
        }
        identities.push(identity);
        if ["owner_id", "holder_id"].iter().any(|field| !entity_ids.contains(&text(field))) {
            return Err(value("Inventory owner and holder must reference known campaign entities"));
        }
        let name = match &holding["name"] {
            Json::String(name)
                if !pystr::strip(name).is_empty()
                    && name == pystr::strip(name)
                    && pystr::len(name) <= 300 =>
            {
                name.clone()
            }
            _ => return Err(value("Inventory item name is invalid")),
        };
        let Some(quantity) = exact_int(&holding["quantity"]).filter(|q| (0..=SAFE_INTEGER).contains(q))
        else {
            return Err(value("Inventory quantity must be a nonnegative safe whole number"));
        };
        let (Some(kind), Json::Bool(stackable)) =
            (holding["kind"].as_str().filter(|k| ["item", "currency"].contains(k)), &holding["stackable"])
        else {
            return Err(value("Inventory kind or stacking declaration is invalid"));
        };
        if kind == "currency" && !stackable {
            return Err(value("Named currency must be stackable"));
        }
        if !is_one_of(&holding["visibility"], &["player_known", "gm_private"]) {
            return Err(value("Inventory visibility is invalid"));
        }
        let Json::Array(known_by) = &holding["known_by"] else {
            return Err(value("Inventory knowledge must be an identity list"));
        };
        let mut known: Vec<&str> = Vec::with_capacity(known_by.len());
        for identity in known_by {
            known.push(validate_authority_identity_value(identity, "Inventory knowledge identity", false)?);
        }
        let mut unique = known.clone();
        unique.sort_unstable();
        unique.dedup();
        if unique.len() != known.len() {
            return Err(value("Inventory knowledge identities are duplicated"));
        }
        let item_id = text("item_id");
        let shape = (name, kind.to_owned(), *stackable);
        match item_shapes.iter().find(|(existing, _)| *existing == item_id) {
            Some((_, existing)) if *existing != shape => {
                return Err(value("One item identity cannot describe different items or currencies"));
            }
            Some(_) => {}
            None => item_shapes.push((item_id.clone(), shape)),
        }
        let total = match item_totals.iter_mut().find(|(existing, _)| *existing == item_id) {
            Some(entry) => {
                entry.1 += i128::from(quantity);
                entry.1
            }
            None => {
                item_totals.push((item_id.clone(), i128::from(quantity)));
                i128::from(quantity)
            }
        };
        if !stackable && total > 1 {
            return Err(value("A distinct object cannot exist in multiple holdings"));
        }
        if quantity != 0 {
            let stack = (item_id, text("owner_id"), text("holder_id"));
            if stacks.contains(&stack) {
                return Err(value("Identical items with the same owner and holder must stack"));
            }
            stacks.push(stack);
        }
        result.push(holding.clone());
    }
    Ok(result)
}

/// The arguments of one `transfer_holding` call, as the caller passes them.
#[derive(Debug, Clone, Copy)]
pub struct HoldingTransfer<'a> {
    pub source_id: &'a Json,
    pub destination_id: &'a Json,
    pub quantity: &'a Json,
    pub holder_id: &'a Json,
    /// `None` keeps the source's owner (loans, storage and returns).
    pub owner_id: Option<&'a Json>,
}

/// `transfer_holding(holdings, source_id=..., ...)`: move an exact quantity; loans and storage
/// preserve ownership unless it is explicitly changed.
pub fn transfer_holding(
    holdings: &[Json],
    transfer: HoldingTransfer<'_>,
    entity_ids: &BTreeSet<String>,
) -> DomainResult<Vec<JsonMap>> {
    let current = validate_holdings(holdings, entity_ids)?;
    let mut ids = Vec::with_capacity(3);
    for value in [transfer.source_id, transfer.destination_id, transfer.holder_id] {
        ids.push(validate_authority_identity_value(value, "Inventory transfer identity", false)?.to_owned());
    }
    let (source_id, destination_id, holder_id) = (ids[0].clone(), ids[1].clone(), ids[2].clone());
    let owner_id = match transfer.owner_id {
        Some(owner) => {
            Some(validate_authority_identity_value(owner, "Inventory transfer owner", false)?.to_owned())
        }
        None => None,
    };
    let Some(quantity) = exact_int(transfer.quantity).filter(|q| *q > 0) else {
        return Err(value("Inventory transfer quantity must be a positive whole number"));
    };
    // `{str(item["holding_id"]): dict(item) for item in current}`, in order.
    let mut by_id: Vec<(String, JsonMap)> = Vec::with_capacity(current.len() + 1);
    for item in current {
        let key = py_str(&item["holding_id"]);
        match by_id.iter_mut().find(|(existing, _)| *existing == key) {
            Some(entry) => entry.1 = item,
            None => by_id.push((key, item)),
        }
    }
    let position =
        |by_id: &[(String, JsonMap)], key: &str| by_id.iter().position(|(existing, _)| existing == key);
    let quantity_of = |item: &JsonMap| exact_int(&item["quantity"]).expect("validated");
    let Some(source_at) = position(&by_id, &source_id).filter(|at| quantity <= quantity_of(&by_id[*at].1))
    else {
        return Err(value("Inventory transfer exceeds the available source quantity"));
    };
    let destination_owner = match owner_id {
        Some(owner) => Json::String(owner),
        None => by_id[source_at].1["owner_id"].clone(),
    };
    if source_id == destination_id {
        if quantity != quantity_of(&by_id[source_at].1) {
            return Err(value("A partial transfer needs a different destination holding identity"));
        }
        let source = &mut by_id[source_at].1;
        source.insert("holder_id".into(), Json::String(holder_id));
        source.insert("owner_id".into(), destination_owner);
    } else {
        let destination_at = match position(&by_id, &destination_id) {
            Some(at) => at,
            None => {
                let mut destination = by_id[source_at].1.clone();
                destination.insert("holding_id".into(), Json::String(destination_id.clone()));
                destination.insert("quantity".into(), json!(0));
                destination.insert("holder_id".into(), Json::String(holder_id.clone()));
                destination.insert("owner_id".into(), destination_owner.clone());
                by_id.push((destination_id.clone(), destination));
                by_id.len() - 1
            }
        };
        let (source, destination) = (&by_id[source_at].1, &by_id[destination_at].1);
        let differs =
            |fields: &[&str]| fields.iter().any(|field| !py_eq(&destination[*field], &source[*field]));
        if differs(&["item_id", "name", "kind", "stackable"]) {
            return Err(value("Inventory transfer cannot convert items or currencies"));
        }
        if !py_eq(&destination["owner_id"], &destination_owner)
            || !py_eq(&destination["holder_id"], &Json::String(holder_id))
        {
            return Err(value("Inventory destination has a different owner or holder"));
        }
        if differs(&["visibility", "known_by"]) {
            return Err(value("Inventory transfer cannot merge different knowledge boundaries"));
        }
        let source_quantity = quantity_of(source) - quantity;
        // Both are at most 2**53 - 1, so the sum fits; `validate_holdings` refuses it above that.
        let destination_quantity = quantity_of(destination) + quantity;
        by_id[source_at].1.insert("quantity".into(), json!(source_quantity));
        by_id[destination_at].1.insert("quantity".into(), json!(destination_quantity));
    }
    let updated: Vec<Json> = by_id.into_iter().map(|(_, item)| Json::Object(item)).collect();
    validate_holdings(&updated, entity_ids)
}

/// `player_inventory(holdings, visible_entities=..., character_id=...)`: release visibility,
/// not character knowledge, projects public holdings and labels.
pub fn player_inventory(
    holdings: &[Json],
    visible_entities: &JsonMap,
    character_id: &str,
) -> DomainResult<JsonMap> {
    let character = Json::from(character_id);
    let mut rows = Vec::new();
    if visible_entities.contains_key(character_id) {
        for item in holdings {
            if !py_eq(py_item(item, "visibility")?, &Json::from("player_known"))
                || !py_truthy(py_item(item, "quantity")?)
            {
                continue;
            }
            let (owner, holder) = (py_item(item, "owner_id")?, py_item(item, "holder_id")?);
            if !py_eq(&character, owner) && !py_eq(&character, holder) {
                continue;
            }
            let label = |id: &Json| {
                visible_entities.get(&py_str(id)).cloned().unwrap_or_else(|| Json::from("Unknown"))
            };
            rows.push(json!({
                "holding_id": py_item(item, "holding_id")?,
                "name": py_item(item, "name")?,
                "kind": py_item(item, "kind")?,
                "quantity": py_item(item, "quantity")?,
                "owner": label(owner),
                "holder": label(holder),
                "carried": py_eq(holder, &character),
                "owned": py_eq(owner, &character),
            }));
        }
    }
    Ok(json!({"character_id": character_id, "items": rows}).as_object().cloned().expect("an object"))
}
