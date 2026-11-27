//! `application/desktop_update/installed.py`: the current-user NSIS replacement and the
//! retained-installer recovery of one registered copy.
//!
//! The registration is read through [`Registration`]: on Windows the product's two fixed
//! current-user keys ([`WindowsRegistration`]), in tests a fake. The installer runs through a
//! caller's function: on Windows [`run_nsis`].

use super::manifest::{bounded_json, Manifest, PayloadFile, Version, MANIFEST_LIMIT};
use super::payload::verify_artifact;
use super::replacement::{copy_file, file_matches};
use super::storage::{
    atomic_receipt, checked_file, directory_identity, disk_free, ensure_directory, is_relative_to, os_error,
    present, same_path, Identity,
};
use super::update_error;
use crate::util::uuid4_hex;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

/// `InstalledPhase`.
pub mod phase {
    pub const PREPARED: &str = "prepared";
    pub const APPLYING: &str = "applying";
    pub const AWAITING_RESTART: &str = "awaiting_restart";
    pub const ROLLING_BACK: &str = "rolling_back";
    pub const ROLLED_BACK: &str = "rolled_back";
    pub const CONFIRMED: &str = "confirmed";
}

/// `INSTALLED_MOVES`.
pub const INSTALLED_MOVES: [(&str, &[&str]); 6] = {
    use phase::*;
    [
        (PREPARED, &[APPLYING, ROLLING_BACK]),
        (APPLYING, &[APPLYING, AWAITING_RESTART, ROLLING_BACK]),
        (AWAITING_RESTART, &[APPLYING, CONFIRMED, ROLLING_BACK]),
        (ROLLING_BACK, &[ROLLING_BACK, ROLLED_BACK]),
        (ROLLED_BACK, &[ROLLING_BACK]),
        (CONFIRMED, &[CONFIRMED, ROLLING_BACK]),
    ]
};

/// `UNINSTALL_KEY`: the current Tauri product and identifier, not a search.
pub const UNINSTALL_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Sylvia GM";
/// `PRODUCT_KEY`: Tauri's default publisher fallback.
pub const PRODUCT_KEY: &str = r"Software\pneuma-io\Sylvia GM";
/// The uninstall key's values `WindowsRegistration.read` asks for, in its order.
pub const UNINSTALL_VALUES: [&str; 6] =
    ["DisplayName", "DisplayVersion", "Publisher", "InstallLocation", "MainBinaryName", "UninstallString"];

/// A registration's values, as UTF-16 code units: Python's `str` from the registry may hold a
/// lone surrogate, which then simply compares unequal. In the order they were read, as Python's
/// `dict` keeps them: the uninstall key's values in [`UNINSTALL_VALUES`] order, then
/// `ProductLocation`.
pub type RegistrationRow = indexmap::IndexMap<String, Vec<u16>>;

/// `InstalledRegistration`.
pub trait Registration: Send + Sync {
    fn read(&self) -> CodedResult<RegistrationRow>;
}

/// `REG_SZ`.
pub const REG_SZ: u32 = sylvia_base::winreg::REG_SZ;

/// A value `WindowsRegistration.read` keeps: a `REG_SZ` (converted by `sylvia_base::winreg`,
/// CPython's `Reg2Py`) of at most 4096 characters, a surrogate pair counting once. `None` is
/// its `ValueError`.
fn registry_string(value: sylvia_base::winreg::Value) -> Option<Vec<u16>> {
    match value {
        sylvia_base::winreg::Value::Sz(units) if sylvia_base::winreg::characters(&units) <= 4096 => {
            Some(units)
        }
        _ => None,
    }
}

/// [`registry_string`] of the raw type and bytes Windows returned.
pub fn registry_text(kind: u32, data: &[u8]) -> Option<Vec<u16>> {
    sylvia_base::winreg::convert(kind, data).ok().and_then(registry_string)
}

fn installation_unavailable() -> CodedError {
    update_error(
        "installation_unavailable",
        "This copy is not the registered current-user NSIS installation. Keep it intact and use an approved manual repair.",
    )
}

