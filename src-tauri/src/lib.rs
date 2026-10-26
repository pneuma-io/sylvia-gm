use hmac::{Hmac, Mac};
use rand::{rngs::OsRng, RngCore};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

mod desktop_storage;
mod desktop_update;
mod desktop_update_process;
mod dictation;
mod instance_policy;
mod panel_drag;
mod panel_windows;
use panel_windows::PanelWindows;

const HANDSHAKE_TRANSCRIPT: &[u8] = b"sylvia-gm-f6-local-owner-handshake-v1";
const MAX_MESSAGE_BYTES: usize = 67_108_864;

type HmacSha256 = Hmac<Sha256>;

struct DesktopUpdateLaunch {
    portable: bool,
    startup: Option<Value>,
    data: PathBuf,
    live: PathBuf,
    applying: AtomicBool,
}

pub struct OwnerBridge {
    recovery_only: bool,
    child: Mutex<Child>,
    input: Mutex<ChildStdin>,
    pending: Arc<Mutex<HashMap<String, mpsc::Sender<Result<Value, String>>>>>,
    next_request_id: AtomicU64,
    closed: Arc<AtomicBool>,
}

impl OwnerBridge {
    pub fn spawn_python(python: &Path, database: &Path) -> Result<Self, String> {
        let mut command = Command::new(python);
        command.args(["-m", "sylvia_gm.interfaces.owner_process"]);
        Self::spawn_command(command, database, None, None)
    }

    pub fn spawn_packaged(executable: &Path, database: &Path) -> Result<Self, String> {
        Self::spawn_command(Command::new(executable), database, None, None)
    }

    fn spawn_default(database: &Path, portable: bool, startup: Option<Value>) -> Result<Self, String> {
        if startup.is_some() && (
            !cfg!(all(windows, target_arch = "x86_64"))
            || (cfg!(debug_assertions) && !portable)
            || std::env::var_os("SYLVIA_GM_OWNER_EXECUTABLE").is_some()
            || std::env::var_os("SYLVIA_GM_AUTHORITY_DATABASE").is_some()
        ) {
            return Err("Update restart is unavailable with this engineering launch".into());
        }
        if let Some(executable) = std::env::var_os("SYLVIA_GM_OWNER_EXECUTABLE") {
            return Self::spawn_packaged(Path::new(&executable), database);
        }

        if cfg!(debug_assertions) && !portable {
            let python = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .ok_or_else(|| "Sylvia GM repository root is unavailable".to_string())?
                .join(".venv")
                .join("Scripts")
                .join("python.exe");
            return Self::spawn_python(&python, database);
        }

        let executable = std::env::current_exe()
            .map_err(|_| "Sylvia GM executable path is unavailable".to_string())?
            .parent()
            .ok_or_else(|| "Sylvia GM executable directory is unavailable".to_string())?
            .join("sylvia-gm-owner.exe");
        let application = std::env::current_exe()
            .map_err(|_| "Sylvia GM executable path is unavailable".to_string())?;
        let context = if !cfg!(all(windows, target_arch = "x86_64"))
            || std::env::var_os("SYLVIA_GM_AUTHORITY_DATABASE").is_some()
        {
            None
        } else {
            Some(desktop_update_launch_context(&application, database, portable)?)
        };
        Self::spawn_command(Command::new(executable), database, context, startup)
    }

    fn spawn_command(
        mut command: Command,
        database: &Path,
        update_context: Option<Value>,
        update_start: Option<Value>,
    ) -> Result<Self, String> {
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000);
        }
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        let mut child = command
            .spawn()
            .map_err(|_| "Sylvia GM authority process could not be launched".to_string())?;
        let mut input = child
            .stdin
            .take()
            .ok_or_else(|| "Sylvia GM authority input pipe is unavailable".to_string())?;
        let output = child
            .stdout
            .take()
            .ok_or_else(|| "Sylvia GM authority output pipe is unavailable".to_string())?;
        let mut output = BufReader::new(output);

        let mut secret = [0_u8; 32];
        OsRng.fill_bytes(&mut secret);
        let mut secret_hex = hex::encode(secret);
        let owner_id = format!("owner-desktop-{}", &secret_hex[..16]);
        let mut launch = json!({
            "type": "launch",
            "secret_hex": secret_hex,
            "database_path": database.to_string_lossy(),
            "owner_id": owner_id,
        });
        if let Some(context) = update_context {
            launch["desktop_update_context"] = context;
        }
        if let Some(start) = update_start {
            launch["desktop_update_start"] = start;
        }
        Self::write_to(&mut input, &launch)?;
        let ready = Self::read_from(&mut output)?;

        let mut mac = HmacSha256::new_from_slice(&secret)
            .map_err(|_| "Sylvia GM authority handshake could not be prepared".to_string())?;
        mac.update(HANDSHAKE_TRANSCRIPT);
        let expected = hex::encode(mac.finalize().into_bytes());
        secret.fill(0);
        secret_hex.clear();

        let response = ready
            .get("response_hex")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if ready.get("type").and_then(Value::as_str) == Some("update_recovery_required") {
            // The owner rejects before opening data and closes normally. No forced shutdown.
            let _ = child.wait();
            return Err("Update recovery is required before opening saved data".into());
        }
        if ready.get("ok") != Some(&Value::Bool(true))
            || ready.get("type").and_then(Value::as_str) != Some("ready")
            || response != expected
        {
            let _ = child.kill();
            let _ = child.wait();
            return Err("Sylvia GM authority launch verification failed".to_string());
        }

        let pending: Arc<Mutex<HashMap<String, mpsc::Sender<Result<Value, String>>>>> =
            Arc::new(Mutex::new(HashMap::new()));
        let reader_pending = Arc::clone(&pending);
        let closed = Arc::new(AtomicBool::new(false));
        let reader_closed = Arc::clone(&closed);
        std::thread::Builder::new()
            .name("sylvia-gm-owner-replies".to_string())
            .spawn(move || loop {
                let reply = Self::read_from(&mut output);
                let request_id = reply
                    .as_ref()
                    .ok()
                    .and_then(|value| value.get("id"))
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                match (request_id, reply) {
                    (Some(request_id), result) => {
                        let sender = reader_pending
                            .lock()
                            .ok()
                            .and_then(|mut pending| pending.remove(&request_id));
                        if let Some(sender) = sender {
                            let _ = sender.send(result);
                        }
                    }
                    (_, Err(error)) => {
                        reader_closed.store(true, Ordering::Release);
                        if let Ok(mut pending) = reader_pending.lock() {
                            for (_, sender) in pending.drain() {
                                let _ = sender.send(Err(error.clone()));
                            }
                        }
                        return;
                    }
                    _ => {}
                }
            })
            .map_err(|_| "Sylvia GM authority response reader could not start".to_string())?;

        Ok(Self {
            recovery_only: ready.get("recovery_only") == Some(&Value::Bool(true)),
            child: Mutex::new(child),
            input: Mutex::new(input),
            pending,
            next_request_id: AtomicU64::new(1),
            closed,
        })
    }

    fn write_to(input: &mut ChildStdin, value: &Value) -> Result<(), String> {
        let encoded = serde_json::to_string(value)
            .map_err(|_| "Sylvia GM authority request could not be encoded".to_string())?;
        if encoded.len() > MAX_MESSAGE_BYTES {
            return Err("Sylvia GM authority request is too large".to_string());
        }
        input
            .write_all(encoded.as_bytes())
            .and_then(|_| input.write_all(b"\n"))
            .and_then(|_| input.flush())
            .map_err(|_| "Sylvia GM authority request pipe closed".to_string())
    }

    fn read_from(output: &mut BufReader<ChildStdout>) -> Result<Value, String> {
        let mut line = String::new();
        let read = output
            .read_line(&mut line)
            .map_err(|_| "Sylvia GM authority response could not be read".to_string())?;
        if read == 0 {
            return Err("Sylvia GM authority process exited".to_string());
        }
        if line.len() > MAX_MESSAGE_BYTES || !line.ends_with('\n') {
            return Err("Sylvia GM authority response exceeded its bounded protocol".to_string());
        }
        serde_json::from_str(&line)
            .map_err(|_| "Sylvia GM authority response was invalid".to_string())
    }

    pub fn request(&self, method: &str, params: Value) -> Result<Value, String> {
        if self.closed.load(Ordering::Acquire) {
            return Err("Sylvia GM authority process is closed".to_string());
        }
        if !params.is_object() {
            return Err("Sylvia GM authority parameters must be an object".to_string());
        }
        let request_id = format!(
            "desktop-{}",
            self.next_request_id.fetch_add(1, Ordering::Relaxed)
        );
        let (sender, receiver) = mpsc::channel();
        self.pending
            .lock()
            .map_err(|_| "Sylvia GM authority reply registry is unavailable".to_string())?
            .insert(request_id.clone(), sender);
        let write_result = self
            .input
            .lock()
            .map_err(|_| "Sylvia GM authority input pipe is unavailable".to_string())
            .and_then(|mut input| {
                Self::write_to(
                    &mut input,
                    &json!({
                        "id": request_id,
                        "method": method,
                        "params": params,
                    }),
                )
            });
        if let Err(error) = write_result {
            if let Ok(mut pending) = self.pending.lock() {
                pending.remove(&request_id);
            }
            return Err(error);
        }
        let reply = receiver
            .recv()
            .map_err(|_| "Sylvia GM authority response channel closed".to_string())??;
        if reply.get("id").and_then(Value::as_str) != Some(request_id.as_str()) {
            return Err("Sylvia GM authority response identity did not match".to_string());
        }
        if reply.get("ok") == Some(&Value::Bool(true)) {
            return Ok(reply.get("result").cloned().unwrap_or(Value::Null));
        }
        let code = reply
            .pointer("/error/code")
            .and_then(Value::as_str)
            .unwrap_or("command_rejected");
        let message = reply
            .pointer("/error/message")
            .and_then(Value::as_str)
            .unwrap_or("Authority command was rejected");
        let diagnostic_details = reply
            .pointer("/error/diagnostics")
            .and_then(Value::as_array)
            .map(|diagnostics| {
                diagnostics
                    .iter()
                    .filter_map(|diagnostic| {
                        let diagnostic_code = diagnostic.get("code")?.as_str()?;
                        let diagnostic_message = diagnostic.get("message")?.as_str()?;
                        let file = diagnostic.get("file").and_then(Value::as_str).unwrap_or("");
                        let path = diagnostic.get("path").and_then(Value::as_str).unwrap_or("");
                        Some(format!(
                            "{diagnostic_code} · {file}{path}: {diagnostic_message}"
                        ))
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        if diagnostic_details.is_empty() {
            Err(format!("{code}: {message}"))
        } else {
            Err(format!(
                "{code}: {message}\n\n{}",
                diagnostic_details.join("\n")
            ))
        }
    }

    pub fn shutdown(&self) -> Result<(), String> {
        if self.closed.load(Ordering::Acquire) {
            return Ok(());
        }
        let request_result = self.request("shutdown", json!({}));
        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            let status = self
                .child
                .lock()
                .map_err(|_| "Sylvia GM authority process handle is unavailable".to_string())?
                .try_wait();
            match status {
                Ok(Some(status)) => {
                    self.closed.store(true, Ordering::Release);
                    if status.success() && request_result.is_ok() {
                        return Ok(());
                    }
                    return Err("Sylvia GM authority process did not close cleanly".to_string());
                }
                Ok(None) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(20));
                }
                _ => {
                    if let Ok(mut child) = self.child.lock() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                    self.closed.store(true, Ordering::Release);
                    return Err("Sylvia GM authority process required forced shutdown".to_string());
                }
            }
        }
    }

    pub fn shutdown_for_update(&self, operation: &str) -> Result<(), String> {
        let reply = self.request("desktop_update_shutdown", json!({"operation_id": operation}))?;
        if reply.get("status").and_then(Value::as_str) != Some("closed") {
            return Err("The update owner did not admit a safe close".into());
        }
        let status = self.child.lock()
            .map_err(|_| "The owned authority handle is unavailable".to_string())?
            .wait()
            .map_err(|_| "The authority's normal exit could not be verified".to_string())?;
        self.closed.store(true, Ordering::Release);
        if !status.success() {
            return Err("The authority did not finish update shutdown normally".into());
        }
        Ok(())
    }

    fn shutdown_for_recovery(&self) -> Result<(), String> {
        if !self.recovery_only { return Err("This is not the recovery server".into()); }
        if self.closed.load(Ordering::Acquire) { return Ok(()); }
        let reply = self.request("shutdown", json!({}))?;
        if reply.get("status").and_then(Value::as_str) != Some("closed") {
            return Err("The recovery server did not admit a safe close".into());
        }
        let status = self.child.lock().map_err(|_| "The owned recovery handle is unavailable")?
            .wait().map_err(|_| "The recovery server exit could not be verified")?;
        self.closed.store(true, Ordering::Release);
        if !status.success() { return Err("The recovery server did not close normally".into()); }
        Ok(())
    }
}

