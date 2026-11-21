//! `safe_file`: the platform-independent parsing against Python's vectors, and the observable
//! behaviour of each platform primitive, including the three wrapped Windows calls.

mod common;

use sylvia_store::safe_file::{self, Publication};

#[test]
fn mount_types_and_classification_match_python() {
    let vectors = common::read_json("tests/vectors/safe_file_vectors.json");
    for case in vectors["mount_filesystem_type"].as_array().unwrap() {
        let got = safe_file::mount_filesystem_type(
            case["mountinfo"].as_str().unwrap(),
            case["path"].as_str().unwrap(),
        );
        assert_eq!(got.as_deref(), case["type"].as_str(), "{case}");
    }
    for (name, expected) in vectors["classify_filesystem"].as_object().unwrap() {
        assert_eq!(safe_file::classify_filesystem(Some(name)), expected.as_str(), "{name}");
    }
    assert_eq!(safe_file::classify_filesystem(None), vectors["classify_none"].as_str());
    let listed = |key: &str| -> Vec<String> { serde_json::from_value(vectors[key].clone()).unwrap() };
    assert_eq!(listed("local_filesystems"), safe_file::LOCAL_FILESYSTEMS);
    assert_eq!(listed("remote_filesystems"), safe_file::REMOTE_FILESYSTEMS);
}

#[test]
fn exclusive_create_refuses_an_existing_name() {
    let folder = common::work_folder("safe-file-exclusive");
    let path = folder.join("claim.tmp");
    let first = safe_file::create_exclusive(&path).expect("first claim");
    let error = safe_file::create_exclusive(&path).expect_err("second claim refused");
    assert_eq!(error.kind(), std::io::ErrorKind::AlreadyExists);
    assert_eq!(safe_file::descriptor_identity(&first).unwrap(), safe_file::handle_identity(&first).unwrap());
}

#[test]
fn handle_bound_delete_removes_only_the_held_file() {
    let folder = common::work_folder("safe-file-delete");
    let path = folder.join("held.tmp");
    std::fs::write(&path, b"held").unwrap();
    let handle = safe_file::open_cleanup_handle(&path).unwrap();
    let identity = safe_file::handle_identity(&handle).unwrap();
    assert_eq!(
        safe_file::handle_identity(&safe_file::open_identity_handle(&path).unwrap()).unwrap(),
        identity
    );
    assert!(safe_file::delete_by_handle(&handle, &path), "disposition requested");
    assert!(safe_file::close_handle(handle));
    assert!(std::fs::symlink_metadata(&path).is_err(), "the held file is gone");
}

/// The store's cleanup proves the name still holds the claimed file before deleting through
/// the handle; after a swap the identity differs and nothing is deleted.
#[test]
fn a_swapped_name_is_refused_before_any_delete() {
    let folder = common::work_folder("safe-file-swap");
    let path = folder.join("claimed.tmp");
    std::fs::write(&path, b"claimed").unwrap();
    let claimed = safe_file::handle_identity(&safe_file::open_identity_handle(&path).unwrap()).unwrap();
    std::fs::rename(&path, folder.join("moved.tmp")).unwrap();
    std::fs::write(&path, b"swapped in").unwrap();
    let handle = safe_file::open_cleanup_handle(&path).unwrap();
    assert_ne!(safe_file::handle_identity(&handle).unwrap(), claimed, "the swap is detected");
    drop(handle);
    assert_eq!(std::fs::read(&path).unwrap(), b"swapped in");
    assert_eq!(std::fs::read(folder.join("moved.tmp")).unwrap(), b"claimed");
}

#[cfg(target_os = "linux")]
#[test]
fn linux_delete_refuses_a_name_that_no_longer_holds_the_file() {
    let folder = common::work_folder("safe-file-linux-swap");
    let path = folder.join("held.tmp");
    std::fs::write(&path, b"held").unwrap();
    let handle = safe_file::open_cleanup_handle(&path).unwrap();
    std::fs::rename(&path, folder.join("moved.tmp")).unwrap();
    std::fs::write(&path, b"swapped in").unwrap();
    assert!(!safe_file::delete_by_handle(&handle, &path), "refused after the swap");
    assert_eq!(std::fs::read(&path).unwrap(), b"swapped in");
}

#[test]
fn replace_publishes_over_an_existing_target() {
    let folder = common::work_folder("safe-file-replace");
    let source = folder.join(".copy.tmp");
    let target = folder.join("copy.sqlite3");
    std::fs::write(&source, b"new").unwrap();
    std::fs::write(&target, b"old").unwrap();
    let parent = safe_file::stat_identity(&folder).unwrap();
    assert_eq!(safe_file::publish_replace(&source, &target, parent), Publication::Published);
    assert_eq!(std::fs::read(&target).unwrap(), b"new");
    assert!(!source.exists());
    assert_eq!(
        safe_file::publish_replace(&source, &target, parent),
        Publication::NotPublished,
        "source gone"
    );
}

#[cfg(windows)]
#[test]
fn windows_volume_admission_tells_fixed_from_remote_and_unmapped() {
    let folder = common::work_folder("safe-file-volume");
    assert_eq!(safe_file::volume_admission(&folder.join("copy.sqlite3")), None, "a fixed disk is admitted");
    assert_eq!(
        safe_file::volume_admission(std::path::Path::new(r"\\localhost\c$\copy.sqlite3")),
        Some(safe_file::VOLUME_REMOTE),
        "an administrative share is a network volume"
    );
    let unmapped = "ZYXWVUTSRQPONMLKJIHGFE"
        .chars()
        .map(|letter| format!("{letter}:\\"))
        .find(|root| !std::path::Path::new(root).exists())
        .expect("an unused drive letter");
    assert_eq!(
        safe_file::volume_admission(&std::path::Path::new(&unmapped).join("copy.sqlite3")),
        Some(safe_file::VOLUME_UNSUPPORTED),
        "an unmapped drive (DRIVE_NO_ROOT_DIR) is unsupported"
    );
}
