//! `ruleset/schema.py` (`RulesetFile.model_validate`, pydantic 2 in lax mode) and
//! `ruleset/validator.py` (`RulesetFile.validate`), over the JSON a package runtime holds once
//! its shape is checked.
//!
//! The package runtime asks two questions: does pydantic accept the document, and does
//! `validate()` report an issue of severity `error`. Only those answers are kept here; the
//! coerced model itself is not needed. Pydantic's lax conversions are reproduced for the JSON
//! inputs that can reach it (`coerce_*`, checked value by value against pydantic in the vectors).
//!
//! `validate()` can report an error for three things only: a skill naming an undefined ability,
//! two skills with the same lower-case name, and two conditions with the same lower-case name.
//! Its modifier-formula check imports `sylvia_gm.core.expression_eval`, which the snapshot does
//! not have, so it never runs.

use sylvia_base::{Json, JsonMap};

/// Why `RulesetFile.model_validate` refused, or the error `validate()` found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ModelRefusal {
    /// `pydantic.ValidationError`.
    Invalid,
    /// `any(issue.severity == "error" for issue in model.validate())`.
    CrossReference,
}

// ── Pydantic's lax conversions ─────────────────────────────────────────────────────────────

/// `str`: only a string.
pub fn coerce_str(value: &Json) -> Option<&str> {
    value.as_str()
}

/// `bool`: a bool, the numbers 0 and 1, or one of pydantic's words in any case (untrimmed).
pub fn coerce_bool(value: &Json) -> Option<bool> {
    match value {
        Json::Bool(flag) => Some(*flag),
        Json::Number(number) => {
            // `-0.0 == 0.0`, as pydantic accepts it.
            let x = number.as_f64()?;
            if x == 0.0 {
                Some(false)
            } else if x == 1.0 {
                Some(true)
            } else {
                None
            }
        }
        Json::String(text) => match text.to_ascii_lowercase().as_str() {
            "0" | "off" | "f" | "false" | "n" | "no" => Some(false),
            "1" | "on" | "t" | "true" | "y" | "yes" => Some(true),
            _ => None,
        },
        _ => None,
    }
}

/// An integer of any size, as pydantic's `int` produces it, ordered as Python orders `int`s.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PyInt {
    negative: bool,
    /// Decimal digits without leading zeros (`"0"` for zero, which is never negative).
    digits: String,
}

impl PyInt {
    fn new(negative: bool, digits: &str) -> Self {
        let digits = digits.trim_start_matches('0');
        if digits.is_empty() {
            Self { negative: false, digits: "0".into() }
        } else {
            Self { negative, digits: digits.into() }
        }
    }

    fn from_i128(value: i128) -> Self {
        Self::new(value < 0, &value.unsigned_abs().to_string())
    }
}

impl std::fmt::Display for PyInt {
    /// `str(int)`.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if self.negative {
            f.write_str("-")?;
        }
        f.write_str(&self.digits)
    }
}

impl Ord for PyInt {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        use std::cmp::Ordering;
        let magnitude =
            self.digits.len().cmp(&other.digits.len()).then_with(|| self.digits.cmp(&other.digits));
        match (self.negative, other.negative) {
            (false, false) => magnitude,
            (true, true) => magnitude.reverse(),
            (false, true) => Ordering::Greater,
            (true, false) => Ordering::Less,
        }
    }
}

