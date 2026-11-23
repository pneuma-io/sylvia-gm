//! `provider/_claude_install.py`: Sylvia's own copy of Claude Code, Anthropic's published binary
//! downloaded and checked. Lane 5.
//!
//! Sylvia never bundles, modifies or redistributes Claude Code. When asked, it downloads the
//! binary Anthropic publishes, checks it against Anthropic's release manifest, and keeps it under
//! Sylvia's runtime home. On Windows the binary must also carry Anthropic's code signature; on
//! Linux the manifest itself must carry Anthropic's release key's signature, which covers every
//! checksum it lists. Claude Code's own installer and updater are never run.
//!
//! What the installer needs from the system comes in an [`InstallSystem`]: the HTTP client
//! (`httpx.Client(timeout=httpx.Timeout(30.0, read=60.0), follow_redirects=False)`), free disk
//! space, this computer's build, and the two signature checks, which call `gpgv` and Windows
//! PowerShell as Python does. The owner gives it the real ones; tests give stand-ins.

use crate::error::{ErrorKind, ProviderError};
use crate::http::{HttpRequest, HttpTransport, TransportError};
use crate::runtime_process::{self, Captured, RunError};
use crate::types::TimeoutConfig;
use sha2::Digest;
use std::ffi::OsString;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use sylvia_base::{Json, JsonMap};

/// `RELEASES`.
pub const RELEASES: &str = "https://downloads.claude.ai/claude-code-releases";
/// `PUBLISHER`: the Windows code-signing name.
pub const PUBLISHER: &str = "Anthropic, PBC";
/// `SIGNING_KEY`: Anthropic's Claude Code release signing key, fetched each time.
pub const SIGNING_KEY: &str = "https://downloads.claude.ai/keys/claude-code.asc";
/// `SIGNING_FINGERPRINT`: the only primary key whose signature is accepted.
pub const SIGNING_FINGERPRINT: &str = "31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE";
/// `_UNSIGNED_MANIFEST`.
pub const UNSIGNED_MANIFEST: &str =
    "The Claude Code release list is not signed by Anthropic, so Sylvia discarded it.";
/// `MAX_BINARY_BYTES`.
pub const MAX_BINARY_BYTES: u64 = 2 * 1024 * 1024 * 1024;
/// `_UNREACHABLE`.
pub const UNREACHABLE: &str =
    "Claude Code could not be downloaded from Anthropic. Check your connection and try again.";
/// The installer client's timeouts: `httpx.Timeout(30.0, read=60.0)`.
pub const INSTALL_TIMEOUTS: TimeoutConfig =
    TimeoutConfig { connect: 30.0, read: 60.0, write: 30.0, pool: 30.0, stream_health_interval: 60.0 };
/// `iter_bytes(1024 * 1024)`.
const CHUNK: usize = 1024 * 1024;

/// `InstallState`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InstallState {
    Idle,
    Checking,
    Downloading,
    Verifying,
    Ready,
    Failed,
}

impl InstallState {
    /// The state's value.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Checking => "checking",
            Self::Downloading => "downloading",
            Self::Verifying => "verifying",
            Self::Ready => "ready",
            Self::Failed => "failed",
        }
    }

    /// `INSTALL_MOVES[self]`.
    pub fn moves(self) -> &'static [InstallState] {
        use InstallState::*;
        match self {
            Idle => &[Checking],
            Checking => &[Downloading, Ready, Failed, Idle],
            Downloading => &[Verifying, Failed, Idle],
            Verifying => &[Ready, Failed],
            Ready | Failed => &[Checking],
        }
    }

    /// `state in _BUSY`.
    pub fn busy(self) -> bool {
        matches!(self, Self::Checking | Self::Downloading | Self::Verifying)
    }
}

fn connection(message: &str) -> ProviderError {
    ProviderError::connection(message, None)
}

// ── The release ───────────────────────────────────────────────────────────────────────────

/// `_musl()`: a Linux whose C library is not glibc.
pub fn musl() -> bool {
    if cfg!(windows) {
        return false;
    }
    // `os.confstr("CS_GNU_LIBC_VERSION")` is empty, or unavailable, off glibc.
    !cfg!(all(target_os = "linux", target_env = "gnu"))
}

