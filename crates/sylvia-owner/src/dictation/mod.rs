//! Local dictation: `sylvia_gm/local_dictation.py`.
//!
//! The verified model cache and its download, the recogniser's segment gate, the input-device
//! list and the bounded in-memory recording. The speech engine, the audio library and the
//! network sit behind traits ([`ModelFactory`], [`Sound`], [`Fetch`]), the parts of Python's
//! `faster_whisper`, `sounddevice` and `urllib` this file calls. A build without them behaves
//! as Python's owner does where the PyInstaller build excludes the speech runtime (everywhere
//! but Windows): [`Absent`].
//!
//! [`worker`] is `local_dictation_process.py`, the line protocol the desktop drives.

pub mod audio;
pub mod features;
pub mod worker;

/// NumPy's OpenBLAS (rule 9; Windows, `dictation` feature).
#[cfg(all(windows, feature = "dictation"))]
#[path = "openblas/windows.rs"]
pub mod openblas;

/// libswresample from the shipped LGPL FFmpeg (rule 9; Windows, `dictation` feature).
#[cfg(all(windows, feature = "dictation"))]
#[path = "swresample/windows.rs"]
pub mod swresample;

use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use sylvia_base::Json;

/// `MODEL_ID`.
pub const MODEL_ID: &str = "Systran/faster-whisper-base";
/// `MODEL_REVISION`.
pub const MODEL_REVISION: &str = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66";
/// `MODEL_FILES`: name, size and SHA-256, in Python's order.
pub const MODEL_FILES: [(&str, u64, &str); 4] = [
    ("config.json", 2309, "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a"),
    ("model.bin", 145_217_532, "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9"),
    ("tokenizer.json", 2_203_239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
    ("vocabulary.txt", 459_861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
];
/// `MAX_SECONDS`.
pub const MAX_SECONDS: i64 = 120;

/// How a Python call in this file ends when it does not return.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Failure {
    /// `SpeechError(code)`: a renderer-known code.
    Speech(&'static str),
    /// Any other exception. The worker reports it as `provider`, `capabilities` as `runtime`.
    Other,
}

impl Failure {
    /// The code the worker replies with: `str(error)` or `"provider"`.
    pub fn code(self) -> &'static str {
        match self {
            Failure::Speech(code) => code,
            Failure::Other => "provider",
        }
    }
}

/// An exception a library call raised, whatever its class; the caller maps it as Python does.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Raised;

/// A result of this module.
pub type Outcome<T> = Result<T, Failure>;

fn speech<T>(code: &'static str) -> Outcome<T> {
    Err(Failure::Speech(code))
}

/// The model files a worker checks: [`MODEL_FILES`], or a test's own.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelSpec {
    /// Name, size and lowercase SHA-256 hex, in order.
    pub files: Vec<(String, u64, String)>,
}

impl ModelSpec {
    /// The pinned faster-whisper base model.
    pub fn shipped() -> Self {
        Self { files: MODEL_FILES.iter().map(|(n, s, h)| ((*n).to_string(), *s, (*h).to_string())).collect() }
    }

    /// `sum(size for size, _ in MODEL_FILES.values())`.
    pub fn download_bytes(&self) -> u64 {
        self.files.iter().map(|(_, size, _)| size).sum()
    }
}

/// `_verified(path, expected)`: a regular file (not a link) of the expected size and digest.
fn verified(path: &Path, size: u64, digest: &str) -> bool {
    let Ok(metadata) = fs::symlink_metadata(path) else { return false };
    if metadata.file_type().is_symlink() || !metadata.is_file() || metadata.len() != size {
        return false;
    }
    let Ok(mut file) = File::open(path) else { return false };
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; 1 << 16];
    loop {
        match file.read(&mut buffer) {
            Ok(0) => break,
            Ok(read) => hasher.update(&buffer[..read]),
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
            Err(_) => return false,
        }
    }
    hex::encode(hasher.finalize()) == digest
}

/// `model_ready(directory)`.
pub fn model_ready(directory: &Path, spec: &ModelSpec) -> bool {
    spec.files.iter().all(|(name, size, digest)| verified(&directory.join(name), *size, digest))
}

