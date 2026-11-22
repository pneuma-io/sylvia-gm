//! The dictation worker against vectors generated from the Python snapshot by
//! `vectors/generate_dictation_vectors.py` (CPython 3.12.11 on Windows, NumPy 2.5.4).
//!
//! Each scenario drives `sylvia_owner::dictation::worker::serve` with stand-ins that behave as
//! the generator's stand-ins for `sounddevice`, `faster_whisper`, `urlopen` and `disk_usage`, and
//! compares the reply bytes, the exit code, every call on the stand-ins and the files left in the
//! model folder. `processes` runs the real `sylvia-gm-owner --dictation-worker` against the real
//! Python worker's recorded output. Desktop dictation is Windows-only, and the vectors carry
//! Windows' `\r\n` replies and universal-newline input.
#![cfg(windows)]

mod support;

use serde_json::{json, Value as J};
use sha2::{Digest, Sha256};
use std::collections::VecDeque;
use std::io::Cursor;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use support::{first_difference, temporary_folder};
use sylvia_owner::dictation::worker::{serve, LineSource, Replies, TextInput, Worker};
use sylvia_owner::dictation::{
    device_id, CaptureSink, DecodeOptions, DeviceRow, Fetch, FetchError, Flow, LoadOptions, Model,
    ModelFactory, ModelSpec, Raised, Response, Runtime, Segment, Segments, Sound, Stream, StreamError,
    LANGUAGE_CODES,
};

fn vectors() -> &'static J {
    static VECTORS: std::sync::OnceLock<J> = std::sync::OnceLock::new();
    VECTORS.get_or_init(|| {
        serde_json::from_str(include_str!("vectors/dictation_vectors.json")).expect("valid JSON")
    })
}

type Calls = Arc<Mutex<Vec<J>>>;

fn log(calls: &Calls, call: J) {
    calls.lock().unwrap().push(call);
}

struct SoundState {
    rows: J,
    hosts: J,
    default: i64,
    fail_initialize: bool,
    fail: serde_json::Map<String, J>,
    deliveries: J,
    initialized: i64,
}

impl SoundState {
    fn apply(&mut self, mutation: &J) {
        for (key, value) in mutation.as_object().unwrap() {
            match key.as_str() {
                "rows" => self.rows = value.clone(),
                "hosts" => self.hosts = value.clone(),
                "default" => self.default = value.as_i64().unwrap(),
                "fail_initialize" => self.fail_initialize = value.as_bool().unwrap(),
                "fail" => self.fail = value.as_object().unwrap().clone(),
                "deliveries" => self.deliveries = value.clone(),
                _ => {}
            }
        }
    }

    fn failure(&self, name: &str) -> Option<StreamError> {
        match self.fail.get(name).and_then(J::as_str) {
            Some("permission") => Some(StreamError::Permission),
            Some(_) => Some(StreamError::Other),
            None => None,
        }
    }
}

struct FakeSound {
    state: Arc<Mutex<SoundState>>,
    calls: Calls,
}

struct FakeStream {
    state: Arc<Mutex<SoundState>>,
    calls: Calls,
    sink: Arc<CaptureSink>,
}

fn sample(value: &J) -> f32 {
    match value {
        J::String(text) => match text.as_str() {
            "nan" => f32::NAN,
            "inf" => f32::INFINITY,
            "-inf" => f32::NEG_INFINITY,
            other => panic!("sample {other}"),
        },
        number => number.as_f64().unwrap() as f32,
    }
}

impl FakeStream {
    fn plain(&self, name: &str) -> Result<(), Raised> {
        log(&self.calls, json!([name]));
        match self.state.lock().unwrap().failure(name) {
            Some(_) => Err(Raised),
            None => Ok(()),
        }
    }
}

