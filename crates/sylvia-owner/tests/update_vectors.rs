//! The desktop updater's checks against vectors generated from the Python snapshot by
//! `vectors/generate_update_vectors.py` (CPython 3.12.11, `cryptography` 50 on OpenSSL 3.0).

use serde_json::{json, Value as J};
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Mutex;
use sylvia_base::{CodedError, CodedResult};
use sylvia_owner::desktop_update::discovery::{discover, retain_current_manifest};
use sylvia_owner::desktop_update::manifest::{
    b64decode_strict, parse_current_recovery_manifest, parse_manifest, payload_path, release_url, Context,
    KeyRing, Manifest, Version, APP_VERSION, RELEASE_CHANNEL,
};
use sylvia_owner::desktop_update::transport::{
    check_final_location, check_redirect, download_artifact, metadata_bytes, official_request, Cancel,
    Response, Transport,
};
use sylvia_owner::desktop_update::url::{unquote_strict, urlsplit};

fn vectors() -> &'static J {
    static VECTORS: std::sync::OnceLock<J> = std::sync::OnceLock::new();
    VECTORS.get_or_init(|| {
        serde_json::from_str(include_str!("vectors/update_vectors.json")).expect("valid JSON")
    })
}

fn rows(name: &str) -> &'static [J] {
    vectors()[name].as_array().expect("a list of cases")
}

/// Python's `outcome(call)`: `{"ok": value}` or `{"error": [code, message]}`.
fn outcome<T>(result: CodedResult<T>, ok: impl FnOnce(T) -> J) -> J {
    match result {
        Ok(value) => json!({"ok": ok(value)}),
        Err(error) => json!({"error": [error.code, error.message]}),
    }
}

fn raw(value: &J) -> Vec<u8> {
    if let Some(text) = value.get("text") {
        text.as_str().unwrap().as_bytes().to_vec()
    } else {
        b64decode_strict(value["b64"].as_str().unwrap()).unwrap()
    }
}

fn key_ring() -> KeyRing {
    let key: [u8; 32] = hex::decode(vectors()["public_key"].as_str().unwrap()).unwrap().try_into().unwrap();
    KeyRing::new(BTreeMap::from([(vectors()["key_id"].as_str().unwrap().to_string(), key)]))
}

fn summary(manifest: Manifest) -> J {
    use sha2::Digest;
    json!({
        "version": manifest.version.text,
        "channel": manifest.channel,
        "target": manifest.target,
        "distribution": manifest.distribution,
        "notes": manifest.notes,
        "artifact_url": manifest.artifact_url,
        "artifact_size": manifest.artifact_size,
        "artifact_sha256": manifest.artifact_sha256,
        "files": manifest.files.iter().map(|row| json!([row.path, row.size, row.sha256])).collect::<Vec<_>>(),
        "signed_sha256": hex::encode(sha2::Sha256::digest(&manifest.signed_bytes)),
        "envelope_sha256": hex::encode(sha2::Sha256::digest(&manifest.envelope_bytes)),
    })
}

#[test]
fn the_reference_is_this_build() {
    assert_eq!(vectors()["app_version"], APP_VERSION);
    assert_eq!(vectors()["release_channel"], RELEASE_CHANNEL);
    assert_eq!(vectors()["python"], "3.12.11");
}

#[test]
fn versions_parse_and_order_like_python() {
    for case in rows("versions") {
        let value = case.get("text").cloned().unwrap_or_else(|| case["value"].clone());
        let result = Version::parse(&value);
        let got = if case.get("text").is_some() {
            outcome(result, |v| {
                json!({"text": v.text, "core": [v.core.0, v.core.1, v.core.2], "prerelease": v.prerelease,
                       "channel": v.channel()})
            })
        } else {
            outcome(result, |v| json!(v.text))
        };
        assert_eq!(got, case["result"], "{value}");
    }
    for case in rows("version_order") {
        let left = Version::parse(&case[0]).unwrap();
        let right = Version::parse(&case[1]).unwrap();
        let got = json!([case[0], case[1], left < right, left == right, left <= right, left > right]);
        assert_eq!(&got, case);
    }
}

