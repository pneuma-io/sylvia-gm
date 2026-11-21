//! The WebUI host's exports, downloads and uploads against
//! `vectors/generate_webui_file_vectors.py`: the real `sylvia-gm` host, started with
//! `SYLVIA_GM_OWNER_EXECUTABLE` naming this test binary, which then runs as the generator's
//! stand-in owner (`STANDIN` there) over the owner pipe. Every recorded step is sent as the
//! same raw HTTP/1.1 request, and the response, what the owner was handed and the data folder
//! must be what Python's `LocalWebUI` gave. (`harness = false`: the stand-in must answer
//! before any test harness reads its arguments.)

use serde_json::{json, Value as J};
use sha2::Digest;
use std::collections::{BTreeMap, BTreeSet};
use std::io::{BufRead, Read, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

/// The log the stand-in owner appends each call to; set only for the stand-in.
const STANDIN: &str = "SYLVIA_WEBUI_STANDIN_LOG";

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/webui_file_vectors.json")).expect("valid JSON")
}

fn sha256(bytes: &[u8]) -> String {
    hex::encode(sha2::Sha256::digest(bytes))
}

// ---- the stand-in owner ----------------------------------------------------------------------

/// The generator's `SUFFIXES`.
fn suffix(method: &str) -> Option<(&'static str, &'static str)> {
    Some(match method {
        "export_campaign_file" => ("exports", ".sylvia-gm-campaign"),
        "export_package_candidate_file" => ("exports", ".sylvia-gm-package"),
        "export_campaign_plan_file" => ("exports", ".sylvia-gm-plan"),
        "backup_database" => ("backups", ".sqlite3"),
        _ => return None,
    })
}

/// `export_bytes(method, identity)`: 1,000 deterministic bytes.
fn export_bytes(method: &str, identity: &str) -> Vec<u8> {
    format!("{method}:{identity}:").into_bytes().into_iter().cycle().take(1000).collect()
}

/// `relative_source(data_root, path)`.
fn relative_source(data_root: &Path, path: &str) -> String {
    let relative = Path::new(path).strip_prefix(data_root).expect("under the data folder");
    let text = relative.to_string_lossy().replace('\\', "/");
    regex::Regex::new(r"^\.webui-upload-[^/]+").unwrap().replace(&text, "<upload>").into_owned()
}

/// `StandIn.call(method, params)`: the result, or the error the owner replies with.
fn standin_call(
    data_root: &Path,
    method: &str,
    params: &serde_json::Map<String, J>,
) -> (J, Result<J, (String, String)>) {
    let mut record = json!({"method": method, "params": {}});
    for (key, value) in params {
        if key == "source_path" {
            let path = value.as_str().unwrap();
            record["params"][key] = json!(relative_source(data_root, path));
            let raw = std::fs::read(path).unwrap();
            let name = Path::new(path).file_name().unwrap().to_string_lossy().into_owned();
            record["file"] = json!({"name": name, "size": raw.len(), "sha256": sha256(&raw)});
        } else if key == "source_paths" {
            let paths: Vec<&str> =
                value.as_array().unwrap().iter().map(|item| item.as_str().unwrap()).collect();
            record["params"][key] =
                json!(paths.iter().map(|path| relative_source(data_root, path)).collect::<Vec<_>>());
            record["files"] =
                json!(paths.iter().map(|path| sha256(&std::fs::read(path).unwrap())).collect::<Vec<_>>());
        } else {
            record["params"][key] = value.clone();
        }
    }
    let refuse = |code: &str, message: &str| Err((code.to_owned(), message.to_owned()));
    let file_name = record["file"]["name"].as_str().unwrap_or_default().to_owned();
    let outcome = if let Some((folder, suffix)) = suffix(method) {
        let key = if method == "backup_database" { "backup_id" } else { "export_id" };
        let identity = params.get(key).and_then(J::as_str).unwrap_or_default();
        if identity == "rejected" {
            refuse("command_rejected", "The stand-in refused this export.")
        } else {
            if identity != "missing" {
                std::fs::create_dir_all(data_root.join(folder)).unwrap();
                std::fs::write(
                    data_root.join(folder).join(format!("{identity}{suffix}")),
                    export_bytes(method, identity),
                )
                .unwrap();
            }
            Ok(json!({"export": {"id": identity, "method": method}, "path": "not for the browser"}))
        }
    } else {
        match method {
            "install_package_file" if file_name.contains("repair") => {
                Ok(json!({"status": "repairable", "reason": "stand-in"}))
            }
            "install_package_file" => Ok(json!({"status": "ready", "package": file_name})),
            "recover_campaign_file" => Ok(json!({"campaign_id": "recovered-1"})),
            "preview_character_card_file" if file_name.ends_with(".json") => {
                refuse("invalid_card", "The stand-in refuses JSON cards.")
            }
            "preview_character_card_file" => Ok(json!({"name": "Stand-in Character"})),
            "ingest_builder_sources" => {
                Ok(json!({"accepted": params.get("source_paths").and_then(J::as_array).map_or(0, Vec::len)}))
            }
            _ => refuse("unsupported", &format!("The stand-in does not answer {method}.")),
        }
    };
    (record, outcome)
}