/// `WindowsRegistration`: only this product's fixed current-user keys, read-only; other
/// installations are never listed.
#[derive(Debug, Clone, Copy, Default)]
pub struct WindowsRegistration;

/// The registration `WindowsRegistration.read` returns from the raw values it read: the
/// uninstall key's values in [`UNINSTALL_VALUES`] order, then the product key's default value
/// as `ProductLocation`. `None` for a value stands for a missing key or value.
pub fn registration_from(
    values: &[Option<(u32, Vec<u8>)>],
    default: Option<(u32, Vec<u8>)>,
) -> CodedResult<RegistrationRow> {
    let mut row = RegistrationRow::new();
    for (name, value) in UNINSTALL_VALUES.iter().zip(values) {
        let (kind, data) = value.as_ref().ok_or_else(installation_unavailable)?;
        row.insert(name.to_string(), registry_text(*kind, data).ok_or_else(installation_unavailable)?);
    }
    if values.len() != UNINSTALL_VALUES.len() {
        return Err(installation_unavailable());
    }
    let (kind, data) = default.ok_or_else(installation_unavailable)?;
    row.insert("ProductLocation".into(), registry_text(kind, &data).ok_or_else(installation_unavailable)?);
    Ok(row)
}

/// `WindowsRegistration.read` on the given current-user keys: the uninstall key's values,
/// then the product key's default value, each key opened read-only in the 64-bit view and
/// closed before the next.
#[cfg(windows)]
fn read_windows_registration(uninstall: &str, product: &str) -> CodedResult<RegistrationRow> {
    use sylvia_base::winreg::{Key, Root, View};
    let read = |subkey: &str, names: &[&str]| -> CodedResult<Vec<Vec<u16>>> {
        let key =
            Key::open(Root::CurrentUser, subkey, View::Wow64_64).map_err(|_| installation_unavailable())?;
        names
            .iter()
            .map(|name| {
                key.query_value_ex(name).ok().and_then(registry_string).ok_or_else(installation_unavailable)
            })
            .collect()
    };
    let mut row: RegistrationRow = UNINSTALL_VALUES
        .iter()
        .map(|name| name.to_string())
        .zip(read(uninstall, &UNINSTALL_VALUES)?)
        .collect();
    let mut default = read(product, &[""])?;
    row.insert("ProductLocation".into(), default.pop().unwrap_or_default());
    Ok(row)
}

impl Registration for WindowsRegistration {
    fn read(&self) -> CodedResult<RegistrationRow> {
        #[cfg(windows)]
        {
            read_windows_registration(UNINSTALL_KEY, PRODUCT_KEY)
        }
        #[cfg(not(windows))]
        {
            Err(update_error("unsupported_platform", "NSIS updating requires Windows."))
        }
    }
}

/// `registered_versions(version)`: the existing Alpha recipe registers `alpha.N` as the
/// Windows-compatible `-N`.
pub fn registered_versions(version: &Version) -> BTreeSet<String> {
    let mut result = BTreeSet::from([version.text.clone()]);
    if let [label, number] = version.prerelease.as_slice() {
        if label == "alpha" && !number.is_empty() && number.bytes().all(|byte| byte.is_ascii_digit()) {
            let (major, minor, patch) = version.core;
            result.insert(format!("{major}.{minor}.{patch}-{number}"));
        }
    }
    result
}

/// `subprocess.list2cmdline(arguments)`.
pub fn list2cmdline(arguments: &[&str]) -> String {
    let mut result = String::new();
    for argument in arguments {
        if !result.is_empty() {
            result.push(' ');
        }
        let quote = argument.contains(' ') || argument.contains('\t') || argument.is_empty();
        if quote {
            result.push('"');
        }
        let mut backslashes = 0usize;
        for c in argument.chars() {
            match c {
                '\\' => backslashes += 1,
                '"' => {
                    result.push_str(&"\\".repeat(backslashes * 2));
                    backslashes = 0;
                    result.push_str("\\\"");
                }
                _ => {
                    result.push_str(&"\\".repeat(backslashes));
                    backslashes = 0;
                    result.push(c);
                }
            }
        }
        result.push_str(&"\\".repeat(backslashes));
        if quote {
            result.push_str(&"\\".repeat(backslashes));
            result.push('"');
        }
    }
    result
}

