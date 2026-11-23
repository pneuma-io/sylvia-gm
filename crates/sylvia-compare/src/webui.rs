//! WebUI host sessions: start a WebUI host on a fresh data folder, send it HTTP requests exactly
//! as a session writes them, and record every response (status, reason, headers, body).
//!
//! The client is a small HTTP/1.1 writer on `std::net` so a session can send what a browser
//! never would: a wrong or repeated `Host`, a missing header, a body larger than a limit, a
//! chunked body that grows past one. Each request uses its own connection (`Connection:
//! close`), so one response cannot leak into the next.

use crate::owner::OwnerCommand;
use serde::Deserialize;
use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::time::{Duration, Instant};
use sylvia_base::{Json, JsonMap};

/// One HTTP request step.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HttpStep {
    /// `GET`, `POST`, ...
    pub method: String,
    /// Request target, for example `/api/command`.
    pub path: String,
    /// Headers in order, as `[name, value]`. `Host` is added first unless named here or
    /// `no_host` is set. `Content-Length` (or chunked framing) and `Connection: close` are added.
    #[serde(default)]
    pub headers: Vec<(String, String)>,
    /// Send no `Host` header at all.
    #[serde(default)]
    pub no_host: bool,
    /// A JSON body, written compactly.
    #[serde(default)]
    pub json: Option<Json>,
    /// A text body, written as is.
    #[serde(default)]
    pub text: Option<String>,
    /// A body of this many `a` bytes, for size limits.
    #[serde(default)]
    pub repeat: Option<usize>,
    /// A `multipart/form-data` body; the `Content-Type` header is added.
    #[serde(default)]
    pub multipart: Option<Vec<Part>>,
    /// Send the body with chunked framing, in pieces of this many bytes.
    #[serde(default)]
    pub chunked: Option<usize>,
}

/// One multipart part.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Part {
    /// The field name.
    pub name: String,
    /// The file name, for a file part.
    #[serde(default)]
    pub filename: Option<String>,
    /// Extra part headers, as `[name, value]`.
    #[serde(default)]
    pub headers: Vec<(String, String)>,
    /// The part's content.
    #[serde(default)]
    pub content: String,
    /// Content made of this many `a` bytes instead, for size limits.
    #[serde(default)]
    pub repeat: Option<usize>,
    /// Content taken from the response body of the earlier step with this id (a download sent
    /// back as an upload). The session runner fills `content_bytes` from it.
    #[serde(default)]
    pub content_from_step: Option<String>,
    /// Content read from this checkout file (relative to the checkout root), byte for byte.
    #[serde(default)]
    pub content_from_file: Option<String>,
    /// The bytes `content_from_step` or `content_from_file` names, filled in before the step is
    /// sent.
    #[serde(skip)]
    pub content_bytes: Option<Vec<u8>>,
}

const BOUNDARY: &str = "sylvia-compare-boundary-5f2c";
/// Response text up to this size is recorded whole; larger bodies by hash.
const INLINE_BODY_BYTES: usize = 16 * 1024;

/// Private HTTPS mode for a WebUI session: the host is started with this origin and a key
/// file holding `key`, on a fixed free port, as a reverse proxy would reach it.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Remote {
    /// `--webui-remote-origin`, for example `https://node.tail1.ts.net`.
    pub origin: String,
    /// The access key written to the `--webui-access-key-file`.
    pub key: String,
}

/// A running WebUI host.
pub struct Host {
    child: Child,
    lines: Receiver<Option<String>>,
    /// Every line the host printed, in order.
    pub output: Vec<String>,
    /// `http://127.0.0.1:<port>`.
    pub origin: String,
    /// The `Host` the browser sends: `127.0.0.1:<port>` locally, the remote origin's authority
    /// in private HTTPS mode.
    pub authority: String,
    /// Where the host listens: `127.0.0.1:<port>`.
    pub address: String,
}