impl Stream for FakeStream {
    fn start(&mut self) -> Result<(), StreamError> {
        log(&self.calls, json!(["start"]));
        let (failure, deliveries) = {
            let state = self.state.lock().unwrap();
            (state.failure("start"), state.deliveries.clone())
        };
        if let Some(failure) = failure {
            return Err(failure);
        }
        for delivery in deliveries.as_array().unwrap() {
            let data: Vec<f32> = match delivery.get("values") {
                Some(values) => values.as_array().unwrap().iter().map(sample).collect(),
                None => vec![0.0; delivery["frames"].as_u64().unwrap() as usize],
            };
            let status = delivery.get("status").and_then(J::as_bool).unwrap_or(false);
            log(&self.calls, json!(["deliver", data.len(), status]));
            if self.sink.deliver(&data, status) == Flow::Stop {
                log(&self.calls, json!(["callback_stop"]));
                break;
            }
        }
        Ok(())
    }
    fn stop(&mut self) -> Result<(), Raised> {
        self.plain("stop")
    }
    fn abort(&mut self) -> Result<(), Raised> {
        self.plain("abort")
    }
    fn close(&mut self) -> Result<(), Raised> {
        self.plain("close")
    }
}

impl Sound for FakeSound {
    fn initialized(&self) -> bool {
        self.state.lock().unwrap().initialized != 0
    }
    fn terminate(&mut self) -> Result<(), Raised> {
        log(&self.calls, json!(["terminate"]));
        self.state.lock().unwrap().initialized = 0;
        Ok(())
    }
    fn initialize(&mut self) -> Result<(), Raised> {
        log(&self.calls, json!(["initialize"]));
        let mut state = self.state.lock().unwrap();
        if state.fail_initialize {
            return Err(Raised);
        }
        state.initialized = 1;
        Ok(())
    }
    fn query_devices(&mut self) -> Result<Vec<DeviceRow>, Raised> {
        let state = self.state.lock().unwrap();
        Ok(state
            .rows
            .as_array()
            .unwrap()
            .iter()
            .map(|row| DeviceRow {
                name: row["name"].as_str().unwrap().to_string(),
                max_input_channels: row["max_input_channels"].as_i64().unwrap(),
                hostapi: row["hostapi"].as_i64().unwrap(),
                default_samplerate: row["default_samplerate"].as_number().unwrap().clone(),
            })
            .collect())
    }
    fn query_hostapis(&mut self) -> Result<Vec<String>, Raised> {
        let state = self.state.lock().unwrap();
        Ok(state.hosts.as_array().unwrap().iter().map(|host| host.as_str().unwrap().to_string()).collect())
    }
    fn default_input(&mut self) -> Result<i64, Raised> {
        Ok(self.state.lock().unwrap().default)
    }
    fn open_stream(
        &mut self,
        device: i64,
        rate: i64,
        sink: Arc<CaptureSink>,
    ) -> Result<Box<dyn Stream>, StreamError> {
        log(&self.calls, json!(["open", device, rate, 1, "float32"]));
        if let Some(failure) = self.state.lock().unwrap().failure("open") {
            return Err(failure);
        }
        Ok(Box::new(FakeStream { state: Arc::clone(&self.state), calls: Arc::clone(&self.calls), sink }))
    }
}

struct FakeModel {
    supported: Vec<String>,
    decodes: Arc<Mutex<VecDeque<J>>>,
    calls: Calls,
}

impl Model for FakeModel {
    fn supports(&self, language: &str) -> bool {
        self.supported.iter().any(|known| known == language)
    }
    fn transcribe<'a>(
        &'a mut self,
        audio: &'a [u8],
        options: &DecodeOptions,
    ) -> Result<Segments<'a>, Raised> {
        log(
            &self.calls,
            json!(["transcribe", hex::encode(Sha256::digest(audio)), audio.len(), options.to_json()]),
        );
        let decode = self.decodes.lock().unwrap().pop_front().unwrap_or_else(|| json!({"segments": []}));
        if decode.get("raise_call").is_some() {
            return Err(Raised);
        }
        let rows = decode["segments"].as_array().cloned().unwrap_or_default();
        let raise_at = decode.get("raise_at").and_then(J::as_u64).map(|at| at as usize);
        let count = rows.len();
        let mut items: Vec<Result<Segment, Raised>> = Vec::new();
        for (index, row) in rows.iter().enumerate() {
            if raise_at == Some(index) {
                items.push(Err(Raised));
                break;
            }
            let text = match row.get("repeat") {
                Some(repeat) => {
                    repeat.as_str().unwrap().repeat(row["times"].as_u64().unwrap() as usize)
                        + row["suffix"].as_str().unwrap()
                }
                None => row["text"].as_str().unwrap().to_string(),
            };
            items.push(Ok(Segment {
                text,
                no_speech_prob: row["no_speech_prob"].as_f64().unwrap(),
                avg_logprob: row["avg_logprob"].as_f64().unwrap(),
            }));
        }
        if raise_at == Some(count) {
            items.push(Err(Raised));
        }
        Ok(Box::new(items.into_iter()))
    }
}