fn desktop_update_start_arguments(arguments: &[std::ffi::OsString]) -> Result<Option<Value>, String> {
    let is_start = |part: &std::ffi::OsString| {
        part == "--sylvia-update-start" || part == "--sylvia-update-recovery-start"
    };
    if !arguments.iter().any(is_start) {
        return Ok(None);
    }
    if arguments.len() != 4 || !is_start(&arguments[0]) {
        return Err("The update restart arguments are incomplete".into());
    }
    let mut ids = Vec::new();
    for part in &arguments[1..] {
        let value = part.to_str().ok_or("The update restart identity is invalid")?;
        if value.len() != 32 || !value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
            return Err("The update restart identity is invalid".into());
        }
        ids.push(value);
    }
    let mut request = json!({"operation_id": ids[0], "attempt": ids[1], "nonce": ids[2]});
    if arguments[0] == "--sylvia-update-recovery-start" { request["recovery"] = Value::Bool(true); }
    Ok(Some(request))
}

// Captured on the private launch pipe, never from renderer paths or per-request retargeting.
fn desktop_update_launch_context(
    application: &Path,
    database: &Path,
    portable: bool,
) -> Result<Value, String> {
    let app_root = application.parent()
        .ok_or_else(|| "Sylvia GM application folder is unavailable".to_string())?;
    let data_root = database.parent()
        .ok_or_else(|| "Sylvia GM data folder is unavailable".to_string())?;
    if !app_root.is_absolute() || !data_root.is_absolute() {
        return Err("Sylvia GM update folders must be absolute".to_string());
    }
    Ok(json!({
        "live": app_root,
        "data_root": data_root,
        "version": env!("CARGO_PKG_VERSION"),
        "target": format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
        "distribution": if portable { "portable" } else { "installed" },
    }))
}

impl Drop for OwnerBridge {
    fn drop(&mut self) {
        if !self.closed.load(Ordering::Acquire) {
            let _ = if self.recovery_only { self.shutdown_for_recovery() } else { self.shutdown() };
        }
    }
}

fn trusted_asset_url(url: &tauri::Url) -> bool {
    let host = url.host_str().unwrap_or_default();
    let release_asset_origin = match url.scheme() {
        "http" | "https" => host == "tauri.localhost" && url.port().is_none(),
        "tauri" => matches!(host, "localhost" | "tauri.localhost") && url.port().is_none(),
        _ => false,
    };
    let debug_asset_origin = cfg!(debug_assertions)
        && url.scheme() == "http"
        && host == "127.0.0.1"
        && url.port() == Some(1430);
    (release_asset_origin || debug_asset_origin)
        && url.username().is_empty()
        && url.password().is_none()
        && matches!(url.path(), "/" | "/app.html" | "/audit.html")
        && url.query().is_none()
        && url.fragment().is_none()
}

fn trusted_caller(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("Sylvia GM command caller is not trusted".to_string());
    }
    let url = window
        .url()
        .map_err(|_| "Sylvia GM command caller URL is unavailable".to_string())?;
    if !trusted_asset_url(&url) {
        return Err("Sylvia GM command caller is not trusted".to_string());
    }
    Ok(())
}

fn trusted_recovery_asset_url(url: &tauri::Url) -> bool {
    if url.path() != "/update-recovery.html" { return false; }
    let mut ordinary = url.clone();
    ordinary.set_path("/app.html");
    trusted_asset_url(&ordinary) // Same exact origin/port/query/fragment restrictions.
}

fn trusted_recovery_caller(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != "main" || !window.state::<OwnerBridge>().recovery_only {
        return Err("Recovery is available only from the main recovery window".into());
    }
    let url = window.url().map_err(|_| "The recovery caller URL is unavailable")?;
    if !trusted_recovery_asset_url(&url) { return Err("The recovery caller is not trusted".into()); }
    Ok(())
}

fn trusted_audit_reader(window: &WebviewWindow) -> Result<(), String> {
    if window.label() == "main" {
        return trusted_caller(window);
    }
    if window.label() != "admin-audit" {
        return Err("Sylvia GM audit caller is not trusted".to_string());
    }
    let url = window
        .url()
        .map_err(|_| "Sylvia GM audit caller URL is unavailable".to_string())?;
    if !trusted_asset_url(&url) || url.path() != "/audit.html" {
        return Err("Sylvia GM audit caller is not trusted".to_string());
    }
    Ok(())
}

#[cfg(test)]
mod trusted_caller_tests {
    use super::{application_info_projection, trusted_asset_url};

    #[test]
    fn accepts_exact_debug_and_release_asset_urls() {
        assert!(trusted_asset_url(
            &tauri::Url::parse("http://127.0.0.1:1430/app.html").unwrap()
        ));
        assert!(trusted_asset_url(
            &tauri::Url::parse("http://tauri.localhost/app.html").unwrap()
        ));
        assert!(trusted_asset_url(
            &tauri::Url::parse("tauri://localhost/app.html").unwrap()
        ));
        assert!(trusted_asset_url(
            &tauri::Url::parse("tauri://localhost/audit.html").unwrap()
        ));
    }

    #[test]
    fn rejects_nearby_untrusted_urls() {
        for candidate in [
            "http://127.0.0.1:1431/app.html",
            "http://localhost:1430/app.html",
            "http://127.0.0.1:1430/other.html",
            "http://127.0.0.1:1430/app.html?command=1",
            "https://example.com/app.html",
            "http://tauri.localhost:8080/app.html",
            "http://user@tauri.localhost/app.html",
            "http://user:pass@127.0.0.1:1430/app.html",
            "tauri://localhost:1234/audit.html",
        ] {
            assert!(!trusted_asset_url(&tauri::Url::parse(candidate).unwrap()));
        }
    }

    #[test]
    fn application_info_uses_the_exact_build_version_and_discloses_unsigned_alpha() {
        assert_eq!(
            application_info_projection(),
            serde_json::json!({
                "product_name": "Sylvia GM",
                "version": env!("CARGO_PKG_VERSION"),
                "release_channel": "alpha",
                "signing_status": "unsigned",
            })
        );
    }

    #[test]
    fn application_info_channel_handles_stable_prerelease_and_build_metadata() {
        for (version, expected) in [
            ("1.0.0", "stable"),
            ("1.0.0+build-with-dashes", "stable"),
            ("1.0.0-alpha.2", "alpha"),
            ("1.0.0-beta.3+build", "beta"),
        ] {
            assert_eq!(super::build_release_channel(version), expected);
        }
    }

    #[test]
    fn update_launch_context_binds_native_copy_and_distribution_without_filesystem_writes() {
        let application = std::env::temp_dir().join("sylvia-fixture/sylvia-gm.exe");
        let database = std::env::temp_dir().join("sylvia-fixture/isolated-data/authority.sqlite3");
        let context = super::desktop_update_launch_context(&application, &database, true).unwrap();
        assert_eq!(context["live"], serde_json::json!(application.parent().unwrap()));
        assert_eq!(context["data_root"], serde_json::json!(database.parent().unwrap()));
        assert_eq!(context["version"], env!("CARGO_PKG_VERSION"));
        assert_eq!(context["distribution"], "portable");
        assert_eq!(context.as_object().unwrap().len(), 5);
        assert!(super::desktop_update_launch_context(
            std::path::Path::new("relative.exe"), &database, false,
        ).is_err());
    }

    #[test]
    fn update_recovery_asset_does_not_expand_the_ordinary_caller_guard() {
        for origin in ["http://tauri.localhost", "tauri://localhost"] {
            let recovery = tauri::Url::parse(&format!("{origin}/update-recovery.html")).unwrap();
            assert!(super::trusted_recovery_asset_url(&recovery));
            assert!(!super::trusted_asset_url(&recovery));
            let ordinary = tauri::Url::parse(&format!("{origin}/app.html")).unwrap();
            assert!(super::trusted_asset_url(&ordinary));
            assert!(!super::trusted_recovery_asset_url(&ordinary));
        }
        for url in [
            "https://example.com/update-recovery.html", "http://tauri.localhost:123/update-recovery.html",
            "http://user@tauri.localhost/update-recovery.html", "tauri://localhost/update-recovery.html?x=1",
            "tauri://localhost/update-recovery.html#command", "tauri://localhost/update-recovery.html/",
        ] {
            assert!(!super::trusted_recovery_asset_url(&tauri::Url::parse(url).unwrap()));
        }
    }

    #[test]
    fn update_start_arguments_bind_only_exact_bounded_normal_or_recovery_restarts() {
        use std::ffi::OsString;
        for flag in ["--sylvia-update-start", "--sylvia-update-recovery-start"] {
            let args: Vec<OsString> = [flag.to_string(), "a".repeat(32), "b".repeat(32), "c".repeat(32)]
                .into_iter().map(Into::into).collect();
            let parsed = super::desktop_update_start_arguments(&args).unwrap().unwrap();
            assert_eq!(parsed["nonce"], "c".repeat(32));
            assert_eq!(parsed.get("recovery").is_some(), flag.ends_with("recovery-start"));
            for bad in ["../other", "D", "", "c?command"] {
                let mut invalid = args.clone();
                invalid[3] = bad.into();
                assert!(super::desktop_update_start_arguments(&invalid).is_err());
            }
            assert!(super::desktop_update_start_arguments(&args[..3]).is_err());
        }
    }
}

async fn owner_call_async(
    window: WebviewWindow,
    method: &'static str,
    params: Value,
) -> Result<Value, String> {
    trusted_caller(&window)?;
    owner_request_async(window, method, params).await
}

// Caller checks stay at each command; backend waits never belong on the desktop thread.
async fn owner_request_async(
    window: WebviewWindow,
    method: &'static str,
    params: Value,
) -> Result<Value, String> {
    let app = window.app_handle().clone();
    tauri::async_runtime::spawn_blocking(move || app.state::<OwnerBridge>().request(method, params))
        .await
        .map_err(|_| "Sylvia GM authority worker could not complete".to_string())?
}

fn build_release_channel(version: &str) -> &str {
    let precedence = version.split('+').next().unwrap_or(version);
    precedence.split_once('-')
        .and_then(|(_, prerelease)| prerelease.split('.').next())
        .unwrap_or("stable")
}

fn application_info_projection() -> Value {
    json!({
        "product_name": "Sylvia GM",
        "version": env!("CARGO_PKG_VERSION"),
        "release_channel": build_release_channel(env!("CARGO_PKG_VERSION")),
        "signing_status": "unsigned",
    })
}

#[tauri::command]
fn application_info(window: WebviewWindow) -> Result<Value, String> {
    trusted_caller(&window)?;
    Ok(application_info_projection())
}