impl Host {
    /// Start `command` with `--webui --data-root <data> --port 0` appended and wait for its
    /// ready line.
    pub fn launch(
        command: &OwnerCommand,
        data_root: &std::path::Path,
        remote: Option<&Remote>,
        timeout: Duration,
    ) -> Result<Self, String> {
        // The host runs beside the data folder. On Linux a relative program path is looked up
        // after that change of folder (on Windows before it), so make a path absolute here.
        let program = std::path::Path::new(&command.program);
        let program = if program.components().count() > 1 && program.is_relative() {
            std::env::current_dir().map_err(|error| error.to_string())?.join(program)
        } else {
            program.to_path_buf()
        };
        let mut process = Command::new(&program);
        process.args(&command.args).arg("--webui").arg("--data-root").arg(data_root);
        match remote {
            None => {
                process.arg("--port").arg("0");
            }
            Some(remote) => {
                let key_file = data_root.parent().unwrap_or(data_root).join("webui-access-key.txt");
                std::fs::write(&key_file, format!("{}\n", remote.key))
                    .map_err(|error| format!("could not write the access-key file: {error}"))?;
                // A free port, released just before the host binds it.
                let port = std::net::TcpListener::bind("127.0.0.1:0")
                    .and_then(|listener| listener.local_addr())
                    .map_err(|error| format!("no free port: {error}"))?
                    .port();
                process
                    .arg("--port")
                    .arg(port.to_string())
                    .arg("--webui-remote-origin")
                    .arg(&remote.origin)
                    .arg("--webui-access-key-file")
                    .arg(&key_file);
            }
        }
        process
            .envs(command.env.iter().cloned())
            .current_dir(data_root.parent().unwrap_or(data_root))
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        let mut child =
            process.spawn().map_err(|error| format!("could not start {}: {error}", command.program))?;
        let stdout = child.stdout.take().expect("stdout is piped");
        let (sender, lines) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                if sender.send(Some(line)).is_err() {
                    return;
                }
            }
            let _ = sender.send(None);
        });
        let mut host = Self {
            child,
            lines,
            output: Vec::new(),
            origin: String::new(),
            authority: String::new(),
            address: String::new(),
        };
        let deadline = Instant::now() + timeout;
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            match host.lines.recv_timeout(remaining) {
                Ok(Some(line)) => {
                    let ready = match remote {
                        None => line
                            .strip_prefix("Sylvia WebUI ready: ")
                            .and_then(|rest| rest.strip_suffix("/app.html"))
                            .map(str::to_owned),
                        Some(_) => line
                            .strip_prefix("Loopback proxy target: ")
                            .and_then(|rest| rest.split(';').next())
                            .map(str::to_owned),
                    };
                    host.output.push(line);
                    if let Some(loopback) = ready {
                        host.address = loopback.trim_start_matches("http://").to_owned();
                        match remote {
                            None => {
                                host.authority = host.address.clone();
                                host.origin = loopback;
                            }
                            Some(remote) => {
                                host.authority = remote.origin.trim_start_matches("https://").to_owned();
                                host.origin = remote.origin.clone();
                            }
                        }
                        // The instruction line follows at once.
                        if let Ok(Some(next)) = host.lines.recv_timeout(Duration::from_secs(5)) {
                            host.output.push(next);
                        }
                        return Ok(host);
                    }
                }
                Ok(None) => return Err(format!("the host exited before it was ready: {:?}", host.output)),
                Err(_) => return Err(format!("the host was not ready within {} s", timeout.as_secs())),
            }
        }
    }

    /// Wait for the host to exit after a Stop, collecting what it printed. A host still
    /// running at the deadline is stopped (only this tool's own child).
    pub fn finish(mut self, timeout: Duration) -> (Option<i32>, Vec<String>, Option<String>) {
        let deadline = Instant::now() + timeout;
        let mut problem = None;
        let code = loop {
            match self.child.try_wait() {
                Ok(Some(status)) => break status.code(),
                Ok(None) if Instant::now() >= deadline => {
                    let _ = self.child.kill();
                    let _ = self.child.wait();
                    problem =
                        Some(format!("the host did not exit within {} s and was stopped", timeout.as_secs()));
                    break None;
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(50)),
                Err(error) => {
                    problem = Some(format!("could not wait for the host: {error}"));
                    break None;
                }
            }
        };
        while let Ok(Some(line)) = self.lines.recv_timeout(Duration::from_millis(500)) {
            self.output.push(line);
        }
        (code, self.output, problem)
    }
}

