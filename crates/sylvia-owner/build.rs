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
    }
}