struct FakeFactory {
    spec: Option<J>,
    fail_loads: u64,
    decodes: Arc<Mutex<VecDeque<J>>>,
    directory: PathBuf,
    calls: Calls,
}

impl ModelFactory for FakeFactory {
    fn load(&mut self, directory: &Path) -> Result<Box<dyn Model>, Raised> {
        let Some(spec) = &self.spec else { return Err(Raised) };
        log(&self.calls, json!(["load", directory == self.directory, LoadOptions::to_json()]));
        if self.fail_loads > 0 {
            self.fail_loads -= 1;
            return Err(Raised);
        }
        let supported = match spec.get("supported") {
            Some(list) => {
                list.as_array().unwrap().iter().map(|code| code.as_str().unwrap().to_string()).collect()
            }
            None => LANGUAGE_CODES.iter().map(|code| (*code).to_string()).collect(),
        };
        Ok(Box::new(FakeModel {
            supported,
            decodes: Arc::clone(&self.decodes),
            calls: Arc::clone(&self.calls),
        }))
    }
}

struct Body(Cursor<Vec<u8>>);

impl Response for Body {
    fn read(&mut self, limit: usize) -> Result<Vec<u8>, FetchError> {
        let mut chunk = vec![0; limit];
        let read = std::io::Read::read(&mut self.0, &mut chunk).unwrap();
        chunk.truncate(read);
        Ok(chunk)
    }
}

struct FakeFetch {
    responses: VecDeque<J>,
    calls: Calls,
}

impl Fetch for FakeFetch {
    fn open(&mut self, url: &str, user_agent: &str, timeout_s: u64) -> Result<Box<dyn Response>, FetchError> {
        log(&self.calls, json!(["fetch", url, user_agent, timeout_s]));
        let response = self.responses.pop_front().expect("a scripted response");
        match response.get("error").and_then(J::as_str) {
            Some("os" | "timeout") => Err(FetchError::Os),
            Some(_) => Err(FetchError::Other),
            None => Ok(Box::new(Body(Cursor::new(response["body"].as_str().unwrap().as_bytes().to_vec())))),
        }
    }
}

struct FakeRuntime {
    sound: Option<FakeSound>,
    languages: bool,
    factory: FakeFactory,
    fetch: FakeFetch,
    free: J,
    directory: PathBuf,
    calls: Calls,
}

impl Runtime for FakeRuntime {
    fn sound(&mut self) -> Option<&mut dyn Sound> {
        self.sound.as_mut().map(|sound| sound as &mut dyn Sound)
    }
    fn languages(&self) -> Option<&'static [&'static str]> {
        self.languages.then_some(&LANGUAGE_CODES[..])
    }
    fn model_factory(&mut self) -> &mut dyn ModelFactory {
        &mut self.factory
    }
    fn fetch(&mut self) -> &mut dyn Fetch {
        &mut self.fetch
    }
    fn disk_free(&mut self, path: &Path) -> std::io::Result<u64> {
        log(&self.calls, json!(["disk_usage", path == self.directory]));
        match &self.free {
            J::String(_) => Err(std::io::Error::other("scripted")),
            J::Null => Ok(1_000_000_000_000),
            number => Ok(number.as_u64().unwrap()),
        }
    }
}

struct Source {
    inner: TextInput<Cursor<Vec<u8>>>,
    count: usize,
    mutations: J,
    sound: Option<Arc<Mutex<SoundState>>>,
    directory: PathBuf,
    names: Vec<String>,
}

