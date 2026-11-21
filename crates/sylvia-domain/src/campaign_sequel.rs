//! `domain/campaign_sequel.py`: reviewed story additions; source preparation never
//! reinitializes live campaign state.

use crate::error::{DomainError, DomainResult};
use crate::package_library::canonical_json_bytes;
use crate::pyops::{py_eq, py_get_attr, py_hashable, py_item, py_iter, py_str, py_unhashable};
use serde_json::json;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pystr::{self, repr_json};
use sylvia_base::{pyjson, Json, JsonMap};

/// `MAX_ADVENTURES`.
pub const MAX_ADVENTURES: usize = 32;

/// `json_object(value)`: narrow an already validated JSON object.
pub fn json_object(value: &Json) -> DomainResult<&JsonMap> {
    value.as_object().ok_or_else(|| DomainError::value("Campaign adventure metadata must be an object"))
}

/// `digest(value)`: SHA-256 of the canonical JSON bytes.
pub fn digest(value: &Json) -> String {
    sha256_hex(canonical_json_bytes(value))
}

/// `mapping[key]` for the identity mapping, with Python's `KeyError` (or `TypeError`).
fn mapped(mapping: &JsonMap, key: &Json) -> DomainResult<String> {
    if !py_hashable(key) {
        return Err(py_unhashable(key));
    }
    key.as_str()
        .and_then(|name| mapping.get(name))
        .and_then(Json::as_str)
        .map(str::to_owned)
        .ok_or_else(|| DomainError::uncaught("KeyError", repr_json(key)))
}

fn id_of(item: &JsonMap) -> &Json {
    &item["id"]
}

/// `prepare_story(source, number)`: allocate reviewed, deterministic story IDs without
/// renaming world or participant identities. Returns the prepared Plan and the mapping.
pub fn prepare_story(source: &JsonMap, number: i64) -> DomainResult<(JsonMap, JsonMap)> {
    let source_json = Json::Object(source.clone());
    let mut plan = match pyjson::loads(&String::from_utf8(canonical_json_bytes(&source_json)).expect("UTF-8"))
    {
        Ok(Json::Object(plan)) => plan,
        _ => unreachable!("a canonical object reads back as an object"),
    };
    let prefix = format!("adventure-{number}-{}:", &digest(&source_json)[..8]);
    let mut mapping = JsonMap::new();
    for field in ["structure", "objectives", "secrets"] {
        let Some(items) = plan.get(field) else { continue };
        for item in py_iter(items)? {
            if let Json::Object(map) = &item {
                if map.contains_key("id") && !map.contains_key("setting_fact_id") {
                    let old = py_str(id_of(map));
                    if field != "secrets" && mapping.contains_key(&old) {
                        return Err(DomainError::value(
                            "Prepared story identities overlap; author distinct IDs in Builder",
                        ));
                    }
                    mapping.insert(old.clone(), Json::String(format!("{prefix}{old}")));
                }
            }
        }
    }
    let empty = Json::Object(JsonMap::new());
    let ending = plan.get("ending").unwrap_or(&empty);
    let outcomes = py_get_attr(ending, "outcomes")?.map(py_iter).transpose()?.unwrap_or_default();
    for item in &outcomes {
        let old = py_str(py_item(item, "id")?);
        mapping.insert(old.clone(), Json::String(format!("{prefix}{old}")));
    }
    if mapping.values().any(|item| pystr::len(item.as_str().unwrap_or_default()) > 160) {
        return Err(DomainError::value(
            "An authored identity is too long to keep separate; shorten it in Builder",
        ));
    }
    for field in ["structure", "objectives", "secrets"] {
        // Python iterates the field's own value; only `dict` items are rewritten in place.
        let Some(Json::Array(items)) = plan.get_mut(field) else { continue };
        for item in items.iter_mut() {
            let Json::Object(map) = item else { continue };
            if !map.contains_key("setting_fact_id") && map.contains_key("id") {
                let new = mapped(&mapping, &Json::String(py_str(id_of(map))))?;
                map.insert("id".into(), Json::String(new));
            }
            if field == "structure" {
                let objectives =
                    map.get("objectives").ok_or_else(|| DomainError::uncaught("KeyError", "'objectives'"))?;
                let mut rewritten = Vec::new();
                for key in py_iter(objectives)? {
                    rewritten.push(Json::String(mapped(&mapping, &Json::String(py_str(&key)))?));
                }
                map.insert("objectives".into(), Json::Array(rewritten));
            }
            if field == "secrets" {
                let reveal =
                    map.get("reveal").ok_or_else(|| DomainError::uncaught("KeyError", "'reveal'"))?;
                let new = mapped(&mapping, py_item(reveal, "from_act")?)?;
                // The read above proved `reveal` is a dict.
                map.get_mut("reveal")
                    .and_then(Json::as_object_mut)
                    .expect("a dict")
                    .insert("from_act".into(), Json::String(new));
            }
        }
    }
    if let Some(Json::Object(ending)) = plan.get_mut("ending") {
        if let Some(Json::Array(outcomes)) = ending.get_mut("outcomes") {
            for item in outcomes.iter_mut() {
                // The first pass proved each outcome is a dict with an `id`.
                let map = item.as_object_mut().expect("a dict");
                let new = mapped(&mapping, &Json::String(py_str(id_of(map))))?;
                map.insert("id".into(), Json::String(new));
            }
        }
    }
    Ok((plan, mapping))
}