impl PartialOrd for PyInt {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

/// `int`: a bool, an integer, a whole finite float inside `i64`, or a string that is (after
/// Rust's `str::trim`, which pydantic uses) an optional sign, digits with single underscores
/// between them, and an optional `.` followed by zeros only.
pub fn coerce_int(value: &Json) -> Option<PyInt> {
    match value {
        Json::Bool(flag) => Some(PyInt::from_i128(i128::from(*flag))),
        Json::Number(number) => {
            if let Some(int) = number.as_i64() {
                return Some(PyInt::from_i128(i128::from(int)));
            }
            if let Some(int) = number.as_u64() {
                return Some(PyInt::from_i128(i128::from(int)));
            }
            let x = number.as_f64()?;
            // Pydantic converts a float only when it is whole and strictly inside +-2**63.
            const BOUND: f64 = 9.223_372_036_854_776e18;
            if x.is_finite() && x.fract() == 0.0 && -BOUND < x && x < BOUND {
                Some(PyInt::from_i128(x as i128))
            } else {
                None
            }
        }
        Json::String(text) => int_text(text.trim()),
        _ => None,
    }
}

fn int_text(text: &str) -> Option<PyInt> {
    let (negative, rest) = match text.as_bytes().first() {
        Some(b'-') => (true, &text[1..]),
        Some(b'+') => (false, &text[1..]),
        _ => (false, text),
    };
    let (whole, fraction) = match rest.split_once('.') {
        Some((whole, fraction)) => (whole, Some(fraction)),
        None => (rest, None),
    };
    if let Some(fraction) = fraction {
        if fraction.is_empty() || !fraction.bytes().all(|b| b == b'0') {
            return None;
        }
    }
    let digits = underscored_digits(whole)?;
    Some(PyInt::new(negative, &digits))
}

/// ASCII digits with single underscores only between digits, the underscores removed.
fn underscored_digits(text: &str) -> Option<String> {
    let bytes = text.as_bytes();
    if bytes.is_empty() || !bytes[0].is_ascii_digit() || !bytes[bytes.len() - 1].is_ascii_digit() {
        return None;
    }
    let mut digits = String::with_capacity(bytes.len());
    let mut previous_underscore = false;
    for &byte in bytes {
        match byte {
            b'0'..=b'9' => {
                digits.push(byte as char);
                previous_underscore = false;
            }
            b'_' if !previous_underscore => previous_underscore = true,
            _ => return None,
        }
    }
    Some(digits)
}

/// `int | float | None` (smart union): `None`, a bool, any number, or a string that `int`
/// accepts or that pydantic reads as a float.
pub fn coerce_int_or_float_or_none(value: &Json) -> bool {
    match value {
        Json::Null | Json::Bool(_) | Json::Number(_) => true,
        Json::String(text) => int_text(text.trim()).is_some() || float_text(text.trim()),
        _ => false,
    }
}

/// Pydantic's float from text: Rust's `f64` syntax (`inf`, `infinity` and `nan` in any case),
/// with underscores removed where each one follows a digit or a sign and precedes a digit
/// (`+_1` and `1e5_0` are read, `_1`, `1_` and `1__0` are not).
fn float_text(text: &str) -> bool {
    let bytes = text.as_bytes();
    let mut cleaned = String::with_capacity(bytes.len());
    for (index, &byte) in bytes.iter().enumerate() {
        if byte == b'_' {
            let before = index.checked_sub(1).map(|i| bytes[i]);
            let after = bytes.get(index + 1).copied();
            let joins =
                matches!(before, Some(b'0'..=b'9' | b'+' | b'-')) && matches!(after, Some(b'0'..=b'9'));
            if !joins {
                return false;
            }
        } else {
            cleaned.push(byte as char);
        }
    }
    !cleaned.is_empty() && cleaned.parse::<f64>().is_ok()
}

// ── The models ─────────────────────────────────────────────────────────────────────────────

/// A model field's check: `true` when pydantic accepts the value.
type Check = fn(&Json) -> bool;

fn is_str(value: &Json) -> bool {
    coerce_str(value).is_some()
}

fn is_optional_str(value: &Json) -> bool {
    value.is_null() || is_str(value)
}

fn is_bool(value: &Json) -> bool {
    coerce_bool(value).is_some()
}

fn is_optional_int(value: &Json) -> bool {
    value.is_null() || coerce_int(value).is_some()
}

fn is_str_list(value: &Json) -> bool {
    matches!(value, Json::Array(items) if items.iter().all(is_str))
}

fn is_object(value: &Json) -> bool {
    value.is_object()
}

fn is_optional_object(value: &Json) -> bool {
    value.is_null() || value.is_object()
}

/// One model: required fields, optional fields, each with its check. Unknown fields are
/// ignored (pydantic's default) or kept (`extra="allow"`); neither refuses.
fn model<'a>(value: &'a Json, required: &[(&str, Check)], optional: &[(&str, Check)]) -> Option<&'a JsonMap> {
    let Json::Object(object) = value else {
        return None;
    };
    let mut valid = true;
    for (name, check) in required {
        valid &= object.get(*name).is_some_and(check);
    }
    for (name, check) in optional {
        valid &= object.get(*name).is_none_or(check);
    }
    valid.then_some(object)
}

fn meta(value: &Json) -> bool {
    let Some(object) = model(
        value,
        &[("id", is_str), ("name", is_str), ("version", is_str), ("author", is_str), ("license", is_str)],
        &[
            ("license_url", is_optional_str),
            ("description", is_optional_str),
            ("source_url", is_optional_str),
            ("attribution", is_optional_str),
        ],
    ) else {
        return false;
    };
    // `re.match(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", id)`; `$` also matches before a final newline.
    let id = object["id"].as_str().unwrap_or_default();
    let id = id.strip_suffix('\n').unwrap_or(id);
    let mut chars = id.chars();
    chars.next().is_some_and(|c| c.is_ascii_lowercase())
        && !id.ends_with('-')
        && !id.contains("--")
        && id.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
}

const FEATURE_FLAGS: [&str; 18] = [
    "death_saves_enabled",
    "spell_slots_enabled",
    "concentration_enabled",
    "proficiency_bonus_enabled",
    "hit_dice_enabled",
    "initiative_enabled",
    "critical_hits_enabled",
    "critical_misses_enabled",
    "advantage_disadvantage_enabled",
    "multi_attack_enabled",
    "reactions_enabled",
    "bonus_actions_enabled",
    "skills_enabled",
    "backgrounds_enabled",
    "species_enabled",
    "classes_enabled",
    "feats_enabled",
    "multiclassing_enabled",
];

fn features(value: &Json) -> bool {
    let Json::Object(object) = value else {
        return false;
    };
    FEATURE_FLAGS.iter().all(|name| object.get(*name).is_none_or(is_bool))
}

