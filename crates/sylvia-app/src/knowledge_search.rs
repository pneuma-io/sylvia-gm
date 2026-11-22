//! `application/knowledge_search.py`: ranking ephemeral references from admitted campaign bytes,
//! with no parallel search store: `knowledge_terms`, `KnowledgeReference`,
//! `bounded_search_page` and `rank_references`.
//!
//! Words are Python's `re.findall(r"[^\W_]+(?:['’][^\W_]+)?", value.casefold())`: runs of
//! `str.isalnum()` characters, optionally joined once by an apostrophe, on Python's casefold.
//! Scores are Python's floats. Python sums some of them over a `set`, whose order follows the
//! string hash; here they are summed in sorted term order. Only an exact tie between two scores
//! could ever tell the orders apart.

use std::collections::{BTreeMap, BTreeSet, HashSet};
use sylvia_base::pystr::{casefold, is_alnum};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::{DomainError, DomainResult};

const STOP_WORDS: &[&str] = &[
    "a", "about", "an", "and", "are", "as", "at", "be", "been", "being", "by", "can", "could", "current",
    "did", "do", "does", "for", "from", "had", "has", "have", "he", "her", "here", "hers", "him", "his",
    "how", "i", "if", "in", "into", "is", "it", "its", "me", "my", "of", "on", "or", "our", "she", "should",
    "tell", "than", "that", "the", "their", "them", "there", "these", "they", "this", "those", "to", "us",
    "was", "we", "were", "what", "when", "where", "which", "who", "why", "will", "with", "would", "you",
    "your", "now",
];

/// `_SOURCE_ORDER`.
fn source_order(source: &str) -> Option<u8> {
    Some(match source {
        "campaign_state" => 0,
        "participant" => 1,
        "campaign_plan" => 2,
        "campaign_setting" => 3,
        "ruleset" => 4,
        _ => return None,
    })
}

/// `knowledge_terms(value)`: whole words, punctuation and possessives treated alike.
pub fn knowledge_terms(value: &str) -> Vec<String> {
    let folded: Vec<char> = casefold(value).chars().collect();
    let mut words = Vec::new();
    let mut at = 0;
    while at < folded.len() {
        if !is_alnum(folded[at]) {
            at += 1;
            continue;
        }
        let start = at;
        while at < folded.len() && is_alnum(folded[at]) {
            at += 1;
        }
        // `(?:['’][^\W_]+)?`: one apostrophe joining a further run, taken only when one follows.
        if at + 1 < folded.len() && matches!(folded[at], '\'' | '’') && is_alnum(folded[at + 1]) {
            at += 1;
            while at < folded.len() && is_alnum(folded[at]) {
                at += 1;
            }
        }
        words.push(folded[start..at].iter().collect::<String>());
    }
    words
        .into_iter()
        .filter(|word| !STOP_WORDS.contains(&word.as_str()))
        .map(|word| {
            // `word.removesuffix("'s").removesuffix("’s")`.
            let word = word.strip_suffix("'s").map_or(word.clone(), str::to_owned);
            word.strip_suffix("’s").map_or(word.clone(), str::to_owned)
        })
        .collect()
}

/// `KnowledgeReference`: separate internal searchable text from the reference-only public result.
#[derive(Debug, Clone, PartialEq)]
pub struct KnowledgeReference {
    pub result: JsonMap,
    pub identity: String,
    pub body: String,
}

fn rejected(message: &str, code: &str) -> DomainError {
    DomainError::command_rejected_with(message, code, Vec::new())
}