/// `str(path)`.
fn text(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

/// `nsis_command_line(artifact, live)`: NSIS needs an unquoted absolute `/D` tail. No shell,
/// no `/R` and never the uninstaller.
pub fn nsis_command_line(artifact: &Path, live: &Path) -> CodedResult<String> {
    let (artifact_text, live_text) = (text(artifact), text(live));
    if !artifact.is_absolute()
        || !live.is_absolute()
        || format!("{artifact_text}{live_text}").chars().any(|c| matches!(c, '"' | '\r' | '\n' | '\0'))
        || live_text.starts_with("\\\\")
        || live_text.starts_with("//")
    {
        return Err(update_error("unsafe_installation", "The exact installer destination is unsupported."));
    }
    Ok(format!("{} /D={live_text}", list2cmdline(&[&artifact_text, "/S", "/UPDATE", "/NS"])))
}

/// The installer's child process as `run_nsis` starts it: the verified installer itself, the
/// three switches, then `/D=` unquoted (`raw_arg`, never `arg`, which would quote a path with
/// spaces). Only the listed environment, its own temporary folder, no window, no console.
/// Rust always quotes the program; NSIS skips it either way.
pub fn nsis_command(artifact: &Path, live: &Path, temporary: &Path) -> CodedResult<std::process::Command> {
    nsis_command_line(artifact, live)?;
    let mut command = std::process::Command::new(artifact);
    command.args(["/S", "/UPDATE", "/NS"]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.raw_arg(format!("/D={}", text(live)));
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    #[cfg(not(windows))]
    command.arg(format!("/D={}", text(live)));
    command.env_clear();
    for name in ["SystemRoot", "WINDIR", "PATH", "LOCALAPPDATA", "APPDATA"] {
        if let Some(value) = std::env::var_os(name) {
            command.env(name, value);
        }
    }
    command
        .env("TEMP", temporary)
        .env("TMP", temporary)
        .current_dir(artifact.parent().unwrap_or(Path::new("")))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    Ok(command)
}

/// `run_nsis(artifact, live)`: the installer's exit code. No forced stop and no time limit.
pub fn run_nsis(artifact: &Path, live: &Path) -> CodedResult<i64> {
    if !cfg!(windows) {
        return Err(update_error("unsupported_platform", "NSIS updating requires Windows."));
    }
    let temporary = artifact.parent().unwrap_or(Path::new("")).join("installer-temp");
    ensure_directory(&temporary)?;
    directory_identity(&temporary)?;
    let mut command = nsis_command(artifact, live, &temporary)?;
    let unavailable = || {
        update_error(
            "installer_unavailable",
            "Windows could not start the verified installer. No permission change or elevation was attempted.",
        )
    };
    let status = command.spawn().map_err(|_| unavailable())?.wait().map_err(|_| unavailable())?;
    Ok(status.code().map_or(1, i64::from))
}

/// The installer a replacement runs: `run(artifact, live)` gives its exit code.
pub type Installer = Arc<dyn Fn(&Path, &Path) -> CodedResult<i64> + Send + Sync>;

fn units(text: &str) -> Vec<u16> {
    text.encode_utf16().collect()
}

/// A registry text as a path (`Path(value)`).
fn registry_path(value: &[u16]) -> PathBuf {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStringExt;
        PathBuf::from(std::ffi::OsString::from_wide(value))
    }
    #[cfg(not(windows))]
    {
        PathBuf::from(String::from_utf16_lossy(value))
    }
}

/// `InstalledReplacement`.
#[derive(Clone)]
pub struct InstalledReplacement {
    pub live: PathBuf,
    pub operation: PathBuf,
    pub artifacts: PathBuf,
    pub manifest: Manifest,
    pub previous: Manifest,
    registration: Arc<dyn Registration>,
    run: Installer,
    live_identity: Identity,
    operation_identity: Identity,
    artifact_identity: Identity,
    journal: PathBuf,
}

impl std::fmt::Debug for InstalledReplacement {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("InstalledReplacement")
            .field("live", &self.live)
            .field("operation", &self.operation)
            .field("artifacts", &self.artifacts)
            .finish_non_exhaustive()
    }
}