/// Why `urlopen` or a read failed: `OSError`/`TimeoutError` (`Os`) or anything else.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FetchError {
    /// `OSError` (including `URLError`, `HTTPError`) or `TimeoutError`.
    Os,
    /// Any other exception.
    Other,
}

/// An open download: `response.read(n)`.
pub trait Response {
    /// Up to `limit` bytes; empty at the end.
    fn read(&mut self, limit: usize) -> Result<Vec<u8>, FetchError>;
}

/// `urllib.request.urlopen(Request(url, headers={"User-Agent": ...}), timeout=...)`.
pub trait Fetch {
    /// Open one fixed model-file address.
    fn open(&mut self, url: &str, user_agent: &str, timeout_s: u64) -> Result<Box<dyn Response>, FetchError>;
}

/// The model download before lane 4's HTTP client is in the owner: every request fails as an
/// unreachable network does (`URLError`, so `download`).
#[derive(Debug, Default)]
pub struct NoNetwork;

impl Fetch for NoNetwork {
    fn open(
        &mut self,
        _url: &str,
        _user_agent: &str,
        _timeout_s: u64,
    ) -> Result<Box<dyn Response>, FetchError> {
        Err(FetchError::Os)
    }
}

/// `_download_lock(directory)`: an OS-owned lock, released when the file closes.
fn download_lock(directory: &Path) -> Outcome<File> {
    fs::create_dir_all(directory).map_err(|_| Failure::Speech("download"))?;
    let mut lock = OpenOptions::new()
        .read(true)
        .append(true)
        .create(true)
        .open(directory.join(".download.lock"))
        .map_err(|_| Failure::Speech("download"))?;
    // "a+b" opens at the end, so `tell() == 0` means the file is empty.
    let length = lock.metadata().map_err(|_| Failure::Speech("download"))?.len();
    if length == 0 {
        lock.write_all(b"0").and_then(|()| lock.flush()).map_err(|_| Failure::Speech("download"))?;
    }
    // `msvcrt.locking(..., LK_NBLCK, 1)` / `fcntl.flock(LOCK_EX | LOCK_NB)`: any failure is
    // `busy`.
    if lock.try_lock().is_err() {
        return speech("busy");
    }
    Ok(lock)
}

/// `prepare_model(directory)`: download only the fixed public files, verify each, then publish
/// it with `os.replace`.
pub fn prepare_model(directory: &Path, spec: &ModelSpec, runtime: &mut dyn Runtime) -> Outcome<()> {
    let _lock = download_lock(directory)?;
    let missing: Vec<&(String, u64, String)> = spec
        .files
        .iter()
        .filter(|(name, size, digest)| !verified(&directory.join(name), *size, digest))
        .collect();
    if !missing.is_empty() {
        let free = runtime.disk_free(directory).map_err(|_| Failure::Speech("download"))?;
        let needed = missing.iter().map(|(_, size, _)| u128::from(*size)).sum::<u128>() * 2 + 64_000_000;
        if u128::from(free) < needed {
            return speech("storage");
        }
    }
    for (name, size, digest) in missing {
        let partial = directory.join(format!("{name}.part"));
        if fs::symlink_metadata(&partial).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
            return speech("storage");
        }
        let result = download_one(directory, name, *size, digest, &partial, runtime.fetch());
        // `finally: partial.unlink(missing_ok=True)`; an `OSError` there replaces the outcome.
        let removed = match fs::remove_file(&partial) {
            Err(error) if error.kind() != std::io::ErrorKind::NotFound => Err(Failure::Speech("download")),
            _ => Ok(()),
        };
        result?;
        removed?;
    }
    Ok(())
}