/// `release_platform()` for a given `platform.machine()`, operating system and C library.
pub fn release_platform_for(
    machine: &str,
    windows: bool,
    linux: bool,
    musl: bool,
) -> Result<String, ProviderError> {
    let machine = machine.to_lowercase();
    let arm = matches!(machine.as_str(), "arm64" | "aarch64");
    if windows {
        return Ok(if arm { "win32-arm64" } else { "win32-x64" }.into());
    }
    if linux && (arm || matches!(machine.as_str(), "x86_64" | "amd64")) {
        let base = if arm { "linux-arm64" } else { "linux-x64" };
        return Ok(format!("{base}{}", if musl { "-musl" } else { "" }));
    }
    Err(connection("Sylvia can set up Claude Code on Windows and Linux only in this version."))
}

/// `platform.machine()` for this build.
fn machine() -> &'static str {
    if cfg!(target_arch = "x86_64") {
        if cfg!(windows) {
            "AMD64"
        } else {
            "x86_64"
        }
    } else if cfg!(target_arch = "aarch64") {
        if cfg!(windows) {
            "ARM64"
        } else {
            "aarch64"
        }
    } else {
        std::env::consts::ARCH
    }
}

/// `release_platform()`: this computer's published build.
pub fn release_platform() -> Result<String, ProviderError> {
    release_platform_for(machine(), cfg!(windows), cfg!(target_os = "linux"), musl())
}

/// `_version(text)`: a plain release number (Unicode digits, read by value), else
/// `_UNREACHABLE`. Each part is its digits without leading zeros.
pub fn release_version(text: &str) -> Result<[String; 3], ProviderError> {
    let parts: Vec<&str> = sylvia_base::pystr::strip(text).split('.').collect();
    let digits = |part: &str| -> Option<String> {
        if part.is_empty() {
            return None;
        }
        let mut value = String::new();
        for c in part.chars() {
            value.push(char::from(b'0' + sylvia_base::pystr::decimal(c)? as u8));
        }
        let trimmed = value.trim_start_matches('0');
        Some(if trimmed.is_empty() { "0".into() } else { trimmed.into() })
    };
    match parts.as_slice() {
        [major, minor, patch] => match (digits(major), digits(minor), digits(patch)) {
            (Some(major), Some(minor), Some(patch)) => Ok([major, minor, patch]),
            _ => Err(connection(UNREACHABLE)),
        },
        _ => Err(connection(UNREACHABLE)),
    }
}

/// Python's comparison of two version tuples of integers.
fn version_order(a: &[String; 3], b: &[String; 3]) -> std::cmp::Ordering {
    a.iter()
        .zip(b)
        .map(|(x, y)| x.len().cmp(&y.len()).then_with(|| x.cmp(y)))
        .find(|order| order.is_ne())
        .unwrap_or(std::cmp::Ordering::Equal)
}

/// `_dearmor(armored)`: the key packets of one ASCII-armored OpenPGP public key block.
pub fn dearmor(armored: &[u8]) -> Result<Vec<u8>, String> {
    // `armored.decode("ascii").replace("\r", "").split("\n")`.
    let text = std::str::from_utf8(armored)
        .ok()
        .filter(|text| text.is_ascii())
        .ok_or("not ASCII")?
        .replace('\r', "");
    let lines: Vec<&str> = text.split('\n').collect();
    let begin =
        lines.iter().position(|line| *line == "-----BEGIN PGP PUBLIC KEY BLOCK-----").ok_or("no begin")?;
    let end = lines[begin..]
        .iter()
        .position(|line| *line == "-----END PGP PUBLIC KEY BLOCK-----")
        .ok_or("no end")?
        + begin;
    let mut body = &lines[begin + 1..end];
    // Armor headers end at the first blank line; the optional "=" line is a CRC24 checksum.
    if let Some(blank) = body.iter().position(|line| line.is_empty()) {
        body = &body[blank + 1..];
    }
    let data: String = body
        .iter()
        .filter(|line| !line.starts_with('='))
        .map(|line| sylvia_base::pystr::strip(line))
        .collect();
    sylvia_base::pybase64::b64decode_validate(&data)
}

