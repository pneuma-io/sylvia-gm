//! `application/reference_text.py`: bounding model excerpts independently of the admitted UTF-8
//! source size: `text_excerpt` and `matching_text_excerpts`, with their limits.
//!
//! Offsets and lengths are Python's: counted in characters (code points), never bytes.

use crate::knowledge_search::{for_each_knowledge_term, knowledge_terms};
use std::collections::BTreeSet;
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
///
/// Windows are counted in characters and sliced at their byte boundaries; only the query's terms
/// are counted, and only kept excerpts are copied.
pub fn matching_text_excerpts(text: &str, query: &str, limit: i64) -> Vec<(i64, String)> {
    let terms: BTreeSet<String> = knowledge_terms(query).into_iter().collect();
    let terms: Vec<&str> = terms.iter().map(String::as_str).collect();
    let step = REFERENCE_EXCERPT_CHARS - 512;
    let length = text.chars().count();
    // The character offsets that start or end a window, and their byte positions, in one pass.
    let mut wanted: Vec<usize> = (0..length)
        .step_by(step)
        .flat_map(|offset| [offset, (offset + REFERENCE_EXCERPT_CHARS).min(length)])
        .collect();
    wanted.sort_unstable();
    wanted.dedup();
    let mut bytes = Vec::with_capacity(wanted.len());
    let mut next = wanted.iter().peekable();
    for (index, (at, _)) in text.char_indices().enumerate() {
        while next.peek().is_some_and(|wanted| **wanted == index) {
            bytes.push(at);
            next.next();
        }
    }
    bytes.extend(next.map(|_| text.len()));
    let byte = |offset: usize| bytes[wanted.binary_search(&offset).expect("a window boundary")];
    let mut counts = vec![0u64; terms.len()];
    let mut candidates: Vec<(usize, u64, usize, &str)> = Vec::new();
    for offset in (0..length).step_by(step) {
        let part = &text[byte(offset)..byte((offset + REFERENCE_EXCERPT_CHARS).min(length))];
        counts.fill(0);
        if !terms.is_empty() {
            for_each_knowledge_term(part, |word| {
                if let Ok(index) = terms.binary_search(&word) {
                    counts[index] += 1;
                }
            });
        }
        let present = counts.iter().filter(|count| **count > 0).count();
        if present > 0 {
            candidates.push((present, counts.iter().sum(), offset, part));
        }
    }
    // `nlargest(limit, (coverage, frequency, -offset, offset, part))`.
    candidates
        .sort_by(|left, right| right.0.cmp(&left.0).then(right.1.cmp(&left.1)).then(left.2.cmp(&right.2)));
    let keep = usize::try_from(limit.max(0)).unwrap_or(usize::MAX);
    candidates.into_iter().take(keep).map(|(_, _, offset, part)| (offset as i64, part.to_owned())).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    /// The implementation before the fast path, kept to prove the outputs equal.
    fn reference_matching_text_excerpts(text: &str, query: &str, limit: i64) -> Vec<(i64, String)> {
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
        candidates.sort_by(|left, right| {
            right.0.cmp(&left.0).then(right.1.cmp(&left.1)).then(left.2.cmp(&right.2))
        });
        let keep = usize::try_from(limit.max(0)).unwrap_or(usize::MAX);
        candidates.into_iter().take(keep).map(|(_, _, offset, part)| (offset as i64, part)).collect()
    }

    fn filler(characters: usize) -> String {
        // Words that are never query terms, a character each beyond ASCII now and then.
        let words = ["mist ", "stone ", "tower ", "é ", "rain "];
        let mut text = String::new();
        let mut index = 0;
        while text.chars().count() < characters {
            text.push_str(words[index % words.len()]);
            index += 1;
        }
        text.chars().take(characters).collect()
    }

    #[test]
    fn a_term_across_a_window_end_is_found_in_the_overlapping_window() {
        // "lantern" starts 4 characters before the first window ends, so the first window holds
        // only "lant" and the second (from 15,488) holds the whole word.
        let mut text = filler(REFERENCE_EXCERPT_CHARS - 5);
        text.push_str(" lantern ");
        text.push_str(&filler(20_000));
        let found = matching_text_excerpts(&text, "Lantern", 5);
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].0, (REFERENCE_EXCERPT_CHARS - 512) as i64);
        assert_eq!(found, reference_matching_text_excerpts(&text, "Lantern", 5));
    }

    #[test]
    fn tied_windows_keep_the_earliest_first() {
        // One "rift" in each of three windows: the same coverage and frequency, so offset order.
        let step = REFERENCE_EXCERPT_CHARS - 512;
        let mut text = String::new();
        for _ in 0..3 {
            text.push_str("rift ");
            text.push_str(&filler(step - 6));
            text.push(' ');
        }
        let found = matching_text_excerpts(&text, "rift", 2);
        assert_eq!(found.iter().map(|(offset, _)| *offset).collect::<Vec<_>>(), vec![0, step as i64]);
        assert_eq!(found, reference_matching_text_excerpts(&text, "rift", 2));
    }

    #[test]
    fn the_fast_excerpts_equal_the_reference_on_generated_text() {
        let words = [
            "Rift",
            "rift's",
            "lantern",
            "Lantern’s",
            "tower",
            "The",
            "ß",
            "strasse",
            "İ",
            "東京",
            "mist",
            "\n",
            "x_y",
            "42",
            "o'clock",
            "Ǆ",
        ];
        let mut seed: u64 = 0x0dd_ba11_cafe;
        let mut next = || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            seed
        };
        for round in 0..12 {
            let size = [10, 900, 15_487, 15_488, 16_000, 16_001, 31_000, 47_000][round % 8] + round;
            let mut text = String::new();
            while text.chars().count() < size {
                text.push_str(words[(next() % words.len() as u64) as usize]);
                text.push(if next().is_multiple_of(4) { '\n' } else { ' ' });
            }
            for query in ["rift", "Lantern tower", "STRASSE ß", "the a", "", "東京 mist rift's", "o'clock 42"]
            {
                for limit in [0, 1, 3, 100, -1] {
                    assert_eq!(
                        matching_text_excerpts(&text, query, limit),
                        reference_matching_text_excerpts(&text, query, limit),
                        "round {round} {query:?} {limit}"
                    );
                }
            }
        }
        assert!(matching_text_excerpts("", "rift", 3).is_empty());
    }

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