impl LineSource for Source {
    fn readline(&mut self, limit: usize) -> Result<String, Raised> {
        let mutation = self.mutations.get(self.count.to_string()).cloned().unwrap_or_else(|| json!({}));
        self.count += 1;
        if mutation.get("delete_model").is_some() {
            for name in &self.names {
                let _ = std::fs::remove_file(self.directory.join(name));
            }
        }
        if let Some(sound) = &self.sound {
            sound.lock().unwrap().apply(&mutation);
        }
        self.inner.readline(limit)
    }
}

fn folder_listing(root: &Path) -> J {
    let mut rows = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(folder) = pending.pop() {
        for entry in std::fs::read_dir(&folder).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                pending.push(path);
                continue;
            }
            let relative = path.strip_prefix(root).unwrap().to_string_lossy().replace('\\', "/");
            rows.push((relative, std::fs::metadata(&path).unwrap().len()));
        }
    }
    rows.sort();
    J::Array(rows.into_iter().map(|(name, size)| json!([name, size])).collect())
}

fn run(index: usize, scenario: &J) -> J {
    let calls: Calls = Arc::default();
    let base = temporary_folder(&format!("dictation-{index}"));
    let directory = base.0.join("model");
    std::fs::create_dir_all(&directory).unwrap();
    let model = scenario.get("model").and_then(J::as_object).cloned().unwrap_or_default();
    let spec = ModelSpec {
        files: model
            .iter()
            .map(|(name, text)| {
                let data = text.as_str().unwrap().as_bytes();
                (name.clone(), data.len() as u64, hex::encode(Sha256::digest(data)))
            })
            .collect(),
    };
    for (name, text) in scenario.get("present").and_then(J::as_object).cloned().unwrap_or_default() {
        std::fs::write(directory.join(name), text.as_str().unwrap()).unwrap();
    }
    let sound = scenario.get("sound").filter(|sound| !sound.is_null()).map(|spec| {
        Arc::new(Mutex::new(SoundState {
            rows: spec.get("rows").cloned().unwrap_or_else(|| json!([])),
            hosts: spec.get("hosts").cloned().unwrap_or_else(|| json!([])),
            default: spec.get("default").and_then(J::as_i64).unwrap_or(0),
            fail_initialize: spec.get("fail_initialize").and_then(J::as_bool).unwrap_or(false),
            fail: spec.get("fail").and_then(J::as_object).cloned().unwrap_or_default(),
            deliveries: spec.get("deliveries").cloned().unwrap_or_else(|| json!([])),
            initialized: spec.get("initialized").and_then(J::as_i64).unwrap_or(1),
        }))
    });
    let whisper = scenario.get("whisper").filter(|whisper| !whisper.is_null()).cloned();
    let decodes = whisper
        .as_ref()
        .and_then(|spec| spec.get("decodes"))
        .and_then(J::as_array)
        .map(|rows| rows.iter().cloned().collect())
        .unwrap_or_default();
    let mut runtime = FakeRuntime {
        sound: sound.as_ref().map(|state| FakeSound { state: Arc::clone(state), calls: Arc::clone(&calls) }),
        languages: scenario.get("languages").and_then(J::as_bool).unwrap_or(true),
        factory: FakeFactory {
            fail_loads: whisper
                .as_ref()
                .and_then(|spec| spec.get("fail_loads"))
                .and_then(J::as_u64)
                .unwrap_or(0),
            spec: whisper,
            decodes: Arc::new(Mutex::new(decodes)),
            directory: directory.clone(),
            calls: Arc::clone(&calls),
        },
        fetch: FakeFetch {
            responses: scenario
                .get("fetch")
                .and_then(J::as_array)
                .map(|rows| rows.iter().cloned().collect())
                .unwrap_or_default(),
            calls: Arc::clone(&calls),
        },
        free: scenario.get("free").cloned().unwrap_or(J::Null),
        directory: directory.clone(),
        calls: Arc::clone(&calls),
    };
    let held = scenario.get("locked").is_some().then(|| {
        let mut lock = std::fs::OpenOptions::new()
            .read(true)
            .append(true)
            .create(true)
            .open(directory.join(".download.lock"))
            .unwrap();
        std::io::Write::write_all(&mut lock, b"0").unwrap();
        lock.try_lock().unwrap();
        lock
    });
    let data = match scenario.get("input") {
        Some(text) => text.as_str().unwrap().as_bytes().to_vec(),
        None => hex::decode(scenario["input_hex"].as_str().unwrap()).unwrap(),
    };
    let mut source = Source {
        inner: TextInput::with_newlines(Cursor::new(data), true),
        count: 0,
        mutations: scenario.get("mutations").cloned().unwrap_or_else(|| json!({})),
        sound,
        directory: directory.clone(),
        names: spec.files.iter().map(|(name, _, _)| name.clone()).collect(),
    };
    let mut output = Replies::with_newline(Vec::new(), "\r\n");
    let worker = Worker { directory: &directory, spec: &spec };
    let code = serve(&mut source, &mut output, &worker, &mut runtime);
    drop(held);
    let written = output.into_inner();
    let mut result = serde_json::Map::new();
    result.insert("exit".into(), json!(code));
    if written.len() < 20_000 {
        result.insert("output".into(), json!(String::from_utf8(written).unwrap()));
    } else {
        result.insert("output_sha256".into(), json!(hex::encode(Sha256::digest(&written))));
        result.insert("output_bytes".into(), json!(written.len()));
    }
    result.insert("calls".into(), J::Array(std::mem::take(&mut *calls.lock().unwrap())));
    result.insert("files".into(), folder_listing(&directory));
    J::Object(result)
}