/// `verify_manifest`'s verdict on `gpgv --status-fd 1`'s run.
pub fn gpgv_verdict(run: Result<Captured, RunError>) -> Result<(), ProviderError> {
    let run = run.map_err(|_| {
        connection("Sylvia could not check the signature on Claude Code's release list. Try again.")
    })?;
    // `[line.split() for line in stdout.splitlines() if line.startswith("[GNUPG:]")]`.
    let status: Vec<Vec<&str>> = sylvia_base::pystr::splitlines(&run.stdout)
        .into_iter()
        .filter(|line| line.starts_with("[GNUPG:]"))
        .map(|line| line.split(sylvia_base::pystr::is_space).filter(|field| !field.is_empty()).collect())
        .collect();
    let words: std::collections::BTreeSet<&str> =
        status.iter().filter_map(|fields| fields.get(1).copied()).collect();
    let primary: std::collections::BTreeSet<String> = status
        .iter()
        .filter(|fields| fields.len() > 2 && fields[1] == "VALIDSIG")
        .map(|fields| fields.get(11).unwrap_or(&fields[2]).to_uppercase())
        .collect();
    let refused = ["BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG", "NO_PUBKEY"];
    let expected: std::collections::BTreeSet<String> = [SIGNING_FINGERPRINT.to_owned()].into();
    if run.code != Some(0)
        || !words.contains("GOODSIG")
        || refused.iter().any(|word| words.contains(word))
        || primary != expected
    {
        return Err(connection(UNSIGNED_MANIFEST));
    }
    Ok(())
}

/// `verify_manifest(manifest, signature, key)`: a good signature by Anthropic's release key on
/// these exact bytes, checked by `gpgv` against a keyring holding only that key, in an empty
/// folder.
pub fn verify_manifest(manifest: &[u8], signature: &[u8], key: &[u8]) -> Result<(), ProviderError> {
    let path = std::env::var_os("PATH");
    let pathext = std::env::var("PATHEXT").ok();
    let Some(gpgv) = crate::codex_rpc::which("gpgv", path.as_deref(), pathext.as_deref()) else {
        return Err(connection(
            "Sylvia needs gpgv, part of GnuPG, to check Anthropic's signature on Claude Code. Install GnuPG, then try again.",
        ));
    };
    let keyring = dearmor(key).map_err(|_| connection(UNSIGNED_MANIFEST))?;
    let folder = std::env::temp_dir().join(format!("claude-release-check-{}", crate::gemini::uuid4()));
    let written = (|| -> std::io::Result<()> {
        std::fs::create_dir(&folder)?;
        std::fs::write(folder.join("release-key.gpg"), &keyring)?;
        std::fs::write(folder.join("manifest.json"), manifest)?;
        std::fs::write(folder.join("manifest.json.sig"), signature)
    })();
    let verdict = written.map_err(RunError::Start).and_then(|()| {
        let environment = vec![
            (OsString::from("PATH"), path.clone().unwrap_or_else(|| OsString::from("/usr/bin:/bin"))),
            (OsString::from("GNUPGHOME"), folder.clone().into_os_string()),
            (OsString::from("LC_ALL"), OsString::from("C")),
        ];
        let arguments: Vec<OsString> = vec![
            "--status-fd".into(),
            "1".into(),
            "--keyring".into(),
            folder.join("release-key.gpg").into_os_string(),
            folder.join("manifest.json.sig").into_os_string(),
            folder.join("manifest.json").into_os_string(),
        ];
        runtime_process::run_captured(
            &gpgv.into_os_string(),
            &arguments,
            &environment,
            Duration::from_secs(60),
        )
    });
    let _ = std::fs::remove_dir_all(&folder);
    gpgv_verdict(verdict)
}

