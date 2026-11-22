//! The account runtimes' child processes, as `provider/_claude_runtime.py` and
//! `provider/_codex_rpc.py` start and talk to them. Lane 5.
//!
//! - **Starting.** Only the environment the caller builds reaches the child; its standard error
//!   is discarded, and on Windows it gets no console window (`CREATE_NO_WINDOW`). A Claude Code
//!   child on Linux dies with the owner ([`Spawn::dies_with_owner`], `_dies_with_owner`).
//! - **Text-mode pipes.** Python opens both pipes in text mode with UTF-8. A frame written is
//!   `json.dumps(message, ensure_ascii=True, allow_nan=False) + "\n"`, and text mode writes that
//!   `"\n"` as the platform's line separator (`"\r\n"` on Windows). Reading translates `"\r\n"`
//!   and `"\r"` to `"\n"` ([`TextLines`]). As Python's `TextIOWrapper` does, each read from the
//!   pipe (up to 8 KiB) is decoded as a whole, so a byte that is not UTF-8 fails the lines read
//!   with it as well.
//! - **Stopping.** `Popen.terminate()` (`SIGTERM`, or `TerminateProcess` on Windows), a wait of
//!   up to 3 seconds, then `kill()` and another wait ([`stop`]).
//! - **Captured runs.** `subprocess.run(..., capture_output=True, text=True, errors="replace",
//!   timeout=...)` ([`run_captured`]): the child inherits the owner's standard input, as Python's
//!   does, and is killed when the timeout passes.

#[cfg(target_os = "linux")]
mod linux;

use std::ffi::OsString;
use std::io::{self, BufRead, BufReader, Read};
use std::path::Path;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::time::{Duration, Instant};
use sylvia_base::Json;

/// What to start, and how.
pub struct Spawn<'a> {
    pub program: &'a OsString,
    pub arguments: &'a [OsString],
    pub workspace: &'a Path,
    /// The child's whole environment.
    pub environment: &'a [(OsString, OsString)],
    /// `_dies_with_owner()`: on Linux, the child is killed when the owner process dies.
    pub dies_with_owner: bool,
}

/// `process_options()`: no console window for the child on Windows.
pub fn hide_console(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        /// `subprocess.CREATE_NO_WINDOW`.
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    let _ = command;
}

/// `subprocess.Popen(..., stdin=PIPE, stdout=PIPE, stderr=DEVNULL, env=..., cwd=...)`.
pub fn spawn(spawn: &Spawn) -> io::Result<Child> {
    let mut command = Command::new(spawn.program);
    command
        .args(spawn.arguments)
        .current_dir(spawn.workspace)
        .env_clear()
        .envs(spawn.environment.iter().map(|(name, value)| (name, value)))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    hide_console(&mut command);
    #[cfg(target_os = "linux")]
    if spawn.dies_with_owner {
        linux::die_with_owner(&mut command);
        return linux::spawn_from_owner_thread(command);
    }
    command.spawn()
}

/// The line separator text mode writes for `"\n"` (`os.linesep`).
pub const LINE_SEPARATOR: &str = if cfg!(windows) { "\r\n" } else { "\n" };

/// One protocol frame as text mode writes it: `json.dumps(message, ensure_ascii=True,
/// allow_nan=False) + "\n"`, with the `"\n"` translated. (A JSON value here holds no `NaN`.)
pub fn frame(message: &Json) -> Vec<u8> {
    let mut text = sylvia_base::pyjson::dumps(message, sylvia_base::pyjson::Dump::DEFAULT);
    text.push_str(LINE_SEPARATOR);
    text.into_bytes()
}

/// A text-mode reader (`io.TextIOWrapper(pipe, encoding="utf-8", errors="strict")`): each read
/// decoded as strict UTF-8, with Python's universal newlines.
pub struct TextLines<R> {
    reader: BufReader<R>,
    /// Bytes of a character split across reads.
    pending: Vec<u8>,
    /// A `"\r"` at the end of a read, which may start a `"\r\n"`.
    pending_cr: bool,
    /// Decoded, translated text not yet returned.
    text: String,
    ended: bool,
}

/// Why a line could not be read.
#[derive(Debug)]
pub enum LineError {
    /// `OSError`.
    Io(io::Error),
    /// `UnicodeDecodeError`.
    Decode,
}