/// The request bytes for a step, and the headers as sent (for the recording).
fn request_bytes(step: &HttpStep, authority: &str) -> (Vec<u8>, Vec<(String, String)>) {
    let mut body = Vec::new();
    let mut headers: Vec<(String, String)> = Vec::new();
    let named = |name: &str| step.headers.iter().any(|(key, _)| key.eq_ignore_ascii_case(name));
    if !step.no_host && !named("Host") {
        headers.push(("Host".into(), authority.into()));
    }
    headers.extend(step.headers.iter().cloned());
    if let Some(json) = &step.json {
        body = json.to_string().into_bytes();
    } else if let Some(text) = &step.text {
        body = text.clone().into_bytes();
    } else if let Some(count) = step.repeat {
        body = vec![b'a'; count];
    } else if let Some(parts) = &step.multipart {
        for part in parts {
            body.extend_from_slice(format!("--{BOUNDARY}\r\n").as_bytes());
            let disposition = match &part.filename {
                Some(filename) => format!("form-data; name=\"{}\"; filename=\"{filename}\"", part.name),
                None => format!("form-data; name=\"{}\"", part.name),
            };
            body.extend_from_slice(format!("Content-Disposition: {disposition}\r\n").as_bytes());
            for (name, value) in &part.headers {
                body.extend_from_slice(format!("{name}: {value}\r\n").as_bytes());
            }
            body.extend_from_slice(b"\r\n");
            match (part.repeat, &part.content_bytes) {
                (Some(count), _) => body.resize(body.len() + count, b'a'),
                (None, Some(bytes)) => body.extend_from_slice(bytes),
                (None, None) => body.extend_from_slice(part.content.as_bytes()),
            }
            body.extend_from_slice(b"\r\n");
        }
        body.extend_from_slice(format!("--{BOUNDARY}--\r\n").as_bytes());
        if !named("Content-Type") {
            headers.push(("Content-Type".into(), format!("multipart/form-data; boundary={BOUNDARY}")));
        }
    }
    let has_body =
        step.json.is_some() || step.text.is_some() || step.repeat.is_some() || step.multipart.is_some();
    match step.chunked {
        Some(_) => headers.push(("Transfer-Encoding".into(), "chunked".into())),
        None if has_body || step.method == "POST" => {
            headers.push(("Content-Length".into(), body.len().to_string()))
        }
        None => {}
    }
    headers.push(("Connection".into(), "close".into()));
    let mut out = format!("{} {} HTTP/1.1\r\n", step.method, step.path).into_bytes();
    for (name, value) in &headers {
        out.extend_from_slice(format!("{name}: {value}\r\n").as_bytes());
    }
    out.extend_from_slice(b"\r\n");
    match step.chunked {
        Some(size) => {
            for piece in body.chunks(size.max(1)) {
                out.extend_from_slice(format!("{:x}\r\n", piece.len()).as_bytes());
                out.extend_from_slice(piece);
                out.extend_from_slice(b"\r\n");
            }
            out.extend_from_slice(b"0\r\n\r\n");
        }
        None => out.extend_from_slice(&body),
    }
    // Large bodies are recorded by size and hash, not inline.
    let mut recorded = headers;
    if body.len() > INLINE_BODY_BYTES {
        recorded.push((
            "<body>".into(),
            format!("{} bytes, sha256 {}", body.len(), sylvia_base::hash::sha256_hex(&body)),
        ));
    }
    (out, recorded)
}

/// A parsed response.
pub struct Response {
    /// Status code.
    pub status: u16,
    /// Reason phrase.
    pub reason: String,
    /// Headers, lowercased names, in received order.
    pub headers: Vec<(String, String)>,
    /// The decoded body.
    pub body: Vec<u8>,
}

impl Response {
    /// The values of one header.
    pub fn header_values(&self, name: &str) -> Vec<&str> {
        self.headers.iter().filter(|(key, _)| key == name).map(|(_, value)| value.as_str()).collect()
    }
}

/// Send one step and read its response. The server may close the connection before reading
/// the whole body (a size limit); whatever response it sent is still read.
pub fn send(
    step: &HttpStep,
    authority: &str,
    address: &str,
    timeout: Duration,
) -> Result<(Response, Vec<(String, String)>), String> {
    let (bytes, sent) = request_bytes(step, authority);
    let mut stream =
        TcpStream::connect(address).map_err(|error| format!("could not connect to the host: {error}"))?;
    stream.set_read_timeout(Some(timeout)).map_err(|error| error.to_string())?;
    // A refused write (the server answered early and closed) is not an error here.
    let writer = {
        let mut stream = stream.try_clone().map_err(|error| error.to_string())?;
        std::thread::spawn(move || {
            for piece in bytes.chunks(64 * 1024) {
                if stream.write_all(piece).is_err() {
                    return;
                }
            }
            let _ = stream.flush();
        })
    };
    let mut raw = Vec::new();
    let read = stream.read_to_end(&mut raw);
    let _ = writer.join();
    if raw.is_empty() {
        return Err(format!("the host sent no response ({read:?})"));
    }
    Ok((parse_response(&raw)?, sent))
}

fn parse_response(raw: &[u8]) -> Result<Response, String> {
    let end = raw.windows(4).position(|window| window == b"\r\n\r\n").ok_or("response head is incomplete")?;
    let head = std::str::from_utf8(&raw[..end]).map_err(|_| "response head is not UTF-8")?;
    let mut lines = head.split("\r\n");
    let status_line = lines.next().ok_or("empty response")?;
    let mut parts = status_line.splitn(3, ' ');
    let _version = parts.next();
    let status: u16 = parts.next().and_then(|code| code.parse().ok()).ok_or("bad status line")?;
    let reason = parts.next().unwrap_or("").to_owned();
    let headers: Vec<(String, String)> = lines
        .filter_map(|line| line.split_once(':'))
        .map(|(name, value)| (name.trim().to_ascii_lowercase(), value.trim().to_owned()))
        .collect();
    let rest = &raw[end + 4..];
    let chunked = headers
        .iter()
        .any(|(name, value)| name == "transfer-encoding" && value.eq_ignore_ascii_case("chunked"));
    let body = if chunked { dechunk(rest) } else { rest.to_vec() };
    Ok(Response { status, reason, headers, body })
}