#[test]
fn the_language_table_is_faster_whispers() {
    let expected: Vec<&str> =
        vectors()["language_codes"].as_array().unwrap().iter().map(|c| c.as_str().unwrap()).collect();
    assert_eq!(expected, LANGUAGE_CODES);
}

#[test]
fn device_ids_hash_as_python_does() {
    for case in vectors()["device_ids"].as_array().unwrap() {
        let row = &case["row"];
        let id = device_id(
            case["index"].as_u64().unwrap() as usize,
            row["name"].as_str().unwrap(),
            row["default_samplerate"].as_number().unwrap(),
            case["host"].as_str().unwrap(),
        );
        assert_eq!(id, case["id"].as_str().unwrap(), "{case}");
    }
}

#[test]
fn every_scenario_matches_the_python_worker() {
    let mut failures = Vec::new();
    let scenarios = vectors()["scenarios"].as_array().unwrap();
    for (index, scenario) in scenarios.iter().enumerate() {
        let got = run(index, scenario);
        let expected = &scenario["expect"];
        if &got != expected {
            failures.push(format!("{}: {}", scenario["name"], first_difference(&got, expected, "")));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} scenarios differ:\n{}",
        failures.len(),
        scenarios.len(),
        failures.join("\n")
    );
}

#[test]
fn the_worker_process_answers_as_pythons_does() {
    let executable = env!("CARGO_BIN_EXE_sylvia-gm-owner");
    let mut failures = Vec::new();
    for (index, case) in vectors()["processes"].as_array().unwrap().iter().enumerate() {
        let folder = temporary_folder(&format!("dictation-process-{index}"));
        let model = folder.0.join("model");
        let mut command = std::process::Command::new(executable);
        command.arg("--dictation-worker");
        for argument in case["arguments"].as_array().unwrap() {
            command.arg(argument.as_str().unwrap().replace("{model}", &model.to_string_lossy()));
        }
        command.env_clear();
        for key in ["SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "LOCALAPPDATA"] {
            if let Some(value) = std::env::var_os(key) {
                command.env(key, value);
            }
        }
        command
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());
        let mut child = command.spawn().unwrap();
        let input = case["input"].as_str().unwrap().as_bytes().to_vec();
        let mut stdin = child.stdin.take().unwrap();
        std::thread::spawn(move || {
            let _ = std::io::Write::write_all(&mut stdin, &input);
        });
        let finished = child.wait_with_output().unwrap();
        let got = json!({
            "exit": finished.status.code(),
            "stdout": String::from_utf8(finished.stdout).unwrap(),
            "stderr": String::from_utf8(finished.stderr).unwrap(),
            "files": folder_listing(&folder.0),
        });
        let expected = json!({"exit": case["exit"], "stdout": case["stdout"], "stderr": case["stderr"], "files": case["files"]});
        if got != expected {
            failures.push(format!("{}: {}", case["name"], first_difference(&got, &expected, "")));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