/// `verify_publisher`'s verdict on PowerShell's report: `Valid`, then the signer's name.
pub fn publisher_verdict(run: Result<Captured, RunError>) -> Result<(), ProviderError> {
    let run = run.map_err(|_| {
        connection("Windows could not check the Claude Code download's signature. Try again.")
    })?;
    if run.code != Some(0) || run.stdout.split('\n').collect::<Vec<_>>() != ["Valid", PUBLISHER] {
        return Err(connection(
            "The Claude Code download is not signed by Anthropic, so Sylvia discarded it.",
        ));
    }
    Ok(())
}

/// The PowerShell script `verify_publisher` runs.
pub const PUBLISHER_SCRIPT: &str = "$s = Get-AuthenticodeSignature -LiteralPath $env:SYLVIA_SIGNED_FILE; \
$n = if ($s.SignerCertificate) { $s.SignerCertificate.GetNameInfo('SimpleName', $false) } else { '' }; \
[Console]::Out.Write(\"$($s.Status)`n$n\")";

/// `verify_publisher(path)`: a valid Windows code signature from Anthropic on the file.
pub fn verify_publisher(path: &Path) -> Result<(), ProviderError> {
    let root = std::env::var_os("SYSTEMROOT").unwrap_or_else(|| OsString::from("C:\\Windows"));
    let powershell =
        Path::new(&root).join("System32").join("WindowsPowerShell").join("v1.0").join("powershell.exe");
    let mut environment = runtime_process::process_environment();
    runtime_process::set_variable(&mut environment, "SYLVIA_SIGNED_FILE", path.as_os_str());
    let arguments: Vec<OsString> =
        vec!["-NoProfile".into(), "-NonInteractive".into(), "-Command".into(), PUBLISHER_SCRIPT.into()];
    publisher_verdict(runtime_process::run_captured(
        &powershell.into_os_string(),
        &arguments,
        &environment,
        Duration::from_secs(180),
    ))
}

// ── The installer ─────────────────────────────────────────────────────────────────────────

/// `shutil.disk_usage(path).free`.
pub type DiskFree = Arc<dyn Fn(&Path) -> std::io::Result<u64> + Send + Sync>;
/// `runtime_version(executable, home)`.
pub type VersionCheck = Arc<dyn Fn(&Path, &Path) -> Result<String, ProviderError> + Send + Sync>;
/// `verify_manifest(manifest, signature, key)`.
pub type ManifestCheck = Arc<dyn Fn(&[u8], &[u8], &[u8]) -> Result<(), ProviderError> + Send + Sync>;
/// `verify_publisher(path)`.
pub type PublisherCheck = Arc<dyn Fn(&Path) -> Result<(), ProviderError> + Send + Sync>;

/// What the installer needs from the system.
#[derive(Clone)]
pub struct InstallSystem {
    /// The download client, built with [`INSTALL_TIMEOUTS`] and no redirects.
    pub transport: Arc<dyn HttpTransport>,
    /// `shutil.disk_usage(path).free`.
    pub disk_free: DiskFree,
    /// `release_platform()`.
    pub release_platform: Arc<dyn Fn() -> Result<String, ProviderError> + Send + Sync>,
    /// The binary's name (`executable_name()`).
    pub executable_name: &'static str,
    pub runtime_version: VersionCheck,
    pub verify_manifest: ManifestCheck,
    pub verify_publisher: PublisherCheck,
}

impl InstallSystem {
    /// The real checks, over the given client and free-space reading.
    pub fn new(transport: Arc<dyn HttpTransport>, disk_free: DiskFree) -> Self {
        Self {
            transport,
            disk_free,
            release_platform: Arc::new(release_platform),
            executable_name: crate::claude_runtime::executable_name(),
            runtime_version: Arc::new(crate::claude_runtime::runtime_version),
            verify_manifest: Arc::new(verify_manifest),
            verify_publisher: Arc::new(verify_publisher),
        }
    }
}

#[derive(Debug, Clone)]
struct Progress {
    state: InstallState,
    version: Option<String>,
    received: u64,
    total: Option<u64>,
    message: Option<String>,
}

