//! Test binaries only: embed a common-controls 6 manifest, so the update status window's task
//! dialog can be tested (`tests/progress_dialog.rs`). The owner executables get no manifest
//! here; the packaged helper's real manifest belongs to packaging.

fn main() {
    println!("cargo:rerun-if-changed=tests/common-controls-6.manifest");
    if std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc") {
        let manifest = std::path::Path::new(&std::env::var("CARGO_MANIFEST_DIR").expect("set by cargo"))
            .join("tests")
            .join("common-controls-6.manifest");
        println!("cargo:rustc-link-arg-tests=/MANIFEST:EMBED");
        println!("cargo:rustc-link-arg-tests=/MANIFESTINPUT:{}", manifest.display());
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
