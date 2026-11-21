//! `domain/state_ownership.py`: package-lock rules for mutable state namespace ownership.

/// `owner_matches_package_lock(package_ids, namespace, owner_package_id)`: whether one recorded
/// owner is compatible with an exact package lock.
///
/// With no packages, only an unowned namespace matches. Otherwise the owner must be one of the
/// lock's packages, and at most one package may be a dotted prefix of the namespace; if one is,
/// it must be the owner.
pub fn owner_matches_package_lock<'a>(
    package_ids: impl IntoIterator<Item = &'a str>,
    namespace: &str,
    owner_package_id: Option<&str>,
) -> bool {
    let selected: Vec<&str> = package_ids.into_iter().collect();
    if selected.is_empty() {
        return owner_package_id.is_none();
    }
    let Some(owner) = owner_package_id.filter(|owner| selected.contains(owner)) else {
        return false;
    };
    let prefix_matches: Vec<&str> = selected
        .iter()
        .copied()
        .filter(|package_id| namespace.strip_prefix(package_id).is_some_and(|rest| rest.starts_with('.')))
        .collect();
    prefix_matches.len() <= 1 && prefix_matches.first().is_none_or(|matched| *matched == owner)
}