impl InstalledReplacement {
    /// `InstalledReplacement(live, operation, manifest, previous, registration, run,
    /// artifacts=)`.
    pub fn new(
        live: &Path,
        operation: &Path,
        manifest: &Manifest,
        previous: &Manifest,
        registration: Arc<dyn Registration>,
        run: Installer,
        artifacts: Option<&Path>,
    ) -> CodedResult<Self> {
        if manifest.distribution != "installed"
            || previous.distribution != "installed"
            || manifest.target != previous.target
            || manifest.version <= previous.version
        {
            return Err(update_error("wrong_distribution", "The installed update/recovery pair is invalid."));
        }
        let artifacts = artifacts.unwrap_or(operation).to_path_buf();
        let live_identity = directory_identity(live)?;
        let operation_identity = directory_identity(operation)?;
        let artifact_identity = directory_identity(&artifacts)?;
        if !is_relative_to(operation, &artifacts) {
            return Err(update_error("unsafe_staging", "Installer receipts must belong to this download."));
        }
        if is_relative_to(live, operation) || is_relative_to(operation, live) {
            return Err(update_error("unsafe_installation", "Installer staging must be outside the app."));
        }
        Ok(Self {
            live: live.to_path_buf(),
            operation: operation.to_path_buf(),
            artifacts,
            manifest: manifest.clone(),
            previous: previous.clone(),
            registration,
            run,
            live_identity,
            operation_identity,
            artifact_identity,
            journal: operation.join("installed-replacement.json"),
        })
    }

    /// `_check(versions)`: the folders, the registration and both retained installers.
    fn check(&self, versions: &[&Version]) -> CodedResult<()> {
        let marker = self.live.join("sylvia-portable.json");
        if directory_identity(&self.live)? != self.live_identity
            || directory_identity(&self.operation)? != self.operation_identity
            || directory_identity(&self.artifacts)? != self.artifact_identity
            || present(&marker)
        {
            return Err(update_error("wrong_installation", "The registered application location changed."));
        }
        let row = self.registration.read()?;
        let field = |name: &str| row.get(name);
        let allowed: BTreeSet<Vec<u16>> = versions
            .iter()
            .flat_map(|version| registered_versions(version))
            .map(|text| units(&text))
            .collect();
        if field("DisplayName") != Some(&units("Sylvia GM"))
            || field("Publisher") != Some(&units("pneuma-io"))
            || field("MainBinaryName") != Some(&units("sylvia-gm.exe"))
            || !field("DisplayVersion").is_some_and(|version| allowed.contains(version))
        {
            return Err(update_error("wrong_installation", "The registered NSIS copy is inconsistent."));
        }
        let quote = u16::from(b'"');
        let uninstall = field("UninstallString").cloned().unwrap_or_default();
        let inner = if uninstall.len() >= 2 { &uninstall[1..uninstall.len() - 1] } else { &[] };
        if uninstall.first() != Some(&quote)
            || uninstall.last() != Some(&quote)
            || inner.contains(&quote)
            || !same_path(&registry_path(inner), &self.live.join("uninstall.exe"))
        {
            return Err(update_error("wrong_installation", "The NSIS registration is inconsistent."));
        }
        for name in ["InstallLocation", "ProductLocation"] {
            let value = field(name).cloned().unwrap_or_default();
            let start = value.iter().position(|unit| *unit != quote).unwrap_or(value.len());
            let end = value.iter().rposition(|unit| *unit != quote).map_or(start, |end| end + 1);
            let location = &value[start..end.max(start)];
            if location.is_empty() || !same_path(&registry_path(location), &self.live) {
                return Err(update_error("wrong_installation", "Updating another copy is not permitted."));
            }
        }
        verify_artifact(&self.artifacts.join("artifact.verified"), &self.manifest)?;
        verify_artifact(&self.artifacts.join("previous-installer/artifact.verified"), &self.previous)
    }

