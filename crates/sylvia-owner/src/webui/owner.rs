//! `OwnerProcess`: bounded requests multiplexed over the same private pipes the desktop uses.

use super::{token_hex, WebUIError, MAX_BYTES, OWNER_EXIT_TIMEOUT};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_store::launch::LaunchHandshake;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::oneshot;

const DISCONNECTED: &str = "The Sylvia owner disconnected. Saved work is retained.";
const STOPPED_AGAIN: &str = "The Sylvia owner is stopped. Launch it again to continue.";

type Pending = Arc<Mutex<HashMap<String, oneshot::Sender<Result<Json, WebUIError>>>>>;

/// The owner child and its pipes.
pub struct OwnerProcess {
    /// `data_root.resolve()`.
    pub data_root: PathBuf,
    program: PathBuf,
    child: tokio::sync::Mutex<Option<Child>>,
    stdin: tokio::sync::Mutex<Option<ChildStdin>>,
    pending: Pending,
    reader: Mutex<Option<tokio::task::JoinHandle<()>>>,
}

/// `line.decode()` then `json.loads`, for one reply line.
fn parse_line(line: &[u8]) -> Option<Json> {
    let text = std::str::from_utf8(line).ok()?;
    pyjson::loads(text.trim_end_matches(['\r', '\n'])).ok()
}

/// Read one line of at most `MAX_BYTES` (asyncio's `limit=MAX_BYTES + 1` readline). `None` is
/// end of output or a line over the limit.
async fn read_line(stdout: &mut BufReader<ChildStdout>) -> Option<Vec<u8>> {
    let mut line = Vec::new();
    loop {
        let available = stdout.fill_buf().await.ok()?;
        if available.is_empty() {
            return if line.is_empty() { None } else { Some(line) };
        }
        let (take, done) = match available.iter().position(|byte| *byte == b'\n') {
            Some(index) => (index + 1, true),
            None => (available.len(), false),
        };
        line.extend_from_slice(&available[..take]);
        stdout.consume(take);
        if line.len() > MAX_BYTES + 1 {
            return None;
        }
        if done {
            return Some(line);
        }
    }
}

impl OwnerProcess {
    /// `OwnerProcess(data_root)`, starting `program` as the owner.
    pub fn new(data_root: &Path, program: PathBuf) -> Self {
        let data_root = std::fs::canonicalize(data_root)
            .map(|path| PathBuf::from(crate::util::plain_path(&path)))
            .unwrap_or_else(|_| data_root.to_path_buf());
        Self {
            data_root,
            program,
            child: tokio::sync::Mutex::new(None),
            stdin: tokio::sync::Mutex::new(None),
            pending: Arc::new(Mutex::new(HashMap::new())),
            reader: Mutex::new(None),
        }
    }

    fn reader_running(&self) -> bool {
        self.reader.lock().expect("reader lock").as_ref().is_some_and(|reader| !reader.is_finished())
    }

    /// `start()`: launch the owner, send the launch envelope and verify its handshake.
    pub async fn start(&self) -> Result<(), WebUIError> {
        let mut handshake = LaunchHandshake::new();
        let credential = handshake.issue().map_err(|error| WebUIError::unavailable(error.message))?;
        let child = Command::new(&self.program)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn();
        let failed = || {
            WebUIError::unavailable(
                "Sylvia could not open this data root. If it is already open in another desktop or \
                 WebUI, close that owner first. No second writer was started.",
            )
        };
        let mut child = match child {
            Ok(child) => child,
            Err(_) => return Err(failed()),
        };
        let stdin = child.stdin.take();
        let mut stdout = BufReader::new(child.stdout.take().expect("stdout is piped"));
        *self.stdin.lock().await = stdin;
        *self.child.lock().await = Some(child);
        let launched = async {
            let mut envelope = JsonMap::new();
            envelope.insert("type".into(), "launch".into());
            envelope.insert("secret_hex".into(), credential.secret_hex.clone().into());
            let database = self.data_root.join("authority.sqlite3");
            envelope.insert("database_path".into(), database.to_string_lossy().into_owned().into());
            envelope.insert("owner_id".into(), format!("webui-{}", token_hex(12)).into());
            self.write(&Json::Object(envelope), None).await?;
            let ready = tokio::time::timeout(Duration::from_secs(120), read_line(&mut stdout))
                .await
                .ok()
                .flatten()
                .and_then(|line| parse_line(&line))
                .ok_or_else(|| WebUIError::unavailable(DISCONNECTED))?;
            if ready.get("type").and_then(Json::as_str) != Some("ready")
                || ready.get("ok") != Some(&Json::Bool(true))
            {
                return Err(WebUIError::unavailable("The owner did not become ready."));
            }
            let response = ready.get("response_hex").and_then(Json::as_str).unwrap_or_default().to_owned();
            handshake.verify(&response).map_err(|error| WebUIError::unavailable(error.message))
        }
        .await;
        if launched.is_err() {
            let _ = self.close().await;
            return Err(failed());
        }
        let pending = Arc::clone(&self.pending);
        *self.reader.lock().expect("reader lock") = Some(tokio::spawn(receive(stdout, pending)));
        Ok(())
    }

    /// `_write(payload, future)`.
    async fn write(
        &self,
        payload: &Json,
        registration: Option<(String, oneshot::Sender<Result<Json, WebUIError>>)>,
    ) -> Result<(), WebUIError> {
        let mut raw = pyjson::dumps(payload, Dump::DEFAULT).into_bytes();
        raw.push(b'\n');
        if raw.len() > MAX_BYTES {
            return Err(WebUIError::new("The request is too large.", "request_too_large"));
        }
        let mut stdin = self.stdin.lock().await;
        let Some(pipe) = stdin.as_mut() else {
            return Err(WebUIError::unavailable("The Sylvia owner is stopped."));
        };
        if let Some((identity, sender)) = registration {
            if !self.reader_running() {
                return Err(WebUIError::unavailable(STOPPED_AGAIN));
            }
            // Register before the write: the owner may commit even if the write then fails.
            self.pending.lock().expect("pending lock").insert(identity, sender);
        }
        pipe.write_all(&raw).await.map_err(|_| outcome_unknown())?;
        pipe.flush().await.map_err(|_| outcome_unknown())
    }

