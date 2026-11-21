//! `config/_env.py`: `SYLVIA_GM_PROVIDERS__OPENCODE_GO__API_KEY` → `providers.opencode_go.api_key`.
//!
//! The process environment is read through an [`Environment`] snapshot, as Python's
//! `os.environ` is: on Windows its names are upper-cased (later duplicates replacing earlier
//! ones) and looked up without regard to case; elsewhere they are kept as they are.

use super::pyvalue::from_json;
use super::ConfigFailure;
use sylvia_base::pystr::{self, IntParseError};
use sylvia_base::pyyaml::{data_key_eq, Data};

/// `os.environ` at one moment.
#[derive(Debug, Clone, Default)]
pub struct Environment {
    /// Names and values in `os.environ` order. A value that is not UTF-8 is `None`: Python
    /// would hold it with surrogate escapes, which a Rust `String` cannot (differences row 3).
    vars: Vec<(String, Option<String>)>,
    case_insensitive: bool,
}

impl Environment {
    /// The current process environment.
    pub fn current() -> Self {
        let vars = std::env::vars_os()
            .filter_map(|(name, value)| Some((name.into_string().ok()?, value.into_string().ok())))
            .collect();
        Self::from_vars(vars, cfg!(windows))
    }

    /// A snapshot from given variables, with Windows (`case_insensitive`) or POSIX naming.
    pub fn from_vars(vars: Vec<(String, Option<String>)>, case_insensitive: bool) -> Self {
        if !case_insensitive {
            return Self { vars, case_insensitive };
        }
        // `{encodekey(key): value for key, value in environ.items()}` with an upper-casing key.
        let mut upper: Vec<(String, Option<String>)> = Vec::new();
        for (name, value) in vars {
            let name = pystr::upper(&name);
            match upper.iter_mut().find(|(existing, _)| *existing == name) {
                Some(slot) => slot.1 = value,
                None => upper.push((name, value)),
            }
        }
        Self { vars: upper, case_insensitive }
    }

    /// Text values only, POSIX naming: for tests.
    pub fn of(pairs: &[(&str, &str)]) -> Self {
        Self::from_vars(pairs.iter().map(|(k, v)| ((*k).to_owned(), Some((*v).to_owned()))).collect(), false)
    }

    /// `os.environ[name]` when present (`name in os.environ`).
    pub fn get(&self, name: &str) -> Option<Result<&str, ConfigFailure>> {
        let wanted = if self.case_insensitive { pystr::upper(name) } else { name.to_owned() };
        self.vars.iter().find(|(existing, _)| *existing == wanted).map(|(_, value)| match value {
            Some(text) => Ok(text.as_str()),
            None => Err(surrogate_refusal()),
        })
    }

    /// `os.environ.items()`.
    pub fn items(&self) -> impl Iterator<Item = (&str, Result<&str, ConfigFailure>)> {
        self.vars.iter().map(|(name, value)| (name.as_str(), value.as_deref().ok_or_else(surrogate_refusal)))
    }
}

fn surrogate_refusal() -> ConfigFailure {
    ConfigFailure::python("UnicodeError", "an environment value is not valid UTF-8 (differences.md row 3)")
}

fn str_key(text: &str) -> Data {
    Data::Str(text.to_owned())
}

/// `apply_env_overrides(config, prefix)`: a new dict with every `PREFIX`-named variable set at
/// its `__`-separated, lower-cased path.
pub fn apply_env_overrides(config: &Data, env: &Environment, prefix: &str) -> Result<Data, ConfigFailure> {
    let mut result = config.clone();
    for (name, value) in env.items() {
        let Some(path) = name.strip_prefix(prefix) else { continue };
        let value = value?;
        // `part.lower()` for each `__`-separated segment.
        let key_parts: Vec<String> = path.split("__").map(python_lower).collect();
        if key_parts.is_empty() || key_parts == [String::new()] {
            continue;
        }
        let parsed = coerce_value(value, &result, &key_parts)?;
        set_nested(&mut result, &key_parts, parsed);
    }
    Ok(result)
}

/// `str.lower()`.
fn python_lower(text: &str) -> String {
    text.to_lowercase()
}