    /// `_save(phase)`.
    fn save(&self, phase: &str) -> CodedResult<()> {
        let mut row = JsonMap::new();
        row.insert("format_version".into(), 1.into());
        row.insert("phase".into(), phase.into());
        row.insert("version".into(), self.manifest.version.text.clone().into());
        row.insert("previous_version".into(), self.previous.version.text.clone().into());
        atomic_receipt(&self.journal, &Json::Object(row))
    }

    /// `_phase()`.
    fn phase(&self) -> CodedResult<String> {
        let row = bounded_json(&checked_file(&self.journal, 4096)?, MANIFEST_LIMIT)?;
        let keys = ["format_version", "phase", "version", "previous_version"];
        let phases = [
            phase::PREPARED,
            phase::APPLYING,
            phase::AWAITING_RESTART,
            phase::ROLLING_BACK,
            phase::ROLLED_BACK,
            phase::CONFIRMED,
        ];
        let valid = row.as_object().and_then(|row| {
            let shaped = row.len() == keys.len() && keys.iter().all(|key| row.contains_key(*key));
            let format = row["format_version"].is_number() && row["format_version"].as_i64() == Some(1);
            let versions = row["version"] == self.manifest.version.text.as_str()
                && row["previous_version"] == self.previous.version.text.as_str();
            let phase = row["phase"].as_str().filter(|phase| phases.contains(phase));
            (shaped && format && versions).then_some(phase).flatten().map(str::to_string)
        });
        valid.ok_or_else(|| update_error("invalid_journal", "Installed recovery needs reconciliation."))
    }

    /// `_matches(manifest)`.
    fn matches(&self, manifest: &Manifest) -> CodedResult<bool> {
        for row in &manifest.files {
            if !file_matches(&self.live.join(&row.path), row)? {
                return Ok(false);
            }
        }
        Ok(true)
    }

    /// `_mixed_files()`: every known path holds the old file, the new one, or nothing; a
    /// manual application repair is preserved.
    fn mixed_files(&self) -> CodedResult<()> {
        let old: BTreeMap<&str, &PayloadFile> =
            self.previous.files.iter().map(|row| (row.path.as_str(), row)).collect();
        let new: BTreeMap<&str, &PayloadFile> =
            self.manifest.files.iter().map(|row| (row.path.as_str(), row)).collect();
        let names: BTreeSet<&str> = old.keys().chain(new.keys()).copied().collect();
        for name in names {
            let target = self.live.join(name);
            let mut matched = false;
            for row in [old.get(name), new.get(name)].into_iter().flatten() {
                if file_matches(&target, row)? {
                    matched = true;
                    break;
                }
            }
            if matched || !present(&target) {
                continue; // A partial NSIS copy can leave an expected destination absent.
            }
            return Err(update_error("application_changed", "A manual application repair is preserved."));
        }
        Ok(())
    }

    /// `prepare()`.
    pub fn prepare(&mut self) -> CodedResult<()> {
        self.check(&[&self.previous.version])?;
        if present(&self.journal) {
            return Err(update_error("staging_exists", "Existing installed preparation must be retained."));
        }
        if !self.matches(&self.previous)? {
            return Err(update_error("application_changed", "Current app files do not match recovery."));
        }
        self.mixed_files()?;
        let needed: u128 = self.manifest.files.iter().map(|row| u128::from(row.size)).sum();
        let free = disk_free(&self.live).map_err(|error| os_error(&error))?;
        if u128::from(free) < needed + 16 * 1024 * 1024 {
            return Err(update_error("disk_full", "There is not enough space to apply this update safely."));
        }
        self.save(phase::PREPARED)
    }