// Only the native host supplies version, architecture and distribution. The renderer
// can select an operation ID but cannot select an artifact, URL, key, path or command.
async fn desktop_update_call(
    window: WebviewWindow,
    action: &'static str,
    operation_id: Option<String>,
) -> Result<Value, String> {
    trusted_caller(&window)?;
    if window.label() != "main" {
        return Err("Application updates are available from the main Settings window".into());
    }
    let portable = window.state::<DesktopUpdateLaunch>().portable;
    let mut params = json!({ "host": {
        "version": env!("CARGO_PKG_VERSION"),
        "target": format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
        "distribution": if portable { "portable" } else { "installed" },
    }});
    if let Some(identity) = operation_id {
        params["operation_id"] = Value::String(identity);
    }
    owner_request_async(window, action, params).await
}

#[tauri::command]
async fn desktop_update_state(window: WebviewWindow) -> Result<Value, String> {
    desktop_update_call(window, "desktop_update_state", None).await
}

#[tauri::command]
async fn desktop_update_check(window: WebviewWindow) -> Result<Value, String> {
    desktop_update_call(window, "desktop_update_check", None).await
}

#[tauri::command]
async fn desktop_update_download(window: WebviewWindow, operation_id: String) -> Result<Value, String> {
    desktop_update_call(window, "desktop_update_download", Some(operation_id)).await
}

#[tauri::command]
async fn desktop_update_cancel(window: WebviewWindow, operation_id: String) -> Result<Value, String> {
    desktop_update_call(window, "desktop_update_cancel", Some(operation_id)).await
}

#[tauri::command]
async fn desktop_update_defer(window: WebviewWindow, operation_id: String) -> Result<Value, String> {
    desktop_update_call(window, "desktop_update_defer", Some(operation_id)).await
}

#[tauri::command]
fn desktop_update_startup_state(window: WebviewWindow) -> Result<Value, String> {
    trusted_caller(&window)?;
    if window.label() != "main" {
        return Err("Only the main window confirms application startup".into());
    }
    let launch = window.state::<DesktopUpdateLaunch>();
    Ok(json!({"pending": launch.startup.is_some(),
              "operation_id": launch.startup.as_ref().and_then(|start| start.get("operation_id"))}))
}

#[tauri::command]
async fn desktop_update_confirm_startup(window: WebviewWindow) -> Result<Value, String> {
    trusted_caller(&window)?;
    if window.label() != "main" {
        return Err("Only the main window confirms application startup".into());
    }
    if window.state::<DesktopUpdateLaunch>().startup.is_none() {
        return Ok(json!({"status": "ordinary_startup"}));
    }
    desktop_update_call(window, "desktop_update_confirm_startup", None).await
}

fn desktop_update_host(portable: bool) -> Value {
    json!({"version": env!("CARGO_PKG_VERSION"),
           "target": format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
           "distribution": if portable { "portable" } else { "installed" }})
}

fn install_desktop_update(app: &tauri::AppHandle, identity: &str) -> Result<Value, String> {
    if !desktop_update::valid_id(identity) { return Err("Invalid update operation".into()); }
    let launch = app.state::<DesktopUpdateLaunch>();
    let bridge = app.state::<OwnerBridge>();
    let host = desktop_update_host(launch.portable);
    let state = bridge.request("desktop_update_state", json!({"host": host}))?;
    // This remains false until the complete installed/portable lifecycle is qualified.
    // A renderer cannot enable apply by invoking the native command directly.
    if state.get("can_apply") != Some(&Value::Bool(true))
        || state.get("operation_id").and_then(Value::as_str) != Some(identity)
    {
        return Err("Application update installation is unavailable in this build".into());
    }
    if launch.applying.compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire).is_err() {
        return Err("Another update restart is already preparing".into());
    }
    let mut reserved = false;
    let result = (|| {
        bridge.request("desktop_update_reserve", json!({"operation_id": identity}))?;
        reserved = true;
        app.state::<dictation::Dictation>().park_for_update()?;
        bridge.request("desktop_update_prepare", json!({"host": host, "operation_id": identity}))?;
        loop {
            let status = bridge.request("desktop_update_state", json!({"host": host}))?;
            if status.get("operation_id").and_then(Value::as_str) != Some(identity) {
                return Err("The update selection changed during preparation".into());
            }
            if status.get("busy") != Some(&Value::Bool(true)) {
                if status.get("phase").and_then(Value::as_str) != Some("prepared") {
                    return Err("Update preparation failed; the current application is retained".into());
                }
                break;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let metadata = bridge.request("desktop_update_handoff", json!({"host": host, "operation_id": identity}))?;
        let spec = desktop_update::HelperSpec::from_prepared(&launch.data, &launch.live, launch.portable, &metadata)?;
        if spec.operation != identity { return Err("The prepared update operation changed".into()); }
        let helper = {
            let owner = bridge.child.lock().map_err(|_| "The owned authority handle is unavailable")?;
            desktop_update::Handoff::spawn(spec, &owner)?
        };
        // The helper is ready but cannot apply until this explicit commit. A rejected
        // shutdown closes its input by Drop, leaving application files untouched.
        bridge.shutdown_for_update(identity)?;
        let _committed_helper = helper.commit()?;
        app.exit(0); // All webviews belong to this desktop; the helper waits for its exact handle.
        Ok(json!({"status": "closing"}))
    })();
    if result.is_err() {
        launch.applying.store(false, Ordering::Release);
        if bridge.closed.load(Ordering::Acquire) {
            // Lost pipe contact is not proof of a clean process exit. Keep editing
            // frozen; the helper's retained handles still govern any replacement.
            return Ok(json!({"status": "owner_unavailable"}));
        }
        if reserved {
            let _ = bridge.request("desktop_update_abort", json!({"operation_id": identity}));
        }
        app.state::<dictation::Dictation>().resume_after_update_abort();
    }
    result
}

#[tauri::command]
async fn desktop_update_install(window: WebviewWindow, operation_id: String) -> Result<Value, String> {
    trusted_caller(&window)?;
    if window.label() != "main" {
        return Err("Application updates are available from the main Settings window".into());
    }
    let app = window.app_handle().clone();
    tauri::async_runtime::spawn_blocking(move || install_desktop_update(&app, &operation_id))
        .await.map_err(|_| "The native update worker could not complete".to_string())?
}

#[tauri::command]
async fn desktop_update_recovery_state(window: WebviewWindow) -> Result<Value, String> {
    trusted_recovery_caller(&window)?;
    let host = desktop_update_host(window.state::<DesktopUpdateLaunch>().portable);
    owner_request_async(window, "desktop_update_recovery_state", json!({"host": host})).await
}

fn recover_desktop_update(
    app: &tauri::AppHandle, identity: &str, choice: &str, restart_nonce: Option<&str>,
) -> Result<Value, String> {
    if !desktop_update::valid_id(identity) || !["keep", "restore"].contains(&choice)
        || restart_nonce.is_some_and(|nonce| !desktop_update::valid_id(nonce)) {
        return Err("The recovery choice is invalid".into());
    }
    let bridge = app.state::<OwnerBridge>();
    if !bridge.recovery_only { return Err("This window is not in update recovery".into()); }
    let launch = app.state::<DesktopUpdateLaunch>();
    let host = desktop_update_host(launch.portable);
    let status = bridge.request("desktop_update_recovery_state", json!({"host": host}))?;
    if status.get("can_recover") != Some(&Value::Bool(true))
        || status.get("operation_id").and_then(Value::as_str) != Some(identity)
        || (choice == "keep" && status.get("can_keep_data") != Some(&Value::Bool(true)))
        || status.get("restart_nonce").and_then(Value::as_str) != restart_nonce
    {
        return Err("Automatic recovery is unavailable for this selection".into());
    }
    if launch.applying.compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire).is_err() {
        return Err("Another recovery is already preparing".into());
    }
    let result = (|| {
        let metadata = bridge.request("desktop_update_recovery_handoff", json!({
            "host": host, "operation_id": identity, "choice": choice, "restart_nonce": restart_nonce,
        }))?;
        let spec = desktop_update::HelperSpec::from_recovery(
            &launch.data, &launch.live, launch.portable, &metadata,
        )?;
        if spec.operation != identity { return Err("The recovery selection changed".into()); }
        let helper = {
            let owner = bridge.child.lock().map_err(|_| "The owned recovery handle is unavailable")?;
            desktop_update::Handoff::spawn_recovery(spec, &owner, choice)?
        };
        bridge.shutdown_for_recovery()?;
        let _committed_helper = helper.commit()?;
        app.exit(0);
        Ok(json!({"status": "closing"}))
    })();
    if result.is_err() {
        launch.applying.store(false, Ordering::Release);
        if bridge.closed.load(Ordering::Acquire) { return Ok(json!({"status": "owner_unavailable"})); }
    }
    result
}

#[tauri::command]
async fn desktop_update_recover(
    window: WebviewWindow, operation_id: String, choice: String, restart_nonce: Option<String>,
) -> Result<Value, String> {
    trusted_recovery_caller(&window)?;
    let app = window.app_handle().clone();
    tauri::async_runtime::spawn_blocking(move || recover_desktop_update(
        &app, &operation_id, &choice, restart_nonce.as_deref(),
    ))
        .await.map_err(|_| "The native recovery worker could not complete".to_string())?
}

// Only the main composer persists drafts. Detached panel windows cannot replace its snapshot.
async fn desktop_drafts_call(window: WebviewWindow, method: &'static str, params: Value) -> Result<Value, String> {
    trusted_caller(&window)?;
    if window.label() != "main" {
        return Err("Unsent drafts belong to the main window".into());
    }
    owner_request_async(window, method, params).await
}

#[tauri::command]
async fn load_desktop_drafts(window: WebviewWindow) -> Result<Value, String> {
    desktop_drafts_call(window, "load_desktop_drafts", json!({})).await
}

#[tauri::command]
async fn save_desktop_drafts(window: WebviewWindow, drafts: Value) -> Result<Value, String> {
    desktop_drafts_call(window, "save_desktop_drafts", json!({"drafts": drafts})).await
}

#[tauri::command]
async fn dictation_capabilities(window: WebviewWindow, id: Option<String>) -> Result<Value, String> {
    trusted_caller(&window)?;
    let app = window.app_handle().clone();
    tauri::async_runtime::spawn_blocking(move || app.state::<dictation::Dictation>().capabilities_for(id.as_deref()))
        .await
        .map_err(|_| "unavailable".to_string())?
}

#[tauri::command]
async fn dictation_start(window: WebviewWindow, id: String, engine: String, device: Option<String>) -> Result<(), String> {
    trusted_caller(&window)?;
    let app = window.app_handle().clone();
    tauri::async_runtime::spawn_blocking(move || {
        app.state::<dictation::Dictation>().start(&id, &engine, device.as_deref().unwrap_or("default"))
    })
    .await
    .map_err(|_| "unavailable".to_string())?
}

#[tauri::command]
async fn dictation_prepare(window: WebviewWindow, id: String) -> Result<Value, String> {
    trusted_caller(&window)?;
    let app = window.app_handle().clone();
    tauri::async_runtime::spawn_blocking(move || app.state::<dictation::Dictation>().prepare(&id))
        .await.map_err(|_| "download".to_string())?
}

#[tauri::command]
async fn dictation_stop(window: WebviewWindow, id: String) -> Result<String, String> {
    trusted_caller(&window)?;
    let app = window.app_handle().clone();
    tauri::async_runtime::spawn_blocking(move || app.state::<dictation::Dictation>().stop(&id))
        .await
        .map_err(|_| "provider".to_string())?
}

#[tauri::command]
async fn dictation_cancel(window: WebviewWindow, id: String) -> Result<(), String> {
    trusted_caller(&window)?;
    let app = window.app_handle().clone();
    tauri::async_runtime::spawn_blocking(move || app.state::<dictation::Dictation>().cancel(&id))
        .await
        .map_err(|_| "provider".to_string())
}