/// The owner side of the pipe: the launch handshake, then one reply per request.
fn standin(log: &Path) {
    let stdin = std::io::stdin();
    let mut lines = stdin.lock().lines();
    let mut stdout = std::io::stdout();
    let mut send = |value: J| {
        writeln!(stdout, "{value}").unwrap();
        stdout.flush().unwrap();
    };
    let launch: J = serde_json::from_str(&lines.next().unwrap().unwrap()).unwrap();
    let data_root = Path::new(launch["database_path"].as_str().unwrap()).parent().unwrap().to_path_buf();
    let response =
        sylvia_store::launch::derive_owner_response(launch["secret_hex"].as_str().unwrap()).unwrap();
    send(json!({"type": "ready", "ok": true, "response_hex": response}));
    let mut journal = std::fs::OpenOptions::new().create(true).append(true).open(log).unwrap();
    for line in lines {
        let request: J = serde_json::from_str(&line.unwrap()).unwrap();
        let (id, method) = (request["id"].clone(), request["method"].as_str().unwrap().to_owned());
        let params = request["params"].as_object().cloned().unwrap_or_default();
        if method == "shutdown" {
            writeln!(journal, "{}", json!({"method": "shutdown", "params": {}})).unwrap();
            send(json!({"id": id, "ok": true, "result": {}}));
            return;
        }
        let (record, outcome) = standin_call(&data_root, &method, &params);
        writeln!(journal, "{record}").unwrap();
        match outcome {
            Ok(result) => send(json!({"id": id, "ok": true, "result": result})),
            Err((code, message)) => {
                send(json!({"id": id, "ok": false, "error": {"code": code, "message": message}}))
            }
        }
    }
}

// ---- the driver ------------------------------------------------------------------------------

fn multipart(parts: &[J], boundary: &str) -> Vec<u8> {
    let mut body = Vec::new();
    for part in parts {
        let mut disposition = format!("form-data; name=\"{}\"", part["name"].as_str().unwrap());
        if let Some(filename) = part.get("filename").and_then(J::as_str) {
            disposition.push_str(&format!("; filename=\"{filename}\""));
        }
        body.extend(format!("--{boundary}\r\nContent-Disposition: {disposition}\r\n").into_bytes());
        for header in part.get("headers").and_then(J::as_array).into_iter().flatten() {
            body.extend(
                format!("{}: {}\r\n", header[0].as_str().unwrap(), header[1].as_str().unwrap()).into_bytes(),
            );
        }
        body.extend(b"\r\n");
        body.extend(
            sylvia_owner::desktop_update::manifest::b64decode_strict(part["b64"].as_str().unwrap()).unwrap(),
        );
        body.extend(b"\r\n");
    }
    body.extend(format!("--{boundary}--\r\n").into_bytes());
    body
}