impl<R: Read> TextLines<R> {
    pub fn new(reader: R) -> Self {
        Self {
            reader: BufReader::new(reader),
            pending: Vec::new(),
            pending_cr: false,
            text: String::new(),
            ended: false,
        }
    }

    /// `IncrementalNewlineDecoder(translate=True)` over one decoded piece.
    fn translate(&mut self, mut piece: String, last: bool) {
        if std::mem::take(&mut self.pending_cr) {
            piece.insert(0, '\r');
        }
        if !last && piece.ends_with('\r') {
            piece.pop();
            self.pending_cr = true;
        }
        self.text.push_str(&piece.replace("\r\n", "\n").replace('\r', "\n"));
    }

    /// One read from the pipe, decoded. `false` at the end of the stream.
    fn fill(&mut self) -> Result<bool, LineError> {
        if self.ended {
            return Ok(false);
        }
        let chunk = self.reader.fill_buf().map_err(LineError::Io)?.to_vec();
        self.reader.consume(chunk.len());
        if chunk.is_empty() {
            self.ended = true;
            if !self.pending.is_empty() {
                return Err(LineError::Decode);
            }
            self.translate(String::new(), true);
            return Ok(false);
        }
        self.pending.extend_from_slice(&chunk);
        let bytes = std::mem::take(&mut self.pending);
        let decoded = match std::str::from_utf8(&bytes) {
            Ok(text) => text.to_owned(),
            Err(error) if error.error_len().is_none() => {
                // A character split across reads waits for the rest.
                self.pending = bytes[error.valid_up_to()..].to_vec();
                std::str::from_utf8(&bytes[..error.valid_up_to()]).expect("valid prefix").to_owned()
            }
            Err(_) => return Err(LineError::Decode),
        };
        self.translate(decoded, false);
        Ok(true)
    }

    /// `readline(limit)`: the next line ending in `"\n"` (translated), or the text left at the
    /// end of the stream without one; `None` at the end. With a `limit`, at most that many
    /// characters.
    pub fn read_line(&mut self, limit: Option<usize>) -> Result<Option<String>, LineError> {
        loop {
            let newline = self.text.find('\n').map(|index| index + 1);
            let limited = limit.and_then(|limit| self.text.char_indices().nth(limit).map(|(index, _)| index));
            let end = match (newline, limited) {
                (Some(newline), Some(limited)) => Some(newline.min(limited)),
                (newline, limited) => newline.or(limited),
            };
            if let Some(end) = end {
                return Ok(Some(self.text.drain(..end).collect()));
            }
            if !self.fill()? {
                return Ok((!self.text.is_empty()).then(|| std::mem::take(&mut self.text)));
            }
        }
    }
}

/// When a child has exited, without waiting (`Popen.poll()`).
pub fn exited(child: &mut Child) -> bool {
    !matches!(child.try_wait(), Ok(None))
}

fn wait_for(child: &mut Child, limit: Duration) -> Option<ExitStatus> {
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Some(status),
            Ok(None) if started.elapsed() < limit => std::thread::sleep(Duration::from_millis(10)),
            _ => return None,
        }
    }
}

/// `Popen.terminate()`: `SIGTERM`, or `TerminateProcess` on Windows.
pub fn terminate(child: &mut Child) {
    #[cfg(target_os = "linux")]
    {
        if let Some(pid) = rustix::process::Pid::from_raw(child.id() as i32) {
            let _ = rustix::process::kill_process(pid, rustix::process::Signal::TERM);
        }
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = child.kill();
    }
}

/// `close()`'s stop: `terminate()`, `wait(timeout=3)`, then `kill()` and `wait(timeout=3)`.
pub fn stop(child: &mut Child) {
    if exited(child) {
        return;
    }
    terminate(child);
    if wait_for(child, Duration::from_secs(3)).is_none() {
        let _ = child.kill();
        let _ = wait_for(child, Duration::from_secs(3));
    }
}

/// What a captured run gave.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Captured {
    /// `returncode`; `None` for a child ended by a signal (a negative code in Python).
    pub code: Option<i32>,
    /// Standard output decoded as UTF-8 with replacement, newlines translated.
    pub stdout: String,
}

/// How a captured run failed: `OSError` or `subprocess.TimeoutExpired`.
#[derive(Debug)]
pub enum RunError {
    Start(io::Error),
    Timeout,
}

/// Text mode's reading of a whole output: decoded with `errors="replace"`, `"\r\n"` and `"\r"`
/// translated to `"\n"`.
pub fn decode_output(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).replace("\r\n", "\n").replace('\r', "\n")
}