/// `bounded_search_page(metadata, matches, offset=..., limit=..., maximum_bytes=...)`: exact
/// references a page at a time, never dropping one that exceeds the remaining bytes.
pub fn bounded_search_page(
    metadata: &JsonMap,
    matches: &[JsonMap],
    offset: i64,
    limit: i64,
    maximum_bytes: i64,
) -> DomainResult<JsonMap> {
    if offset < 0 || offset as u64 > matches.len() as u64 {
        return Err(rejected(
            "Campaign knowledge parameter 'offset' is outside the current results",
            "campaign_knowledge_offset_invalid",
        ));
    }
    let offset = offset as usize;
    let mut selected: Vec<JsonMap> = Vec::new();
    let payload = |selected: &[JsonMap]| {
        let end = offset + selected.len();
        let mut value = metadata.clone();
        value.insert("offset".into(), (offset as i64).into());
        value.insert("limit".into(), limit.into());
        value.insert("matches".into(), Json::Array(selected.iter().cloned().map(Json::Object).collect()));
        value.insert("truncated".into(), (end < matches.len()).into());
        value
            .insert("next_offset".into(), if end < matches.len() { (end as i64).into() } else { Json::Null });
        value
    };
    let fits =
        |value: &JsonMap| canonical_json_bytes(&Json::Object(value.clone())).len() as i64 <= maximum_bytes;
    if !fits(&payload(&selected)) {
        return Err(rejected(
            "Campaign knowledge search metadata exceeds the reply byte bound",
            "campaign_knowledge_metadata_too_large",
        ));
    }
    // `matches[offset : offset + limit]`: a negative stop counts back from the end.
    let length = matches.len() as i128;
    let raw_stop = offset as i128 + i128::from(limit);
    let stop = if raw_stop < 0 { (length + raw_stop).max(0) } else { raw_stop.min(length) } as usize;
    for item in matches.get(offset..stop.max(offset)).unwrap_or(&[]) {
        selected.push(item.clone());
        if fits(&payload(&selected)) {
            continue;
        }
        selected.pop();
        if selected.is_empty() {
            return Err(rejected(
                "An exact campaign knowledge reference exceeds the reply byte bound",
                "campaign_knowledge_result_too_large",
            ));
        }
        break;
    }
    Ok(payload(&selected))
}

fn idf_of(idf: &BTreeMap<String, f64>, term: &str) -> f64 {
    idf.get(term).copied().unwrap_or_default()
}

fn label_of(result: &JsonMap) -> DomainResult<String> {
    result
        .get("label")
        .map(sylvia_domain::pyops::py_str)
        .ok_or_else(|| DomainError::uncaught("KeyError", "'label'"))
}

fn field_text(result: &JsonMap, key: &'static str) -> DomainResult<String> {
    result
        .get(key)
        .map(sylvia_domain::pyops::py_str)
        .ok_or_else(|| DomainError::uncaught("KeyError", format!("'{key}'")))
}