/// How an install ended without finishing.
enum Stop {
    /// `_CancelledError`.
    Cancelled,
    /// `ProviderConnectionError`, shown as it is.
    Connection(String),
    /// `httpx.HTTPError`, `OSError`, `ValueError`, `KeyError` or `TypeError`: `_UNREACHABLE`.
    Unreachable,
    /// Any other exception, which leaves the state as it was and escapes.
    Raw(String),
}

impl From<ProviderError> for Stop {
    fn from(error: ProviderError) -> Self {
        if error.is(ErrorKind::Connection) {
            Self::Connection(error.message)
        } else {
            Self::Raw(error.message)
        }
    }
}

impl From<std::io::Error> for Stop {
    fn from(_: std::io::Error) -> Self {
        Self::Unreachable
    }
}

impl From<TransportError> for Stop {
    fn from(_: TransportError) -> Self {
        Self::Unreachable
    }
}

struct Inner {
    home: PathBuf,
    system: InstallSystem,
    progress: Mutex<Progress>,
    cancel: AtomicBool,
}

/// `ClaudeInstaller`: one download at a time for a runtime home, with plain progress.
pub struct ClaudeInstaller {
    inner: Arc<Inner>,
}

impl Inner {
    /// `_move(state, message)`.
    fn move_to(&self, state: InstallState, message: Option<String>) -> Result<(), Stop> {
        let mut progress = self.progress.lock().expect("progress lock");
        if !progress.state.moves().contains(&state) {
            return Err(Stop::Raw(format!(
                "Claude Code setup cannot move from {} to {}",
                progress.state.as_str(),
                state.as_str()
            )));
        }
        progress.state = state;
        progress.message = message;
        Ok(())
    }

    fn begin(&self) -> Result<(), Stop> {
        self.cancel.store(false, Ordering::SeqCst);
        {
            let mut progress = self.progress.lock().expect("progress lock");
            progress.version = None;
            progress.received = 0;
            progress.total = None;
        }
        self.move_to(InstallState::Checking, None)
    }

