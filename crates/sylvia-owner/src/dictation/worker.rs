//! `sylvia_gm/local_dictation_process.py`: the bounded speech-worker line protocol. Only an
//! explicit Start opens an input stream.
//!
//! Input is read as Python's `sys.stdin` reads it after `reconfigure(encoding="utf-8",
//! errors="strict")`: on Windows with universal newlines (`\r\n` and a lone `\r` end a line and
//! read as `\n`), elsewhere split at `\n` only. Replies are written as `sys.stdout` writes them:
//! `\n` becomes `\r\n` on Windows.
//!
//! One timing differs from Python and cannot be observed by the desktop: Python decodes stdin a
//! pipe read at a time, so an invalid UTF-8 byte fails the `readline` of the first line in the
//! same read. Here it fails the `readline` of its own line. The desktop writes one request and
//! waits for its reply, so each read holds one line and the two agree.

use super::{
    input_devices, model_ready, prepare_model, transcribe, Device, Failure, ModelSpec, Outcome, Raised,
    Recognizer, Recording, Runtime,
};
use serde_json::json;
use std::io::{BufRead, Write};
use std::path::Path;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::Json;

/// A `TextIO` read with `readline(limit)`. `Err(Raised)` is a `UnicodeDecodeError`.
pub trait LineSource {
    /// Up to `limit` characters, through the first newline; empty at the end of input.
    fn readline(&mut self, limit: usize) -> Result<String, Raised>;
}

/// Python's text stdin over a byte reader.
pub struct TextInput<R> {
    reader: R,
    universal: bool,
}

impl<R: BufRead> TextInput<R> {
    /// The platform's `sys.stdin` newline mode.
    pub fn stdin(reader: R) -> Self {
        Self { reader, universal: cfg!(windows) }
    }

    /// An explicit newline mode: `universal` is `newline=None`, otherwise `newline="\n"`.
    pub fn with_newlines(reader: R, universal: bool) -> Self {
        Self { reader, universal }
    }

    fn next_byte(&mut self) -> Option<u8> {
        loop {
            let buffer = match self.reader.fill_buf() {
                Ok(buffer) => buffer,
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(_) => return None,
            };
            let byte = *buffer.first()?;
            self.reader.consume(1);
            return Some(byte);
        }
    }

    fn peek_byte(&mut self) -> Option<u8> {
        loop {
            match self.reader.fill_buf() {
                Ok(buffer) => return buffer.first().copied(),
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
                Err(_) => return None,
            }
        }
    }

    /// One strict UTF-8 character, `Ok(None)` at the end of input.
    fn next_char(&mut self) -> Result<Option<char>, Raised> {
        let Some(first) = self.next_byte() else { return Ok(None) };
        let width = match first {
            0x00..=0x7F => return Ok(Some(char::from(first))),
            0xC2..=0xDF => 2,
            0xE0..=0xEF => 3,
            0xF0..=0xF4 => 4,
            _ => return Err(Raised),
        };
        let mut bytes = vec![first];
        for _ in 1..width {
            let next = self.peek_byte().ok_or(Raised)?;
            if next & 0xC0 != 0x80 {
                return Err(Raised);
            }
            self.reader.consume(1);
            bytes.push(next);
        }
        std::str::from_utf8(&bytes).map_err(|_| Raised)?.chars().next().ok_or(Raised).map(Some)
    }
}

impl<R: BufRead> LineSource for TextInput<R> {
    fn readline(&mut self, limit: usize) -> Result<String, Raised> {
        let mut line = String::new();
        let mut count = 0;
        while count < limit {
            let Some(character) = self.next_char()? else { break };
            count += 1;
            if character == '\n' {
                line.push('\n');
                break;
            }
            if self.universal && character == '\r' {
                if self.peek_byte() == Some(b'\n') {
                    self.reader.consume(1);
                }
                line.push('\n');
                break;
            }
            line.push(character);
        }
        Ok(line)
    }
}