/// `request_bytes(step, values)`.
fn request_bytes(step: &J, values: &BTreeMap<String, String>, boundary: &str) -> Vec<u8> {
    let fill = |text: &str| {
        let mut text = text.to_owned();
        for (key, value) in values {
            text = text.replace(&format!("${{{key}}}"), value);
        }
        text
    };
    let method = step["method"].as_str().unwrap();
    let mut lines = vec![
        format!("{method} {} HTTP/1.1", fill(step["path"].as_str().unwrap())),
        format!("Host: {}", values["authority"]),
    ];
    for header in step["headers"].as_array().unwrap() {
        lines.push(format!("{}: {}", header[0].as_str().unwrap(), fill(header[1].as_str().unwrap())));
    }
    let mut body = Vec::new();
    if step.get("json").is_some_and(|value| !value.is_null()) {
        body = step["json"].to_string().into_bytes();
        lines.push("Content-Type: application/json".into());
    } else if let Some(parts) = step.get("multipart").and_then(J::as_array) {
        body = multipart(parts, boundary);
        lines.push(format!("Content-Type: multipart/form-data; boundary={boundary}"));
    }
    if method == "POST" || !body.is_empty() {
        lines.push(format!("Content-Length: {}", body.len()));
    }
    lines.push("Connection: close".into());
    let mut raw = (lines.join("\r\n") + "\r\n\r\n").into_bytes();
    raw.extend(body);
    raw
}

/// `parse_response(data)`: status, reason, headers (lower-cased names) and the body.
fn parse_response(data: &[u8]) -> (u16, String, Vec<(String, String)>, Vec<u8>) {
    let split = data.windows(4).position(|window| window == b"\r\n\r\n").unwrap_or(data.len());
    let head: String = data[..split].iter().map(|byte| *byte as char).collect();
    let mut body = data.get(split + 4..).unwrap_or_default().to_vec();
    let mut lines = head.split("\r\n");
    let status_line = lines.next().unwrap();
    let mut words = status_line.splitn(3, ' ');
    let _ = words.next();
    let status = words.next().unwrap().parse().unwrap();
    let reason = words.next().unwrap_or_default().to_owned();
    let headers: Vec<(String, String)> = lines
        .map(|line| {
            let (name, value) = line.split_once(':').unwrap_or((line, ""));
            (name.trim().to_lowercase(), value.trim().to_owned())
        })
        .collect();
    if headers.iter().any(|(name, value)| name == "transfer-encoding" && value.contains("chunked")) {
        let (mut decoded, mut rest) = (Vec::new(), body.as_slice());
        while !rest.is_empty() {
            let line_end = rest.windows(2).position(|window| window == b"\r\n").unwrap();
            let size_text = String::from_utf8_lossy(&rest[..line_end]).to_string();
            let size = usize::from_str_radix(size_text.split(';').next().unwrap().trim(), 16).unwrap();
            rest = &rest[line_end + 2..];
            if size == 0 {
                break;
            }
            decoded.extend_from_slice(&rest[..size]);
            rest = &rest[size + 2..];
        }
        body = decoded;
    }
    (status, reason, headers, body)
}

fn describe(
    (status, reason, headers, body): &(u16, String, Vec<(String, String)>, Vec<u8>),
    values: &BTreeMap<String, String>,
    aliases: &[(String, String)],
) -> J {
    let clean = |text: &str| {
        let mut text = text
            .replace(&values["authority"], "127.0.0.1:<port>")
            .replace(&format!("sylvia_webui_{}", values["port"]), "sylvia_webui_<port>");
        for (token, name) in aliases {
            text = text.replace(token, name);
        }
        text
    };
    let cookie = regex::Regex::new("=([^;]*)").unwrap();
    let mut shown: Vec<(String, String)> = headers
        .iter()
        .filter(|(name, _)| !matches!(name.as_str(), "date" | "server" | "connection" | "keep-alive"))
        .map(|(name, value)| {
            let value = if name == "set-cookie" {
                cookie.replace(value, "=<cookie>").into_owned()
            } else {
                value.clone()
            };
            (clean(name), clean(&value))
        })
        .collect();
    shown.sort();
    let content_type =
        headers.iter().find(|(name, _)| name == "content-type").map_or("", |(_, value)| value.as_str());
    let shown_body = if content_type.starts_with("application/json") {
        json!({"json": serde_json::from_str::<J>(&clean(&String::from_utf8(body.clone()).unwrap())).unwrap()})
    } else if content_type.starts_with("text/") && body.len() <= 4096 {
        json!({"text": clean(&String::from_utf8(body.clone()).unwrap())})
    } else {
        let start: String = body.iter().take(24).map(|byte| *byte as char).collect();
        json!({"size": body.len(), "sha256": sha256(body), "start": start})
    };
    let shown: Vec<J> = shown.into_iter().map(|(name, value)| json!([name, value])).collect();
    json!({"status": status, "reason": reason, "headers": shown, "body": shown_body})
}