fn download_one(
    directory: &Path,
    name: &str,
    size: u64,
    digest: &str,
    partial: &Path,
    fetch: &mut dyn Fetch,
) -> Outcome<()> {
    let fetched = |error: FetchError| match error {
        FetchError::Os => Failure::Speech("download"),
        FetchError::Other => Failure::Other,
    };
    let url = format!("https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{name}?download=true");
    let mut response = fetch.open(&url, "Sylvia-GM-local-dictation", 30).map_err(fetched)?;
    {
        let mut output = File::create(partial).map_err(|_| Failure::Speech("download"))?;
        let mut remaining = size;
        while remaining > 0 {
            let limit = usize::try_from(remaining.min(1024 * 1024)).unwrap_or(1024 * 1024);
            let chunk = response.read(limit).map_err(fetched)?;
            if chunk.is_empty() {
                return speech("download");
            }
            output.write_all(&chunk).map_err(|_| Failure::Speech("download"))?;
            remaining = remaining.saturating_sub(chunk.len() as u64);
        }
        if !response.read(1).map_err(fetched)?.is_empty() {
            return speech("download");
        }
        output.flush().map_err(|_| Failure::Speech("download"))?;
    }
    if !verified(partial, size, digest) {
        return speech("download");
    }
    fs::rename(partial, directory.join(name)).map_err(|_| Failure::Speech("download"))
}

/// The `WhisperModel(...)` arguments `transcribe` passes after the model folder.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LoadOptions;

impl LoadOptions {
    /// Python's keyword arguments, in order.
    pub fn to_json() -> Json {
        serde_json::json!({
            "device": "cpu", "compute_type": "int8", "cpu_threads": 2, "num_workers": 1,
            "local_files_only": true,
        })
    }
}

/// The `model.transcribe(audio, **options)` options.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecodeOptions {
    /// `language`, absent for `auto`.
    pub language: Option<String>,
}

impl DecodeOptions {
    /// Python's options dictionary, in its insertion order.
    pub fn to_json(&self) -> Json {
        let mut options = serde_json::json!({
            "beam_size": 5,
            "condition_on_previous_text": false,
            "vad_filter": true,
            "vad_parameters": {"min_silence_duration_ms": 500},
            "no_speech_threshold": 0.6,
            "log_prob_threshold": -1.0,
        });
        if let Some(language) = &self.language {
            options["language"] = Json::String(language.clone());
        }
        options
    }
}

/// One decoded segment.
#[derive(Debug, Clone, PartialEq)]
pub struct Segment {
    /// `segment.text`.
    pub text: String,
    /// `segment.no_speech_prob`.
    pub no_speech_prob: f64,
    /// `segment.avg_logprob`.
    pub avg_logprob: f64,
}

/// The segments of one decode; faster-whisper decodes lazily, so any item may fail.
pub type Segments<'a> = Box<dyn Iterator<Item = Result<Segment, Raised>> + 'a>;

/// A loaded `WhisperModel`.
pub trait Model {
    /// `language in model.supported_languages`.
    fn supports(&self, language: &str) -> bool;
    /// `model.transcribe(audio, **options)`; `Err` is an exception raised by the call itself.
    fn transcribe<'a>(&'a mut self, audio: &'a [u8], options: &DecodeOptions)
        -> Result<Segments<'a>, Raised>;
}

/// `WhisperModel(str(directory), **LoadOptions)`; `Err` is any exception, including a failed
/// `from faster_whisper import WhisperModel`.
pub trait ModelFactory {
    /// Load the model from its verified folder.
    fn load(&mut self, directory: &Path) -> Result<Box<dyn Model>, Raised>;
}

/// `Recognizer`: one verified CPU model kept between utterances, never an utterance.
pub struct Recognizer {
    directory: PathBuf,
    model: Option<Box<dyn Model>>,
}

impl Recognizer {
    /// `Recognizer(directory)`.
    pub fn new(directory: &Path) -> Self {
        Self { directory: directory.to_path_buf(), model: None }
    }

    /// Whether a model is loaded and kept.
    pub fn warm(&self) -> bool {
        self.model.is_some()
    }

    /// `Recognizer.transcribe(audio, language)`: `transcribe` through the kept model; a
    /// `model` or `provider` failure discards it.
    pub fn transcribe(
        &mut self,
        audio: &[u8],
        language: &str,
        spec: &ModelSpec,
        factory: &mut dyn ModelFactory,
    ) -> Outcome<String> {
        let result = self.decode(audio, language, spec, factory);
        if matches!(result, Err(Failure::Speech("model" | "provider")) | Err(Failure::Other)) {
            self.model = None;
        }
        result
    }

