//! Start an owner executable and speak its line protocol, as the desktop shell does.

use hmac::{Hmac, Mac};
use sha2::Sha256;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::time::{Duration, Instant};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};

const HANDSHAKE_TRANSCRIPT: &[u8] = b"sylvia-gm-f6-local-owner-handshake-v1";

/// How to start the owner under test.
#[derive(Debug, Clone, Default)]
pub struct OwnerCommand {
    /// Program to run.
    pub program: String,
    /// Arguments.
    pub args: Vec<String>,
    /// Extra environment variables.
    pub env: Vec<(String, String)>,
}

/// A running owner.
pub struct Owner {
    child: Child,
    stdin: Option<ChildStdin>,
    lines: Receiver<Option<Json>>,
    /// Replies that arrived while waiting for a different request.
    parked: HashMap<String, Json>,
    /// The `ready` message, without the handshake proof.
    pub ready: Json,
}

impl Owner {
    /// Start the owner, send the launch envelope and verify its handshake reply.
    pub fn launch(
        command: &OwnerCommand,
        database: &Path,
        owner_id: &str,
        timeout: Duration,
    ) -> Result<Self, String> {
        // The owner runs inside the data folder. On Linux a relative program path is looked
        // up after that change of folder (on Windows before it), so make a path absolute here.
        let program = std::path::Path::new(&command.program);
        let program = if program.components().count() > 1 && program.is_relative() {
            std::env::current_dir().map_err(|error| error.to_string())?.join(program)
        } else {
            program.to_path_buf()
        };
        let mut process = Command::new(&program);
        process
            .args(&command.args)
            .envs(command.env.iter().cloned())
            .current_dir(database.parent().ok_or("database path has no parent folder")?)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        let mut child =
            process.spawn().map_err(|error| format!("could not start {}: {error}", command.program))?;
        let stdin = child.stdin.take().expect("stdin is piped");
        let stdout = child.stdout.take().expect("stdout is piped");

        let (sender, lines) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                let value = serde_json::from_str::<Json>(&line)
                    .unwrap_or_else(|_| Json::String(format!("<<not JSON>> {line}")));
                if sender.send(Some(value)).is_err() {
                    return;
                }
            }
            let _ = sender.send(None);
        });

        let mut secret = [0u8; 32];
        getrandom::getrandom(&mut secret).map_err(|error| format!("no random source: {error}"))?;
        let mut mac = Hmac::<Sha256>::new_from_slice(&secret).expect("HMAC accepts any key length");
        mac.update(HANDSHAKE_TRANSCRIPT);
        let expected = hex::encode(mac.finalize().into_bytes());

        let mut owner = Self { child, stdin: Some(stdin), lines, parked: HashMap::new(), ready: Json::Null };
        let mut envelope = JsonMap::new();
        envelope.insert("type".into(), "launch".into());
        envelope.insert("secret_hex".into(), hex::encode(secret).into());
        envelope.insert("database_path".into(), database.to_string_lossy().into_owned().into());
        envelope.insert("owner_id".into(), owner_id.into());
        owner.send(&Json::Object(envelope))?;

        let mut ready = match owner.lines.recv_timeout(timeout) {
            Ok(Some(ready)) => ready,
            Ok(None) => return Err("the owner exited before reporting ready".into()),
            Err(_) => return Err(format!("the owner was not ready within {} s", timeout.as_secs())),
        };
        if ready.get("ok") != Some(&Json::Bool(true)) {
            return Err(format!("the owner rejected the launch: {ready}"));
        }
        if ready.get("response_hex").and_then(Json::as_str) != Some(expected.as_str()) {
            return Err("the owner's handshake reply does not match the launch secret".into());
        }
        // The proof is per launch; keep only that it verified.
        ready["response_hex"] = Json::String("<verified>".into());
        owner.ready = ready;
        Ok(owner)
    }

    fn send(&mut self, message: &Json) -> Result<(), String> {
        let stdin = self.stdin.as_mut().ok_or("the owner's input is already closed")?;
        // The shell writes plain compact JSON; the owner must accept any valid encoding.
        let mut line = pyjson::dumps(message, Dump::WIRE);
        line.push('\n');
        stdin
            .write_all(line.as_bytes())
            .and_then(|()| stdin.flush())
            .map_err(|error| format!("could not write to the owner: {error}"))
    }

    /// Send a request without waiting for its reply.
    pub fn request(&mut self, id: &str, method: &str, params: &Json) -> Result<(), String> {
        let mut request = JsonMap::new();
        request.insert("id".into(), id.into());
        request.insert("method".into(), method.into());
        request.insert("params".into(), params.clone());
        self.send(&Json::Object(request))
    }

    /// Wait for the reply to `id`. Replies to other requests are kept for their own waiters.
    pub fn reply(&mut self, id: &str, timeout: Duration) -> Result<Json, String> {
        if let Some(reply) = self.parked.remove(id) {
            return Ok(reply);
        }
        let deadline = Instant::now() + timeout;
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            match self.lines.recv_timeout(remaining) {
                Ok(Some(reply)) => match reply.get("id").and_then(Json::as_str) {
                    Some(found) if found == id => return Ok(reply),
                    Some(found) => {
                        self.parked.insert(found.to_owned(), reply);
                    }
                    // A reply without an id belongs to whichever request was malformed; hand it
                    // to the current waiter, which is the request that caused it.
                    None => return Ok(reply),
                },
                Ok(None) => return Err(format!("the owner exited before answering {id}")),
                Err(RecvTimeoutError::Timeout) => {
                    return Err(format!("no reply to {id} within {} s", timeout.as_secs()))
                }
                Err(RecvTimeoutError::Disconnected) => {
                    return Err(format!("the owner's output closed before answering {id}"))
                }
            }
        }
    }

    /// Close the owner's input and wait for it to exit. Returns its exit code.
    pub fn close(mut self, timeout: Duration) -> Result<Option<i32>, String> {
        drop(self.stdin.take());
        let deadline = Instant::now() + timeout;
        loop {
            match self.child.try_wait() {
                Ok(Some(status)) => return Ok(status.code()),
                Ok(None) if Instant::now() >= deadline => {
                    // Only this tool's own child; never another process.
                    let _ = self.child.kill();
                    let _ = self.child.wait();
                    return Err(format!(
                        "the owner did not exit within {} s and was stopped",
                        timeout.as_secs()
                    ));
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(50)),
                Err(error) => return Err(format!("could not wait for the owner: {error}")),
            }
        }
    }
}
