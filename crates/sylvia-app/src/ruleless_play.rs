//! `application/ruleless_play.py`: the application guide a No Ruleset campaign's GM gets
//! (`RULELESS_GM_GUIDE`, `has_no_ruleset`, `is_ruleless_play`, `ruleless_gm_guide`) and the
//! mechanics check on held replies (`mechanics_in_response`, `ruleless_mechanics_rejection`).
//!
//! The check's patterns, labels and correction are exported from Python into
//! `ruleless_mechanics.json` (`generate_ruleless_mechanics.py`) with tables of what Python's
//! `re` matches for `\w`, `\s`, `\d` and each case-folded letter, so each pattern means here
//! exactly what it means to Python.

use serde_json::Value as Json;
use std::sync::OnceLock;
use sylvia_base::JsonMap;
use sylvia_domain::authority::{CampaignState, WriterRole};

/// `RULELESS_GM_GUIDE`, byte for byte (SHA-256 `222ec9f8e4eea1ba5755bd738417140006daded0860a1beea0bbd08edabaab39`).
pub const RULELESS_GM_GUIDE: &str = r#"NO RULESET PLAY (Sylvia application-owned guide; this campaign has no Ruleset)

There is no Ruleset, so there are no game mechanics here: no combat mode, turn order or rounds, no dice or rolls, no checks, saves or difficulty numbers, no numeric health, damage, defence or other statistics, and no levels or experience. Never ask the player to roll or to make a check, and never present a number as a mechanical result, in narration, character speech or out-of-character notes. Parts of the shared GM contract about Ruleset tools, game panels, action queues, character classes or exposed mechanics do not apply to this campaign.

Resolve uncertain, risky or contested actions with GM judgment in prose. Weigh the established fiction, what the character has shown they can do, their situation and the Campaign Plan, then narrate a clear and fair outcome: success, partial success, a cost, or a consequence. Fights and danger still happen as story. Describe them in the fiction and give the player meaningful choices, without rounds or turn order. If the player asks for a roll or a statistic, briefly say out of character that this campaign uses story judgment instead, then resolve the action.

Sylvia is this campaign's memory, so ground every response in it. Campaign State in the fresh context is current truth. Before introducing a person, place, object, clue or piece of history, check whether it already exists: search the campaign knowledge and read the Campaign Plan or the exact record. Follow the Campaign Plan's premise, direction and prepared material, and never contradict it or recorded facts. When play needs something new, make it consistent with both and record it.

Record lasting truth in the Campaign File in the same call as the response: people and places as entities with their current location, status and relationships; decisions, discoveries, promises and consequences as memories; the current scene and location; and Plan Progress when it is offered.

Track useful items. Record each notable object as an entity with entity_kind "object" and attributes for: description (what it is and looks like); purpose (what it does or is for, in story terms, never as a numeric bonus); location (where it is, or who has it); and any limits or conditions play has established. When Fallback Inventory updates are offered, also record who holds and owns a carried item there, using the object's entity_id as its item_id. Update the object's record whenever it moves, changes hands, is used up, breaks or reveals something new. An item does only what its record and the Plan say. A new ability needs a story reason and an updated record.
"#;

/// `has_no_ruleset(snapshot)`: the admitted empty No Ruleset closure.
pub fn has_no_ruleset(snapshot: &CampaignState) -> bool {
    snapshot.package_lock.entries().is_empty()
}

/// `is_ruleless_play(snapshot)`: an admitted No Ruleset campaign under DM authority.
pub fn is_ruleless_play(snapshot: &CampaignState) -> bool {
    snapshot.writer == WriterRole::Dm && has_no_ruleset(snapshot)
}

/// `ruleless_gm_guide(snapshot)`: the guide for a No Ruleset DM campaign, otherwise `None`.
pub fn ruleless_gm_guide(snapshot: &CampaignState) -> Option<&'static str> {
    is_ruleless_play(snapshot).then_some(RULELESS_GM_GUIDE)
}

/// The exported patterns, correction and Python `re` tables (`generate_ruleless_mechanics.py`).
struct MechanicsCheck {
    patterns: Vec<(String, fancy_regex::Regex)>,
    negation: fancy_regex::Regex,
    code: String,
    instruction: String,
}

/// A class body (no brackets) of `[start, end]` code point ranges.
fn class_of_ranges(ranges: &Json) -> String {
    let mut body = String::new();
    for range in ranges.as_array().expect("exported ranges") {
        let (start, end) =
            (range[0].as_u64().expect("a code point"), range[1].as_u64().expect("a code point"));
        body.push_str(&format!("\\x{{{start:X}}}"));
        if end != start {
            body.push_str(&format!("-\\x{{{end:X}}}"));
        }
    }
    body
}

/// A class body of single code points.
fn class_of_points(points: &Json) -> String {
    points
        .as_array()
        .expect("exported code points")
        .iter()
        .map(|point| format!("\\x{{{:X}}}", point.as_u64().expect("a code point")))
        .collect()
}