fn dechunk(mut rest: &[u8]) -> Vec<u8> {
    let mut body = Vec::new();
    while let Some(line_end) = rest.windows(2).position(|window| window == b"\r\n") {
        let size = std::str::from_utf8(&rest[..line_end])
            .ok()
            .and_then(|text| usize::from_str_radix(text.split(';').next().unwrap_or("").trim(), 16).ok());
        let Some(size) = size else { break };
        rest = &rest[line_end + 2..];
        if size == 0 || rest.len() < size {
            body.extend_from_slice(&rest[..size.min(rest.len())]);
            break;
        }
        body.extend_from_slice(&rest[..size]);
        rest = rest.get(size + 2..).unwrap_or_default();
    }
    body
}

/// The recorded form of a response: status, reason, headers as an object with sorted names
/// (a repeated header keeps its values in order; the hop-by-hop `Connection` and `Keep-Alive`
/// are left out), and the body.
pub fn record_response(response: &Response) -> Json {
    let mut grouped: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (name, value) in &response.headers {
        // Hop-by-hop framing, not the host's behaviour: every request asks to close.
        if name == "connection" || name == "keep-alive" {
            continue;
        }
        grouped.entry(name.clone()).or_default().push(value.clone());
    }
    let mut headers = JsonMap::new();
    for (name, values) in grouped {
        let value = if values.len() == 1 {
            Json::String(values.into_iter().next().unwrap_or_default())
        } else {
            Json::Array(values.into_iter().map(Json::String).collect())
        };
        headers.insert(name, value);
    }
    let content_type = response.header_values("content-type").first().copied().unwrap_or("").to_owned();
    let mut entry = JsonMap::new();
    entry.insert("status".into(), response.status.into());
    entry.insert("reason".into(), response.reason.clone().into());
    entry.insert("headers".into(), Json::Object(headers));
    let text = std::str::from_utf8(&response.body).ok();
    match (content_type.starts_with("application/json"), text) {
        (true, Some(text)) => match serde_json::from_str::<Json>(text) {
            Ok(parsed) => {
                entry.insert(
                    "body_encoding".into(),
                    sylvia_compare::dbdump::encoding_of(text, &parsed).into(),
                );
                entry.insert("json".into(), parsed);
            }
            Err(_) => {
                entry.insert("text".into(), text.into());
            }
        },
        (_, Some(text)) if response.body.len() <= INLINE_BODY_BYTES => {
            entry.insert("text".into(), text.into());
        }
        _ => {
            entry.insert("bytes".into(), response.body.len().into());
            entry.insert("sha256".into(), sylvia_base::hash::sha256_hex(&response.body).into());
        }
    }
    Json::Object(entry)
}

/// `name=value` of every cookie a response sets, joined for a `Cookie` header.
pub fn cookies(response: &Response) -> String {
    response
        .header_values("set-cookie")
        .iter()
        .filter_map(|value| value.split(';').next())
        .map(str::trim)
        .collect::<Vec<_>>()
        .join("; ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_requests_with_host_length_and_close() {
        let step: HttpStep = serde_json::from_value(serde_json::json!({
            "method": "POST", "path": "/api/command",
            "headers": [["Origin", "http://127.0.0.1:1"]], "json": {"id": "a"}
        }))
        .unwrap();
        let (bytes, _) = request_bytes(&step, "127.0.0.1:1");
        assert_eq!(
            String::from_utf8(bytes).unwrap(),
            "POST /api/command HTTP/1.1\r\nHost: 127.0.0.1:1\r\nOrigin: http://127.0.0.1:1\r\n\
             Content-Length: 10\r\nConnection: close\r\n\r\n{\"id\":\"a\"}"
        );
    }

    #[test]
    fn parses_chunked_responses_and_groups_headers() {
        let raw = b"HTTP/1.1 200 OK\r\nSet-Cookie: a=1; Path=/\r\nSet-Cookie: b=2\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n";
        let response = parse_response(raw).unwrap();
        assert_eq!(response.body, b"abc");
        assert_eq!(cookies(&response), "a=1; b=2");
        let recorded = record_response(&response);
        assert_eq!(recorded["headers"]["set-cookie"], serde_json::json!(["a=1; Path=/", "b=2"]));
        assert_eq!(recorded["text"], "abc");
    }
}
