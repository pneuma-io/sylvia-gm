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
use sylvia_base::pystr::{casefold, casefold_char, is_alnum};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::{DomainError, DomainResult};

/// `_STOP_WORDS`, sorted for a binary search.
const STOP_WORDS: &[&str] = &[
    "a", "about", "an", "and", "are", "as", "at", "be", "been", "being", "by", "can", "could", "current",
    "did", "do", "does", "for", "from", "had", "has", "have", "he", "her", "here", "hers", "him", "his",
    "how", "i", "if", "in", "into", "is", "it", "its", "me", "my", "now", "of", "on", "or", "our", "she",
    "should", "tell", "than", "that", "the", "their", "them", "there", "these", "they", "this", "those",
    "to", "us", "was", "we", "were", "what", "when", "where", "which", "who", "why", "will", "with", "would",
    "you", "your",
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

/// `str.isalnum()` for one character, with ASCII answered directly.
fn alnum(character: char) -> bool {
    if character.is_ascii() {
        character.is_ascii_alphanumeric()
    } else {
        is_alnum(character)
    }
}

/// The longest of `STOP_WORDS` ("current"), in bytes.
const LONGEST_STOP_WORD: usize = 7;

/// Whether `word` is one of `STOP_WORDS` (kept sorted for this search; a longer word is none).
fn is_stop_word(word: &str) -> bool {
    word.len() <= LONGEST_STOP_WORD && STOP_WORDS.binary_search(&word).is_ok()
}

/// Where [`Words`] is in Python's `[^\W_]+(?:['’][^\W_]+)?`.
#[derive(Clone, Copy)]
enum Place {
    /// Between words.
    Outside,
    /// In the first run of alphanumerics.
    First,
    /// After the first run, at an apostrophe that joins only if an alphanumeric follows.
    Apostrophe(char),
    /// In the run after a joining apostrophe; the next apostrophe ends the word.
    Second,
}

/// The words of a folded character stream, matched as Python's leftmost, greedy `findall`
/// matches them, each passed on (stop words dropped, `'s` and `’s` removed) from one reused
/// buffer.
struct Words<F> {
    word: String,
    place: Place,
    visit: F,
}

impl<F: FnMut(&str)> Words<F> {
    fn feed(&mut self, character: char) {
        let joins = alnum(character);
        match self.place {
            Place::Outside if joins => {
                self.word.push(character);
                self.place = Place::First;
            }
            Place::Outside => {}
            Place::First | Place::Second if joins => self.word.push(character),
            Place::First if matches!(character, '\'' | '’') => self.place = Place::Apostrophe(character),
            Place::Apostrophe(apostrophe) if joins => {
                self.word.push(apostrophe);
                self.word.push(character);
                self.place = Place::Second;
            }
            // The word ends here; the character itself cannot start one (it is not alphanumeric).
            Place::First | Place::Second | Place::Apostrophe(_) => self.end(),
        }
    }

    fn end(&mut self) {
        if !matches!(self.place, Place::Outside) && !is_stop_word(&self.word) {
            // `word.removesuffix("'s").removesuffix("’s")`.
            let word = self.word.strip_suffix("'s").unwrap_or(&self.word);
            (self.visit)(word.strip_suffix("’s").unwrap_or(word));
        }
        self.word.clear();
        self.place = Place::Outside;
    }
}

/// [`knowledge_terms`] without collecting: each term in order, from one reused buffer, so a
/// caller that only counts or looks terms up allocates nothing per word. Each character is
/// folded as `str.casefold()` folds it (per character, ASCII to its lowercase) and matched as it
/// comes, with no folded copy of the text.
pub fn for_each_knowledge_term(value: &str, visit: impl FnMut(&str)) {
    let mut words = Words { word: String::new(), place: Place::Outside, visit };
    let bytes = value.as_bytes();
    let mut at = 0;
    while at < bytes.len() {
        let byte = bytes[at];
        if byte.is_ascii() {
            words.feed(char::from(byte.to_ascii_lowercase()));
            at += 1;
            continue;
        }
        let character = value[at..].chars().next().expect("a character starts here");
        at += character.len_utf8();
        match casefold_char(character) {
            Some(folded) => folded.chars().for_each(|part| words.feed(part)),
            None => words.feed(character),
        }
    }
    words.end();
}

/// `knowledge_terms(value)`: whole words, punctuation and possessives treated alike.
pub fn knowledge_terms(value: &str) -> Vec<String> {
    let mut terms = Vec::new();
    for_each_knowledge_term(value, |term| terms.push(term.to_owned()));
    terms
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

/// One candidate body as `rank_references` reads it: how often each query term occurs, in the
/// query terms' sorted order, and how many terms it has (`sum(body.values())`).
struct BodyCounts<'a> {
    terms: Vec<&'a str>,
    counts: Vec<u64>,
    length: u64,
}

impl<'a> BodyCounts<'a> {
    fn of(body: &str, terms: &'a BTreeSet<String>) -> Self {
        let terms: Vec<&str> = terms.iter().map(String::as_str).collect();
        let mut counts = vec![0u64; terms.len()];
        let mut length = 0;
        for_each_knowledge_term(body, |word| {
            length += 1;
            if let Ok(index) = terms.binary_search(&word) {
                counts[index] += 1;
            }
        });
        BodyCounts { terms, counts, length }
    }

    /// `body[term]` for a query term (`0` when absent: `term in body` is `count > 0`).
    fn count(&self, term: &str) -> u64 {
        self.terms.binary_search(&term).map_or(0, |index| self.counts[index])
    }

    /// The query terms the body holds (its keys, as far as they are query terms).
    fn present(&self) -> impl Iterator<Item = &'a str> + '_ {
        self.terms.iter().zip(&self.counts).filter(|(_, count)| **count > 0).map(|(term, _)| *term)
    }
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
    let mut bodies: Vec<BodyCounts> = Vec::new();
    for item in candidates {
        titles.push(knowledge_terms(&format!("{} {}", item.identity, label_of(&item.result)?)));
        bodies.push(BodyCounts::of(&item.body, &terms));
    }
    let mut frequencies: BTreeMap<&str, u64> = BTreeMap::new();
    for (title, body) in titles.iter().zip(&bodies) {
        let present: BTreeSet<&str> = title
            .iter()
            .map(String::as_str)
            .chain(body.present())
            .filter(|term| terms.contains(*term))
            .collect();
        for term in present {
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
    let total_length: u64 = bodies.iter().map(|body| body.length).sum();
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
            terms.iter().map(String::as_str).filter(|term| body.count(term) > 0).collect();
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
        let length = body.length as f64;
        let length_normalizer = 1.2 * (0.25 + 0.75 * length / average_length);
        let mut score: f64 = matched
            .iter()
            .map(|term| {
                let frequency = body.count(term) as f64;
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

#[cfg(test)]
mod fast_path_tests {
    use super::*;

    /// The implementation before the fast path, kept to prove the outputs equal.
    fn reference_knowledge_terms(value: &str) -> Vec<String> {
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

    /// A small deterministic generator (xorshift), so every run checks the same texts.
    struct Rng(u64);

    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 << 13;
            self.0 ^= self.0 >> 7;
            self.0 ^= self.0 << 17;
            self.0
        }
    }

    /// Texts mixing ASCII words, stop words, possessives, both apostrophes, underscores, digits
    /// and non-ASCII letters whose folding expands or changes (`ß`, `İ`, `ǅ`, `ﬁ`, Greek
    /// sigma, Kelvin sign, Cyrillic, CJK, a combining mark, Arabic-Indic digits).
    fn texts() -> Vec<String> {
        let pieces = [
            "The",
            "the",
            "Lantern",
            "lantern's",
            "Rift",
            "rift’s",
            "o'clock",
            "rock'n'roll",
            "a",
            "now",
            "Now",
            "x_y",
            "_",
            "42",
            "4'2",
            "'s",
            "’",
            "'",
            " ",
            "  ",
            ",",
            ".",
            "-",
            "\n",
            "Straße",
            "İstanbul",
            "ǅemal",
            "ﬁre",
            "ΣΊΣΥΦΟΣ",
            "ς",
            "\u{212A}elvin",
            "Москва",
            "東京",
            "e\u{301}",
            "\u{661}\u{662}",
            "ſ",
            "ÆØÅ",
            "Éclair",
            "’s",
            "s’",
            "can't",
            "Tavin's",
        ];
        let mut rng = Rng(0x5eed_1234_abcd_ef01);
        let mut texts: Vec<String> = pieces.iter().map(|piece| (*piece).to_owned()).collect();
        for _ in 0..4000 {
            let words = 1 + (rng.next() % 24) as usize;
            let mut text = String::new();
            for _ in 0..words {
                text.push_str(pieces[(rng.next() % pieces.len() as u64) as usize]);
                if rng.next().is_multiple_of(3) {
                    text.push(' ');
                }
            }
            texts.push(text);
        }
        texts
    }

    #[test]
    fn stop_words_are_sorted_and_unique() {
        assert!(STOP_WORDS.windows(2).all(|pair| pair[0] < pair[1]));
        assert_eq!(STOP_WORDS.iter().map(|word| word.len()).max(), Some(LONGEST_STOP_WORD));
    }

    #[test]
    fn the_fast_terms_equal_the_reference_on_generated_text() {
        for text in texts() {
            assert_eq!(knowledge_terms(&text), reference_knowledge_terms(&text), "{text:?}");
            let mut visited = Vec::new();
            for_each_knowledge_term(&text, |term| visited.push(term.to_owned()));
            assert_eq!(visited, reference_knowledge_terms(&text), "{text:?}");
        }
    }
}
