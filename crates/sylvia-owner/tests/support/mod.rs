//! Test helpers shared by the desktop update vector tests: the generators' scripted transport,
//! a temporary folder that is removed afterwards, and the scenario record (outcomes, file
//! listings and their normalization) the preparation and helper generators share.

#![allow(dead_code)]

use serde_json::{json, Value as J};
use std::path::Path;
use std::path::PathBuf;
use std::sync::Mutex;
use sylvia_base::CodedResult;
use sylvia_owner::desktop_update::storage::is_update_error;
use sylvia_owner::desktop_update::transport::{Cancel, Response, Transport};

/// `ScriptedTransport` and `ScriptedResponse` of the generator.
pub struct Scripted {
    pub scripts: serde_json::Map<String, J>,
    pub opened: Mutex<Vec<String>>,
    pub reads: Mutex<Vec<Vec<usize>>>,
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

pub fn segment(value: &J) -> Vec<u8> {
    if let Some(fill) = value.get("fill") {
        hex::decode(fill.as_str().unwrap()).unwrap().repeat(value["count"].as_u64().unwrap() as usize)
    } else {
        hex::decode(value["hex"].as_str().unwrap()).unwrap()
    }
}

impl Scripted {
    pub fn new(scripts: &J) -> Self {
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
            return Err(sylvia_owner::desktop_update::update_error(
                "service_unavailable",
                "The official release service is unavailable. Try again later.",
            ));
        };
        if let Some(error) = script.get("open_error") {
            return Err(sylvia_owner::desktop_update::update_error(
                error[0].as_str().unwrap(),
                error[1].as_str().unwrap(),
            ));
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

pub struct Folder(pub PathBuf);

impl Drop for Folder {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

pub fn temporary_folder(name: &str) -> Folder {
    let base = std::env::temp_dir().join(format!("sylvia-update-vectors-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base).unwrap();
    // `tempfile.mkdtemp` gives a resolved folder; resolve so the alias checks see no alias.
    let resolved = std::fs::canonicalize(&base).unwrap();
    let text = resolved.to_string_lossy().to_string();
    Folder(PathBuf::from(text.strip_prefix(r"\\?\").unwrap_or(&text)))
}

/// Python's `outcome(call)`: an `UpdateError` by code and message, any other exception by class.
pub fn outcome(result: CodedResult<J>) -> J {
    match result {
        Ok(value) => json!({"ok": value}),
        Err(error) if is_update_error(&error) => json!({"error": [error.code, error.message]}),
        Err(error) => json!({"other": error.error_type.unwrap_or("Exception")}),
    }
}

pub fn listing(root: &Path) -> J {
    use sha2::Digest;
    // Each restored file gets its own random temporary folder; its name is folded as the
    // generator folds it.
    let temp = regex::Regex::new("((?:^|/)(?:restoring|restore-payload)/)[0-9a-f]{32}").unwrap();
    let mut rows = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(folder) = pending.pop() {
        for entry in std::fs::read_dir(&folder).unwrap() {
            let path = entry.unwrap().path();
            let relative = path.strip_prefix(root).unwrap().to_string_lossy().replace('\\', "/");
            let relative = temp.replace_all(&relative, "${1}<temp>").to_string();
            if path.is_dir() {
                rows.push(json!([relative, "dir"]));
                pending.push(path);
                continue;
            }
            let raw = std::fs::read(&path).unwrap();
            let parsed = (relative.ends_with(".json") && relative.contains(".application-updates/"))
                .then(|| serde_json::from_slice::<J>(&raw).ok())
                .flatten();
            rows.push(json!([
                relative,
                parsed.unwrap_or_else(|| json!(hex::encode(sha2::Sha256::digest(&raw))))
            ]));
        }
    }
    // Sorted by the relative path text before aliasing, as the generator does.
    rows.sort_by(|left, right| left[0].as_str().cmp(&right[0].as_str()));
    J::Array(rows)
}

pub fn normalize(value: &J) -> J {
    match value {
        J::Object(map) => J::Object(
            map.iter()
                .map(|(key, item)| {
                    let item = if key == "data_identity" || key == "app_identity" {
                        json!("<identity>")
                    } else {
                        normalize(item)
                    };
                    (key.clone(), item)
                })
                .collect(),
        ),
        J::Array(items) => J::Array(items.iter().map(normalize).collect()),
        other => other.clone(),
    }
}

pub fn alias(value: &J) -> J {
    let text = String::from_utf8(sylvia_base::pyjson::canonical_bytes(value)).unwrap();
    let pattern = regex::Regex::new("[0-9a-f]+").unwrap();
    let mut seen: Vec<String> = Vec::new();
    let replaced = pattern.replace_all(&text, |found: &regex::Captures<'_>| {
        let found = found[0].to_string();
        if found.len() != 32 {
            return found;
        }
        let index = seen.iter().position(|known| *known == found).unwrap_or_else(|| {
            seen.push(found);
            seen.len() - 1
        });
        format!("<id:{}>", index + 1)
    });
    serde_json::from_str(&replaced).unwrap()
}

pub fn record(results: Vec<J>, files: J) -> J {
    let mut aliased = alias(&json!([normalize(&J::Array(results)), normalize(&files)]));
    let mut files = aliased[1].take();
    if let Some(rows) = files.as_array_mut() {
        rows.sort_by(|left, right| left[0].as_str().cmp(&right[0].as_str()));
    }
    json!({"results": aliased[0].take(), "files": files})
}

/// Where two values first differ, as a JSON pointer with both values.
pub fn first_difference(got: &J, expected: &J, path: &str) -> String {
    match (got, expected) {
        (J::Object(left), J::Object(right)) => {
            for key in left.keys().chain(right.keys()) {
                let (a, b) = (left.get(key).unwrap_or(&J::Null), right.get(key).unwrap_or(&J::Null));
                if a != b {
                    return first_difference(a, b, &format!("{path}/{key}"));
                }
            }
        }
        (J::Array(left), J::Array(right)) if left.len() == right.len() => {
            for (index, (a, b)) in left.iter().zip(right).enumerate() {
                if a != b {
                    return first_difference(a, b, &format!("{path}/{index}"));
                }
            }
        }
        _ => {}
    }
    format!("{path}: got {got}, expected {expected}")
}