    /// `_retire(source, keep, directory, processes_exited)`: move each file only `source`
    /// has into `directory`, by a verified copy first (the two folders may be on different
    /// volumes), then unlink the original.
    fn retire(
        &self,
        source: &Manifest,
        keep: &Manifest,
        directory: &str,
        processes_exited: &dyn Fn() -> CodedResult<bool>,
    ) -> CodedResult<()> {
        let keep_names: BTreeSet<&str> = keep.files.iter().map(|row| row.path.as_str()).collect();
        for row in &source.files {
            if keep_names.contains(row.path.as_str()) {
                continue;
            }
            let target = self.live.join(&row.path);
            if !present(&target) {
                continue;
            }
            if !processes_exited()? || !file_matches(&target, row)? {
                return Err(update_error("application_changed", "An obsolete application file changed."));
            }
            let retained = self.operation.join(directory).join(&row.path);
            let parent = retained.parent().unwrap_or(Path::new("")).to_path_buf();
            ensure_directory(&parent)?;
            directory_identity(&parent)?;
            if present(&retained) {
                if !file_matches(&retained, row)? {
                    return Err(update_error("staging_exists", "An existing retired file is preserved."));
                }
            } else {
                // A crash between the copy and the unlink keeps both copies.
                let free = disk_free(&self.operation).map_err(|error| os_error(&error))?;
                if u128::from(free) < u128::from(row.size) + 16 * 1024 * 1024 {
                    return Err(update_error("disk_full", "There is not enough recovery storage."));
                }
                let temporary = parent.join(format!(".retiring-{}.tmp", uuid4_hex()));
                copy_file(&target, &temporary, row)?;
                if present(&retained) {
                    return Err(update_error("staging_exists", "An existing retired file is preserved."));
                }
                std::fs::rename(&temporary, &retained).map_err(|error| os_error(&error))?;
            }
            if !processes_exited()? {
                return Err(update_error("application_busy", "Close owned processes before retiring files."));
            }
            if !file_matches(&retained, row)? || !file_matches(&target, row)? {
                return Err(update_error("application_changed", "An obsolete application file changed."));
            }
            std::fs::remove_file(&target).map_err(|error| os_error(&error))?;
        }
        Ok(())
    }

    /// The registered `DisplayVersion` is one of `version`'s.
    fn registered_as(&self, version: &Version) -> CodedResult<bool> {
        let row = self.registration.read()?;
        let allowed: BTreeSet<Vec<u16>> =
            registered_versions(version).iter().map(|text| units(text)).collect();
        Ok(row.get("DisplayVersion").is_some_and(|registered| allowed.contains(registered)))
    }

    /// `apply(processes_exited)`.
    pub fn apply(&mut self, processes_exited: &dyn Fn() -> CodedResult<bool>) -> CodedResult<()> {
        self.check(&[&self.previous.version, &self.manifest.version])?;
        if ![phase::PREPARED, phase::APPLYING, phase::AWAITING_RESTART].contains(&self.phase()?.as_str()) {
            return Err(update_error("invalid_phase", "This installer cannot apply in its current state."));
        }
        if !processes_exited()? {
            return Err(update_error("application_busy", "Close all owned processes before updating."));
        }
        self.mixed_files()?;
        self.save(phase::APPLYING)?;
        let needed = !self.matches(&self.manifest)? || !self.registered_as(&self.manifest.version)?;
        if needed && (self.run)(&self.artifacts.join("artifact.verified"), &self.live)? != 0 {
            return Err(update_error(
                "installer_failed",
                "The installer did not complete. Recovery files are retained; no restart was attempted.",
            ));
        }
        self.check(&[&self.manifest.version])?;
        if !self.matches(&self.manifest)? {
            return Err(update_error(
                "installation_incomplete",
                "Installed files failed final verification.",
            ));
        }
        let (previous, manifest) = (self.previous.clone(), self.manifest.clone());
        self.retire(&previous, &manifest, "retired", processes_exited)?;
        self.save(phase::AWAITING_RESTART)
    }