/// `review_payload(value)`: an explicit narrative connection, including treatment of any
/// narrated future.
pub fn review_payload(value: &Json) -> DomainResult<JsonMap> {
    let fields = ["connection", "timeline", "reviewed"];
    let Some(map) =
        value.as_object().filter(|map| map.len() == 3 && fields.iter().all(|f| map.contains_key(*f)))
    else {
        return Err(DomainError::value(
            "Review how the adventure connects to this campaign before appending",
        ));
    };
    let note = match &map["connection"] {
        Json::String(note) if !pystr::strip(note).is_empty() && pystr::len(note) <= 4000 => note,
        _ => return Err(DomainError::value("Describe how this adventure fits the retained campaign facts")),
    };
    if !py_eq(&map["timeline"], &Json::from("after")) || map["reviewed"] != Json::Bool(true) {
        return Err(DomainError::value("Confirm the reviewed timeline and preserved campaign facts"));
    }
    let mut payload = map.clone();
    payload.insert("connection".into(), Json::from(pystr::strip(note)));
    Ok(payload)
}

/// `repr(dict(value))`, with the exceptions `dict()` raises for a value that is not a mapping
/// or a sequence of pairs.
fn dict_repr(value: &Json) -> DomainResult<String> {
    if let Json::Object(map) = value {
        return Ok(repr_json(&Json::Object(map.clone())));
    }
    let mut pairs: Vec<(Json, Json)> = Vec::new();
    for (index, element) in py_iter(value)?.iter().enumerate() {
        let parts = match element {
            Json::Array(_) | Json::String(_) | Json::Object(_) => py_iter(element)?,
            _ => {
                return Err(DomainError::type_error(format!(
                    "cannot convert dictionary update sequence element #{index} to a sequence"
                )))
            }
        };
        if parts.len() != 2 {
            return Err(DomainError::value(format!(
                "dictionary update sequence element #{index} has length {}; 2 is required",
                parts.len()
            )));
        }
        let (key, item) = (parts[0].clone(), parts[1].clone());
        if !py_hashable(&key) {
            return Err(py_unhashable(&key));
        }
        match pairs.iter_mut().find(|(existing, _)| py_eq(existing, &key)) {
            Some(pair) => pair.1 = item,
            None => pairs.push((key, item)),
        }
    }
    let body: Vec<String> =
        pairs.iter().map(|(key, item)| format!("{}: {}", repr_json(key), repr_json(item))).collect();
    Ok(format!("{{{}}}", body.join(", ")))
}

/// `metadata["request"]["review"]`.
fn review(metadata: &Json) -> DomainResult<&Json> {
    py_item(py_item(metadata, "request")?, "review")
}

/// `sequel_markdown(content)`: make the active preparation and the retained-state rule explicit
/// to the GM.
pub fn sequel_markdown(content: &JsonMap) -> DomainResult<String> {
    let metadata = content.get("sequel");
    let text = content.get("campaign_plan_markdown").map(py_str).unwrap_or_default();
    let Some(metadata @ Json::Object(_)) = metadata else { return Ok(text) };
    let review = review(metadata)?;
    let timeline = py_str(py_item(review, "timeline")?);
    let connection = py_str(py_item(review, "connection")?);
    let mapping = dict_repr(py_item(metadata, "identity_mapping")?)?;
    Ok(format!(
        "# Appended authored adventure\n\n\
         This is new private preparation in the SAME campaign. All played history, characters, \
         world facts, boundaries, unresolved consequences and mechanical values remain true. \
         Do not apply this source's initial_state, starting party, starting location, or party \
         placements as a reset. Establish any transition through play. Earlier narrated futures \
         remain binding; do not rewind or erase them.\n\n\
         Reviewed timeline: {timeline}. Connection: {connection}\n\n\
         Read campaign_plan path 'adventures' for the complete retained sequence; use \
         'adventures/N/structured/...' or 'adventures/N/markdown' for earlier preparation. \
         The active structured Plan uses the reviewed identity mapping below. Use its exact \
         IDs in scenes, progress and reveals, even when the original prose uses older IDs.\n\n\
         Identity mapping: {mapping}\n\n{text}"
    ))
}

/// `sequel_preparation(content)`: application-owned continuation instructions for the
/// structured GM context.
pub fn sequel_preparation(content: &JsonMap) -> DomainResult<Option<JsonMap>> {
    let Some(metadata @ Json::Object(_)) = content.get("sequel") else { return Ok(None) };
    let review = review(metadata)?;
    let number = py_item(py_item(metadata, "preview")?, "number")?.clone();
    let connection = py_item(review, "connection")?.clone();
    let timeline = py_item(review, "timeline")?.clone();
    let mapping = py_item(metadata, "identity_mapping")?.clone();
    let payload = json!({
        "number": number,
        "connection": connection,
        "timeline": timeline,
        "continuity_rule": "Continue the SAME campaign. Keep played events, characters, world \
            facts, boundaries, unresolved consequences and mechanical values true.",
        "setup_rule": "Do not apply this source's initial_state, starting party, starting \
            location or placements as a reset. Establish any transition through play.",
        "timeline_rule": "Already narrated futures remain binding. Follow them; do not rewind \
            or erase them. Read the saved history when their details are needed.",
        "retained_preparation": {
            "source": "campaign_plan",
            "path": "adventures",
            "detail_paths": ["adventures/N/structured/...", "adventures/N/markdown"],
        },
        "identity_rule": "Use the active structured Plan's mapped IDs for scenes, progress \
            and reveals even if source prose uses older IDs. World identities are unchanged.",
        "identity_mapping": mapping,
    });
    Ok(payload.as_object().cloned())
}
