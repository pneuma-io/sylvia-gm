//! The bundled package installs at launch on a real store: the first launch installs the SRD
//! and the Riichi closure under Python's install ids, and a later launch installs nothing.
//! (The comparison sessions check the first launch's database against Python's byte for byte.)

use sha2::{Digest, Sha256};
use std::path::PathBuf;
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_owner::preinstall::{preinstall_bundled, SRD_PACKAGE_ID};
use sylvia_store::AuthorityStore;

fn repository_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn ids(store: &AuthorityStore) -> Vec<(String, String)> {
    let mut ids: Vec<(String, String)> = store
        .package_candidates()
        .unwrap()
        .iter()
        .map(|candidate| (candidate.candidate_id().to_string(), candidate.install_id().to_string()))
        .collect();
    ids.sort();
    ids
}

#[test]
fn a_second_launch_installs_nothing_and_ids_follow_python() {
    let folder = std::env::temp_dir().join(format!("sylvia-preinstall-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&folder);
    std::fs::create_dir_all(&folder).unwrap();
    let database = folder.join("authority.sqlite3");
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&repository_root()));

    let mut store = AuthorityStore::open_for_application_owner(&database, None).unwrap();
    store.prepare_new_campaign_storage().unwrap();
    assert!(ids(&store).is_empty());
    preinstall_bundled(&mut store, &bundled).unwrap();
    let first = ids(&store);

    let version = bundled.bundled_srd_version().unwrap();
    let seed = hex::encode(Sha256::digest(format!("{SRD_PACKAGE_ID}\0{version}").as_bytes()));
    let riichi = hex::encode(Sha256::digest(bundled.bundled_riichi_archive().unwrap()));
    let srd_install = format!("install-bundled-srd-{}", &seed[..24]);
    let riichi_install = format!("install-bundled-riichi-{}", &riichi[..24]);
    let mut expected = vec![
        (format!("candidate-{srd_install}"), srd_install),
        (format!("candidate-{riichi_install}"), riichi_install),
    ];
    expected.sort();
    assert_eq!(first, expected);

    // The same store again, and a reopened one: nothing more is installed.
    preinstall_bundled(&mut store, &bundled).unwrap();
    assert_eq!(ids(&store), first);
    drop(store);
    let mut reopened = AuthorityStore::open_for_application_owner(&database, None).unwrap();
    preinstall_bundled(&mut reopened, &bundled).unwrap();
    assert_eq!(ids(&reopened), first);
    drop(reopened);
    std::fs::remove_dir_all(&folder).unwrap();
}