#[tauri::command]
async fn campaign_index(window: WebviewWindow) -> Result<Value, String> {
    owner_call_async(window.clone(), "campaign_index", json!({})).await
}

#[tauri::command]
async fn campaign_creation_catalog(
    window: WebviewWindow,
    include_participants: Option<bool>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "campaign_creation_catalog",
        json!({ "include_participants": include_participants.unwrap_or(true) }),
    )
    .await
}

#[tauri::command]
async fn campaign_sequel_context(window: WebviewWindow, request: Value) -> Result<Value, String> {
    owner_call_async(window, "campaign_sequel_context", request).await
}

#[tauri::command]
async fn preview_campaign_sequel(window: WebviewWindow, request: Value) -> Result<Value, String> {
    owner_call_async(window, "preview_campaign_sequel", request).await
}

#[tauri::command]
async fn append_campaign_sequel(window: WebviewWindow, request: Value) -> Result<Value, String> {
    owner_call_async(window, "append_campaign_sequel", request).await
}

#[tauri::command]
async fn delete_reusable_library_entry(
    window: WebviewWindow,
    kind: String,
    artifact_id: String,
    expected_content_sha256: String,
    confirmation: String,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "delete_reusable_library_entry",
        json!({
            "kind": kind,
            "artifact_id": artifact_id,
            "expected_content_sha256": expected_content_sha256,
            "confirmation": confirmation,
        }),
    )
    .await
}

#[tauri::command]
async fn builder_chat_index(window: WebviewWindow) -> Result<Value, String> {
    owner_call_async(window.clone(), "builder_chat_index", json!({})).await
}

#[tauri::command]
async fn create_builder_chat(
    window: WebviewWindow,
    operation_id: String,
    provider_id: Option<String>,
    model_id: Option<String>,
    display_name: Option<String>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "create_builder_chat",
        json!({
            "operation_id": operation_id,
            "provider_id": provider_id,
            "model_id": model_id,
            "display_name": display_name,
        }),
    )
    .await
}

#[tauri::command]
async fn rename_builder_chat(
    window: WebviewWindow,
    session_id: String,
    display_name: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "rename_builder_chat",
        json!({"session_id": session_id, "display_name": display_name}),
    )
    .await
}

#[tauri::command]
async fn archive_builder_chat(window: WebviewWindow, session_id: String) -> Result<Value, String> {
    owner_call_async(
        window,
        "archive_builder_chat",
        json!({"session_id": session_id}),
    )
    .await
}

#[tauri::command]
async fn restore_builder_chat(window: WebviewWindow, session_id: String) -> Result<Value, String> {
    owner_call_async(
        window,
        "restore_builder_chat",
        json!({"session_id": session_id}),
    )
    .await
}

#[tauri::command]
async fn delete_builder_chat(
    window: WebviewWindow,
    session_id: String,
    confirmation: String,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "delete_builder_chat",
        json!({"session_id": session_id, "confirmation": confirmation}),
    )
    .await
}

#[tauri::command]
async fn display_snapshot(
    window: WebviewWindow,
    campaign_id: String,
    load_id: Option<String>,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "display_snapshot",
        json!({"campaign_id": campaign_id, "load_id": load_id}),
    )
    .await
}

#[tauri::command]
async fn snapshot(window: WebviewWindow, campaign_id: String) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "snapshot",
        json!({"campaign_id": campaign_id}),
    )
    .await
}

#[tauri::command]
async fn player_projection_page(
    window: WebviewWindow,
    campaign_id: String,
    after_sequence: Option<u64>,
    load_id: Option<String>,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "player_projection_page",
        json!({"campaign_id": campaign_id, "after_sequence": after_sequence, "load_id": load_id}),
    )
    .await
}

#[tauri::command]
async fn player_projection(
    window: WebviewWindow,
    campaign_id: String,
    load_id: Option<String>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "player_projection",
        json!({"campaign_id": campaign_id, "load_id": load_id}),
    )
    .await
}

#[tauri::command]
async fn campaign_panels(
    window: WebviewWindow,
    campaign_id: String,
    after_revision: Option<u64>,
    load_id: Option<String>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "campaign_panels",
        json!({"campaign_id": campaign_id, "after_revision": after_revision, "load_id": load_id}),
    )
    .await
}

#[tauri::command]
async fn cancel_display_load(window: WebviewWindow, load_id: String) -> Result<Value, String> {
    owner_call_async(window, "cancel_display_load", json!({"load_id": load_id})).await
}

#[tauri::command]
async fn action_sequence(
    window: WebviewWindow,
    session_id: String,
    sequence_id: Option<String>,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "action_sequence",
        json!({"session_id":session_id,"sequence_id":sequence_id}),
    )
    .await
}

#[tauri::command]
async fn compose_action_sequence(window: WebviewWindow, command: Value) -> Result<Value, String> {
    owner_call_async(window, "compose_action_sequence", command).await
}

#[tauri::command]
async fn execute_action_sequence(window: WebviewWindow, command: Value) -> Result<Value, String> {
    owner_call_async(window, "execute_action_sequence", command).await
}

#[tauri::command]
async fn player_action_availability(
    window: WebviewWindow,
    campaign_id: String,
    expected_revision: u64,
    requests: Value,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "player_action_availability",
        json!({"campaign_id": campaign_id, "expected_revision": expected_revision,
            "requests": requests}),
    )
    .await
}

#[tauri::command]
async fn execute_panel_action(
    window: WebviewWindow,
    campaign_id: String,
    surface_id: String,
    panel_action_id: String,
    operation_id: String,
    expected_revision: u64,
    selection: Option<Value>,
    session_id: Option<String>,
    confirmation: Option<String>,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "execute_panel_action",
        json!({
            "campaign_id": campaign_id,
            "surface_id": surface_id,
            "panel_action_id": panel_action_id,
            "operation_id": operation_id,
            "expected_revision": expected_revision,
            "selection": selection,
            "session_id": session_id,
            "confirmation": confirmation,
        }),
    )
    .await
}

#[tauri::command]
async fn panel_action_receipt(
    window: WebviewWindow,
    campaign_id: String,
    surface_id: String,
    panel_action_id: String,
    operation_id: String,
    expected_revision: u64,
    selection: Option<Value>,
    session_id: Option<String>,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "panel_action_receipt",
        json!({
            "campaign_id": campaign_id,
            "surface_id": surface_id,
            "panel_action_id": panel_action_id,
            "operation_id": operation_id,
            "expected_revision": expected_revision,
            "selection": selection,
            "session_id": session_id,
        }),
    )
    .await
}

#[tauri::command]
async fn preview_panel_action(
    window: WebviewWindow,
    campaign_id: String,
    surface_id: String,
    panel_action_id: String,
    operation_id: String,
    expected_revision: u64,
    selection: Option<Value>,
    session_id: Option<String>,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "preview_panel_action",
        json!({
            "campaign_id": campaign_id,
            "surface_id": surface_id,
            "panel_action_id": panel_action_id,
            "operation_id": operation_id,
            "expected_revision": expected_revision,
            "selection": selection,
            "session_id": session_id,
        }),
    )
    .await
}

#[tauri::command]
async fn execute_player_action(
    window: WebviewWindow,
    campaign_id: String,
    session_id: String,
    operation_id: String,
    expected_revision: u64,
    source: Value,
    selection: Option<Value>,
    confirmation: Option<String>,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "execute_player_action",
        json!({
            "campaign_id": campaign_id,
            "session_id": session_id,
            "operation_id": operation_id,
            "expected_revision": expected_revision,
            "source": source,
            "selection": selection,
            "confirmation": confirmation,
        }),
    )
    .await
}

#[tauri::command]
async fn preview_player_action(
    window: WebviewWindow,
    campaign_id: String,
    session_id: String,
    operation_id: String,
    expected_revision: u64,
    source: Value,
    selection: Option<Value>,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "preview_player_action",
        json!({
            "campaign_id": campaign_id,
            "session_id": session_id,
            "operation_id": operation_id,
            "expected_revision": expected_revision,
            "source": source,
            "selection": selection,
        }),
    )
    .await
}

#[tauri::command]
async fn resolve_mechanics_choice(
    window: WebviewWindow,
    campaign_id: String,
    action_id: String,
    resolution_id: String,
    expected_revision: u64,
    choice_id: String,
    option_id: String,
    session_id: Option<String>,
    choice_sequence: Option<u32>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "resolve_mechanics_choice",
        json!({
            "campaign_id": campaign_id,
            "action_id": action_id,
            "resolution_id": resolution_id,
            "expected_revision": expected_revision,
            "choice_id": choice_id,
            "option_id": option_id,
            "session_id": session_id,
            "choice_sequence": choice_sequence.unwrap_or(0),
        }),
    )
    .await
}

#[tauri::command]
async fn session_history_page(
    window: WebviewWindow,
    session_id: String,
    before_sequence: Option<u64>,
    limit: Option<u32>,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "session_history_page",
        json!({"session_id": session_id, "before_sequence": before_sequence,
            "limit": limit.unwrap_or(50)}),
    )
    .await
}

#[tauri::command]
async fn session_context(window: WebviewWindow, session_id: String) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "session_context",
        json!({"session_id": session_id}),
    )
    .await
}

#[tauri::command]
async fn inspect_provider_thinking(
    window: WebviewWindow,
    session_id: String,
    attempt_id: String,
    response_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "inspect_provider_thinking",
        json!({"session_id": session_id, "attempt_id": attempt_id, "response_id": response_id}),
    )
    .await
}

#[tauri::command]
async fn steer_session(
    window: WebviewWindow,
    session_id: String,
    message_id: String,
    text: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "steer_session",
        json!({"session_id": session_id, "message_id": message_id, "text": text}),
    )
    .await
}

#[tauri::command]
async fn package_candidates(window: WebviewWindow) -> Result<Value, String> {
    owner_call_async(window.clone(), "package_candidates", json!({})).await
}

#[tauri::command]
async fn campaign_rules_update_options(
    window: WebviewWindow,
    campaign_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "campaign_rules_update_options",
        json!({"campaign_id": campaign_id}),
    )
    .await
}

#[tauri::command]
async fn preview_campaign_rules_update(
    window: WebviewWindow,
    campaign_id: String,
    candidate_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "preview_campaign_rules_update",
        json!({"campaign_id": campaign_id, "candidate_id": candidate_id}),
    )
    .await
}

#[tauri::command]
async fn adopt_campaign_rules_update(
    window: WebviewWindow,
    campaign_id: String,
    candidate_id: String,
    activation_id: String,
    expected_revision: u64,
    expected_lock_id: String,
    confirmation: String,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "adopt_campaign_rules_update",
        json!({"campaign_id": campaign_id, "candidate_id": candidate_id,
            "activation_id": activation_id, "expected_revision": expected_revision,
            "expected_lock_id": expected_lock_id, "confirmation": confirmation}),
    )
    .await
}

#[tauri::command]
async fn inspect_package_candidate(
    window: WebviewWindow,
    candidate_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "inspect_package_candidate",
        json!({"candidate_id": candidate_id}),
    )
    .await
}

#[tauri::command]
async fn package_candidate_text(
    window: WebviewWindow,
    candidate_id: String,
    package_id: String,
    path: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "package_candidate_text",
        json!({
            "candidate_id": candidate_id,
            "package_id": package_id,
            "path": path,
        }),
    )
    .await
}

#[tauri::command]
async fn compare_package_candidates(
    window: WebviewWindow,
    left_candidate_id: String,
    right_candidate_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "compare_package_candidates",
        json!({
            "left_candidate_id": left_candidate_id,
            "right_candidate_id": right_candidate_id,
        }),
    )
    .await
}