    /// `rollback(processes_exited, compatibility)`: a deliberate recovery, never a normal
    /// downgrade offer.
    pub fn rollback(
        &mut self,
        processes_exited: &dyn Fn() -> CodedResult<bool>,
        compatibility: &dyn Fn() -> CodedResult<()>,
    ) -> CodedResult<()> {
        self.check(&[&self.previous.version, &self.manifest.version])?;
        self.phase()?;
        if !processes_exited()? {
            return Err(update_error("application_busy", "Close all owned processes before recovery."));
        }
        compatibility()?;
        self.mixed_files()?;
        self.save(phase::ROLLING_BACK)?;
        let needed = !self.matches(&self.previous)? || !self.registered_as(&self.previous.version)?;
        if needed
            && (self.run)(&self.artifacts.join("previous-installer/artifact.verified"), &self.live)? != 0
        {
            return Err(update_error(
                "recovery_failed",
                "The retained installer could not repair this copy.",
            ));
        }
        self.check(&[&self.previous.version])?;
        if !self.matches(&self.previous)? {
            return Err(update_error("recovery_failed", "Recovered application files failed verification."));
        }
        let (previous, manifest) = (self.previous.clone(), self.manifest.clone());
        self.retire(&manifest, &previous, "failed-new", processes_exited)?;
        self.save(phase::ROLLED_BACK)
    }

    /// `confirm()`.
    pub fn confirm(&mut self) -> CodedResult<()> {
        self.check(&[&self.manifest.version])?;
        if ![phase::AWAITING_RESTART, phase::CONFIRMED].contains(&self.phase()?.as_str()) {
            return Err(update_error(
                "invalid_phase",
                "This installer has not reached restart confirmation.",
            ));
        }
        self.save(phase::CONFIRMED)
    }

    /// `helper_payload()`: the current owner executable, which runs the helper.
    pub fn helper_payload(&self) -> CodedResult<(PathBuf, PayloadFile)> {
        let row = self.previous.files.iter().find(|row| row.path == "sylvia-gm-owner.exe").cloned();
        let row = row.ok_or_else(|| {
            update_error("invalid_installation", "The current helper is missing from recovery.")
        })?;
        Ok((self.live.join(&row.path), row))
    }