#[test]
fn base64_decodes_like_binascii_strict_mode() {
    for case in rows("base64") {
        let got = b64decode_strict(case["text"].as_str().unwrap()).map(hex::encode);
        assert_eq!(json!(got), case["bytes"], "{}", case["text"]);
    }
}

#[test]
fn urls_split_and_unquote_like_python() {
    for case in rows("urlsplit") {
        let got = urlsplit(case["url"].as_str().unwrap())
            .ok()
            .map(|split| json!([split.scheme, split.netloc, split.path, split.query, split.fragment]));
        assert_eq!(json!(got), case["split"], "{}", case["url"]);
    }
    for case in rows("unquote") {
        let got = unquote_strict(case["text"].as_str().unwrap()).ok();
        assert_eq!(json!(got), case["result"], "{}", case["text"]);
    }
}

#[test]
fn release_locations_are_checked_like_python() {
    for case in rows("release_url") {
        let got = outcome(release_url(&case["url"], "0.4.2", case["distribution"].as_str()), J::String);
        assert_eq!(got, case["result"], "{} {}", case["url"], case["distribution"]);
    }
    for case in rows("official_request") {
        let got = outcome(official_request(case["url"].as_str().unwrap()), |()| J::Null);
        assert_eq!(got, case["result"], "{}", case["url"]);
    }
    for case in rows("redirects") {
        // Python's `redirect_request` returns the next request (`is None` is false) once allowed.
        let got = outcome(check_redirect(case["url"].as_str().unwrap()), |()| json!(false));
        assert_eq!(got, case["result"], "{}", case["url"]);
    }
    for case in rows("final_location") {
        let result = check_final_location(case["url"].as_str().unwrap());
        let got = match result {
            Ok(()) => json!(true),
            Err(error) if error.code == "unsafe_redirect" => json!(false),
            Err(error) => {
                assert_eq!(error.code, "update_failed");
                J::Null
            }
        };
        assert_eq!(got, case["trusted"], "{}", case["url"]);
    }
}

#[test]
fn payload_paths_are_checked_like_python() {
    for case in rows("payload_path") {
        assert_eq!(outcome(payload_path(&case["path"]), J::String), case["result"], "{}", case["path"]);
    }
}

#[test]
fn signatures_verify_like_openssl() {
    for case in rows("signatures") {
        let ring = match case["key"].as_str() {
            None => KeyRing::default(),
            Some(key) => KeyRing::new(BTreeMap::from([(
                "k".to_string(),
                hex::decode(key).unwrap().try_into().unwrap(),
            )])),
        };
        let key_id = case.get("key_id").and_then(J::as_str).unwrap_or("k");
        let message = hex::decode(case["message"].as_str().unwrap()).unwrap();
        let signature = hex::decode(case["signature"].as_str().unwrap()).unwrap();
        let got = outcome(ring.verify(key_id, &message, &signature), |()| J::Null);
        assert_eq!(got, case["result"], "{}", case["name"]);
    }
}

/// Cases whose Rust outcome is an agreed difference (`docs/rust-port/differences.md`).
fn declared_difference(name: &str) -> Option<J> {
    // Row 1: an integer wider than 64 bits is refused when the JSON is read.
    (name == "big integer size")
        .then(|| json!({"error": ["invalid_metadata", "Release metadata could not be read."]}))
}

#[test]
fn manifests_are_accepted_and_refused_like_python() {
    let ring = key_ring();
    for case in rows("manifests") {
        let name = case["name"].as_str().unwrap();
        let current = Version::parse(&case["current"]).unwrap();
        let context = Context {
            current: &current,
            target: case["target"].as_str().unwrap(),
            distribution: case["distribution"].as_str().unwrap(),
            keyring: &ring,
        };
        let raw = raw(&case["raw"]);
        let result = if case["recovery"] == true {
            parse_current_recovery_manifest(&raw, context)
        } else {
            parse_manifest(&raw, context)
        };
        let expected = declared_difference(name).unwrap_or_else(|| case["result"].clone());
        assert_eq!(outcome(result, summary), expected, "{name}");
    }
}