#[tauri::command]
async fn submit_session_input(
    window: WebviewWindow,
    session_id: String,
    turn_id: Option<String>,
    attempt_id: String,
    message_id: String,
    user_text: String,
    ooc_text: Option<String>,
    allow_public_web: bool,
    builder_start: Option<bool>,
    player_action_id: Option<String>,
    automatic_response: Option<bool>,
    redirect_interruption_id: Option<String>,
    campaign_ending_choice: Option<Value>,
    builder_question_answers: Option<Value>,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "submit_session_input",
        json!({
            "session_id": session_id,
            "turn_id": turn_id,
            "attempt_id": attempt_id,
            "message_id": message_id,
            "user_text": user_text,
            "ooc_text": ooc_text,
            "allow_public_web": allow_public_web,
            "builder_start": builder_start.unwrap_or(false),
            "player_action_id": player_action_id,
            "automatic_response": automatic_response.unwrap_or(false),
            "redirect_interruption_id": redirect_interruption_id,
            "campaign_ending_choice": campaign_ending_choice,
            "builder_question_answers": builder_question_answers,
        }),
    )
    .await
}

#[tauri::command]
async fn interrupt_stream(
    window: WebviewWindow,
    campaign_id: String,
    attempt_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "interrupt_stream",
        json!({"campaign_id": campaign_id, "attempt_id": attempt_id}),
    )
    .await
}

#[tauri::command]
async fn interrupt_provider_stream(
    window: WebviewWindow,
    campaign_id: String,
    attempt_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "interrupt_provider_stream",
        json!({"campaign_id": campaign_id, "attempt_id": attempt_id}),
    )
    .await
}

#[tauri::command]
async fn retryable_interruption(
    window: WebviewWindow,
    campaign_id: String,
    failed_attempt_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "retryable_interruption",
        json!({
            "campaign_id": campaign_id,
            "failed_attempt_id": failed_attempt_id,
        }),
    )
    .await
}

#[tauri::command]
async fn manual_retry_session(
    window: WebviewWindow,
    session_id: String,
    interruption_id: String,
    retry_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "manual_retry_session",
        json!({
            "session_id": session_id,
            "interruption_id": interruption_id,
            "retry_id": retry_id,
        }),
    )
    .await
}

#[tauri::command]
async fn disconnect_stream(
    window: WebviewWindow,
    campaign_id: String,
    attempt_id: String,
    attachment_generation: u64,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "disconnect_stream",
        json!({
            "campaign_id": campaign_id,
            "attempt_id": attempt_id,
            "attachment_generation": attachment_generation,
        }),
    )
    .await
}

#[tauri::command]
async fn disconnect_provider_stream(
    window: WebviewWindow,
    campaign_id: String,
    attempt_id: String,
    attachment_generation: Option<u64>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "disconnect_provider_stream",
        json!({
            "campaign_id": campaign_id,
            "attempt_id": attempt_id,
            "attachment_generation": attachment_generation,
        }),
    )
    .await
}

#[tauri::command]
async fn stream_heartbeat(
    window: WebviewWindow,
    campaign_id: String,
    attempt_id: String,
    attachment_generation: Option<u64>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "stream_heartbeat",
        json!({
            "campaign_id": campaign_id,
            "attempt_id": attempt_id,
            "attachment_generation": attachment_generation,
        }),
    )
    .await
}

#[tauri::command]
async fn provider_stream_heartbeat(
    window: WebviewWindow,
    campaign_id: String,
    attempt_id: String,
    attachment_generation: Option<u64>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "provider_stream_heartbeat",
        json!({
            "campaign_id": campaign_id,
            "attempt_id": attempt_id,
            "attachment_generation": attachment_generation,
        }),
    )
    .await
}

#[tauri::command]
async fn narration_attempt_status(
    window: WebviewWindow,
    campaign_id: String,
    attempt_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "narration_attempt_status",
        json!({"campaign_id": campaign_id, "attempt_id": attempt_id}),
    )
    .await
}

#[tauri::command]
async fn provider_stream_status(
    window: WebviewWindow,
    campaign_id: String,
    attempt_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "provider_stream_status",
        json!({"campaign_id": campaign_id, "attempt_id": attempt_id}),
    )
    .await
}

#[tauri::command]
async fn reconnect_stream(
    window: WebviewWindow,
    campaign_id: String,
    attempt_id: String,
    acknowledged_sequence: u64,
    attachment_generation: u64,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "reconnect_stream",
        json!({
            "campaign_id": campaign_id,
            "attempt_id": attempt_id,
            "acknowledged_sequence": acknowledged_sequence,
            "attachment_generation": attachment_generation,
        }),
    )
    .await
}

#[tauri::command]
async fn reconnect_provider_stream(
    window: WebviewWindow,
    campaign_id: String,
    attempt_id: String,
    attachment_generation: Option<u64>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "reconnect_provider_stream",
        json!({
            "campaign_id": campaign_id,
            "attempt_id": attempt_id,
            "attachment_generation": attachment_generation,
        }),
    )
    .await
}

#[tauri::command]
async fn model_choices(window: WebviewWindow) -> Result<Value, String> {
    owner_call_async(window.clone(), "model_choices", json!({})).await
}

#[tauri::command]
async fn provider_settings(window: WebviewWindow) -> Result<Value, String> {
    owner_call_async(window.clone(), "provider_settings", json!({})).await
}

#[tauri::command]
async fn open_provider_setup(window: WebviewWindow, preset_id: String) -> Result<Value, String> {
    owner_call_async(window, "open_provider_setup", json!({"preset_id": preset_id})).await
}

#[tauri::command]
async fn admin_full_audit_status(window: WebviewWindow) -> Result<Value, String> {
    trusted_audit_reader(&window)?;
    owner_request_async(window, "admin_full_audit_status", json!({})).await
}

#[tauri::command]
async fn chatgpt_connection(
    window: WebviewWindow,
    action: String,
    model: Option<Value>,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "chatgpt_connection",
        json!({"action": action, "model": model}),
    )
    .await
}

#[tauri::command]
async fn admin_full_audit_record(window: WebviewWindow, index: u64) -> Result<Value, String> {
    trusted_audit_reader(&window)?;
    owner_request_async(window, "admin_full_audit_record", json!({"index": index})).await
}

#[tauri::command]
async fn open_admin_audit_window(window: WebviewWindow) -> Result<Value, String> {
    trusted_caller(&window)?;
    let app = window.app_handle();
    if let Some(inspector) = app.get_webview_window("admin-audit") {
        inspector
            .unminimize()
            .map_err(|_| "Full Audit inspector could not be restored".to_string())?;
        inspector
            .show()
            .map_err(|_| "Full Audit inspector could not be shown".to_string())?;
        inspector
            .set_focus()
            .map_err(|_| "Full Audit inspector could not be focused".to_string())?;
        return Ok(json!({"status": "focused"}));
    }
    WebviewWindowBuilder::new(app, "admin-audit", WebviewUrl::App("audit.html".into()))
        .title("Sylvia GM — Full Audit")
        .inner_size(1180.0, 820.0)
        .min_inner_size(760.0, 520.0)
        .build()
        .map_err(|_| "Full Audit inspector could not be opened".to_string())?;
    Ok(json!({"status": "opened"}))
}

#[tauri::command]
async fn set_admin_full_audit(window: WebviewWindow, enabled: bool) -> Result<Value, String> {
    let result = owner_call_async(
        window.clone(),
        "set_admin_full_audit",
        json!({"enabled": enabled}),
    )
    .await?;
    let _ = window
        .app_handle()
        .emit_to("admin-audit", "admin-full-audit-changed", result.clone());
    Ok(result)
}

#[tauri::command]
async fn discover_provider_models(
    window: WebviewWindow,
    provider_id: String,
    preset_id: Option<String>,
    base_url: String,
    credential: String,
    headers: Option<Value>,
    query: Option<String>,
    offset: Option<u64>,
    limit: Option<u64>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "discover_provider_models",
        json!({
            "provider_id": provider_id,
            "preset_id": preset_id,
            "base_url": base_url,
            "credential": credential,
            "headers": headers,
            "query": query,
            "offset": offset,
            "limit": limit,
        }),
    )
    .await
}

#[tauri::command]
async fn detect_local_providers(window: WebviewWindow) -> Result<Value, String> {
    owner_call_async(window.clone(), "detect_local_providers", json!({})).await
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
async fn validate_provider_model(
    window: WebviewWindow,
    provider_id: String,
    preset_id: Option<String>,
    base_url: String,
    credential: String,
    model_id: String,
    transport: String,
    headers: Option<Value>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "validate_provider_model",
        json!({
            "provider_id": provider_id,
            "preset_id": preset_id,
            "base_url": base_url,
            "credential": credential,
            "model_id": model_id,
            "transport": transport,
            "headers": headers,
        }),
    )
    .await
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
async fn save_provider_settings(
    window: WebviewWindow,
    provider_id: String,
    preset_id: Option<String>,
    display_name: Option<String>,
    base_url: String,
    credential: String,
    model_id: String,
    model_name: String,
    context_window: Option<u64>,
    declared_context_window: Option<u64>,
    declared_context_source: Option<String>,
    runtime_context_window: Option<u64>,
    runtime_context_source: Option<String>,
    configured_context_window: Option<u64>,
    context_observed_at: Option<String>,
    declared_context_observed_at: Option<String>,
    runtime_context_observed_at: Option<String>,
    configured_context_observed_at: Option<String>,
    max_output_tokens: Option<u64>,
    declared_max_output_tokens: Option<u64>,
    builder_output_tokens: Option<u64>,
    transport: String,
    headers: Option<Value>,
    supports_streaming: Option<bool>,
    supports_tools: Option<bool>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "save_provider_settings",
        json!({
            "provider_id": provider_id,
            "preset_id": preset_id,
            "display_name": display_name,
            "base_url": base_url,
            "credential": credential,
            "model_id": model_id,
            "model_name": model_name,
            "context_window": context_window,
            "declared_context_window": declared_context_window,
            "declared_context_source": declared_context_source,
            "runtime_context_window": runtime_context_window,
            "runtime_context_source": runtime_context_source,
            "configured_context_window": configured_context_window,
            "context_observed_at": context_observed_at,
            "declared_context_observed_at": declared_context_observed_at,
            "runtime_context_observed_at": runtime_context_observed_at,
            "configured_context_observed_at": configured_context_observed_at,
            "max_output_tokens": max_output_tokens,
            "declared_max_output_tokens": declared_max_output_tokens,
            "builder_output_tokens": builder_output_tokens,
            "transport": transport,
            "headers": headers,
            "supports_streaming": supports_streaming,
            "supports_tools": supports_tools,
        }),
    )
    .await
}

#[tauri::command]
async fn switch_session_reasoning(
    window: WebviewWindow,
    session_id: String,
    effort: Option<String>,
    mode: String,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "switch_session_reasoning",
        json!({"session_id": session_id, "effort": effort, "mode": mode}),
    )
    .await
}

#[tauri::command]
async fn switch_session_model(
    window: WebviewWindow,
    session_id: String,
    provider_id: String,
    model_id: String,
    mode: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "switch_session_model",
        json!({
            "session_id": session_id,
            "provider_id": provider_id,
            "model_id": model_id,
            "mode": mode,
        }),
    )
    .await
}

#[tauri::command]
async fn set_default_model(
    window: WebviewWindow,
    provider_id: String,
    model_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "set_default_model",
        json!({"provider_id": provider_id, "model_id": model_id}),
    )
    .await
}

#[tauri::command]
async fn remove_provider_settings(
    window: WebviewWindow,
    provider_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "remove_provider_settings",
        json!({"provider_id": provider_id}),
    )
    .await
}

#[tauri::command]
async fn install_package(
    window: WebviewWindow,
    install_id: String,
    archive_base64: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "install_package",
        json!({"install_id": install_id, "archive_base64": archive_base64}),
    )
    .await
}