/// `rank_references(query, candidates)`: BM25 body relevance plus name and phrase weight, with
/// conservative body-only admission; distinct documents come before their further sections.
pub fn rank_references(query: &str, candidates: &[KnowledgeReference]) -> DomainResult<Vec<JsonMap>> {
    let query_words = knowledge_terms(query);
    let terms: BTreeSet<String> = query_words.iter().cloned().collect();
    if terms.is_empty() || candidates.is_empty() {
        return Ok(Vec::new());
    }
    let mut titles = Vec::new();
    let mut bodies: Vec<BTreeMap<String, u64>> = Vec::new();
    for item in candidates {
        titles.push(knowledge_terms(&format!("{} {}", item.identity, label_of(&item.result)?)));
        let mut counts = BTreeMap::new();
        for word in knowledge_terms(&item.body) {
            *counts.entry(word).or_insert(0u64) += 1;
        }
        bodies.push(counts);
    }
    let mut frequencies: BTreeMap<&str, u64> = BTreeMap::new();
    for (title, body) in titles.iter().zip(&bodies) {
        let present: BTreeSet<&str> =
            title.iter().map(String::as_str).chain(body.keys().map(String::as_str)).collect();
        for term in present.into_iter().filter(|term| terms.contains(*term)) {
            *frequencies.entry(term).or_insert(0) += 1;
        }
    }
    let count = candidates.len() as f64;
    let idf: BTreeMap<String, f64> = terms
        .iter()
        .map(|term| {
            let frequency = frequencies.get(term.as_str()).copied().unwrap_or(0) as f64;
            (term.clone(), (1.0 + (count - frequency + 0.5) / (frequency + 0.5)).ln())
        })
        .collect();
    let total_length: u64 = bodies.iter().map(|body| body.values().sum::<u64>()).sum();
    let average_length = (total_length as f64 / bodies.len() as f64).max(1.0);
    let pairs = |words: &[String]| -> BTreeSet<(String, String)> {
        words.windows(2).map(|pair| (pair[0].clone(), pair[1].clone())).collect()
    };
    let query_pairs = pairs(&query_words);
    let query_weight: f64 = idf.values().sum();
    let mut ranked: Vec<(u8, f64, u8, String, &JsonMap)> = Vec::new();
    for ((reference, title), body) in candidates.iter().zip(&titles).zip(&bodies) {
        let title_set: BTreeSet<&str> = title.iter().map(String::as_str).collect();
        let named: Vec<&str> =
            terms.iter().map(String::as_str).filter(|term| title_set.contains(term)).collect();
        let matched: Vec<&str> =
            terms.iter().map(String::as_str).filter(|term| body.contains_key(*term)).collect();
        let result = &reference.result;
        let path = field_text(result, "path")?;
        let document = path.split('#').next().unwrap_or_default().to_owned();
        let document_terms: BTreeSet<String> = knowledge_terms(&document).into_iter().collect();
        let title_pairs = pairs(title);
        let shared_pairs: Vec<&(String, String)> = query_pairs.intersection(&title_pairs).collect();
        let named_subject =
            terms.iter().any(|term| document_terms.contains(term)) || !shared_pairs.is_empty();
        let matched_weight: f64 = matched.iter().map(|term| idf_of(&idf, term)).sum();
        if !named_subject && matched_weight < 0.6 * query_weight {
            continue;
        }
        let length = body.values().sum::<u64>() as f64;
        let length_normalizer = 1.2 * (0.25 + 0.75 * length / average_length);
        let mut score: f64 = matched
            .iter()
            .map(|term| {
                let frequency = body[*term] as f64;
                idf_of(&idf, term) * frequency * 2.2 / (frequency + length_normalizer)
            })
            .sum();
        score += 4.0 * named.iter().map(|term| idf_of(&idf, term)).sum::<f64>();
        score += 2.0
            * shared_pairs.iter().map(|(left, right)| idf_of(&idf, left) + idf_of(&idf, right)).sum::<f64>();
        let label = label_of(result)?;
        if terms.iter().all(|term| document_terms.contains(term)) && casefold(&label) == "overview" {
            score += 4.0 * query_weight;
        }
        // Named current records take precedence; similarly named records remain distinct choices.
        let exact_name = knowledge_terms(&label).into_iter().collect::<BTreeSet<_>>() == terms;
        let source = result.get("source").ok_or_else(|| DomainError::uncaught("KeyError", "'source'"))?;
        let current = source == &Json::from("campaign_state")
            && ((result.get("kind") == Some(&Json::from("entity")) && !named.is_empty()) || exact_name);
        let precedence = if current && exact_name {
            0
        } else if current {
            1
        } else {
            2
        };
        let order = source_order(&sylvia_domain::pyops::py_str(source))
            .ok_or_else(|| DomainError::uncaught("KeyError", sylvia_domain::pyops::py_str(source)))?;
        ranked.push((precedence, -score, order, path, result));
    }
    // `ranked.sort(key=lambda item: item[:4])`: stable, by precedence, score, source and path.
    ranked.sort_by(|a, b| {
        a.0.cmp(&b.0)
            .then(a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal))
            .then(a.2.cmp(&b.2))
            .then(a.3.cmp(&b.3))
    });
    let mut seen: HashSet<(String, String)> = HashSet::new();
    let (mut distinct, mut additional) = (Vec::new(), Vec::new());
    for (_, _, _, path, result) in ranked {
        let source = result.get("source").map(sylvia_domain::pyops::py_str).unwrap_or_default();
        let document = (source, path.split('#').next().unwrap_or_default().to_owned());
        if seen.contains(&document) {
            additional.push(result.clone());
        } else {
            distinct.push(result.clone());
        }
        seen.insert(document);
    }
    distinct.extend(additional);
    Ok(distinct)
}