    /// `verify_rollback()`: the exact previous registered copy, before recovery is
    /// acknowledged.
    pub fn verify_rollback(&mut self) -> CodedResult<()> {
        self.check(&[&self.previous.version])?;
        if self.phase()? != phase::ROLLED_BACK || !self.matches(&self.previous)? {
            return Err(update_error("recovery_incomplete", "Installed recovery has not finished."));
        }
        let old: BTreeSet<&str> = self.previous.files.iter().map(|row| row.path.as_str()).collect();
        for row in &self.manifest.files {
            if !old.contains(row.path.as_str()) && present(&self.live.join(&row.path)) {
                return Err(update_error("application_changed", "A recovered application path changed."));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn list2cmdline_quotes_as_cpython() {
        assert_eq!(list2cmdline(&["a", "b c", ""]), r#"a "b c" """#);
        assert_eq!(list2cmdline(&[r#"a\"b"#, r"c\\", r"d e\"]), r#"a\\\"b c\\ "d e\\""#);
    }

    #[test]
    fn registry_text_follows_reg2py() {
        let wide = |text: &str| text.encode_utf16().flat_map(u16::to_le_bytes).collect::<Vec<u8>>();
        assert_eq!(registry_text(REG_SZ, &[0x41, 0, 0x42]), Some(units("A")), "odd byte dropped");
        assert_eq!(registry_text(REG_SZ, &wide("AB")), Some(units("AB")), "no NUL");
        assert_eq!(registry_text(REG_SZ, &wide("A\0B\0")), Some(units("A")), "cut at the first NUL");
        assert_eq!(registry_text(REG_SZ, &[]), Some(vec![]));
        assert_eq!(registry_text(2, &wide("A\0")), None, "REG_EXPAND_SZ is refused");
        assert_eq!(registry_text(REG_SZ, &wide(&"x".repeat(4096))).map(|v| v.len()), Some(4096));
        assert_eq!(registry_text(REG_SZ, &wide(&"x".repeat(4097))), None);
        let pairs = format!("{}\u{1F3B2}", "x".repeat(4095));
        assert!(registry_text(REG_SZ, &wide(&pairs)).is_some(), "a surrogate pair is one character");
    }

    /// Each registry shape of `generate_helper_vectors.py`, written to a scratch current-user
    /// key with `reg import` exactly as the generator wrote it, then read through the real
    /// registry calls: the result must be Python's `WindowsRegistration.read`.
    #[cfg(windows)]
    #[test]
    fn the_real_registry_reads_like_python() {
        let vectors: Json =
            serde_json::from_str(include_str!("../../tests/vectors/helper_vectors.json")).unwrap();
        // A key of its own directly under Software, so deleting it leaves no parent behind.
        let key = format!(r"Software\sylvia-gm-port-test-registry-read-{}", std::process::id());
        struct Cleanup(String);
        impl Drop for Cleanup {
            fn drop(&mut self) {
                let _ = delete_scratch_key(&self.0);
            }
        }
        let _cleanup = Cleanup(key.clone());
        let entry = |name: &str, value: &Json| {
            let label = if name.is_empty() { "@".to_string() } else { Json::String(name.into()).to_string() };
            let data = value[1].as_str().unwrap();
            let bytes: Vec<&str> = (0..data.len()).step_by(2).map(|index| &data[index..index + 2]).collect();
            format!("{label}=hex({:x}):{}", value[0].as_u64().unwrap(), bytes.join(","))
        };
        for case in vectors["registry"].as_array().unwrap() {
            let _ =
                std::process::Command::new("reg").args(["delete", &format!(r"HKCU\{key}"), "/f"]).output();
            let mut lines = vec![
                "Windows Registry Editor Version 5.00".to_string(),
                String::new(),
                format!(r"[HKEY_CURRENT_USER\{key}\Uninstall]"),
            ];
            for (name, value) in case["uninstall"].as_object().unwrap() {
                lines.push(entry(name, value));
            }
            if !case["product"].is_null() {
                lines.extend([
                    String::new(),
                    format!(r"[HKEY_CURRENT_USER\{key}\Product]"),
                    entry("", &case["product"]),
                ]);
            }
            let text = lines.join("\r\n") + "\r\n";
            let file = std::env::temp_dir().join(format!("sylvia-registry-read-{}.reg", std::process::id()));
            let bytes: Vec<u8> =
                [0xFF, 0xFE].into_iter().chain(text.encode_utf16().flat_map(u16::to_le_bytes)).collect();
            std::fs::write(&file, bytes).unwrap();
            let imported = std::process::Command::new("reg").arg("import").arg(&file).output().unwrap();
            let _ = std::fs::remove_file(&file);
            assert!(imported.status.success(), "{}", String::from_utf8_lossy(&imported.stderr));
            let got =
                match read_windows_registration(&format!(r"{key}\Uninstall"), &format!(r"{key}\Product")) {
                    // The row in its own order (Python's dict order), each value as its UTF-16 units.
                    Ok(row) => serde_json::json!({"ok": serde_json::Value::Object(
                        row.iter().map(|(name, units)| (name.clone(), serde_json::json!(units))).collect()
                    )}),
                    Err(error) => serde_json::json!({"error": [error.code, error.message]}),
                };
            sylvia_base::testing::assert_same_json(&got, &case["result"], &case["name"]);
        }
        assert!(delete_scratch_key(&key), "the scratch key HKCU\\{key} is still there");
    }

    /// Delete a scratch current-user key in both registry views; `true` once it is gone in both.
    #[cfg(windows)]
    fn delete_scratch_key(subkey: &str) -> bool {
        let key = format!(r"HKCU\{subkey}");
        for view in ["/reg:64", "/reg:32"] {
            // A view where the key is already gone answers 1; the query below is the check.
            let _ = std::process::Command::new("reg").args(["delete", &key, "/f", view]).output();
        }
        ["/reg:64", "/reg:32"].iter().all(|view| {
            std::process::Command::new("reg")
                .args(["query", &key, view])
                .output()
                .is_ok_and(|output| !output.status.success())
        })
    }
}