/// Python's text stdout: one JSON line per reply, flushed.
pub struct Replies<W> {
    output: W,
    newline: &'static str,
}

impl<W: Write> Replies<W> {
    /// The platform's `sys.stdout` newline translation.
    pub fn stdout(output: W) -> Self {
        Self { output, newline: if cfg!(windows) { "\r\n" } else { "\n" } }
    }

    /// An explicit line ending.
    pub fn with_newline(output: W, newline: &'static str) -> Self {
        Self { output, newline }
    }

    /// `reply(value)`.
    pub fn reply(&mut self, value: &Json) -> std::io::Result<()> {
        let mut line = pyjson::dumps(value, Dump::WIRE);
        line.push_str(self.newline);
        self.output.write_all(line.as_bytes())?;
        self.output.flush()
    }

    /// The writer, for tests.
    pub fn into_inner(self) -> W {
        self.output
    }
}

/// A worker's fixed inputs: its cache folder and the model files it checks.
pub struct Worker<'a> {
    /// The profile's model folder (`args[0]`).
    pub directory: &'a Path,
    /// The model files ([`ModelSpec::shipped`] in the product).
    pub spec: &'a ModelSpec,
}

/// One `capabilities` device: Python's `{"id", "label"}`, and `previous` (the identity hashes a
/// saved PortAudio-era id of it may carry, difference row 38) when the audio library gives any.
fn device_json(device: &Device) -> Json {
    let mut value = json!({"id": device.id, "label": device.label});
    if !device.previous.is_empty() {
        value["previous"] = json!(device.previous);
    }
    value
}

/// `capabilities(directory)`.
pub fn capabilities(worker: &Worker<'_>, runtime: &mut dyn Runtime) -> Outcome<Json> {
    let languages = runtime.languages();
    let devices: Vec<Device> = match (runtime.sound(), languages) {
        (Some(sound), Some(_)) => input_devices(sound).unwrap_or_default(),
        _ => return Err(Failure::Speech("runtime")),
    };
    let languages = languages.unwrap_or_default();
    let mut engines =
        vec![json!({"id": "auto", "language": "auto", "label": "Detect language automatically"})];
    engines
        .extend(languages.iter().take(99).map(|code| json!({"id": code, "language": code, "label": code})));
    Ok(json!({
        "engines": engines,
        "devices": devices.iter().map(device_json).collect::<Vec<_>>(),
        "ready": model_ready(worker.directory, worker.spec),
        "model": {
            "id": super::MODEL_ID,
            "revision": super::MODEL_REVISION,
            "download_bytes": worker.spec.download_bytes(),
            "device": "cpu",
            "compute_type": "int8",
        },
    }))
}

/// Why the worker stops reading: a protocol failure inside `serve` or a failed reply write.
enum Stop {
    Failed(Failure),
    Write,
}

impl From<Failure> for Stop {
    fn from(failure: Failure) -> Self {
        Stop::Failed(failure)
    }
}

/// The parsed request object, or the exception `json.loads(line)` and `request.get` raise.
fn parse(line: &str) -> Outcome<serde_json::Map<String, Json>> {
    match pyjson::loads(line) {
        Ok(Json::Object(request)) => Ok(request),
        _ => Err(Failure::Other),
    }
}

fn line_too_long(line: &str) -> bool {
    !line.ends_with('\n') || line.len() > 8192
}

/// `serve(source, output, directory)`: one request, or a session.
pub fn serve<R: LineSource, W: Write>(
    source: &mut R,
    output: &mut Replies<W>,
    worker: &Worker<'_>,
    runtime: &mut dyn Runtime,
) -> i32 {
    let mut recording: Option<Recording> = None;
    let result = serve_one(source, output, worker, runtime, &mut recording);
    let code = match result {
        Ok(code) => Ok(code),
        Err(Stop::Failed(failure)) => {
            output.reply(&json!({"error": failure.code()})).map(|()| 1).map_err(|_| Raised)
        }
        Err(Stop::Write) => Err(Raised),
    };
    if let Some(mut recording) = recording {
        let _ = recording.cancel();
    }
    code.unwrap_or(1)
}