/// `subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
/// errors="replace", timeout=..., env=..., **process_options())`.
pub fn run_captured(
    program: &OsString,
    arguments: &[OsString],
    environment: &[(OsString, OsString)],
    timeout: Duration,
) -> Result<Captured, RunError> {
    let mut command = Command::new(program);
    command
        .args(arguments)
        .env_clear()
        .envs(environment.iter().map(|(name, value)| (name, value)))
        .stdin(Stdio::inherit())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    hide_console(&mut command);
    let mut child = command.spawn().map_err(RunError::Start)?;
    let drain = |pipe: Option<Box<dyn Read + Send>>| {
        std::thread::spawn(move || {
            let mut bytes = Vec::new();
            if let Some(mut pipe) = pipe {
                let _ = pipe.read_to_end(&mut bytes);
            }
            bytes
        })
    };
    let stdout = drain(child.stdout.take().map(|pipe| Box::new(pipe) as Box<dyn Read + Send>));
    let stderr = drain(child.stderr.take().map(|pipe| Box::new(pipe) as Box<dyn Read + Send>));
    let Some(status) = wait_for(&mut child, timeout) else {
        let _ = child.kill();
        let _ = child.wait();
        return Err(RunError::Timeout);
    };
    let output = stdout.join().unwrap_or_default();
    let _ = stderr.join();
    Ok(Captured { code: status.code(), stdout: decode_output(&output) })
}

/// The process's environment as Python's `os.environ` holds it: on Windows the names are
/// upper-cased (`os.environ` stores them so).
pub fn process_environment() -> Vec<(OsString, OsString)> {
    std::env::vars_os()
        .map(|(name, value)| {
            if cfg!(windows) {
                (OsString::from(name.to_string_lossy().to_uppercase()), value)
            } else {
                (name, value)
            }
        })
        .collect()
}

/// A Python dict assignment on an environment: the same name keeps its place.
pub fn set_variable(environment: &mut Vec<(OsString, OsString)>, name: &str, value: impl Into<OsString>) {
    let value = value.into();
    match environment.iter_mut().find(|(existing, _)| existing == name) {
        Some(slot) => slot.1 = value,
        None => environment.push((OsString::from(name), value)),
    }
}

/// `{k: v for k, v in source.items() if k.lower() in allowed}`.
pub fn allowed_variables(source: &[(OsString, OsString)], allowed: &[&str]) -> Vec<(OsString, OsString)> {
    source
        .iter()
        .filter(|(name, _)| name.to_str().is_some_and(|name| allowed.contains(&name.to_lowercase().as_str())))
        .cloned()
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lines(data: &[u8], limit: Option<usize>) -> Vec<Result<String, String>> {
        let mut reader = TextLines::new(data);
        let mut out = Vec::new();
        loop {
            match reader.read_line(limit) {
                Ok(Some(line)) => out.push(Ok(line)),
                Ok(None) => return out,
                Err(error) => {
                    out.push(Err(format!("{error:?}")));
                    return out;
                }
            }
        }
    }

    #[test]
    fn universal_newlines_as_text_mode_reads_them() {
        assert_eq!(
            lines(b"a\nb\r\nc\rd", None),
            vec![Ok("a\n".into()), Ok("b\n".into()), Ok("c\n".into()), Ok("d".into())]
        );
        assert_eq!(
            lines("\u{e9}t\u{e9}\n".as_bytes(), Some(2)),
            vec![Ok("\u{e9}t".into()), Ok("\u{e9}\n".into())]
        );
        assert_eq!(lines(b"\xff\n", None), vec![Err("Decode".into())]);
        // The bad byte fails the whole read it arrived in, as Python's wrapper does.
        assert_eq!(lines(b"ok\n\xff\n", None), vec![Err("Decode".into())]);
    }

    #[test]
    fn frames_end_with_the_platform_separator() {
        assert_eq!(LINE_SEPARATOR, if cfg!(windows) { "\r\n" } else { "\n" });
        let frame = frame(&serde_json::json!({"type": "user", "text": "\u{e9}"}));
        assert_eq!(
            String::from_utf8(frame).unwrap(),
            format!("{{\"type\": \"user\", \"text\": \"\\u00e9\"}}{LINE_SEPARATOR}")
        );
        assert_eq!(decode_output(b"1\r\n2\r3\xff"), "1\n2\n3\u{fffd}");
    }
}