/// `ScriptedTransport` and `ScriptedResponse` of the generator.
struct Scripted {
    scripts: serde_json::Map<String, J>,
    opened: Mutex<Vec<String>>,
    reads: Mutex<Vec<Vec<usize>>>,
}

struct ScriptedResponse<'a> {
    owner: &'a Scripted,
    headers: Vec<(String, String)>,
    chunks: std::collections::VecDeque<Vec<u8>>,
    fail_at: Option<u64>,
    cancel_at: Option<u64>,
    count: u64,
    cancel: Cancel,
    requests: Vec<usize>,
}

fn segment(value: &J) -> Vec<u8> {
    if let Some(fill) = value.get("fill") {
        hex::decode(fill.as_str().unwrap()).unwrap().repeat(value["count"].as_u64().unwrap() as usize)
    } else {
        hex::decode(value["hex"].as_str().unwrap()).unwrap()
    }
}

impl Scripted {
    fn new(scripts: &J) -> Self {
        Self {
            scripts: scripts.as_object().unwrap().clone(),
            opened: Mutex::default(),
            reads: Mutex::default(),
        }
    }
}

impl Transport for Scripted {
    fn open<'a>(&'a self, url: &str, cancel: &Cancel) -> CodedResult<Box<dyn Response + 'a>> {
        self.opened.lock().unwrap().push(url.to_string());
        let Some(script) = self.scripts.get(url) else {
            return Err(CodedError::new(
                "service_unavailable",
                "The official release service is unavailable. Try again later.",
            ));
        };
        if let Some(error) = script.get("open_error") {
            return Err(CodedError::new(error[0].as_str().unwrap(), error[1].as_str().unwrap()));
        }
        let headers = script
            .get("headers")
            .and_then(J::as_array)
            .map(|rows| {
                rows.iter()
                    .map(|row| (row[0].as_str().unwrap().to_string(), row[1].as_str().unwrap().to_string()))
                    .collect()
            })
            .unwrap_or_default();
        let chunks = script
            .get("chunks")
            .and_then(J::as_array)
            .map(|rows| rows.iter().map(segment).collect())
            .unwrap_or_default();
        Ok(Box::new(ScriptedResponse {
            owner: self,
            headers,
            chunks,
            fail_at: script.get("fail_at").and_then(J::as_u64),
            cancel_at: script.get("cancel_at").and_then(J::as_u64),
            count: 0,
            cancel: cancel.clone(),
            requests: Vec::new(),
        }))
    }
}

impl Response for ScriptedResponse<'_> {
    fn headers(&self) -> &[(String, String)] {
        &self.headers
    }

    fn read(&mut self, maximum: usize) -> std::io::Result<Vec<u8>> {
        self.requests.push(maximum);
        let index = self.count;
        self.count += 1;
        if self.cancel_at == Some(index) {
            self.cancel.set();
        }
        if self.fail_at == Some(index) {
            return Err(std::io::Error::new(std::io::ErrorKind::ConnectionReset, "dropped"));
        }
        let Some(chunk) = self.chunks.pop_front() else {
            return Ok(Vec::new());
        };
        if chunk.len() > maximum {
            self.chunks.push_front(chunk[maximum..].to_vec());
            return Ok(chunk[..maximum].to_vec());
        }
        Ok(chunk)
    }
}

impl Drop for ScriptedResponse<'_> {
    fn drop(&mut self) {
        self.owner.reads.lock().unwrap().push(std::mem::take(&mut self.requests));
    }
}

