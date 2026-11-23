//! The store's exports for `retained_plan_snapshot` and the selection owner: both call lane 3's
//! `export_package_candidate_on`, whose bytes lane 3's `package_export` vectors prove against
//! Python's owner. This checks the wiring on a real store with the Riichi archive installed: the
//! retained snapshot of a payload bound to the installed closure is the candidate's export, it
//! admits to the same closure, and the selection owner exports the same bytes.

use std::path::{Path, PathBuf};
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::campaign_library_selection::SelectionOwner;
use sylvia_app::package_library::install_package_archive;
use sylvia_app::plan_snapshot::{retained_plan_snapshot, PlanSnapshotOwner};
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_library::closure_sha256;
use sylvia_store::AuthorityStore;

/// A fresh work folder: one left by a killed run must not show up later as an unrelated error.
fn fresh_work(name: &str) -> PathBuf {
    let work = Path::new(env!("CARGO_TARGET_TMPDIR")).join(name);
    match std::fs::remove_dir_all(&work) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => panic!("cannot clear the old work folder {}: {error}", work.display()),
    }
    work
}

#[test]
fn store_exports_feed_the_retained_snapshot() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let work = fresh_work("snapshot-export-store");
    std::fs::create_dir_all(&work).unwrap();
    let store = AuthorityStore::open_for_application_owner(&work.join("authority.sqlite3"), None).unwrap();
    let riichi =
        std::fs::read(root.join("rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package")).unwrap();
    let candidate = install_package_archive(&store, &riichi, "snapshot-export").unwrap();

    let exported = PlanSnapshotOwner::export_package_candidate(&store, candidate.candidate_id()).unwrap();
    let admitted = admit_package_archive(&exported).unwrap();
    assert_eq!(closure_sha256(&admitted.package_lock), candidate.closure_sha256());

    let payload = serde_json::json!({"bindings": {"package_closure_sha256": candidate.closure_sha256()}});
    let retained = retained_plan_snapshot(&store, &bundled, &payload).unwrap();
    assert_eq!(retained, exported);
    assert_eq!(SelectionOwner::export_package_candidate(&store, candidate.candidate_id()).unwrap(), exported);

    let missing = PlanSnapshotOwner::export_package_candidate(&store, "missing-candidate").unwrap_err();
    assert_eq!(missing.rejection().map(|coded| coded.code), Some("candidate_unavailable".to_owned()));
    drop(store);
    let _ = std::fs::remove_dir_all(&work);
}