#[tauri::command]
async fn preview_character_card_file(window: WebviewWindow) -> Result<Value, String> {
    trusted_caller(&window)?;
    let Some(source) = rfd::AsyncFileDialog::new().set_parent(&window)
        .add_filter("Character Card JSON / PNG", &["json", "png"]).pick_file().await
    else { return Ok(json!({"status":"cancelled"})); };
    owner_call_async(window, "preview_character_card_file", json!({"source_path":source.path()})).await
}

#[tauri::command]
async fn confirm_character_card_import(window: WebviewWindow, source_sha256: String) -> Result<Value, String> {
    owner_call_async(window, "confirm_character_card_import", json!({"source_sha256":source_sha256})).await
}

#[tauri::command]
async fn install_package_file(window: WebviewWindow, install_id: String) -> Result<Value, String> {
    trusted_caller(&window)?;
    let Some(source) = rfd::AsyncFileDialog::new()
        .set_parent(&window)
        .add_filter(
            "Sylvia GM Content",
            &["sylvia-gm-package", "sylvia-gm-plan", "zip"],
        )
        .pick_file()
        .await
    else {
        return Ok(json!({"status": "cancelled"}));
    };
    let source = source.path().to_path_buf();
    let candidate = owner_call_async(
        window,
        "install_package_file",
        json!({"install_id": install_id, "source_path": source}),
    )
    .await?;
    if candidate.get("status").and_then(Value::as_str) == Some("repairable") {
        return Ok(candidate);
    }
    Ok(json!({
        "status": "installed",
        "file_name": source.file_name().and_then(|value| value.to_str()),
        "candidate": candidate,
    }))
}

#[tauri::command]
async fn start_import_repair(
    window: WebviewWindow,
    repair_id: String,
    operation_id: String,
    provider_id: Option<String>,
    model_id: Option<String>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "start_import_repair",
        json!({
            "repair_id": repair_id, "operation_id": operation_id,
            "provider_id": provider_id, "model_id": model_id,
        }),
    )
    .await
}

fn handoff_package_archive(payload: Value, destination: &Path) -> Result<Value, String> {
    handoff_content_archive(payload, destination, "sylvia-gm-package")
}

fn handoff_content_archive(
    payload: Value,
    destination: &Path,
    extension: &str,
) -> Result<Value, String> {
    if destination.extension().and_then(|value| value.to_str()) != Some(extension) {
        return Err(format!(
            "Sylvia GM content exports require the .{extension} extension"
        ));
    }
    let source_text = payload
        .get("artifact_path")
        .and_then(Value::as_str)
        .ok_or_else(|| "Authority package export did not produce a handoff artifact".to_string())?;
    let source = PathBuf::from(source_text)
        .canonicalize()
        .map_err(|_| "Authority package export artifact is unavailable".to_string())?;
    if !source.is_file() || source.extension().and_then(|value| value.to_str()) != Some(extension) {
        return Err("Authority export artifact is not a Sylvia GM package archive".to_string());
    }
    let export = payload
        .get("export")
        .ok_or_else(|| "Authority package export receipt is unavailable".to_string())?;
    let expected_bytes = export
        .get("byte_count")
        .and_then(Value::as_u64)
        .ok_or_else(|| "Authority package export size is unavailable".to_string())?;
    let expected_sha256 = export
        .get("archive_sha256")
        .and_then(Value::as_str)
        .ok_or_else(|| "Authority package export hash is unavailable".to_string())?;
    let mut input = std::fs::File::open(&source)
        .map_err(|_| "Authority package export artifact is unavailable".to_string())?;
    let mut digest = Sha256::new();
    let mut actual_bytes = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = input
            .read(&mut buffer)
            .map_err(|_| "Authority package export artifact could not be verified".to_string())?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
        actual_bytes += count as u64;
    }
    if actual_bytes != expected_bytes || hex::encode(digest.finalize()) != expected_sha256 {
        return Err("Authority package export artifact failed exact-byte verification".to_string());
    }
    std::fs::copy(&source, destination)
        .map_err(|_| "Sylvia GM could not save the selected package archive".to_string())?;
    Ok(json!({
        "status": "exported",
        "file_name": destination.file_name().and_then(|value| value.to_str()),
        "export": export,
    }))
}

#[tauri::command]
async fn export_package_candidate_file(
    window: WebviewWindow,
    export_id: String,
    candidate_id: String,
) -> Result<Value, String> {
    trusted_caller(&window)?;
    let Some(destination) = rfd::AsyncFileDialog::new()
        .set_parent(&window)
        .add_filter("Sylvia GM Package", &["sylvia-gm-package"])
        .set_file_name("ruleset-package.sylvia-gm-package")
        .save_file()
        .await
    else {
        return Ok(json!({"status": "cancelled"}));
    };
    let destination = destination.path().to_path_buf();
    let payload = owner_call_async(
        window,
        "export_package_candidate_file",
        json!({"export_id": export_id, "candidate_id": candidate_id}),
    )
    .await?;
    tauri::async_runtime::spawn_blocking(move || handoff_package_archive(payload, &destination))
        .await
        .map_err(|error| format!("Export handoff failed: {error}"))?
}

#[tauri::command]
async fn export_campaign_plan_file(
    window: WebviewWindow,
    export_id: String,
    plan_id: String,
    expected_content_sha256: String,
) -> Result<Value, String> {
    trusted_caller(&window)?;
    let Some(destination) = rfd::AsyncFileDialog::new()
        .set_parent(&window)
        .add_filter("Sylvia GM Campaign Plan", &["sylvia-gm-plan"])
        .set_file_name("campaign-plan.sylvia-gm-plan")
        .save_file()
        .await
    else {
        return Ok(json!({"status": "cancelled"}));
    };
    let destination = destination.path().to_path_buf();
    let payload = owner_call_async(
        window,
        "export_campaign_plan_file",
        json!({"export_id": export_id, "plan_id": plan_id,
               "expected_content_sha256": expected_content_sha256}),
    )
    .await?;
    tauri::async_runtime::spawn_blocking(move || {
        handoff_content_archive(payload, &destination, "sylvia-gm-plan")
    })
    .await
    .map_err(|error| format!("Export handoff failed: {error}"))?
}

#[tauri::command]
async fn ingest_builder_sources(
    window: WebviewWindow,
    session_id: String,
    ingestion_id: String,
) -> Result<Value, String> {
    trusted_caller(&window)?;
    let Some(sources) = rfd::AsyncFileDialog::new()
        .set_parent(&window)
        .add_filter(
            "Builder sources",
            &["txt", "md", "json", "yaml", "yml", "csv", "pdf"],
        )
        .pick_files()
        .await
    else {
        return Ok(json!({"status": "cancelled"}));
    };
    let sources: Vec<PathBuf> = sources
        .iter()
        .map(|source| source.path().to_path_buf())
        .collect();
    owner_call_async(
        window,
        "ingest_builder_sources",
        json!({
            "session_id": session_id,
            "ingestion_id": ingestion_id,
            "source_paths": sources,
        }),
    )
    .await
}

#[tauri::command]
async fn create_guided_campaign_draft(
    window: WebviewWindow,
    operation_id: String,
    display_name: String,
    provider_id: String,
    model_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "create_guided_campaign_draft",
        json!({
            "operation_id": operation_id,
            "display_name": display_name,
            "provider_id": provider_id,
            "model_id": model_id,
        }),
    )
    .await
}

#[tauri::command]
async fn navigate_guided_campaign_draft(
    window: WebviewWindow,
    operation_id: String,
    campaign_id: String,
    builder_session_id: String,
    expected_construction_revision: u64,
    target_step: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "navigate_guided_campaign_draft",
        json!({
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "builder_session_id": builder_session_id,
            "expected_construction_revision": expected_construction_revision,
            "target_step": target_step,
        }),
    )
    .await
}

#[tauri::command]
async fn advance_guided_campaign_draft(
    window: WebviewWindow,
    operation_id: String,
    campaign_id: String,
    builder_session_id: String,
    expected_construction_revision: u64,
    current_step: String,
    visible_answer: String,
    answer: Value,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "advance_guided_campaign_draft",
        json!({
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "builder_session_id": builder_session_id,
            "expected_construction_revision": expected_construction_revision,
            "current_step": current_step,
            "visible_answer": visible_answer,
            "answer": answer,
        }),
    )
    .await
}

#[tauri::command]
async fn guided_campaign_contribution(
    window: WebviewWindow,
    builder_session_id: String,
    contribution_step: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "guided_campaign_contribution",
        json!({
            "builder_session_id": builder_session_id,
            "contribution_step": contribution_step,
        }),
    )
    .await
}

#[tauri::command]
async fn save_reusable_campaign_plan(
    window: WebviewWindow,
    operation_id: String,
    campaign_id: String,
    builder_session_id: String,
    expected_construction_revision: u64,
    title: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "save_reusable_campaign_plan",
        json!({
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "builder_session_id": builder_session_id,
            "expected_construction_revision": expected_construction_revision,
            "title": title,
        }),
    )
    .await
}

#[tauri::command]
async fn start_guided_creation_subflow(
    window: WebviewWindow,
    operation_id: String,
    campaign_id: String,
    parent_session_id: String,
    expected_construction_revision: u64,
    purpose: String,
    visible_answer: String,
    trigger_message_id: Option<String>,
    character_profile_id: Option<String>,
    character_profile_sha256: Option<String>,
    character_artifact_id: Option<String>,
    character_artifact_sha256: Option<String>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "start_guided_creation_subflow",
        json!({
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "parent_session_id": parent_session_id,
            "expected_construction_revision": expected_construction_revision,
            "purpose": purpose,
            "visible_answer": visible_answer,
            "trigger_message_id": trigger_message_id,
            "character_profile_id": character_profile_id,
            "character_profile_sha256": character_profile_sha256,
            "character_artifact_id": character_artifact_id,
            "character_artifact_sha256": character_artifact_sha256,
        }),
    )
    .await
}

#[tauri::command]
async fn return_guided_creation_subflow(
    window: WebviewWindow,
    operation_id: String,
    campaign_id: String,
    parent_session_id: String,
    child_session_id: String,
    expected_construction_revision: u64,
    use_result: bool,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "return_guided_creation_subflow",
        json!({
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "parent_session_id": parent_session_id,
            "child_session_id": child_session_id,
            "expected_construction_revision": expected_construction_revision,
            "use_result": use_result,
        }),
    )
    .await
}

#[tauri::command]
async fn confirm_new_campaign(
    window: WebviewWindow,
    operation_id: String,
    display_name: String,
    candidate_id: Option<String>,
    turn_id: String,
    provider_id: String,
    model_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "confirm_new_campaign",
        json!({
            "operation_id": operation_id,
            "display_name": display_name,
            "candidate_id": candidate_id,
            "turn_id": turn_id,
            "provider_id": provider_id,
            "model_id": model_id,
        }),
    )
    .await
}

#[tauri::command]
async fn rift_template(window: WebviewWindow) -> Result<Value, String> {
    owner_call_async(window.clone(), "rift_template", json!({})).await
}

#[tauri::command]
async fn campaign_party_options(
    window: WebviewWindow,
    _state: State<'_, OwnerBridge>,
    ruleset_selection: String,
    setting_id: String,
    plan_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "campaign_party_options",
        json!({
            "ruleset_selection": ruleset_selection,
            "setting_id": setting_id,
            "plan_id": plan_id,
        }),
    )
    .await
}