#[test]
fn metadata_reads_like_python() {
    use sha2::Digest;
    for case in rows("metadata") {
        let url = sylvia_owner::desktop_update::manifest::OFFICIAL_RELEASES;
        let scripted = Scripted::new(&json!({ url: case["script"] }));
        let cancel = Cancel::default();
        if case.get("cancelled") == Some(&json!(true)) {
            cancel.set();
        }
        let got = outcome(metadata_bytes(&scripted, url, &cancel), |raw| {
            json!(hex::encode(sha2::Sha256::digest(raw)))
        });
        assert_eq!(got, case["result"], "{}", case["name"]);
        assert_eq!(json!(*scripted.reads.lock().unwrap()), case["reads"], "{}", case["name"]);
    }
}

#[test]
fn discovery_selects_like_python() {
    let ring = key_ring();
    let current = Version::parse_text(APP_VERSION).unwrap();
    for case in rows("discovery") {
        let scripted = Scripted::new(&case["scripts"]);
        let context = Context {
            current: &current,
            target: "windows-x86_64",
            distribution: case["distribution"].as_str().unwrap(),
            keyring: &ring,
        };
        let cancel = Cancel::default();
        let got = if case["retain"] == true {
            outcome(retain_current_manifest(&scripted, &cancel, context), summary)
        } else {
            outcome(discover(&scripted, &cancel, context), |found| found.map_or(J::Null, summary))
        };
        assert_eq!(got, case["result"], "{}", case["name"]);
        assert_eq!(json!(*scripted.opened.lock().unwrap()), case["opened"], "{}", case["name"]);
    }
}

struct Folder(PathBuf);

impl Drop for Folder {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn temporary_folder(name: &str) -> Folder {
    let base = std::env::temp_dir().join(format!("sylvia-update-vectors-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base).unwrap();
    // `tempfile.mkdtemp` gives a resolved folder; resolve so the alias checks see no alias.
    let resolved = std::fs::canonicalize(&base).unwrap();
    let text = resolved.to_string_lossy().to_string();
    Folder(PathBuf::from(text.strip_prefix(r"\\?\").unwrap_or(&text)))
}

#[test]
fn downloads_stage_like_python() {
    use sha2::Digest;
    let ring = key_ring();
    let current = Version::parse_text(APP_VERSION).unwrap();
    for (index, case) in rows("downloads").iter().enumerate() {
        let name = case["name"].as_str().unwrap();
        let context =
            Context { current: &current, target: "windows-x86_64", distribution: "portable", keyring: &ring };
        let manifest = parse_manifest(&raw(&case["envelope"]), context).unwrap();
        let folder = temporary_folder(&format!("download-{index}"));
        let stage = folder.0.join("stage");
        std::fs::create_dir(&stage).unwrap();
        for entry in case["preexisting"].as_array().unwrap() {
            std::fs::write(stage.join(entry.as_str().unwrap()), b"x").unwrap();
        }
        let scripted = Scripted::new(&json!({ manifest.artifact_url.clone(): case["script"] }));
        let cancel = Cancel::default();
        if case["cancelled"] == true {
            cancel.set();
        }
        let mut progress = Vec::new();
        let result = download_artifact(&scripted, &manifest, &stage, &cancel, &mut |received, total| {
            progress.push(json!([received, total.map(|total| total as u64)]));
        });
        let got = outcome(result, |path| json!(path.file_name().unwrap().to_string_lossy()));
        assert_eq!(got, case["result"], "{name}");
        assert_eq!(json!(progress), case["progress"], "{name}");
        let mut files = serde_json::Map::new();
        let mut entries: Vec<_> =
            std::fs::read_dir(&stage).unwrap().map(|entry| entry.unwrap().path()).collect();
        entries.sort();
        for entry in entries {
            files.insert(
                entry.file_name().unwrap().to_string_lossy().to_string(),
                json!(hex::encode(sha2::Sha256::digest(std::fs::read(&entry).unwrap()))),
            );
        }
        assert_eq!(J::Object(files), case["files"], "{name}");
        assert_eq!(json!(*scripted.reads.lock().unwrap()), case["reads"], "{name}");
    }
}