    fn decode(
        &mut self,
        audio: &[u8],
        language: &str,
        spec: &ModelSpec,
        factory: &mut dyn ModelFactory,
    ) -> Outcome<String> {
        if !model_ready(&self.directory, spec) {
            return speech("model");
        }
        if self.model.is_none() {
            self.model = Some(factory.load(&self.directory).map_err(|Raised| Failure::Speech("provider"))?);
        }
        let Some(model) = self.model.as_mut() else { return speech("provider") };
        if language != "auto" && !model.supports(language) {
            return speech("language");
        }
        let options = DecodeOptions { language: (language != "auto").then(|| language.to_string()) };
        let segments = model.transcribe(audio, &options).map_err(|Raised| Failure::Speech("provider"))?;
        let mut text = Vec::new();
        for segment in segments {
            let segment = segment.map_err(|Raised| Failure::Speech("provider"))?;
            // `float(...)` never fails on the engine's numbers; NaN compares false, as in Python.
            let silence = segment.no_speech_prob > 0.6 && segment.avg_logprob < -1.0;
            if !silence {
                text.push(sylvia_base::pystr::strip(&segment.text).to_string());
            }
        }
        let result = sylvia_base::pystr::strip(&text.join(" ")).to_string();
        if result.is_empty() {
            return speech("empty");
        }
        if result.len() > 128_000 {
            return speech("provider");
        }
        Ok(result)
    }
}

/// `transcribe(audio, directory, language)`: one decode with a freshly loaded model.
pub fn transcribe(
    audio: &[u8],
    directory: &Path,
    language: &str,
    spec: &ModelSpec,
    factory: &mut dyn ModelFactory,
) -> Outcome<String> {
    Recognizer::new(directory).transcribe(audio, language, spec, factory)
}

/// One row of `sounddevice.query_devices()`.
#[derive(Debug, Clone, PartialEq)]
pub struct DeviceRow {
    /// `name`.
    pub name: String,
    /// `max_input_channels`.
    pub max_input_channels: i64,
    /// `hostapi`: an index into `query_hostapis()`.
    pub hostapi: i64,
    /// `default_samplerate`, a Python int or float.
    pub default_samplerate: serde_json::Number,
}

/// One entry of `input_devices(sound)`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Device {
    /// `id`: `default` or `d-<index>-<hash>`.
    pub id: String,
    /// `label`.
    pub label: String,
    /// `index`: PortAudio's device index.
    pub index: i64,
    /// `rate`: `int(default_samplerate)`.
    pub rate: i64,
}

/// What a stream open or start raised.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StreamError {
    /// `PermissionError`.
    Permission,
    /// Anything else.
    Other,
}

/// An open `sounddevice.InputStream`. `Err(Raised)` is any exception.
pub trait Stream: Send {
    /// `stream.start()`.
    fn start(&mut self) -> Result<(), StreamError>;
    /// `stream.stop()`.
    fn stop(&mut self) -> Result<(), Raised>;
    /// `stream.abort()`.
    fn abort(&mut self) -> Result<(), Raised>;
    /// `stream.close()`.
    fn close(&mut self) -> Result<(), Raised>;
}

/// The `sounddevice` module (PortAudio) as `local_dictation.py` uses it. `Err(Raised)` is any
/// exception.
pub trait Sound {
    /// `bool(sound._initialized)`.
    fn initialized(&self) -> bool;
    /// `sound._terminate()`.
    fn terminate(&mut self) -> Result<(), Raised>;
    /// `sound._initialize()`.
    fn initialize(&mut self) -> Result<(), Raised>;
    /// `sound.query_devices()`.
    fn query_devices(&mut self) -> Result<Vec<DeviceRow>, Raised>;
    /// The `name` of each `sound.query_hostapis()` entry.
    fn query_hostapis(&mut self) -> Result<Vec<String>, Raised>;
    /// `int(sound.default.device[0])`.
    fn default_input(&mut self) -> Result<i64, Raised>;
    /// `sound.InputStream(device=, samplerate=, channels=1, dtype="float32", callback=)`: the
    /// stream hands each block of mono samples to `sink`.
    fn open_stream(
        &mut self,
        device: i64,
        rate: i64,
        sink: Arc<CaptureSink>,
    ) -> Result<Box<dyn Stream>, StreamError>;
}