fn listing(data_root: &Path, aliases: &[(String, String)]) -> J {
    let downloads = regex::Regex::new(r"^\.webui-download-[^/]+").unwrap();
    let uploads = regex::Regex::new(r"^\.webui-upload-[^/]+").unwrap();
    let mut rows = Vec::new();
    let mut pending = vec![data_root.to_path_buf()];
    while let Some(folder) = pending.pop() {
        for entry in std::fs::read_dir(&folder).unwrap() {
            let path = entry.unwrap().path();
            let mut text = path.strip_prefix(data_root).unwrap().to_string_lossy().replace('\\', "/");
            text = downloads.replace(&text, "<downloads>").into_owned();
            text = uploads.replace(&text, "<upload>").into_owned();
            for (token, name) in aliases {
                text = text.replace(token, name);
            }
            if path.is_dir() {
                rows.push((text, "dir".to_owned()));
                pending.push(path);
            } else {
                rows.push((text, sha256(&std::fs::read(&path).unwrap())));
            }
        }
    }
    rows.sort();
    J::Array(rows.into_iter().map(|(path, value)| json!([path, value])).collect())
}

fn send(port: u16, raw: &[u8]) -> Vec<u8> {
    let mut stream = std::net::TcpStream::connect(("127.0.0.1", port)).unwrap();
    stream.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    stream.write_all(raw).unwrap();
    let mut data = Vec::new();
    stream.read_to_end(&mut data).unwrap();
    data
}

/// Pin each new download copy's modification time, as the generator does.
fn pin(data_root: &Path, pinned: &mut BTreeSet<PathBuf>, nanos: u64) {
    for entry in std::fs::read_dir(data_root).unwrap() {
        let folder = entry.unwrap().path();
        if !folder.file_name().unwrap().to_string_lossy().starts_with(".webui-download-") {
            continue;
        }
        for file in std::fs::read_dir(&folder).unwrap() {
            let path = file.unwrap().path();
            if pinned.insert(path.clone()) {
                let time = std::time::UNIX_EPOCH + Duration::from_nanos(nanos);
                std::fs::OpenOptions::new().write(true).open(&path).unwrap().set_modified(time).unwrap();
            }
        }
    }
}

