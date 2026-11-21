//! Drive the built owner executable over its pipes, as the desktop shell does.

use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};

struct Session {
    child: std::process::Child,
    stdin: Option<std::process::ChildStdin>,
    stdout: BufReader<std::process::ChildStdout>,
}

impl Session {
    fn start() -> Self {
        let mut child = Command::new(env!("CARGO_BIN_EXE_sylvia-gm-owner"))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("the owner starts");
        let stdin = child.stdin.take();
        let stdout = BufReader::new(child.stdout.take().expect("stdout is piped"));
        Self { child, stdin, stdout }
    }

    fn send(&mut self, line: &str) {
        let stdin = self.stdin.as_mut().expect("stdin is open");
        stdin.write_all(line.as_bytes()).unwrap();
        stdin.write_all(b"\n").unwrap();
        stdin.flush().unwrap();
    }

    fn read(&mut self) -> String {
        let mut line = String::new();
        self.stdout.read_line(&mut line).unwrap();
        line.trim_end().to_owned()
    }

    fn launch(&mut self, folder: &std::path::Path) {
        let database = folder.join("authority.sqlite3");
        let envelope = serde_json::json!({
            "type": "launch", "secret_hex": "00".repeat(32),
            "database_path": database, "owner_id": "protocol-test",
        });
        self.send(&envelope.to_string());
        let expected = include_str!("vectors/handshake_zero.txt").trim();
        assert_eq!(self.read(), format!(r#"{{"ok":true,"type":"ready","response_hex":"{expected}"}}"#));
    }

    fn exit_code(mut self) -> Option<i32> {
        drop(self.stdin.take());
        self.child.wait().unwrap().code()
    }
}

fn scratch(name: &str) -> std::path::PathBuf {
    let folder = std::env::temp_dir().join(format!("sylvia-owner-{name}-{}", std::process::id()));
    std::fs::create_dir_all(&folder).unwrap();
    folder
}

#[test]
fn launch_validation_and_shutdown() {
    let folder = scratch("protocol");
    let mut owner = Session::start();
    owner.launch(&folder);

    // The same texts the Python owner gives for malformed requests.
    owner.send("not json");
    assert_eq!(
        owner.read(),
        r#"{"id":null,"ok":false,"error":{"code":"invalid_owner_request","message":"Owner message is not valid JSON"}}"#
    );
    owner.send("[1]");
    assert_eq!(
        owner.read(),
        r#"{"id":null,"ok":false,"error":{"code":"invalid_owner_request","message":"Owner message must be a JSON object"}}"#
    );
    owner.send(r#"{"id":"","method":"campaign_index"}"#);
    assert_eq!(
        owner.read(),
        r#"{"id":null,"ok":false,"error":{"code":"invalid_owner_request","message":"Owner request id must be a nonempty string"}}"#
    );
    owner.send(r#"{"id":"a"}"#);
    assert_eq!(
        owner.read(),
        r#"{"id":"a","ok":false,"error":{"code":"invalid_owner_request","message":"method must be a nonempty string"}}"#
    );
    owner.send(r#"{"id":"b","method":"campaign_index","params":[]}"#);
    assert_eq!(
        owner.read(),
        r#"{"id":"b","ok":false,"error":{"code":"invalid_owner_request","message":"Owner request params must be an object"}}"#
    );
    owner.send(r#"{"id":"c","method":"display_snapshot","params":{"load_id":7}}"#);
    assert_eq!(
        owner.read(),
        r#"{"id":"c","ok":false,"error":{"code":"invalid_owner_request","message":"Display load identity must be a bounded string"}}"#
    );

    // An unknown method is rejected with Python's words; a known one is still pending.
    // Every command error carries its local diagnostics record's identity.
    owner.send(r#"{"id":"d","method":"no_such_method","params":{}}"#);
    let reply = owner.read();
    let prefix = r#"{"id":"d","ok":false,"error":{"code":"command_rejected","message":"Unsupported owner method: no_such_method","diagnostics":[],"diagnostic_id":""#;
    assert!(reply.starts_with(prefix), "{reply}");
    let identity = &reply[prefix.len()..reply.len() - 3];
    assert!(identity.len() == 32 && identity.bytes().all(|byte| byte.is_ascii_hexdigit()), "{reply}");
    owner.send(r#"{"id":"e","method":"catalog"}"#);
    assert!(owner.read().contains(r#""code":"rust_port_pending""#));
    // A ported command answers from the store: a fresh database has no campaigns.
    owner.send(r#"{"id":"g","method":"campaign_index"}"#);
    assert_eq!(owner.read(), r#"{"id":"g","ok":true,"result":[]}"#);

    owner.send(r#"{"id":"f","method":"shutdown"}"#);
    assert_eq!(owner.read(), r#"{"id":"f","ok":true,"result":{"status":"closed"}}"#);
    assert_eq!(owner.exit_code(), Some(0));
    let _ = std::fs::remove_dir_all(folder);
}

#[test]
fn closing_input_ends_the_owner_cleanly() {
    let folder = scratch("eof");
    let mut owner = Session::start();
    owner.launch(&folder);
    assert_eq!(owner.exit_code(), Some(0));
    let _ = std::fs::remove_dir_all(folder);
}

#[test]
fn a_bad_launch_ends_the_owner_without_a_reply() {
    for envelope in [
        r#"{"type":"hello"}"#,
        r#"{"type":"launch","secret_hex":"abc","database_path":"x/y.sqlite3","owner_id":"o"}"#,
        "not json",
    ] {
        let mut owner = Session::start();
        owner.send(envelope);
        assert_eq!(owner.read(), "", "no reply to {envelope}");
        assert_eq!(owner.exit_code(), Some(2));
    }
}