/// `_device_id(index, row, host)`.
pub fn device_id(index: usize, name: &str, samplerate: &serde_json::Number, host: &str) -> String {
    let rate = python_number_text(samplerate);
    let identity = hex::encode(Sha256::digest(format!("{host}\n{name}\n{rate}").as_bytes()));
    format!("d-{index}-{}", &identity[..16])
}

/// `str(number)` for a Python int or float.
fn python_number_text(number: &serde_json::Number) -> String {
    if number.is_i64() || number.is_u64() {
        number.to_string()
    } else {
        sylvia_base::pyjson::float_repr(number.as_f64().unwrap_or(f64::NAN))
    }
}

/// `int(number)`.
fn python_int(number: &serde_json::Number) -> Result<i64, Raised> {
    if let Some(value) = number.as_i64() {
        return Ok(value);
    }
    let value = number.as_f64().ok_or(Raised)?;
    if !value.is_finite() || value.trunc().abs() >= 9.2e18 {
        return Err(Raised);
    }
    Ok(value.trunc() as i64)
}

/// `input_devices(sound)`: refresh PortAudio's idle metadata and list the inputs, the system
/// default first. Any failure is `device`.
pub fn input_devices(sound: &mut dyn Sound) -> Outcome<Vec<Device>> {
    list_devices(sound).map_err(|Raised| Failure::Speech("device"))
}

fn list_devices(sound: &mut dyn Sound) -> Result<Vec<Device>, Raised> {
    if sound.initialized() {
        sound.terminate()?;
    }
    sound.initialize()?;
    let rows = sound.query_devices()?;
    let hosts = sound.query_hostapis()?;
    let default = sound.default_input()?;
    let mut devices = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        if row.max_input_channels > 0 {
            // A Python list index: negative counts from the end.
            let position = if row.hostapi < 0 { row.hostapi + hosts.len() as i64 } else { row.hostapi };
            let host = usize::try_from(position).ok().and_then(|at| hosts.get(at)).ok_or(Raised)?;
            devices.push(Device {
                id: device_id(index, &row.name, &row.default_samplerate, host),
                label: format!("{} ({host})", row.name),
                index: index as i64,
                rate: python_int(&row.default_samplerate)?,
            });
        }
    }
    let selected = devices.iter().find(|device| device.index == default);
    let label = match selected {
        Some(device) => format!("System default: {}", device.label),
        None => "System default input (unavailable)".to_string(),
    };
    let rate = selected.map_or(0, |device| device.rate);
    let mut options = vec![Device { id: "default".into(), label, index: default, rate }];
    options.extend(devices);
    Ok(options)
}

/// Whether the stream should stop after a block (`raise sound.CallbackStop`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Flow {
    /// Keep recording.
    Continue,
    /// The two-minute bound is reached.
    Stop,
}

#[derive(Debug, Default)]
struct CaptureState {
    chunks: Vec<Vec<f32>>,
    frames: i64,
    failed: bool,
}

/// The recording's callback side: `Recording.start`'s `capture` and the chunks it keeps.
#[derive(Debug)]
pub struct CaptureSink {
    limit: i64,
    state: Mutex<CaptureState>,
}

impl CaptureSink {
    fn new(limit: i64) -> Self {
        Self { limit, state: Mutex::new(CaptureState::default()) }
    }

    /// `capture(data, frames, time, status)` for one block of mono samples.
    pub fn deliver(&self, data: &[f32], status: bool) -> Flow {
        let mut state = self.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        if status {
            state.failed = true;
        }
        let available = self.limit - state.frames;
        if available > 0 {
            let take = usize::try_from(available).map_or(data.len(), |available| available.min(data.len()));
            state.chunks.push(data[..take].to_vec());
            state.frames += take as i64;
        }
        if state.frames >= self.limit {
            Flow::Stop
        } else {
            Flow::Continue
        }
    }

    /// `recording.frames`.
    pub fn frames(&self) -> i64 {
        self.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner).frames
    }

    fn clear(&self) {
        let mut state = self.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        state.chunks.clear();
        state.frames = 0;
    }
}

/// `Recording`: a bounded, in-memory mono recording until Stop or cancellation.
pub struct Recording {
    sink: Arc<CaptureSink>,
    stream: Option<Box<dyn Stream>>,
    /// `recording.rate`.
    pub rate: i64,
    /// `recording.device`.
    pub device: i64,
}