fn first_difference(got: &J, expected: &J, path: &str) -> String {
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

fn drive() {
    let vectors = vectors();
    let boundary = vectors["boundary"].as_str().unwrap().to_owned();
    let pinned_ns = vectors["pinned_ns"].as_u64().unwrap();
    let base = std::env::temp_dir().join(format!("sylvia-webui-files-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(base.join("data")).unwrap();
    let base = PathBuf::from(sylvia_owner::util::plain_path(&std::fs::canonicalize(&base).unwrap()));
    let data_root = base.join("data");
    let log = base.join("owner-calls.jsonl");
    let mut host = std::process::Command::new(env!("CARGO_BIN_EXE_sylvia-gm"))
        .args(["--webui", "--data-root"])
        .arg(&data_root)
        .args(["--port", "0"])
        .env("SYLVIA_GM_OWNER_EXECUTABLE", std::env::current_exe().unwrap())
        .env(STANDIN, &log)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    let mut announcements = std::io::BufReader::new(host.stdout.take().unwrap()).lines();
    let port: u16 = loop {
        let line = announcements.next().expect("the host announces its address").unwrap();
        if let Some(rest) = line.strip_prefix("Sylvia WebUI ready: http://127.0.0.1:") {
            break rest.split('/').next().unwrap().parse().unwrap();
        }
    };
    std::thread::spawn(move || announcements.for_each(drop));
    let mut values = BTreeMap::from([
        ("origin".to_owned(), format!("http://127.0.0.1:{port}")),
        ("authority".to_owned(), format!("127.0.0.1:{port}")),
        ("port".to_owned(), port.to_string()),
        ("cookie".to_owned(), String::new()),
    ]);
    let mut aliases: Vec<(String, String)> = Vec::new();
    let mut pinned = BTreeSet::new();
    let mut calls_seen = 0;
    let mut results = Vec::new();
    let steps = vectors["steps"].as_array().unwrap();
    let mut files_before_cleanup = J::Null;
    for (index, step) in steps.iter().enumerate() {
        if index + 1 == steps.len() {
            // Python lists the folder after Stop, which changes no file; here the host removes
            // its downloads as soon as it stops.
            files_before_cleanup = listing(&data_root, &aliases);
        }
        let response = parse_response(&send(port, &request_bytes(step, &values, &boundary)));
        let (_, _, headers, body) = &response;
        if step.get("save_cookie") == Some(&J::Bool(true)) {
            let cookie = headers.iter().find(|(name, _)| name == "set-cookie").unwrap();
            values.insert("cookie".into(), cookie.1.split(';').next().unwrap().to_owned());
        }
        if headers.iter().any(|(name, value)| name == "content-type" && value.starts_with("application/json"))
        {
            let reply: J = serde_json::from_slice(body).unwrap();
            if let Some(download) =
                reply.get("result").and_then(|result| result.get("download")).and_then(J::as_str)
            {
                let token = download.rsplit('/').next().unwrap().to_owned();
                if !aliases.iter().any(|(known, _)| *known == token) {
                    let name = format!("<token:{}>", aliases.len() + 1);
                    aliases.push((token, name));
                }
                values.entry("first_download".into()).or_insert_with(|| download.to_owned());
                values.insert("download".into(), download.to_owned());
            }
        }
        if index + 1 < steps.len() {
            pin(&data_root, &mut pinned, pinned_ns);
        }
        if let Some((_, etag)) = headers.iter().find(|(name, _)| name == "etag") {
            values.insert("etag".into(), etag.clone());
        }
        let deadline = Instant::now() + Duration::from_secs(10);
        let expected_calls = vectors["recorded"]["steps"][index]["owner"].as_array().unwrap().len();
        let calls: Vec<J> = loop {
            let text = std::fs::read_to_string(&log).unwrap_or_default();
            let all: Vec<J> = text.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
            if all.len() >= calls_seen + expected_calls || Instant::now() > deadline {
                let new = all[calls_seen.min(all.len())..].to_vec();
                calls_seen = all.len();
                break new;
            }
            std::thread::sleep(Duration::from_millis(20));
        };
        results.push(
            json!({"name": step["name"], "response": describe(&response, &values, &aliases), "owner": calls}),
        );
    }
    let deadline = Instant::now() + Duration::from_secs(60);
    while host.try_wait().unwrap().is_none() {
        assert!(Instant::now() < deadline, "the host did not stop");
        std::thread::sleep(Duration::from_millis(50));
    }
    let got = json!({
        "steps": results,
        "files_before_cleanup": files_before_cleanup,
        "files_after_cleanup": listing(&data_root, &aliases),
    });
    let _ = std::fs::remove_dir_all(&base);
    let expected = &vectors["recorded"];
    for (index, step) in expected["steps"].as_array().unwrap().iter().enumerate() {
        let got_step = &got["steps"][index];
        assert!(
            got_step == step,
            "step {} ({}): {}",
            index,
            step["name"],
            first_difference(got_step, step, "")
        );
    }
    assert_eq!(got["files_before_cleanup"], expected["files_before_cleanup"]);
    assert_eq!(got["files_after_cleanup"], expected["files_after_cleanup"]);
    println!("webui_files: {} steps as Python", got["steps"].as_array().unwrap().len());
}

fn main() {
    if let Some(log) = std::env::var_os(STANDIN) {
        standin(Path::new(&log));
        return;
    }
    drive();
}