fn serve_one<R: LineSource, W: Write>(
    source: &mut R,
    output: &mut Replies<W>,
    worker: &Worker<'_>,
    runtime: &mut dyn Runtime,
    recording: &mut Option<Recording>,
) -> Result<i32, Stop> {
    let line = source.readline(8193).map_err(|Raised| Failure::Other)?;
    if line_too_long(&line) {
        return Err(Failure::Speech("unavailable").into());
    }
    let request = parse(&line)?;
    let mode = request.get("mode").and_then(Json::as_str);
    match mode {
        Some("session") => return serve_session(source, output, worker, runtime),
        Some("capabilities") => {
            let reply = capabilities(worker, runtime)?;
            output.reply(&reply).map_err(|_| Stop::Write)?;
        }
        Some("prepare") => {
            prepare_model(worker.directory, worker.spec, runtime)?;
            output.reply(&json!({"status": "ready"})).map_err(|_| Stop::Write)?;
        }
        Some("start") => {
            if !model_ready(worker.directory, worker.spec) {
                return Err(Failure::Speech("model").into());
            }
            let (Some(Json::String(language)), Some(device)) = (
                request.get("engine"),
                match request.get("device") {
                    None => Some("default"),
                    Some(device) => device.as_str().map(|device| device as &str),
                },
            ) else {
                return Err(Failure::Speech("unavailable").into());
            };
            let codes = runtime.languages().ok_or(Failure::Other)?;
            if language != "auto" && !codes[..99].contains(&language.as_str()) {
                return Err(Failure::Speech("language").into());
            }
            let sound = runtime.sound().ok_or(Failure::Other)?;
            *recording = Some(Recording::new(device, sound)?);
            if let Some(capture) = recording.as_mut() {
                capture.start(sound)?;
            }
            output.reply(&json!({"status": "recording"})).map_err(|_| Stop::Write)?;
            let action = source.readline(16).map_err(|Raised| Failure::Other)?;
            if action == "stop\n" {
                let audio = recording.as_mut().map_or(Err(Failure::Speech("device")), Recording::stop)?;
                let text =
                    transcribe(&audio, worker.directory, language, worker.spec, runtime.model_factory())?;
                output.reply(&json!({"text": text})).map_err(|_| Stop::Write)?;
            }
        }
        _ => return Err(Failure::Speech("unavailable").into()),
    }
    Ok(0)
}

