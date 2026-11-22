//! `agent/_campaign_memory.py`: bounded working memory selected from authoritative campaign
//! records.
//!
//! Recency and relevance each get a turn, so neither unrelated recent events nor many older
//! name matches can starve the other; each cue is ranked on its own. The ranking is
//! `application/knowledge_search.rank_references`, lane 3's port; [`select_with`] takes it as a
//! function so the selection is proven on its own.

use serde_json::{json, Value as Json};
use std::collections::HashSet;
use sylvia_base::{pyjson, pystr, JsonMap};

const MAX_MEMORY_RECORDS: usize = 128;
const MAX_MEMORY_BYTES: usize = 48 * 1024;

/// `KnowledgeReference(result=..., identity=..., body=...)`.
#[derive(Debug, Clone, PartialEq)]
pub struct KnowledgeReference {
    pub result: JsonMap,
    pub identity: String,
    pub body: String,
}

/// `_is_entity(record)`.
fn is_entity(record: &JsonMap) -> bool {
    record.get("record_kind").and_then(Json::as_str) == Some("entity") || record.contains_key("entity_kind")
}

/// `_revision(record)`: the admitted chronology, or 0.
fn revision(record: &JsonMap) -> i64 {
    record.get("admitted_revision").filter(|value| !value.is_boolean()).and_then(Json::as_i64).unwrap_or(0)
}

/// `str(value)` for a JSON value.
fn py_str(value: &Json) -> String {
    match value {
        Json::String(text) => text.clone(),
        other => pystr::repr_json(other),
    }
}

/// `record.get(key, default)` as `str`.
fn text_or(record: &JsonMap, key: &str, default: &Json) -> String {
    py_str(record.get(key).unwrap_or(default))
}

/// `zip_longest(*groups)` flattened, skipping the fill values.
fn interleave(groups: &[Vec<usize>]) -> Vec<usize> {
    let longest = groups.iter().map(Vec::len).max().unwrap_or(0);
    (0..longest).flat_map(|row| groups.iter().filter_map(move |group| group.get(row).copied())).collect()
}

/// `select_campaign_memory(facts, queries=...)` with the given ranking.
pub fn select_with(
    facts: &[JsonMap],
    queries: &[String],
    rank: impl Fn(&str, &[KnowledgeReference]) -> Vec<JsonMap>,
) -> (Vec<JsonMap>, JsonMap) {
    let records: Vec<JsonMap> = facts.to_vec();
    let references: Vec<KnowledgeReference> = records
        .iter()
        .enumerate()
        .map(|(index, item)| {
            let index_json = Json::from(index);
            let record_id_or_index = item.get("record_id").cloned().unwrap_or_else(|| index_json.clone());
            let mut result = JsonMap::new();
            result.insert("source".into(), "campaign_state".into());
            result.insert("path".into(), py_str(&record_id_or_index).into());
            result.insert("label".into(), text_or(item, "name", &record_id_or_index).into());
            result.insert("kind".into(), if is_entity(item) { "entity" } else { "fact" }.into());
            result.insert("index".into(), index_json);
            KnowledgeReference {
                result,
                identity: text_or(item, "record_id", &Json::String(String::new())),
                body: String::from_utf8(pyjson::canonical_bytes(&Json::Object(item.clone())))
                    .expect("UTF-8 JSON"),
            }
        })
        .collect();
    let rankings: Vec<Vec<usize>> = queries
        .iter()
        .filter(|query| !pystr::strip(query).is_empty())
        .map(|query| {
            let bounded: String = query.chars().take(2000).collect();
            rank(&bounded, &references)
                .iter()
                .filter_map(|item| item.get("index").and_then(Json::as_u64).map(|index| index as usize))
                .collect()
        })
        .collect();
    let mut seen_relevant = HashSet::new();
    let relevant: Vec<usize> =
        interleave(&rankings).into_iter().filter(|index| seen_relevant.insert(*index)).collect();
    // `sorted(range(n), key=(revision, index), reverse=True)`.
    let mut recent: Vec<usize> = (0..records.len()).collect();
    recent.sort_by(|&a, &b| (revision(&records[b]), b).cmp(&(revision(&records[a]), a)));
    let lanes = [
        recent.iter().copied().filter(|&i| !is_entity(&records[i])).collect::<Vec<_>>(),
        relevant.iter().copied().filter(|&i| is_entity(&records[i])).collect(),
        relevant.iter().copied().filter(|&i| !is_entity(&records[i])).collect(),
        recent.iter().copied().filter(|&i| is_entity(&records[i])).collect(),
    ];
    let mut selected: Vec<JsonMap> = Vec::new();
    let mut seen = HashSet::new();
    let mut used_bytes = 2usize;
    for index in interleave(&lanes) {
        if !seen.insert(index) {
            continue;
        }
        let record = &records[index];
        let size =
            pyjson::canonical_bytes(&Json::Object(record.clone())).len() + usize::from(!selected.is_empty());
        if selected.len() < MAX_MEMORY_RECORDS && used_bytes + size <= MAX_MEMORY_BYTES {
            selected.push(record.clone());
            used_bytes += size;
        }
    }
    let summary = json!({
        "total_records": records.len(),
        "selected_records": selected.len(),
        "omitted_records": records.len() - selected.len(),
        "maximum_records": MAX_MEMORY_RECORDS,
        "maximum_bytes": MAX_MEMORY_BYTES,
        "meaning": concat!(
            "Partial selection of recent facts and relevant/current entities, not all memory. ",
            "admitted_revision orders when records were saved, not in-world time. Old plans and ",
            "relative dates may have later outcomes. Search and read exact records before denying ",
            "an earlier event or repeating a planned task. Omission or lookup failure is not ",
            "evidence that an event never happened. NPC knowledge remains limited by known_by."
        ),
    });
    let Json::Object(summary) = summary else { unreachable!() };
    (selected, summary)
}