impl Recording {
    /// `Recording(device, sound=sound)`: select the input by its listed id.
    pub fn new(device: &str, sound: &mut dyn Sound) -> Outcome<Self> {
        let options = input_devices(sound)?;
        let Some(selected) = options.iter().find(|option| option.id == device) else {
            return speech("device");
        };
        if selected.index < 0 || selected.rate <= 0 {
            return speech("device");
        }
        let limit = MAX_SECONDS.checked_mul(selected.rate).ok_or(Failure::Other)?;
        Ok(Self {
            sink: Arc::new(CaptureSink::new(limit)),
            stream: None,
            rate: selected.rate,
            device: selected.index,
        })
    }

    /// The callback side, for a stream to deliver into.
    pub fn sink(&self) -> Arc<CaptureSink> {
        Arc::clone(&self.sink)
    }

    /// Whether a stream is open (`recording.stream is not None`).
    pub fn has_stream(&self) -> bool {
        self.stream.is_some()
    }

    /// `recording.start()`.
    pub fn start(&mut self, sound: &mut dyn Sound) -> Outcome<()> {
        let opened = sound.open_stream(self.device, self.rate, Arc::clone(&self.sink));
        let started = match opened {
            Ok(stream) => {
                self.stream = Some(stream);
                self.stream.as_mut().map_or(Ok(()), |stream| stream.start())
            }
            Err(error) => Err(error),
        };
        match started {
            Ok(()) => Ok(()),
            Err(error) => {
                // `self.cancel()` inside the handler: its own exception would replace the code.
                self.cancel()?;
                speech(if error == StreamError::Permission { "permission" } else { "device" })
            }
        }
    }

    /// `recording.cancel()`: abort and close the stream, then drop what was recorded.
    pub fn cancel(&mut self) -> Outcome<()> {
        let mut result = Ok(());
        if let Some(mut stream) = self.stream.take() {
            let aborted = stream.abort();
            let closed = stream.close();
            if aborted.is_err() || closed.is_err() {
                result = Err(Failure::Other);
            }
        }
        self.sink.clear();
        result
    }

    /// `recording.stop()`: close the stream and return the recording as 16-bit WAV bytes.
    pub fn stop(&mut self) -> Outcome<Vec<u8>> {
        let Some(stream) = self.stream.as_mut() else { return speech("device") };
        let stopped = stream.stop();
        let closed = stream.close();
        // `self.stream = None` follows the close in the same `finally`, so a failed close
        // leaves the stream for `cancel`.
        if closed.is_ok() {
            self.stream = None;
        }
        if stopped.is_err() || closed.is_err() {
            return Err(Failure::Other);
        }
        let (chunks, failed) = {
            let mut state = self.sink.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            state.frames = 0;
            (std::mem::take(&mut state.chunks), state.failed)
        };
        if failed {
            return speech("device");
        }
        if chunks.is_empty() {
            return speech("empty");
        }
        let samples: Vec<f32> = chunks.concat();
        wav_bytes(&samples, self.rate).ok_or(Failure::Other)
    }
}

/// `(np.clip(samples, -1, 1) * 32767).astype("<i2")`: float32 arithmetic, then truncation
/// toward zero (NaN becomes 0, as the x86 conversion leaves it).
pub fn pcm16(samples: &[f32]) -> Vec<i16> {
    samples.iter().map(|sample| (sample.clamp(-1.0, 1.0) * 32767.0_f32) as i16).collect()
}

/// A mono 16-bit WAV file as Python's `wave` module writes it.
pub fn wav_bytes(samples: &[f32], rate: i64) -> Option<Vec<u8>> {
    let rate = u32::try_from(rate).ok()?;
    let data: Vec<u8> = pcm16(samples).iter().flat_map(|sample| sample.to_le_bytes()).collect();
    let length = u32::try_from(data.len()).ok()?;
    let mut out = Vec::with_capacity(44 + data.len());
    out.extend_from_slice(b"RIFF");
    out.extend_from_slice(&length.checked_add(36)?.to_le_bytes());
    out.extend_from_slice(b"WAVEfmt ");
    out.extend_from_slice(&16_u32.to_le_bytes());
    out.extend_from_slice(&1_u16.to_le_bytes());
    out.extend_from_slice(&1_u16.to_le_bytes());
    out.extend_from_slice(&rate.to_le_bytes());
    out.extend_from_slice(&rate.checked_mul(2)?.to_le_bytes());
    out.extend_from_slice(&2_u16.to_le_bytes());
    out.extend_from_slice(&16_u16.to_le_bytes());
    out.extend_from_slice(b"data");
    out.extend_from_slice(&length.to_le_bytes());
    out.extend_from_slice(&data);
    Some(out)
}

