// Global focus is safe only when every launch selects the default installed dataset.
// Portable and explicit datasets use the owner's existing per-database writer lock.
pub(crate) fn use_installed_focus(portable: bool, explicit_database: bool) -> bool {
    !portable && !explicit_database
}

#[cfg(test)]
mod tests {
    use super::use_installed_focus;

    #[test]
    fn ordinary_installed_relaunch_keeps_focus_behavior() {
        assert!(use_installed_focus(false, false));
    }

    #[test]
    fn portable_and_explicit_launches_cannot_redirect_to_installed_dataset() {
        assert!(!use_installed_focus(true, false));
        assert!(!use_installed_focus(false, true));
        assert!(!use_installed_focus(true, true));
    }
}
