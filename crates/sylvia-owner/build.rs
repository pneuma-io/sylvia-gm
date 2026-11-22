//! Application manifests (Windows, MSVC).
//!
//! - The executables (`sylvia-gm-owner`, whose `--desktop-update-helper` mode is the update
//!   helper, and `sylvia-gm`) embed `sylvia-gm-owner.manifest`: `longPathAware`, as `python.exe`
//!   declares it, and the common-controls 6 dependency the helper's task dialog needs.
//! - Test binaries embed `tests/common-controls-6.manifest`, so the update status window's task
//!   dialog can be tested (`tests/progress_dialog.rs`).

fn main() {
    println!("cargo:rerun-if-changed=tests/common-controls-6.manifest");
    println!("cargo:rerun-if-changed=sylvia-gm-owner.manifest");
    if std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc") {
        let crate_dir = std::path::PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("set by cargo"));
        let tests = crate_dir.join("tests").join("common-controls-6.manifest");
        println!("cargo:rustc-link-arg-tests=/MANIFEST:EMBED");
        println!("cargo:rustc-link-arg-tests=/MANIFESTINPUT:{}", tests.display());
        let executables = crate_dir.join("sylvia-gm-owner.manifest");
        println!("cargo:rustc-link-arg-bins=/MANIFEST:EMBED");
        println!("cargo:rustc-link-arg-bins=/MANIFESTINPUT:{}", executables.display());
        // Dictation: the shipped `ctranslate2.dll` is loaded at its first use, not at start, so
        // the worker checks the file beside the owner before anything calls it, and an owner
        // without it still starts (`dictation::engine`).
        if std::env::var_os("CARGO_FEATURE_DICTATION").is_some() {
            println!("cargo:rustc-link-arg=/DELAYLOAD:ctranslate2.dll");
            println!("cargo:rustc-link-lib=delayimp");
            // LNK4199: binaries that never call the engine (most test binaries) have nothing to
            // delay-load.
            println!("cargo:rustc-link-arg=/IGNORE:4199");
        }
    }
}
