//! `runtime/_template_resolver.py`: `{field.path}` interpolation against the context.

use super::context::ExecutionContext;
use crate::expr::value::{Dict, Value};

/// A placeholder's path, `[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*`, if `text` is
/// exactly one (the `re.fullmatch` of `resolve_params`).
fn placeholder_path(text: &str) -> bool {
    !text.is_empty()
        && text.split('.').all(|part| {
            let mut chars = part.chars();
            chars.next().is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
                && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
        })
}

/// `str(value)` as a template shows it: scalar sequences as comma-separated text (`"none"` when
/// empty), everything else as Python's `str()`.
fn shown(value: &Value) -> String {
    if let Some(items) = value.sequence() {
        if items.iter().all(|item| {
            matches!(item, Value::None | Value::Str(_) | Value::Bool(_) | Value::Int(_) | Value::Float(_))
        }) {
            let joined = items.iter().map(Value::to_str).collect::<Vec<_>>().join(", ");
            return if joined.is_empty() { "none".into() } else { joined };
        }
    }
    value.to_str()
}

/// `resolve_template(template, context)`: each `{path}` replaced by its value, left as is when
/// the value is `None`. Python's `re.sub` scans left to right without overlaps, so a `{` that
/// opens no valid placeholder is skipped one character at a time.
pub fn resolve_template(template: &str, context: &ExecutionContext<'_>) -> String {
    let mut out = String::with_capacity(template.len());
    let mut rest = template;
    while let Some(open) = rest.find('{') {
        out.push_str(&rest[..open]);
        let after = &rest[open + 1..];
        match after.find('}').filter(|&close| placeholder_path(&after[..close])) {
            Some(close) => {
                let path = &after[..close];
                match context.resolve_field(path) {
                    Value::None => {
                        out.push('{');
                        out.push_str(path);
                        out.push('}');
                    }
                    value => out.push_str(&shown(&value)),
                }
                rest = &after[close + 1..];
            }
            None => {
                out.push('{');
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

/// `resolve_params(params, context)`: an exact `{path}` keeps the value's type, other text is
/// interpolated, and non-text values pass through.
pub fn resolve_params(params: &Dict, context: &ExecutionContext<'_>) -> Dict {
    params
        .iter()
        .map(|(key, value)| {
            let resolved = match value {
                Value::Str(text) => match text.strip_prefix('{').and_then(|t| t.strip_suffix('}')) {
                    Some(path) if placeholder_path(path) => context.resolve_field(path),
                    _ => Value::str(resolve_template(text, context)),
                },
                other => other.clone(),
            };
            (key.clone(), resolved)
        })
        .collect()
}