fn valid_identity(identity: &Json) -> Option<&str> {
    let identity = identity.as_str()?;
    let length = identity.chars().count();
    (0 < length && length <= 64 && identity.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'))
        .then_some(identity)
}

/// `serve_session(source, reply, directory)`: serialised, identified attempts; only the model
/// stays between them while the microphone is idle.
fn serve_session<R: LineSource, W: Write>(
    source: &mut R,
    output: &mut Replies<W>,
    worker: &Worker<'_>,
    runtime: &mut dyn Runtime,
) -> Result<i32, Stop> {
    let mut session = Session {
        recognizer: Recognizer::new(worker.directory),
        recording: None,
        active: None,
        language: "auto".into(),
    };
    let result = session.run(source, output, worker, runtime);
    if let Some(mut recording) = session.recording.take() {
        let _ = recording.cancel();
    }
    result
}

struct Session {
    recognizer: Recognizer,
    recording: Option<Recording>,
    active: Option<String>,
    language: String,
}

impl Session {
    fn run<R: LineSource, W: Write>(
        &mut self,
        source: &mut R,
        output: &mut Replies<W>,
        worker: &Worker<'_>,
        runtime: &mut dyn Runtime,
    ) -> Result<i32, Stop> {
        output.reply(&json!({"status": "ready", "protocol": 1})).map_err(|_| Stop::Write)?;
        loop {
            let line = source.readline(8193).map_err(|Raised| Failure::Other)?;
            if line.is_empty() {
                return Ok(0);
            }
            if line_too_long(&line) {
                return Ok(1);
            }
            let request = parse(&line)?;
            let Some(identity) = request.get("id").and_then(valid_identity).map(str::to_string) else {
                return Ok(1);
            };
            let result = match self.attempt(&identity, &request, worker, runtime) {
                Ok(result) => result,
                Err(failure) => json!({"error": failure.code()}),
            };
            let mut reply = serde_json::Map::new();
            reply.insert("id".into(), Json::String(identity));
            if let Json::Object(fields) = result {
                reply.extend(fields);
            }
            reply.insert("idle".into(), Json::Bool(self.recording.is_none()));
            output.reply(&Json::Object(reply)).map_err(|_| Stop::Write)?;
        }
    }

    fn attempt(
        &mut self,
        identity: &str,
        request: &serde_json::Map<String, Json>,
        worker: &Worker<'_>,
        runtime: &mut dyn Runtime,
    ) -> Outcome<Json> {
        let mode = request.get("mode").and_then(Json::as_str);
        match mode {
            Some("capabilities") if self.recording.is_none() => capabilities(worker, runtime),
            Some("start") if self.recording.is_none() => {
                if !model_ready(worker.directory, worker.spec) {
                    return Err(Failure::Speech("model"));
                }
                let codes = runtime.languages().ok_or(Failure::Other)?;
                let language = match request.get("engine") {
                    Some(Json::String(language))
                        if language == "auto" || codes[..99].contains(&language.as_str()) =>
                    {
                        language.clone()
                    }
                    _ => return Err(Failure::Speech("language")),
                };
                let device = match request.get("device") {
                    None => "default",
                    Some(Json::String(device)) => device.as_str(),
                    Some(_) => return Err(Failure::Speech("device")),
                };
                let sound = runtime.sound().ok_or(Failure::Other)?;
                let mut candidate = Recording::new(device, sound)?;
                if let Err(failure) = candidate.start(sound) {
                    candidate.cancel()?;
                    return Err(failure);
                }
                self.recording = Some(candidate);
                self.active = Some(identity.to_string());
                self.language = language;
                Ok(json!({"status": "recording"}))
            }
            Some("stop") if self.recording.is_some() && self.active.as_deref() == Some(identity) => {
                let mut capture = self.recording.take().ok_or(Failure::Other)?;
                self.active = None;
                let decoded = capture.stop().and_then(|audio| {
                    self.recognizer.transcribe(&audio, &self.language, worker.spec, runtime.model_factory())
                });
                // `finally: capture.cancel()`: its exception replaces the decode's outcome.
                capture.cancel()?;
                let text = decoded?;
                Ok(json!({"text": text}))
            }
            _ => Err(Failure::Speech("unavailable")),
        }
    }
}

/// `main(args)`: `sylvia-gm-owner --dictation-worker <model folder>` on the real stdin and
/// stdout.
pub fn main(args: &[std::ffi::OsString], runtime: &mut dyn Runtime) -> i32 {
    let [directory] = args else { return 2 };
    let directory = Path::new(directory);
    let spec = ModelSpec::shipped();
    let worker = Worker { directory, spec: &spec };
    let stdin = std::io::stdin();
    let mut source = TextInput::stdin(stdin.lock());
    let mut output = Replies::stdout(std::io::stdout().lock());
    serve(&mut source, &mut output, &worker, runtime)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dictation::Absent;
    use std::io::Cursor;

    fn lines(input: &[u8], universal: bool, limit: usize) -> Vec<Result<String, Raised>> {
        let mut source = TextInput::with_newlines(Cursor::new(input.to_vec()), universal);
        let mut read = Vec::new();
        loop {
            let line = source.readline(limit);
            let end = matches!(&line, Ok(text) if text.is_empty()) || line.is_err();
            read.push(line);
            if end {
                return read;
            }
        }
    }

    #[test]
    fn universal_newlines_read_crlf_and_cr_as_newline() {
        let read = lines(b"a\r\nb\rc\nd", true, 8193);
        assert_eq!(
            read,
            [Ok("a\n".into()), Ok("b\n".into()), Ok("c\n".into()), Ok("d".into()), Ok(String::new())]
        );
    }

    #[test]
    fn newline_mode_splits_at_newline_only() {
        let read = lines(b"a\r\nb\rc\n", false, 8193);
        assert_eq!(read, [Ok("a\r\n".into()), Ok("b\rc\n".into()), Ok(String::new())]);
    }

    #[test]
    fn the_limit_counts_characters() {
        let mut source =
            TextInput::with_newlines(Cursor::new("\u{e9}\u{e9}\u{e9}\n".as_bytes().to_vec()), true);
        assert_eq!(source.readline(2), Ok("\u{e9}\u{e9}".into()));
        assert_eq!(source.readline(16), Ok("\u{e9}\n".into()));
    }

    #[test]
    fn invalid_utf8_fails_its_line() {
        assert_eq!(lines(b"ok\n\xff\n", true, 8193)[1], Err(Raised));
        assert_eq!(lines(b"\xc3", true, 8193)[0], Err(Raised));
        assert_eq!(lines(b"\xed\xa0\x80\n", true, 8193)[0], Err(Raised));
    }

    fn answer(input: &str, directory: &Path, spec: &ModelSpec) -> (i32, String) {
        let mut source = TextInput::with_newlines(Cursor::new(input.as_bytes().to_vec()), false);
        let mut output = Replies::with_newline(Vec::new(), "\n");
        let worker = Worker { directory, spec };
        let code = serve(&mut source, &mut output, &worker, &mut Absent::offline());
        (code, String::from_utf8(output.into_inner()).unwrap())
    }

    #[test]
    fn without_the_speech_runtime_the_worker_answers_as_pythons_excluded_build() {
        let directory = std::env::temp_dir().join(format!("sylvia-dictation-absent-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&directory);
        std::fs::create_dir_all(&directory).unwrap();
        std::fs::write(directory.join("model.bin"), b"m").unwrap();
        let spec = ModelSpec {
            files: vec![("model.bin".into(), 1, hex::encode(<sha2::Sha256 as sha2::Digest>::digest(b"m")))],
        };
        assert_eq!(
            answer("{\"mode\":\"capabilities\"}\n", &directory, &spec),
            (1, "{\"error\":\"runtime\"}\n".into())
        );
        assert_eq!(
            answer("{\"mode\":\"start\",\"engine\":\"en\"}\n", &directory, &spec),
            (1, "{\"error\":\"provider\"}\n".into())
        );
        assert_eq!(
            answer("{\"mode\":\"session\"}\n{\"id\":\"a\",\"mode\":\"capabilities\"}\n", &directory, &spec),
            (
                0,
                "{\"status\":\"ready\",\"protocol\":1}\n{\"id\":\"a\",\"error\":\"runtime\",\"idle\":true}\n"
                    .into()
            )
        );
        // Nothing to download: the model is already there.
        assert_eq!(
            answer("{\"mode\":\"prepare\"}\n", &directory, &spec),
            (0, "{\"status\":\"ready\"}\n".into())
        );
        std::fs::remove_file(directory.join("model.bin")).unwrap();
        // Offline, a download fails as an unreachable network does.
        assert_eq!(
            answer("{\"mode\":\"prepare\"}\n", &directory, &spec),
            (1, "{\"error\":\"download\"}\n".into())
        );
        let _ = std::fs::remove_dir_all(&directory);
    }
}
