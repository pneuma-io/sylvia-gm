//! The small shape checks `package_library.py` applies to portable records.

use crate::authority::validate_authority_identity_value;
use crate::error::{DomainError, DomainResult};
use sylvia_base::{Json, JsonMap};

/// `_object(value, label)`.
pub(crate) fn object<'a>(value: &'a Json, label: &str) -> DomainResult<&'a JsonMap> {
    value.as_object().ok_or_else(|| DomainError::value(format!("{label} must be an object")))
}

/// `_array(value, label)`.
pub(crate) fn array<'a>(value: &'a Json, label: &str) -> DomainResult<&'a Vec<Json>> {
    value.as_array().ok_or_else(|| DomainError::value(format!("{label} must be an array")))
}

/// `_bounded_array(value, label, maximum_items)`.
pub(crate) fn bounded_array<'a>(value: &'a Json, label: &str, maximum: usize) -> DomainResult<&'a Vec<Json>> {
    let items = array(value, label)?;
    if items.len() > maximum {
        return Err(DomainError::value(format!("{label} has too many entries")));
    }
    Ok(items)
}

/// `_exact_keys(value, expected, label)`: the member names are exactly `expected`.
pub(crate) fn exact_keys(value: &JsonMap, expected: &[&str], label: &str) -> DomainResult<()> {
    if value.len() != expected.len() || !expected.iter().all(|key| value.contains_key(*key)) {
        return Err(DomainError::value(format!("{label} fields do not match the portable format")));
    }
    Ok(())
}

/// `_safe_small_label(value, label)`.
pub(crate) fn safe_small_label<'a>(value: &'a Json, label: &str) -> DomainResult<&'a str> {
    let text = validate_authority_identity_value(value, label, false)?;
    if text.len() > 256 || text.contains(['/', '\\', ':']) {
        return Err(DomainError::value(format!("{label} is not a portable safe label")));
    }
    Ok(text)
}

/// `tuple(sorted(set(items))) == items`: strictly ascending by code point.
pub(crate) fn sorted_unique<T: Ord>(items: &[T]) -> bool {
    items.windows(2).all(|pair| pair[0] < pair[1])
}

/// `_sorted_unique_identities(value, label, maximum_items)`.
pub(crate) fn sorted_unique_identities(
    value: &Json,
    label: &str,
    maximum: usize,
) -> DomainResult<Vec<String>> {
    let items = bounded_array(value, label, maximum)?;
    if items.iter().any(|item| !item.is_string()) {
        return Err(DomainError::value(format!("{label} must contain strings")));
    }
    let mut result = Vec::with_capacity(items.len());
    for item in items {
        let text = validate_authority_identity_value(item, label, false)?;
        if text.len() > 512 {
            return Err(DomainError::value(format!("{label} contains an oversized identity")));
        }
        result.push(text.to_owned());
    }
    if !sorted_unique(&result) {
        return Err(DomainError::value(format!("{label} must be sorted and unique")));
    }
    Ok(result)
}