    /// `_finish()`: the outcome of `_install()` as a state, then the partial downloads removed.
    fn finish(&self) -> Result<(), String> {
        let outcome = self.install();
        let moved = match outcome {
            Ok(()) => Ok(()),
            Err(Stop::Cancelled) => {
                self.move_to(InstallState::Idle, Some("Setup was cancelled. Nothing was changed.".into()))
            }
            Err(Stop::Connection(message)) => self.move_to(InstallState::Failed, Some(message)),
            Err(Stop::Unreachable) => self.move_to(InstallState::Failed, Some(UNREACHABLE.into())),
            Err(Stop::Raw(message)) => Err(Stop::Raw(message)),
        };
        if let Ok(entries) = std::fs::read_dir(self.home.join("install")) {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().into_owned();
                // `Path.glob("*.partial")`, whose `*` also matches a leading dot.
                if name.ends_with(".partial") {
                    let _ = std::fs::remove_dir_all(entry.path());
                }
            }
        }
        match moved {
            Err(Stop::Raw(message)) => Err(message),
            _ => Ok(()),
        }
    }

    fn get(&self, runtime: &tokio::runtime::Runtime, url: &str) -> Result<Vec<u8>, Stop> {
        runtime.block_on(async {
            let mut response = self.system.transport.send(request(url)).await?;
            // `raise_for_status()`.
            if !(200..300).contains(&response.status) {
                return Err(Stop::Unreachable);
            }
            Ok(response.read_all().await?)
        })
    }

    /// `_install()`: Anthropic's stable release, never older than the version Sylvia requires.
    fn install(&self) -> Result<(), Stop> {
        let build = (self.system.release_platform)()?;
        let executable = self.system.executable_name;
        let directory = self.home.join("install");
        std::fs::create_dir_all(&directory)?;
        let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build()?;
        let stable = self.get(&runtime, &format!("{RELEASES}/stable"))?;
        let parsed = release_version(&String::from_utf8_lossy(&stable))?;
        let minimum = crate::claude_runtime::MINIMUM_VERSION;
        let minimum = [minimum.0.to_string(), minimum.1.to_string(), minimum.2.to_string()];
        let chosen = if version_order(&parsed, &minimum).is_lt() { minimum } else { parsed };
        let version = chosen.join(".");
        self.progress.lock().expect("progress lock").version = Some(version.clone());
        let final_folder = directory.join(&version);
        if final_folder.join(executable).is_file() {
            if (self.system.runtime_version)(&final_folder.join(executable), &self.home)
                .is_ok_and(|found| found == version)
            {
                return self
                    .move_to(InstallState::Ready, Some("Sylvia's copy is already up to date.".into()));
            }
            std::fs::remove_dir_all(&final_folder)?;
        }
        let listing = self.get(&runtime, &format!("{RELEASES}/{version}/manifest.json"))?;
        if build.starts_with("linux-") {
            // The checksums below are only as good as the list they come from.
            let signature = self.get(&runtime, &format!("{RELEASES}/{version}/manifest.json.sig"))?;
            let key = self.get(&runtime, SIGNING_KEY)?;
            (self.system.verify_manifest)(&listing, &signature, &key)?;
        }
        let (checksum, size) = manifest_entry(&listing, &build, executable)?;
        let free = (self.system.disk_free)(&directory)?;
        if u128::from(free) < u128::from(size) + 64 * 1024 * 1024 {
            return Err(Stop::Connection(
                "There is not enough free disk space to install Claude Code.".into(),
            ));
        }
        let staging = directory.join(format!("{version}.partial"));
        let _ = std::fs::remove_dir_all(&staging);
        std::fs::create_dir(&staging)?;
        let download = staging.join(executable);
        self.progress.lock().expect("progress lock").total = Some(size);
        self.move_to(InstallState::Downloading, None)?;
        let mut digest = sha2::Sha256::new();
        let address = format!("{RELEASES}/{version}/{build}/{executable}");
        let received = runtime.block_on(async {
            let mut response = self.system.transport.send(request(&address)).await?;
            let mut file = std::fs::File::create(&download)?;
            if !(200..300).contains(&response.status) {
                return Err(Stop::Unreachable);
            }
            let mut received = 0u64;
            while let Some(piece) = response.body.next_piece().await {
                for chunk in piece?.chunks(CHUNK) {
                    if self.cancel.load(Ordering::SeqCst) {
                        return Err(Stop::Cancelled);
                    }
                    received += chunk.len() as u64;
                    if received > size {
                        return Err(Stop::Connection(UNREACHABLE.into()));
                    }
                    digest.update(chunk);
                    file.write_all(chunk)?;
                    self.progress.lock().expect("progress lock").received = received;
                }
            }
            Ok(received)
        })?;
        self.move_to(InstallState::Verifying, None)?;
        if received != size || hex::encode(digest.finalize()) != checksum {
            return Err(Stop::Connection(
                "The Claude Code download did not match Anthropic's published checksum, so Sylvia discarded it. Try again."
                    .into(),
            ));
        }
        if build.starts_with("win32-") {
            (self.system.verify_publisher)(&download)?;
        } else {
            // Off Windows the published file arrives without its executable bit.
            set_executable(&download)?;
        }
        std::fs::rename(&staging, &final_folder)?;
        if (self.system.runtime_version)(&final_folder.join(executable), &self.home)? != version {
            let _ = std::fs::remove_dir_all(&final_folder);
            return Err(Stop::Connection(
                "The Claude Code download did not report the expected version, so Sylvia discarded it."
                    .into(),
            ));
        }
        // A copy still running an unfinished turn stays until a later setup can remove it.
        for entry in std::fs::read_dir(&directory)?.flatten() {
            if entry.path() != final_folder {
                let _ = std::fs::remove_dir_all(entry.path());
            }
        }
        self.move_to(InstallState::Ready, Some(format!("Claude Code {version} is installed for Sylvia.")))
    }
}

fn set_executable(path: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755))
    }
    #[cfg(not(unix))]
    {
        // `os.chmod(path, 0o755)` on Windows only clears the read-only flag.
        let mut permissions = std::fs::metadata(path)?.permissions();
        #[allow(clippy::permissions_set_readonly_false)]
        permissions.set_readonly(false);
        std::fs::set_permissions(path, permissions)
    }
}