/// The speech runtime a worker uses: Python's `sounddevice` and `faster_whisper` imports, the
/// network and `shutil.disk_usage`.
pub trait Runtime {
    /// `import sounddevice`; `None` when the import fails.
    fn sound(&mut self) -> Option<&mut dyn Sound>;
    /// `from faster_whisper.tokenizer import _LANGUAGE_CODES`; `None` when the import fails.
    fn languages(&self) -> Option<&'static [&'static str]>;
    /// `WhisperModel`.
    fn model_factory(&mut self) -> &mut dyn ModelFactory;
    /// `urllib.request.urlopen`.
    fn fetch(&mut self) -> &mut dyn Fetch;
    /// `shutil.disk_usage(path).free`.
    fn disk_free(&mut self, path: &Path) -> std::io::Result<u64>;
}

/// `faster_whisper.tokenizer._LANGUAGE_CODES` (faster-whisper 1.2.1), in order.
pub const LANGUAGE_CODES: [&str; 100] = [
    "af", "am", "ar", "as", "az", "ba", "be", "bg", "bn", "bo", "br", "bs", "ca", "cs", "cy", "da", "de",
    "el", "en", "es", "et", "eu", "fa", "fi", "fo", "fr", "gl", "gu", "ha", "haw", "he", "hi", "hr", "ht",
    "hu", "hy", "id", "is", "it", "ja", "jw", "ka", "kk", "km", "kn", "ko", "la", "lb", "ln", "lo", "lt",
    "lv", "mg", "mi", "mk", "ml", "mn", "mr", "ms", "mt", "my", "ne", "nl", "nn", "no", "oc", "pa", "pl",
    "ps", "pt", "ro", "ru", "sa", "sd", "si", "sk", "sl", "sn", "so", "sq", "sr", "su", "sv", "sw", "ta",
    "te", "tg", "th", "tk", "tl", "tr", "tt", "uk", "ur", "uz", "vi", "yi", "yo", "zh", "yue",
];

/// The owner built without the speech runtime, as Python's PyInstaller owner is everywhere but
/// Windows: `sounddevice` and `faster_whisper` fail to import. The model download needs lane 4's
/// HTTP client and fails as an unreachable network until then.
#[derive(Default)]
pub struct Absent {
    model: AbsentModel,
    fetch: NoNetwork,
}

#[derive(Default)]
struct AbsentModel;

impl ModelFactory for AbsentModel {
    fn load(&mut self, _directory: &Path) -> Result<Box<dyn Model>, Raised> {
        Err(Raised)
    }
}

impl Runtime for Absent {
    fn sound(&mut self) -> Option<&mut dyn Sound> {
        None
    }
    fn languages(&self) -> Option<&'static [&'static str]> {
        None
    }
    fn model_factory(&mut self) -> &mut dyn ModelFactory {
        &mut self.model
    }
    fn fetch(&mut self) -> &mut dyn Fetch {
        &mut self.fetch
    }
    fn disk_free(&mut self, path: &Path) -> std::io::Result<u64> {
        crate::desktop_update::storage::disk_free(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_language_table_ends_with_cantonese_which_the_worker_leaves_out() {
        assert_eq!(LANGUAGE_CODES.len(), 100);
        assert_eq!(LANGUAGE_CODES[99], "yue");
    }

    #[test]
    fn samples_convert_as_numpy_casts_them() {
        assert_eq!(
            pcm16(&[0.0, 1.0, -1.0, 2.0, -2.0, 0.5, -0.5, f32::NAN]),
            [0, 32767, -32767, 32767, -32767, 16383, -16383, 0]
        );
    }
}
