//! The owner's startup installs of the bundled packages: `_preinstall_bundled_srd` and
//! `_preinstall_bundled_riichi` in `owner_process.py`, run after storage preparation and before
//! the owner reports ready. Each is idempotent: it installs only when no admitted candidate
//! already holds the bundled packages at their versions, so installed content and same-version
//! edits are kept.

use sha2::{Digest, Sha256};
use sylvia_app::bundled_packages::{BundledPackages, BUNDLED_RIICHI_PACKAGES};
use sylvia_app::package_library::install_package_archive;
use sylvia_domain::package_library::PackageCandidate;
use sylvia_store::{AuthorityStore, StoreResult};

/// `RIFT_RULESET_ID`: the bundled SRD's package id.
pub const SRD_PACKAGE_ID: &str = "sylvia.srd-lite.bg3";

fn sha256_hex(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn holds(candidate: &PackageCandidate, package_id: &str, version: &str) -> bool {
    candidate
        .package_lock()
        .entries()
        .iter()
        .any(|entry| entry.package_id() == package_id && entry.version() == version)
}

/// `_preinstall_bundled_srd(bus)`: install the bundled SRD unless a candidate already holds it
/// at the bundled version.
pub fn preinstall_bundled_srd(store: &mut AuthorityStore, bundled: &BundledPackages) -> StoreResult<()> {
    let version = bundled.bundled_srd_version()?;
    if store.package_candidates()?.iter().any(|candidate| holds(candidate, SRD_PACKAGE_ID, &version)) {
        return Ok(());
    }
    let archive = bundled.bundled_srd_archive()?;
    let seed = sha256_hex(format!("{SRD_PACKAGE_ID}\0{version}").as_bytes());
    install_package_archive(&*store, &archive, &format!("install-bundled-srd-{}", &seed[..24]))?;
    Ok(())
}

/// `_preinstall_bundled_riichi(bus)`: add the shipped Riichi closure once, unless one candidate
/// already holds every package of it at its version.
pub fn preinstall_bundled_riichi(store: &mut AuthorityStore, bundled: &BundledPackages) -> StoreResult<()> {
    let installed = store.package_candidates()?.iter().any(|candidate| {
        BUNDLED_RIICHI_PACKAGES.iter().all(|(package_id, version)| holds(candidate, package_id, version))
    });
    if installed {
        return Ok(());
    }
    let archive = bundled.bundled_riichi_archive()?;
    let digest = sha256_hex(&archive);
    install_package_archive(&*store, &archive, &format!("install-bundled-riichi-{}", &digest[..24]))?;
    Ok(())
}

/// Both, in Python's order.
pub fn preinstall_bundled(store: &mut AuthorityStore, bundled: &BundledPackages) -> StoreResult<()> {
    preinstall_bundled_srd(store, bundled)?;
    preinstall_bundled_riichi(store, bundled)
}
