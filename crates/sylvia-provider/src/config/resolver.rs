//! `config/_resolver.py`: `${VAR}` resolution, with `$${VAR}` for a literal `${VAR}`.

use super::env::Environment;
use super::pyvalue::py_str;
use super::ConfigFailure;
use sylvia_base::pyyaml::Data;

/// `_ESCAPE_PLACEHOLDER`.
const ESCAPE_PLACEHOLDER: &str = "\u{0}ESC_DOLLAR_BRACE\u{0}";

/// `resolve_vars(value, source)`: strings resolved; dicts and lists walked (their paths extend
/// `source` as `.key` and `[index]`); every other value kept.
pub fn resolve_vars(value: &Data, source: &str, env: &Environment) -> Result<Data, ConfigFailure> {
    Ok(match value {
        Data::Str(text) => Data::Str(resolve_string(text, source, env)?),
        Data::Dict(pairs) => Data::Dict(
            pairs
                .iter()
                .map(|(key, item)| {
                    Ok((key.clone(), resolve_vars(item, &format!("{source}.{}", py_str(key)), env)?))
                })
                .collect::<Result<_, ConfigFailure>>()?,
        ),
        Data::List(items) => Data::List(
            items
                .iter()
                .enumerate()
                .map(|(index, item)| resolve_vars(item, &format!("{source}[{index}]"), env))
                .collect::<Result<_, _>>()?,
        ),
        other => other.clone(),
    })
}

fn is_name_start(c: char) -> bool {
    c.is_ascii_alphabetic() || c == '_'
}

fn is_name_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// `_resolve_string`: `re.sub(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}", ...)` over the text
/// with `$${` protected, then the protection undone.
pub(crate) fn resolve_string(value: &str, source: &str, env: &Environment) -> Result<String, ConfigFailure> {
    let protected: Vec<char> = value.replace("$${", ESCAPE_PLACEHOLDER).chars().collect();
    let mut out = String::with_capacity(protected.len());
    let mut index = 0;
    while index < protected.len() {
        let matched = (protected[index] == '$'
            && protected.get(index + 1) == Some(&'{')
            && (index == 0 || protected[index - 1] != '$'))
            .then(|| {
                let start = index + 2;
                let first = *protected.get(start)?;
                if !is_name_start(first) {
                    return None;
                }
                let mut end = start + 1;
                while end < protected.len() && is_name_char(protected[end]) {
                    end += 1;
                }
                (protected.get(end) == Some(&'}')).then_some((start, end))
            })
            .flatten();
        match matched {
            Some((start, end)) => {
                let name: String = protected[start..end].iter().collect();
                match env.get(&name) {
                    Some(value) => out.push_str(value?),
                    None => {
                        return Err(ConfigFailure::config(format!(
                            "Environment variable '{name}' referenced in {source} but not set. Set it in .env or your shell environment."
                        )))
                    }
                }
                index = end + 1;
            }
            None => {
                out.push(protected[index]);
                index += 1;
            }
        }
    }
    Ok(out.replace(ESCAPE_PLACEHOLDER, "${"))
}