/// A GET as `httpx.Client` sends it: Sylvia's `User-Agent` for httpx's own (differences row 8).
fn request(url: &str) -> HttpRequest {
    HttpRequest {
        method: "GET".into(),
        url: url.into(),
        headers: vec![("User-Agent".into(), crate::openai_compat::sylvia_user_agent())],
        body: Vec::new(),
    }
}

/// `json.loads(listing.content)["platforms"][build]`, and its checks: the binary's name, a
/// SHA-256 hex checksum and a plain `int` size within the limit.
fn manifest_entry(listing: &[u8], build: &str, executable: &str) -> Result<(String, u64), Stop> {
    let manifest = sylvia_base::pyjson::loads_bytes(listing).map_err(|_| Stop::Unreachable)?;
    let entry = match manifest.get("platforms").and_then(|platforms| platforms.get(build)) {
        Some(Json::Object(entry)) => entry.clone(),
        _ => return Err(Stop::Unreachable),
    };
    let (Some(checksum), Some(size)) = (entry.get("checksum"), entry.get("size")) else {
        return Err(Stop::Unreachable);
    };
    let unreachable = || Stop::Connection(UNREACHABLE.into());
    if entry.get("binary").and_then(Json::as_str) != Some(executable) {
        return Err(unreachable());
    }
    let checksum = match checksum {
        Json::String(checksum)
            if checksum.len() == 64
                && checksum.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')) =>
        {
            checksum.clone()
        }
        _ => return Err(unreachable()),
    };
    let size = match size {
        Json::Number(number) if !number.is_f64() => {
            number.as_u64().filter(|size| (1..=MAX_BINARY_BYTES).contains(size))
        }
        _ => None,
    }
    .ok_or_else(unreachable)?;
    Ok((checksum, size))
}

impl ClaudeInstaller {
    /// `ClaudeInstaller(home)`.
    pub fn new(home: &Path, system: InstallSystem) -> Self {
        Self {
            inner: Arc::new(Inner {
                home: home.to_owned(),
                system,
                progress: Mutex::new(Progress {
                    state: InstallState::Idle,
                    version: None,
                    received: 0,
                    total: None,
                    message: None,
                }),
                cancel: AtomicBool::new(false),
            }),
        }
    }

    /// `progress()`: the state and byte counts; never a path, URL or raw error.
    pub fn progress(&self) -> JsonMap {
        let progress = self.inner.progress.lock().expect("progress lock").clone();
        let mut out = JsonMap::new();
        out.insert("state".into(), Json::String(progress.state.as_str().into()));
        out.insert("version".into(), progress.version.map_or(Json::Null, Json::String));
        out.insert("received".into(), Json::from(progress.received));
        out.insert("total".into(), progress.total.map_or(Json::Null, Json::from));
        out.insert("message".into(), progress.message.map_or(Json::Null, Json::String));
        out
    }

    /// `start()`: set up in the background unless a setup is already running.
    pub fn start(&self) -> Result<(), String> {
        if self.inner.progress.lock().expect("progress lock").state.busy() {
            return Ok(());
        }
        if let Err(Stop::Raw(message)) = self.inner.begin() {
            return Err(message);
        }
        let inner = Arc::clone(&self.inner);
        std::thread::Builder::new()
            .name("sylvia-claude-install".into())
            .spawn(move || {
                let _ = inner.finish();
            })
            .map_err(|error| error.to_string())?;
        Ok(())
    }

    /// `run()`: set up in the calling thread, for headless use. An exception Python lets
    /// escape is the `Err`.
    pub fn run(&self) -> Result<(), String> {
        if self.inner.progress.lock().expect("progress lock").state.busy() {
            return Ok(());
        }
        if let Err(Stop::Raw(message)) = self.inner.begin() {
            return Err(message);
        }
        self.inner.finish()
    }

    /// `cancel()`: stop a download in progress; an installed copy stays.
    pub fn cancel(&self) {
        self.inner.cancel.store(true, Ordering::SeqCst);
    }
}
