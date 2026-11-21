//! Test helpers shared by the desktop update vector tests: the generators' scripted transport
//! and a temporary folder that is removed afterwards.

#![allow(dead_code)]

use serde_json::Value as J;
use std::path::PathBuf;
use std::sync::Mutex;
use sylvia_base::CodedResult;
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