    /// `call(method, params)`: one owner command; a rejection becomes its code and message.
    pub async fn call(&self, method: &str, params: &JsonMap) -> Result<Json, WebUIError> {
        if !self.reader_running() {
            return Err(WebUIError::unavailable(STOPPED_AGAIN));
        }
        let identity = token_hex(16);
        let (sender, receiver) = oneshot::channel();
        let mut request = JsonMap::new();
        request.insert("id".into(), identity.clone().into());
        request.insert("method".into(), method.into());
        request.insert("params".into(), Json::Object(params.clone()));
        let outcome = async {
            self.write(&Json::Object(request), Some((identity.clone(), sender))).await?;
            let reply =
                receiver.await.map_err(|_| WebUIError::new(DISCONNECTED_UNKNOWN, "outcome_unknown"))??;
            if reply.get("ok").and_then(Json::as_bool) != Some(true) {
                let error = reply.get("error").and_then(Json::as_object);
                let field = |key: &str| error.and_then(|error| error.get(key)).and_then(Json::as_str);
                return Err(WebUIError::new(
                    field("message").unwrap_or("The command was rejected."),
                    field("code").unwrap_or("command_rejected"),
                ));
            }
            Ok(reply.get("result").cloned().unwrap_or(Json::Null))
        }
        .await;
        self.pending.lock().expect("pending lock").remove(&identity);
        outcome
    }

    /// `close()`: collect the child, reporting a nonzero exit or forced cleanup as failure.
    pub async fn close(&self) -> Result<(), WebUIError> {
        let mut child_slot = self.child.lock().await;
        let Some(child) = child_slot.as_mut() else { return Ok(()) };
        let mut failure: Option<&'static str> = None;
        if child.try_wait().ok().flatten().is_none() {
            let attempt = async {
                if self.reader_running() {
                    tokio::time::timeout(Duration::from_secs(10), self.call("shutdown", &JsonMap::new()))
                        .await
                        .map_err(|_| "timed out")?
                        .map_err(|_| "did not finish gracefully")?;
                } else {
                    self.stdin.lock().await.take();
                }
                tokio::time::timeout(Duration::from_secs(OWNER_EXIT_TIMEOUT), child.wait())
                    .await
                    .map_err(|_| "timed out")?
                    .map_err(|_| "did not finish gracefully")?;
                Ok::<(), &'static str>(())
            };
            if let Err(reason) = attempt.await {
                failure = Some(reason);
                // Only this host's own child.
                let _ = child.start_kill();
                let _ = child.wait().await;
            }
        }
        let status = child.try_wait().ok().flatten();
        let code = status.map(exit_code);
        if status.is_some() {
            if let Some(reader) = self.reader.lock().expect("reader lock").take() {
                reader.abort();
            }
            self.stdin.lock().await.take();
            *child_slot = None;
        }
        let shown = code.map_or_else(|| "None".to_owned(), |code| code.to_string());
        if let Some(reason) = failure {
            return Err(WebUIError::new(
                format!("Sylvia owner shutdown {reason} (exit {shown}). Saved work is retained; launch again to inspect it."),
                "owner_close_failed",
            ));
        }
        if code != Some(0) {
            return Err(WebUIError::new(
                format!("Sylvia owner did not close cleanly (exit {shown}). Saved work is retained; launch again to inspect it."),
                "owner_close_failed",
            ));
        }
        Ok(())
    }
}

const DISCONNECTED_UNKNOWN: &str =
    "The Sylvia owner disconnected. Reload to inspect saved work; restart Sylvia \
before reconnecting if its owner stopped. This request's outcome is unknown; it was not repeated.";

fn outcome_unknown() -> WebUIError {
    WebUIError::new(
        "The Sylvia owner connection failed before confirming the result. Restart Sylvia, then reconnect and \
         inspect saved work before trying again; this request was not repeated.",
        "outcome_unknown",
    )
}

/// Python's `returncode`: the exit code, or minus the signal that ended the process.
fn exit_code(status: std::process::ExitStatus) -> i32 {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(signal) = status.signal() {
            return -signal;
        }
    }
    status.code().unwrap_or(-1)
}

fn fail_pending(pending: &Pending, message: &str) {
    for (_, sender) in pending.lock().expect("pending lock").drain() {
        let _ = sender.send(Err(WebUIError::new(message, "outcome_unknown")));
    }
}

/// `_receive()`: hand each reply to its waiting request.
async fn receive(mut stdout: BufReader<ChildStdout>, pending: Pending) {
    loop {
        let Some(line) = read_line(&mut stdout).await else { break };
        if line.len() > MAX_BYTES {
            break;
        }
        let Some(reply) = parse_line(&line) else { break };
        let identity = reply.get("id").and_then(Json::as_str).filter(|identity| !identity.is_empty());
        let Some(identity) = identity else {
            fail_pending(
                &pending,
                "The Sylvia owner replied without a request ID. Reload to inspect saved work. The pending \
                 requests were not repeated.",
            );
            continue;
        };
        if let Some(sender) = pending.lock().expect("pending lock").remove(identity) {
            let _ = sender.send(Ok(reply));
        }
    }
    fail_pending(&pending, DISCONNECTED_UNKNOWN);
}