/// Python's `re` meaning of a pattern from `ruleless_play.py`, spelled for `fancy_regex` with
/// explicit classes from the exported tables: `\w`, `\s`, `\d`, `\b` and (under `IGNORECASE`)
/// each letter and `[a-z]`. The patterns use nothing else besides groups, alternation,
/// quantifiers, lookaround and literal punctuation; anything else is refused.
fn translate(pattern: &str, ignorecase: bool, tables: &Json) -> String {
    let word = class_of_ranges(&tables["word"]);
    let space = class_of_ranges(&tables["space"]);
    let digit = class_of_ranges(&tables["digit"]);
    let letter = |c: char| {
        if ignorecase {
            format!("[{}]", class_of_points(&tables["letters"][c.to_string()]))
        } else {
            c.to_string()
        }
    };
    let mut out = String::new();
    let mut chars = pattern.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '\\' => match chars.next() {
                Some('w') => out.push_str(&format!("[{word}]")),
                Some('s') => out.push_str(&format!("[{space}]")),
                Some('d') => out.push_str(&format!("[{digit}]")),
                Some('b') => {
                    out.push_str(&format!("(?:(?<=[{word}])(?![{word}])|(?<![{word}])(?=[{word}]))"))
                }
                other => panic!("ruleless pattern {pattern:?} uses the escape {other:?}"),
            },
            '[' => {
                let mut body = String::new();
                for inner in chars.by_ref() {
                    if inner == ']' {
                        break;
                    }
                    body.push(inner);
                }
                let spelled = match body.as_str() {
                    r"\w-" => format!(r"[{word}\-]"),
                    "a-z" if ignorecase => format!("[{}]", class_of_points(&tables["lower_class"])),
                    "a-z" => "[a-z]".to_owned(),
                    other => panic!("ruleless pattern {pattern:?} uses the class [{other}]"),
                };
                out.push_str(&spelled);
            }
            letter_char if letter_char.is_ascii_alphabetic() => out.push_str(&letter(letter_char)),
            other => out.push(other),
        }
    }
    out
}

fn mechanics_check() -> &'static MechanicsCheck {
    static CHECK: OnceLock<MechanicsCheck> = OnceLock::new();
    CHECK.get_or_init(|| {
        let data: Json =
            serde_json::from_str(include_str!("ruleless_mechanics.json")).expect("exported data is JSON");
        let tables = &data["tables"];
        let compile = |entry: &Json| {
            let source = entry["pattern"].as_str().expect("a pattern");
            let translated = translate(source, entry["ignorecase"].as_bool().expect("a flag"), tables);
            fancy_regex::Regex::new(&translated)
                .unwrap_or_else(|error| panic!("{source:?} does not compile: {error}"))
        };
        MechanicsCheck {
            patterns: data["patterns"]
                .as_array()
                .expect("patterns")
                .iter()
                .map(|entry| (entry["label"].as_str().expect("a label").to_owned(), compile(entry)))
                .collect(),
            negation: compile(&data["negation"]),
            code: data["code"].as_str().expect("a code").to_owned(),
            instruction: data["instruction"].as_str().expect("an instruction").to_owned(),
        }
    })
}

/// `_block_text(block)`: the block's `text`, else its `spoken_words`, else `""`.
fn block_text(block: &JsonMap) -> &str {
    ["text", "spoken_words"].iter().find_map(|key| block.get(*key).and_then(Json::as_str)).unwrap_or("")
}

/// `_findings_in_text(text, allow_negated=...)`: the label of each pattern that matches, once
/// each. With `allow_negated`, a match within 32 characters after a negation does not count.
fn findings_in_text(text: &str, allow_negated: bool) -> Vec<&'static str> {
    let check = mechanics_check();
    let mut found = Vec::new();
    for (label, pattern) in &check.patterns {
        for matched in pattern.find_iter(text) {
            let matched = matched.unwrap_or_else(|error| panic!("pattern {label:?} failed: {error}"));
            if allow_negated {
                // `text[max(0, match.start() - 32):match.start()]`, in characters.
                let before = &text[..matched.start()];
                let skip = before.chars().count().saturating_sub(32);
                let window: String = before.chars().skip(skip).collect();
                if check.negation.is_match(&window).unwrap_or_else(|error| panic!("negation failed: {error}"))
                {
                    continue;
                }
            }
            found.push(label.as_str());
            break;
        }
    }
    found
}

/// `mechanics_in_response(blocks, text=None)`: each mechanical idiom in a held response, in the
/// patterns' order. `blocks` are the held response blocks; `text` is plain held narration used
/// when no blocks exist. An `ooc` block may say that rolls or checks are not used, so a phrase
/// directly after a negation is ignored there.
pub fn mechanics_in_response(blocks: Option<&[JsonMap]>, text: Option<&str>) -> Vec<&'static str> {
    let mut found = Vec::new();
    match (blocks, text) {
        (Some(blocks), _) => {
            for block in blocks {
                let ooc = block.get("kind").and_then(Json::as_str) == Some("ooc");
                found.extend(findings_in_text(block_text(block), ooc));
            }
        }
        (None, Some(text)) if !text.is_empty() => found.extend(findings_in_text(text, false)),
        _ => {}
    }
    mechanics_check()
        .patterns
        .iter()
        .map(|(label, _)| label.as_str())
        .filter(|label| found.contains(label))
        .collect()
}

/// `ruleless_mechanics_rejection(findings)`: the provider-facing correction for a response
/// that used mechanics.
pub fn ruleless_mechanics_rejection(findings: &[&str]) -> JsonMap {
    let check = mechanics_check();
    let mut rejection = JsonMap::new();
    rejection.insert("status".into(), Json::from("rejected"));
    rejection.insert("code".into(), Json::from(check.code.as_str()));
    rejection.insert("field_paths".into(), Json::from(vec!["revised_player_facing_response"]));
    rejection.insert("findings".into(), Json::from(findings.to_vec()));
    rejection.insert("state_changed".into(), Json::Bool(false));
    rejection.insert(
        "instruction".into(),
        Json::from(check.instruction.replace("{findings}", &findings.join(", "))),
    );
    rejection
}