#[tauri::command]
async fn start_rift_campaign(
    window: WebviewWindow,
    _state: State<'_, OwnerBridge>,
    operation_id: String,
    campaign_id: String,
    builder_session_id: String,
    ruleset_selection: String,
    setting_id: String,
    plan_id: String,
    display_name: String,
    lane: String,
    archetype_id: String,
    companion_ids: Vec<String>,
    customization_notes: String,
    provider_id: String,
    model_id: String,
    construction_intent: Value,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "start_rift_campaign",
        json!({
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "builder_session_id": builder_session_id,
            "ruleset_selection": ruleset_selection,
            "setting_id": setting_id,
            "plan_id": plan_id,
            "display_name": display_name,
            "lane": lane,
            "archetype_id": archetype_id,
            "companion_ids": companion_ids,
            "customization_notes": customization_notes,
            "provider_id": provider_id,
            "model_id": model_id,
            "construction_intent": construction_intent,
        }),
    )
    .await
}

#[tauri::command]
async fn ready_campaign_catalog(window: WebviewWindow) -> Result<Value, String> {
    owner_call_async(window, "ready_campaign_catalog", json!({})).await
}

#[tauri::command]
async fn start_ready_campaign(window: WebviewWindow, command: Value) -> Result<Value, String> {
    owner_call_async(window, "start_ready_campaign", command).await
}

#[tauri::command]
async fn customize_ready_campaign(window: WebviewWindow, command: Value) -> Result<Value, String> {
    owner_call_async(window, "customize_ready_campaign", command).await
}

#[tauri::command]
async fn start_guided_campaign(
    window: WebviewWindow,
    _state: State<'_, OwnerBridge>,
    operation_id: String,
    campaign_id: String,
    builder_session_id: String,
    display_name: String,
    candidate_id: Option<String>,
    premise: String,
    setting_id: Option<String>,
    plan_id: Option<String>,
    player_character_id: Option<String>,
    companion_ids: Option<Vec<String>>,
    construction_intent: Option<Value>,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "start_guided_campaign",
        json!({
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "builder_session_id": builder_session_id,
            "display_name": display_name,
            "candidate_id": candidate_id,
            "premise": premise,
            "setting_id": setting_id,
            "plan_id": plan_id,
            "player_character_id": player_character_id,
            "companion_ids": companion_ids,
            "construction_intent": construction_intent,
        }),
    )
    .await
}

#[tauri::command]
async fn begin_campaign_construction(
    window: WebviewWindow,
    _state: State<'_, OwnerBridge>,
    operation_id: String,
    campaign_id: String,
    lane: String,
    expected_revision: u64,
    canonical_input: Value,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "begin_campaign_construction",
        json!({
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "lane": lane,
            "expected_revision": expected_revision,
            "canonical_input": canonical_input,
        }),
    )
    .await
}

#[tauri::command]
async fn finalize_campaign_construction(
    window: WebviewWindow,
    _state: State<'_, OwnerBridge>,
    campaign_id: String,
    dm_provider_id: String,
    dm_model_id: String,
    model_guidance_override: bool,
) -> Result<Value, String> {
    owner_call_async(
        window,
        "finalize_campaign_construction",
        json!({
            "campaign_id": campaign_id,
            "dm_provider_id": dm_provider_id,
            "dm_model_id": dm_model_id,
            "model_guidance_override": model_guidance_override,
        }),
    )
    .await
}

#[tauri::command]
async fn start_dm_session(
    window: WebviewWindow,
    campaign_id: String,
    session_id: String,
    turn_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "start_dm_session",
        json!({
            "campaign_id": campaign_id,
            "session_id": session_id,
            "turn_id": turn_id,
        }),
    )
    .await
}

#[tauri::command]
async fn model_guidance(
    window: WebviewWindow,
    campaign_id: String,
    model_id: String,
    override_accepted: bool,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "model_guidance",
        json!({
            "campaign_id": campaign_id,
            "model_id": model_id,
            "override_accepted": override_accepted,
        }),
    )
    .await
}

#[tauri::command]
async fn catalog(window: WebviewWindow, campaign_id: String) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "catalog",
        json!({"campaign_id": campaign_id}),
    )
    .await
}

#[tauri::command]
async fn stop_agent_session(window: WebviewWindow, session_id: String) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "stop_agent_session",
        json!({"session_id": session_id}),
    )
    .await
}

#[tauri::command]
async fn resume_agent_session(window: WebviewWindow, session_id: String) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "resume_agent_session",
        json!({"session_id": session_id}),
    )
    .await
}

async fn lifecycle_call(
    window: &WebviewWindow,
    method: &'static str,
    operation_id: String,
    campaign_id: String,
    expected_revision: u64,
    designated_session_id: Option<String>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        method,
        json!({
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "expected_revision": expected_revision,
            "designated_session_id": designated_session_id,
        }),
    )
    .await
}

#[tauri::command]
async fn archive_campaign(
    window: WebviewWindow,
    operation_id: String,
    campaign_id: String,
    expected_revision: u64,
    designated_session_id: Option<String>,
) -> Result<Value, String> {
    lifecycle_call(
        &window,
        "archive_campaign",
        operation_id,
        campaign_id,
        expected_revision,
        designated_session_id,
    )
    .await
}

#[tauri::command]
async fn restore_archived_campaign(
    window: WebviewWindow,
    operation_id: String,
    campaign_id: String,
    expected_revision: u64,
    session_id: Option<String>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "restore_archived_campaign",
        json!({
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "expected_revision": expected_revision,
            "session_id": session_id,
        }),
    )
    .await
}

#[tauri::command]
async fn trash_campaign(
    window: WebviewWindow,
    operation_id: String,
    campaign_id: String,
    expected_revision: u64,
    designated_session_id: Option<String>,
) -> Result<Value, String> {
    lifecycle_call(
        &window,
        "trash_campaign",
        operation_id,
        campaign_id,
        expected_revision,
        designated_session_id,
    )
    .await
}

#[tauri::command]
async fn restore_trashed_campaign(
    window: WebviewWindow,
    operation_id: String,
    campaign_id: String,
    expected_revision: u64,
) -> Result<Value, String> {
    lifecycle_call(
        &window,
        "restore_trashed_campaign",
        operation_id,
        campaign_id,
        expected_revision,
        None,
    )
    .await
}

#[tauri::command]
async fn rename_campaign(
    window: WebviewWindow,
    operation_id: String,
    campaign_id: String,
    display_name: String,
    expected_revision: u64,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "rename_campaign",
        json!({
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "display_name": display_name,
            "expected_revision": expected_revision,
        }),
    )
    .await
}

#[tauri::command]
async fn purge_campaign(
    window: WebviewWindow,
    operation_id: String,
    campaign_id: String,
    expected_revision: u64,
    confirmation: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "purge_campaign",
        json!({
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "expected_revision": expected_revision,
            "confirmation": confirmation,
        }),
    )
    .await
}

#[tauri::command]
async fn backup_authority(window: WebviewWindow, backup_id: String) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "backup_database",
        json!({"backup_id": backup_id}),
    )
    .await
}

#[tauri::command]
async fn export_campaign(
    window: WebviewWindow,
    export_id: String,
    operation_id: String,
    campaign_id: String,
    include_thinking: Option<bool>,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "export_campaign",
        json!({
            "export_id": export_id,
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "include_thinking": include_thinking.unwrap_or(false),
        }),
    )
    .await
}

fn handoff_export_archive(payload: Value, destination: &Path) -> Result<Value, String> {
    if destination.extension().and_then(|value| value.to_str()) != Some("sylvia-gm-campaign") {
        return Err("Sylvia GM exports require the .sylvia-gm-campaign extension".to_string());
    }
    let source_text = payload
        .get("artifact_path")
        .and_then(Value::as_str)
        .ok_or_else(|| "Authority export did not produce a handoff artifact".to_string())?;
    let source = PathBuf::from(source_text)
        .canonicalize()
        .map_err(|_| "Authority export artifact is unavailable".to_string())?;
    if !source.is_file()
        || source.extension().and_then(|value| value.to_str()) != Some("sylvia-gm-campaign")
    {
        return Err("Authority export artifact is not an Sylvia GM campaign archive".to_string());
    }
    std::fs::copy(&source, destination)
        .map_err(|_| "Sylvia GM could not save the selected campaign archive".to_string())?;
    Ok(json!({
        "status": "exported",
        "file_name": destination.file_name().and_then(|value| value.to_str()),
        "export": payload.get("export").cloned().unwrap_or(Value::Null),
    }))
}

#[cfg(test)]
mod selected_file_handoff_tests {
    use super::handoff_export_archive;
    use serde_json::json;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn selected_export_handoff_copies_exact_bytes_without_leaking_private_path() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock follows Unix epoch")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("sylvia-gm-handoff-{nonce}"));
        fs::create_dir_all(&root).expect("create handoff fixture directory");
        let private_source = root.join("private.sylvia-gm-campaign");
        let selected_destination = root.join("selected.sylvia-gm-campaign");
        let archive_bytes = b"sealed portable campaign fixture";
        fs::write(&private_source, archive_bytes).expect("write private owner artifact");

        let result = handoff_export_archive(
            json!({
                "artifact_path": private_source,
                "export": {
                    "campaign_id": "campaign-portable",
                    "archive_sha256": "fixture-hash",
                    "byte_count": archive_bytes.len(),
                },
            }),
            &selected_destination,
        )
        .expect("copy owner artifact to selected destination");

        assert_eq!(
            fs::read(&selected_destination).expect("read selected destination"),
            archive_bytes
        );
        assert_eq!(result["status"], "exported");
        assert_eq!(result["file_name"], "selected.sylvia-gm-campaign");
        assert_eq!(result["export"]["campaign_id"], "campaign-portable");
        assert!(result.get("artifact_path").is_none());
        fs::remove_dir_all(root).expect("remove handoff fixture directory");
    }
}

#[tauri::command]
async fn export_campaign_file(
    window: WebviewWindow,
    export_id: String,
    operation_id: String,
    campaign_id: String,
    include_thinking: Option<bool>,
) -> Result<Value, String> {
    trusted_caller(&window)?;
    let suggested = format!("{campaign_id}.sylvia-gm-campaign");
    // Explain the portable archive before opening its destination; never block renderer heartbeats.
    let confirmed = rfd::AsyncMessageDialog::new()
        .set_parent(&window)
        .set_title("Export campaign")
        .set_description(
            "This archive includes conversations, campaign preparation and other campaign material. \
             It is not encrypted. Share it only with people you want to see that content.",
        )
        .set_buttons(rfd::MessageButtons::OkCancel)
        .show()
        .await;
    if confirmed != rfd::MessageDialogResult::Ok {
        return Ok(json!({"status": "cancelled"}));
    }
    let Some(destination) = rfd::AsyncFileDialog::new()
        .set_parent(&window)
        .add_filter("Sylvia GM Campaign", &["sylvia-gm-campaign"])
        .set_file_name(&suggested)
        .save_file()
        .await
    else {
        return Ok(json!({"status": "cancelled"}));
    };
    let destination = destination.path().to_path_buf();
    let payload = owner_call_async(
        window,
        "export_campaign_file",
        json!({
            "export_id": export_id,
            "operation_id": operation_id,
            "campaign_id": campaign_id,
            "include_thinking": include_thinking.unwrap_or(false),
        }),
    )
    .await?;
    tauri::async_runtime::spawn_blocking(move || handoff_export_archive(payload, &destination))
        .await
        .map_err(|error| format!("Export handoff failed: {error}"))?
}

#[tauri::command]
async fn recover_campaign(
    window: WebviewWindow,
    export_id: String,
    recovery_id: String,
) -> Result<Value, String> {
    owner_call_async(
        window.clone(),
        "recover_campaign",
        json!({"export_id": export_id, "recovery_id": recovery_id}),
    )
    .await
}