/// `_coerce_value`: JSON for a value that looks like an array or object, else the type of the
/// value already at the path.
fn coerce_value(env_value: &str, config: &Data, key_parts: &[String]) -> Result<Data, ConfigFailure> {
    let stripped = pystr::strip(env_value);
    if stripped.starts_with('[') || stripped.starts_with('{') {
        if let Some(parsed) = json_loads(env_value)? {
            return Ok(parsed);
        }
    }
    let mut existing = config;
    for part in &key_parts[..key_parts.len() - 1] {
        match existing.get(&str_key(part)) {
            Some(next) if existing.as_dict().is_some() => existing = next,
            _ => return Ok(Data::Str(env_value.to_owned())),
        }
    }
    let last = key_parts.last().expect("at least one part");
    match existing.get(&str_key(last)) {
        Some(Data::Bool(_)) => {
            Ok(Data::Bool(matches!(python_lower(env_value).as_str(), "true" | "1" | "yes")))
        }
        Some(Data::Int(_)) => match pystr::parse_int(env_value) {
            Ok(number) => Ok(Data::Int(number)),
            Err(IntParseError::OutOfRange { .. }) => {
                Err(ConfigFailure::python("ValueError", pystr::INT_RANGE_MESSAGE))
            }
            Err(IntParseError::Invalid(error)) => Err(ConfigFailure::python("ValueError", error.message)),
        },
        Some(Data::Float(_)) => pystr::parse_float(env_value)
            .map(Data::Float)
            .map_err(|error| ConfigFailure::python("ValueError", error.message)),
        _ => Ok(Data::Str(env_value.to_owned())),
    }
}

/// `json.loads(text)`, `None` where Python raises `JSONDecodeError` (the override then stays
/// text). JSON Python reads but the port cannot hold (integers beyond 64 bits, `NaN`,
/// `Infinity`, floats beyond range: differences rows 1 and 2) is refused, never read as text.
fn json_loads(text: &str) -> Result<Option<Data>, ConfigFailure> {
    match sylvia_base::pyjson::loads(text) {
        Ok(value) => from_json(&value).map(Some),
        Err(error) => {
            let message = error.to_string();
            if message.contains("outside the supported 64-bit range")
                || message.contains("number out of range")
                || has_non_finite_literal(text)
            {
                Err(ConfigFailure::python("ValueError", format!("{message} (differences.md rows 1 and 2)")))
            } else {
                Ok(None)
            }
        }
    }
}

/// Whether `NaN`, `Infinity` or `-Infinity` appears outside a string.
fn has_non_finite_literal(text: &str) -> bool {
    let bytes = text.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'"' => {
                index += 1;
                while index < bytes.len() && bytes[index] != b'"' {
                    index += if bytes[index] == b'\\' { 2 } else { 1 };
                }
                index += 1;
            }
            b'N' if text[index..].starts_with("NaN") => return true,
            b'I' if text[index..].starts_with("Infinity") => return true,
            _ => index += 1,
        }
    }
    false
}

/// `_set_nested`: create or replace intermediate dicts, then assign.
fn set_nested(target: &mut Data, key_parts: &[String], value: Data) {
    let mut current = target;
    for part in &key_parts[..key_parts.len() - 1] {
        let pairs = current.as_dict_mut().expect("a dict at every level");
        let key = str_key(part);
        let index = match pairs.iter().position(|(existing, _)| data_key_eq(existing, &key)) {
            Some(index) => {
                if pairs[index].1.as_dict().is_none() {
                    pairs[index].1 = Data::Dict(Vec::new());
                }
                index
            }
            None => {
                pairs.push((key, Data::Dict(Vec::new())));
                pairs.len() - 1
            }
        };
        current = &mut pairs[index].1;
    }
    let pairs = current.as_dict_mut().expect("a dict at every level");
    let key = str_key(key_parts.last().expect("at least one part"));
    match pairs.iter_mut().find(|(existing, _)| data_key_eq(existing, &key)) {
        Some(slot) => slot.1 = value,
        None => pairs.push((key, value)),
    }
}
