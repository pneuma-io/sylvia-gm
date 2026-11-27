//! `application/reference_text.py`: bounding model excerpts independently of the admitted UTF-8
//! source size: `text_excerpt` and `matching_text_excerpts`, with their limits.
//!
//! Offsets and lengths are Python's: counted in characters (code points), never bytes.

use crate::knowledge_search::knowledge_terms;
use std::collections::{BTreeMap, BTreeSet};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::{DomainError, DomainResult};

/// `REFERENCE_REPLY_BYTE_LIMIT`.
pub const REFERENCE_REPLY_BYTE_LIMIT: i64 = 64 * 1024;
/// `REFERENCE_CONTENT_BYTE_LIMIT`.
pub const REFERENCE_CONTENT_BYTE_LIMIT: i64 = 48 * 1024;
/// `REFERENCE_EXCERPT_CHARS`.
pub const REFERENCE_EXCERPT_CHARS: usize = 16_000;

/// `TextExcerpt`.
#[derive(Debug, Clone, PartialEq)]
pub struct TextExcerpt {
    pub content: String,
    /// `{"kind": "text_excerpt", "offset", "characters", "total_characters"}`.
    pub selection: JsonMap,
    pub truncated: bool,
    pub next_offset: Option<i64>,
}

fn rejected(message: &str, code: &str) -> DomainError {
    DomainError::command_rejected_with(message, code, Vec::new())
}

/// The JSON-escaped length of `text` without its quotes (`len(canonical_json_bytes(text)) - 2`).
fn escaped_length(text: &str) -> usize {
    canonical_json_bytes(&Json::from(text)).len() - 2
}

/// `text_excerpt(text, offset, maximum_bytes=...)`: one UTF-8-safe excerpt and its exact
/// character continuation.
pub fn text_excerpt(text: &str, offset: i64, maximum_bytes: i64) -> DomainResult<TextExcerpt> {
    if !(1..=REFERENCE_REPLY_BYTE_LIMIT).contains(&maximum_bytes) {
        return Err(rejected(
            "Reference reply limit must be from 1 to 65536 bytes",
            "campaign_reference_limit_invalid",
        ));
    }
    let characters: Vec<char> = text.chars().collect();
    let total = characters.len();
    if offset < 0 || offset as u64 > total as u64 || (total > 0 && offset as usize == total) {
        return Err(rejected("Reference text offset is unavailable", "campaign_reference_offset_invalid"));
    }
    let offset = offset as usize;
    let part: String = characters[offset..(offset + REFERENCE_EXCERPT_CHARS).min(total)].iter().collect();
    let budget = maximum_bytes.min(REFERENCE_CONTENT_BYTE_LIMIT) as usize;
    // `part.encode("utf-8")[:budget].decode("utf-8", errors="ignore")`: a cut character drops.
    let mut cut = budget.min(part.len());
    while !part.is_char_boundary(cut) {
        cut -= 1;
    }
    let mut content: Vec<char> = part[..cut].chars().collect();
    // JSON escaping can make valid text larger on the wire (control characters, say).
    let escaped = |chars: &[char]| escaped_length(&chars.iter().collect::<String>());
    if escaped(&content) > budget {
        let (mut low, mut high) = (0usize, content.len());
        while low < high {
            let middle = (low + high).div_ceil(2);
            if escaped(&content[..middle]) <= budget {
                low = middle;
            } else {
                high = middle - 1;
            }
        }
        content.truncate(low);
    }
    if !part.is_empty() && content.is_empty() {
        return Err(rejected(
            "Reference reply limit cannot hold the next UTF-8 character",
            "campaign_reference_limit_invalid",
        ));
    }
    let following = offset + content.len();
    let mut selection = JsonMap::new();
    selection.insert("kind".into(), "text_excerpt".into());
    selection.insert("offset".into(), (offset as i64).into());
    selection.insert("characters".into(), (content.len() as i64).into());
    selection.insert("total_characters".into(), (total as i64).into());
    Ok(TextExcerpt {
        content: content.into_iter().collect(),
        selection,
        truncated: following < total,
        next_offset: (following < total).then_some(following as i64),
    })
}

/// `matching_text_excerpts(text, query, limit)`: the best `limit` excerpts of the text (offset
/// and content), by how many query terms each holds, then how often, then the earliest.
/// Excerpts overlap by 512 characters so a term at a boundary is still found.
pub fn matching_text_excerpts(text: &str, query: &str, limit: i64) -> Vec<(i64, String)> {
    let terms: BTreeSet<String> = knowledge_terms(query).into_iter().collect();
    let characters: Vec<char> = text.chars().collect();
    let mut candidates: Vec<(usize, u64, usize, String)> = Vec::new();
    let mut offset = 0;
    while offset < characters.len() {
        let part: String =
            characters[offset..(offset + REFERENCE_EXCERPT_CHARS).min(characters.len())].iter().collect();
        let mut counts: BTreeMap<String, u64> = BTreeMap::new();
        for word in knowledge_terms(&part) {
            *counts.entry(word).or_insert(0) += 1;
        }
        let present: Vec<&String> = terms.iter().filter(|term| counts.contains_key(*term)).collect();
        if !present.is_empty() {
            let frequency = present.iter().map(|term| counts[*term]).sum();
            candidates.push((present.len(), frequency, offset, part));
        }
        offset += REFERENCE_EXCERPT_CHARS - 512;
    }
    // `nlargest(limit, (coverage, frequency, -offset, offset, part))`.
    candidates
        .sort_by(|left, right| right.0.cmp(&left.0).then(right.1.cmp(&left.1)).then(left.2.cmp(&right.2)));
    let keep = usize::try_from(limit.max(0)).unwrap_or(usize::MAX);
    candidates.into_iter().take(keep).map(|(_, _, offset, part)| (offset as i64, part)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn excerpts_count_characters_and_drop_a_cut_character() {
        let excerpt = text_excerpt("aé b", 1, 2).unwrap();
        assert_eq!(excerpt.content, "é");
        assert_eq!(excerpt.next_offset, Some(2));
        assert!(text_excerpt("é", 0, 1).is_err());
        assert!(text_excerpt("ab", 2, 10).is_err());
        assert_eq!(text_excerpt("", 0, 10).unwrap().content, "");
        // A control character escapes to six bytes, so only the first fits in eight.
        assert_eq!(text_excerpt("\u{1}\u{1}", 0, 8).unwrap().content, "\u{1}");
    }
}
