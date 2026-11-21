//! `storage::disk_free` against CPython's `shutil.disk_usage(path).free` on the same temporary
//! folder, a file in it, and a missing path.
//!
//! Free space moves while the two run, so values are compared within 64 MiB, and the refusal
//! decision the updater makes (`free < needed + 16 MiB`) is compared at a threshold far from
//! both. The test needs the reference interpreter, so it is ignored by default; reference
//! tooling, like the vector generators (rule 8). Run it with:
//!
//! ```text
//! SYLVIA_REFERENCE_PYTHON=<repo>/.venv/Scripts/python.exe \
//!     cargo test -p sylvia-owner --test disk_free_reference -- --ignored
//! ```

mod support;

use std::path::Path;
use std::process::Command;
use sylvia_owner::desktop_update::storage::disk_free;

/// `shutil.disk_usage(path).free`, or `None` when Python raises.
fn python_free(python: &str, path: &Path) -> Option<u64> {
    let output = Command::new(python)
        .args(["-I", "-c", "import shutil, sys; print(shutil.disk_usage(sys.argv[1]).free)"])
        .arg(path)
        .output()
        .expect("the reference interpreter runs");
    if !output.status.success() {
        return None;
    }
    String::from_utf8(output.stdout).ok()?.trim().parse().ok()
}

#[test]
#[ignore = "needs SYLVIA_REFERENCE_PYTHON, the snapshot's interpreter"]
fn free_space_matches_pythons_shutil_disk_usage() {
    let python =
        std::env::var("SYLVIA_REFERENCE_PYTHON").expect("SYLVIA_REFERENCE_PYTHON names the interpreter");
    let folder = support::temporary_folder("disk-free");
    let file = folder.0.join("file.txt");
    std::fs::write(&file, b"x").unwrap();
    const TOLERANCE: u64 = 64 * 1024 * 1024;
    for path in [folder.0.clone(), file] {
        let rust = disk_free(&path).expect("free space reads");
        let python = python_free(&python, &path).expect("Python reads free space");
        assert!(rust.abs_diff(python) < TOLERANCE, "{}: Rust {rust}, Python {python}", path.display());
        // The updater's decision at a threshold well away from the measured value.
        for needed in [rust / 2, rust.saturating_mul(2)] {
            let refuse = |free: u64| u128::from(free) < u128::from(needed) + 16 * 1024 * 1024;
            assert_eq!(refuse(rust), refuse(python), "{} needing {needed}", path.display());
        }
        eprintln!("{}: Rust {rust}, Python {python}", path.display());
    }
    let missing = folder.0.join("missing").join("deeper");
    assert!(disk_free(&missing).is_err());
    assert_eq!(python_free(&python, &missing), None);
}