fn ability(value: &Json) -> bool {
    let Some(object) = model(
        value,
        &[("name", is_str)],
        &[
            ("active", is_bool),
            ("description", is_optional_str),
            ("generation", is_optional_str),
            ("modifier_formula", is_optional_str),
            ("used_for", is_str_list),
            ("save_label", is_optional_str),
        ],
    ) else {
        return false;
    };
    let one = PyInt::from_i128(1);
    let hundred = PyInt::from_i128(100);
    // `default_min: int | None = Field(ge=1)`; a refused value is absent from `info.data`.
    let minimum = match object.get("default_min") {
        None | Some(Json::Null) => None,
        Some(value) => match coerce_int(value) {
            Some(int) if int >= one => Some(int),
            _ => return false,
        },
    };
    match object.get("default_max") {
        None | Some(Json::Null) => true,
        Some(value) => match coerce_int(value) {
            // `le=100`, then `min_less_than_max`.
            Some(int) if int <= hundred => minimum.is_none_or(|minimum| minimum < int),
            _ => false,
        },
    }
}

fn skill(value: &Json) -> bool {
    model(
        value,
        &[("name", is_str)],
        &[("ability", is_optional_str), ("description", is_optional_str), ("trained_only", is_bool)],
    )
    .is_some()
}

fn resource_pool(value: &Json) -> bool {
    model(
        value,
        &[("refresh", is_str)],
        &[
            ("count", coerce_int_or_float_or_none),
            ("unit", is_optional_str),
            ("condition", is_optional_str),
            ("source", is_optional_str),
        ],
    )
    .is_some()
}

fn actor(value: &Json) -> bool {
    model(
        value,
        &[("timing", is_str)],
        &[("entity", is_optional_str), ("resources", |value| dict_of(value, resource_pool))],
    )
    .is_some()
}

fn turn_system(value: &Json) -> bool {
    model(
        value,
        &[("type", is_str)],
        &[
            ("label", is_optional_str),
            ("actors", |value| list_of(value, actor)),
            ("order", is_optional_str),
            ("resolution", is_optional_str),
            ("exit_condition", is_optional_str),
        ],
    )
    .is_some()
}

fn mode(value: &Json) -> bool {
    model(value, &[("turn_system", turn_system)], &[]).is_some()
}

fn condition_level(value: &Json) -> bool {
    model(
        value,
        &[
            ("level", |value| coerce_int(value).is_some_and(|level| level >= PyInt::from_i128(1))),
            ("effect", is_str),
        ],
        &[],
    )
    .is_some()
}

fn condition(value: &Json) -> bool {
    model(
        value,
        &[("name", is_str), ("description", is_str), ("mechanical_effect", is_str)],
        &[
            ("stackable", is_bool),
            ("max_stacks", is_optional_int),
            ("levels", |value| list_of(value, condition_level)),
            ("cleared_by", is_str_list),
        ],
    )
    .is_some()
}

fn list_of(value: &Json, item: Check) -> bool {
    matches!(value, Json::Array(items) if items.iter().all(item))
}

fn dict_of(value: &Json, item: Check) -> bool {
    matches!(value, Json::Object(object) if object.values().all(item))
}

/// `RulesetFile.model_validate(raw)` and then `validate()`'s errors.
pub fn check_ruleset_file(raw: &Json) -> Result<(), ModelRefusal> {
    let accepted = model(
        raw,
        &[],
        &[
            ("meta", |value| value.is_null() || meta(value)),
            ("features", |value| value.is_null() || features(value)),
            ("abilities", |value| dict_of(value, ability)),
            ("skills", |value| list_of(value, skill)),
            ("modes", |value| dict_of(value, mode)),
            ("magic", is_object),
            ("equipment", is_object),
            ("character_creation", is_object),
            ("progression", is_object),
            ("conditions", |value| list_of(value, condition)),
            ("tools", is_optional_object),
            ("procedures", is_optional_object),
            ("definitions", is_optional_object),
            ("definition_row_schemas", is_object),
            ("narrative", is_optional_object),
        ],
    );
    let Some(raw) = accepted else {
        return Err(ModelRefusal::Invalid);
    };
    let empty = Vec::new();
    let abilities: Vec<&str> = raw
        .get("abilities")
        .and_then(Json::as_object)
        .map_or_else(Vec::new, |map| map.keys().map(String::as_str).collect());
    let skills = raw.get("skills").and_then(Json::as_array).unwrap_or(&empty);
    let mut skill_names = std::collections::HashSet::new();
    for skill in skills {
        if let Some(Json::String(ability)) = skill.get("ability") {
            if !abilities.contains(&ability.as_str()) {
                return Err(ModelRefusal::CrossReference);
            }
        }
        if !skill_names.insert(skill["name"].as_str().unwrap_or_default().to_lowercase()) {
            return Err(ModelRefusal::CrossReference);
        }
    }
    let conditions = raw.get("conditions").and_then(Json::as_array).unwrap_or(&empty);
    let mut condition_names = std::collections::HashSet::new();
    for condition in conditions {
        if !condition_names.insert(condition["name"].as_str().unwrap_or_default().to_lowercase()) {
            return Err(ModelRefusal::CrossReference);
        }
    }
    Ok(())
}