#[tauri::command]
async fn recover_campaign_file(
    window: WebviewWindow,
    recovery_id: String,
) -> Result<Value, String> {
    trusted_caller(&window)?;
    let Some(source) = rfd::AsyncFileDialog::new()
        .set_parent(&window)
        .add_filter("Sylvia GM Campaign", &["sylvia-gm-campaign"])
        .pick_file()
        .await
    else {
        return Ok(json!({"status": "cancelled"}));
    };
    let source = source.path().to_path_buf();
    let recovery = owner_call_async(
        window,
        "recover_campaign_file",
        json!({"recovery_id": recovery_id, "source_path": source}),
    )
    .await?;
    Ok(json!({
        "status": "recovered",
        "file_name": source.file_name().and_then(|value| value.to_str()),
        "recovery": recovery,
    }))
}

#[tauri::command]
async fn record_local_fault(
    window: WebviewWindow,
    category: String,
    delta: u32,
) -> Result<Value, String> {
    trusted_caller(&window)?;
    owner_call_async(
        window,
        "record_local_fault",
        json!({"category": category, "delta": delta}),
    )
    .await
}

#[tauri::command]
async fn preview_local_report(
    window: WebviewWindow,
    kind: String,
    reproduction: String,
    feedback: String,
) -> Result<Value, String> {
    trusted_caller(&window)?;
    owner_call_async(
        window,
        "preview_local_report",
        json!({
            "kind": kind, "reproduction": reproduction, "feedback": feedback,
        }),
    )
    .await
}

#[tauri::command]
async fn cancel_local_report(window: WebviewWindow, preview_id: String) -> Result<Value, String> {
    trusted_caller(&window)?;
    owner_call_async(
        window,
        "cancel_local_report",
        json!({"preview_id": preview_id}),
    )
    .await
}

#[tauri::command]
async fn save_local_report(window: WebviewWindow, preview_id: String) -> Result<Value, String> {
    trusted_caller(&window)?;
    let Some(destination) = rfd::AsyncFileDialog::new()
        .set_parent(&window)
        .add_filter("Sylvia local report JSON", &["json"])
        .set_file_name("sylvia-local-report.json")
        .save_file()
        .await
    else {
        owner_call_async(
            window,
            "cancel_local_report",
            json!({"preview_id": preview_id}),
        )
        .await?;
        return Ok(json!({"status": "cancelled"}));
    };
    owner_call_async(
        window,
        "save_local_report",
        json!({
            "preview_id": preview_id, "destination_path": destination.path().to_string_lossy(),
        }),
    )
    .await
}

#[tauri::command]
async fn export_admin_audit_file(window: WebviewWindow) -> Result<Value, String> {
    trusted_caller(&window)?;
    // Audit has a passive spoilers notice; only the ordinary save chooser is needed here.
    let Some(destination) = rfd::AsyncFileDialog::new()
        .set_parent(&window)
        .add_filter("Sylvia GM Audit JSON", &["json"])
        .set_file_name("sylvia-gm-admin-audit.json")
        .save_file()
        .await
    else {
        return Ok(json!({"status": "cancelled"}));
    };
    let export = owner_call_async(
        window,
        "export_admin_full_audit",
        json!({"destination_path": destination.path().to_string_lossy()}),
    )
    .await?;
    Ok(json!({
        "status": "exported",
        "file_name": destination.file_name(),
        "trace_count": export.get("trace_count"),
        "bytes": export.get("bytes"),
    }))
}

pub fn run() {
    let portable = std::env::current_exe()
        .map_err(|error| format!("Sylvia GM executable path is unavailable: {error}"))
        .and_then(|executable| desktop_storage::portable_data(&executable))
        .and_then(|portable| {
            if let Some(data) = &portable {
                desktop_storage::configure_portable(data)?;
            }
            Ok(portable)
        });
    let portable = match portable {
        Ok(portable) => portable,
        Err(error) => {
            rfd::MessageDialog::new()
                .set_title("Sylvia GM - storage unavailable")
                .set_description(error)
                .set_level(rfd::MessageLevel::Error)
                .show();
            return;
        }
    };
    let explicit_database = std::env::var_os("SYLVIA_GM_AUTHORITY_DATABASE");
    let mut builder = tauri::Builder::default();
    if instance_policy::use_installed_focus(portable.is_some(), explicit_database.is_some()) {
        builder = builder.plugin(tauri_plugin_single_instance::init(
            |app, _arguments, _cwd| {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.unminimize();
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            },
        ));
    }
    builder
        .manage(PanelWindows::default())
        .manage(panel_drag::PanelDrags::default())
        .setup(move |app| {
            let database = desktop_storage::database_path(
                portable.as_deref(),
                explicit_database,
                || {
                    app.path()
                        .app_local_data_dir()
                        .map_err(|error| error.to_string())
                },
            )
            .map_err(std::io::Error::other)?;
            if let Some(parent) = database.parent() {
                std::fs::create_dir_all(parent)?;
                app.manage(dictation::Dictation::new(parent.join("dictation-models/base-ebe41f70")));
            }
            let startup = desktop_update_start_arguments(&std::env::args_os().skip(1).collect::<Vec<_>>())
                .map_err(std::io::Error::other)?;
            let bridge = OwnerBridge::spawn_default(&database, portable.is_some(), startup.clone()).map_err(|error| {
                let interrupted = startup.is_some() || database.parent().map(|root| {
                    root.join(".application-updates/current.json").is_file()
                }).unwrap_or(false);
                let description = if interrupted {
                    "Sylvia could not start the update recovery component. Keep saved data and retained update files intact. Repair with the same or a newer compatible official Sylvia version. For an installed copy, use its ordinary installer. For a portable copy, replace only application files and keep isolated-data in place. Do not open an older app against migrated data; use checkpoint recovery after repairing the application."
                } else {
                    "Sylvia cannot open the selected data folder. Close any other Sylvia copy using that same folder, then try again. A different installed or portable copy keeps its own data."
                };
                rfd::MessageDialog::new()
                    .set_title(if interrupted { "Sylvia GM - update repair required" } else { "Sylvia GM - selected data unavailable" })
                    .set_description(description)
                    .set_level(rfd::MessageLevel::Error)
                    .show();
                std::io::Error::other(error)
            })?;
            let recovery_only = bridge.recovery_only;
            app.manage(bridge);
            // Retain the distribution that selected this launch's data root.
            let application = std::env::current_exe()?;
            app.manage(DesktopUpdateLaunch {
                portable: portable.is_some(), startup,
                data: database.parent().ok_or_else(|| std::io::Error::other("Data root unavailable"))?.into(),
                live: application.parent().ok_or_else(|| std::io::Error::other("Application root unavailable"))?.into(),
                applying: AtomicBool::new(false),
            });
            let main = WebviewWindowBuilder::new(app, "main", WebviewUrl::App(
                if recovery_only { "update-recovery.html" } else { "app.html" }.into(),
            ))
                .title("Sylvia GM")
                .inner_size(1400.0, 900.0)
                .min_inner_size(960.0, 640.0)
                .disable_drag_drop_handler()
                .build()?;
            let panel_app = app.handle().clone();
            main.on_window_event(move |event| {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    if panel_app.state::<DesktopUpdateLaunch>().applying.load(Ordering::Acquire) {
                        api.prevent_close();
                        let _ = panel_app.emit("desktop_update_close_blocked", json!({}));
                        return;
                    }
                }
                if matches!(
                    event,
                    tauri::WindowEvent::Destroyed | tauri::WindowEvent::CloseRequested { .. }
                ) {
                    panel_app.state::<dictation::Dictation>().close();
                    panel_app
                        .state::<panel_drag::PanelDrags>()
                        .cancel_window("main");
                }
                if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                    let state = panel_app.state::<PanelWindows>();
                    let _ = panel_windows::close_all(&panel_app, &state);
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            panel_windows::sync_panel_windows,
            panel_windows::publish_panel_workspace,
            panel_windows::focus_panel_window,
            panel_windows::panel_window_message,
            panel_windows::panel_window_reply,
            panel_drag::begin_panel_drag,
            panel_drag::finish_panel_drag,
            application_info,
            desktop_update_state,
            desktop_update_check,
            desktop_update_download,
            desktop_update_cancel,
            desktop_update_defer,
            desktop_update_startup_state,
            desktop_update_confirm_startup,
            desktop_update_recovery_state,
            desktop_update_recover,
            desktop_update_install,
            load_desktop_drafts,
            save_desktop_drafts,
            dictation_capabilities,
            dictation_prepare,
            dictation_start,
            dictation_stop,
            dictation_cancel,
            campaign_index,
            campaign_creation_catalog,
            campaign_sequel_context,
            preview_campaign_sequel,
            append_campaign_sequel,
            delete_reusable_library_entry,
            builder_chat_index,
            create_builder_chat,
            rename_builder_chat,
            archive_builder_chat,
            restore_builder_chat,
            delete_builder_chat,
            snapshot,
            display_snapshot,
            player_projection,
            player_projection_page,
            cancel_display_load,
            campaign_panels,
            action_sequence,
            compose_action_sequence,
            execute_action_sequence,
            player_action_availability,
            execute_panel_action,
            preview_panel_action,
            panel_action_receipt,
            execute_player_action,
            preview_player_action,
            resolve_mechanics_choice,
            session_context,
            session_history_page,
            inspect_provider_thinking,
            steer_session,
            package_candidates,
            campaign_rules_update_options,
            preview_campaign_rules_update,
            adopt_campaign_rules_update,
            inspect_package_candidate,
            package_candidate_text,
            compare_package_candidates,
            export_package_candidate_file,
            export_campaign_plan_file,
            submit_session_input,
            interrupt_stream,
            interrupt_provider_stream,
            disconnect_stream,
            disconnect_provider_stream,
            stream_heartbeat,
            provider_stream_heartbeat,
            narration_attempt_status,
            provider_stream_status,
            retryable_interruption,
            manual_retry_session,
            reconnect_stream,
            reconnect_provider_stream,
            model_choices,
            provider_settings,
            chatgpt_connection,
            admin_full_audit_status,
            admin_full_audit_record,
            open_admin_audit_window,
            set_admin_full_audit,
            discover_provider_models,
            open_provider_setup,
            detect_local_providers,
            validate_provider_model,
            save_provider_settings,
            remove_provider_settings,
            switch_session_model,
            switch_session_reasoning,
            set_default_model,
            install_package,
            install_package_file,
            preview_character_card_file,
            confirm_character_card_import,
            start_import_repair,
            ingest_builder_sources,
            rift_template,
            campaign_party_options,
            start_rift_campaign,
            ready_campaign_catalog,
            start_ready_campaign,
            customize_ready_campaign,
            start_guided_campaign,
            create_guided_campaign_draft,
            advance_guided_campaign_draft,
            navigate_guided_campaign_draft,
            guided_campaign_contribution,
            save_reusable_campaign_plan,
            start_guided_creation_subflow,
            return_guided_creation_subflow,
            confirm_new_campaign,
            begin_campaign_construction,
            finalize_campaign_construction,
            start_dm_session,
            model_guidance,
            catalog,
            stop_agent_session,
            resume_agent_session,
            archive_campaign,
            restore_archived_campaign,
            trash_campaign,
            restore_trashed_campaign,
            rename_campaign,
            purge_campaign,
            backup_authority,
            export_campaign,
            export_campaign_file,
            recover_campaign,
            recover_campaign_file,
            export_admin_audit_file,
            preview_local_report,
            record_local_fault,
            cancel_local_report,
            save_local_report,
        ])
        .build(tauri::generate_context!())
        .expect("error while building Sylvia GM")
        .run(|app, event| {
            // Tauri exits the process without dropping managed state. Wait for the owner
            // here so it cannot retain database or executable handles after desktop exit.
            if matches!(event, tauri::RunEvent::Exit) {
                app.state::<dictation::Dictation>().close();
                let owner = app.state::<OwnerBridge>();
                let _ = if owner.recovery_only { owner.shutdown_for_recovery() } else { owner.shutdown() };
            }
        });
}
