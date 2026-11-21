//! `sylvia-gm`: product information and the explicit local browser launch (`sylvia_gm.main`).
//!
//! The WebUI host starts `sylvia-gm-owner` from this executable's own folder, the Rust
//! counterpart of Python's `sys.executable -m sylvia_gm.interfaces.owner_process`.
//! `SYLVIA_GM_OWNER_EXECUTABLE` names another owner, as it does for the desktop shell.
//! Packaging decides the final layout.

use std::path::PathBuf;
use std::process::ExitCode;
use sylvia_owner::webui::{host, RemoteAccess};

const USAGE: &str = "usage: sylvia-gm [-h] [--version] [--webui] [--data-root DATA_ROOT] [--port PORT]
                 [--webui-remote-origin WEBUI_REMOTE_ORIGIN]
                 [--webui-access-key-file WEBUI_ACCESS_KEY_FILE]";

const HELP: &str = "
Use the installed Windows desktop or launch the WebUI with an explicit data root.
Campaign and provider authority remains in its supervised owner process.

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
  --webui               Run the loopback browser UI
  --data-root DATA_ROOT
                        Explicit local Sylvia data directory
  --port PORT           Loopback port; 0 selects a free port
  --webui-remote-origin WEBUI_REMOTE_ORIGIN
                        Exact private Tailscale HTTPS origin
  --webui-access-key-file WEBUI_ACCESS_KEY_FILE
                        User-managed random access-key file for private HTTPS mode";

/// `parser.error(message)`: usage and the message on stderr, exit 2.
fn usage_error(message: &str) -> ExitCode {
    eprintln!("{USAGE}\nsylvia-gm: error: {message}");
    ExitCode::from(2)
}

#[derive(Default)]
struct Arguments {
    webui: bool,
    data_root: Option<PathBuf>,
    port: i64,
    remote_origin: Option<String>,
    access_key_file: Option<PathBuf>,
}

enum Parsed {
    Run(Arguments),
    Exit(ExitCode),
}

fn parse() -> Parsed {
    let mut arguments = Arguments::default();
    let mut words = std::env::args().skip(1);
    while let Some(word) = words.next() {
        let (flag, inline) = match word.split_once('=') {
            Some((flag, value)) if flag.starts_with("--") => (flag.to_owned(), Some(value.to_owned())),
            _ => (word.clone(), None),
        };
        let mut value = |name: &str| -> Result<String, ExitCode> {
            inline
                .clone()
                .or_else(|| words.next())
                .ok_or_else(|| usage_error(&format!("argument {name}: expected one argument")))
        };
        match flag.as_str() {
            "-h" | "--help" => {
                println!("{USAGE}\n{HELP}");
                return Parsed::Exit(ExitCode::SUCCESS);
            }
            "--version" => {
                println!("sylvia-gm {}", env!("CARGO_PKG_VERSION"));
                return Parsed::Exit(ExitCode::SUCCESS);
            }
            "--webui" => arguments.webui = true,
            "--data-root" => match value("--data-root DATA_ROOT") {
                Ok(text) => arguments.data_root = Some(PathBuf::from(text)),
                Err(code) => return Parsed::Exit(code),
            },
            "--port" => match value("--port PORT") {
                Ok(text) => match text.trim().parse::<i64>() {
                    Ok(port) => arguments.port = port,
                    Err(_) => {
                        return Parsed::Exit(usage_error(&format!(
                            "argument --port: invalid int value: '{text}'"
                        )))
                    }
                },
                Err(code) => return Parsed::Exit(code),
            },
            "--webui-remote-origin" => match value("--webui-remote-origin WEBUI_REMOTE_ORIGIN") {
                Ok(text) => arguments.remote_origin = Some(text),
                Err(code) => return Parsed::Exit(code),
            },
            "--webui-access-key-file" => match value("--webui-access-key-file WEBUI_ACCESS_KEY_FILE") {
                Ok(text) => arguments.access_key_file = Some(PathBuf::from(text)),
                Err(code) => return Parsed::Exit(code),
            },
            other => return Parsed::Exit(usage_error(&format!("unrecognized arguments: {other}"))),
        }
    }
    Parsed::Run(arguments)
}

/// The owner to start: `SYLVIA_GM_OWNER_EXECUTABLE`, or `sylvia-gm-owner` beside this program.
fn owner_program() -> PathBuf {
    if let Some(program) = std::env::var_os("SYLVIA_GM_OWNER_EXECUTABLE").filter(|value| !value.is_empty()) {
        return PathBuf::from(program);
    }
    let name = format!("sylvia-gm-owner{}", std::env::consts::EXE_SUFFIX);
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|folder| folder.join(&name)))
        .unwrap_or_else(|| name.into())
}

/// The frontend: `webui` beside a packaged program, else the source checkout's
/// `src-tauri/frontend` (Python's `parents[3] / "src-tauri" / "frontend"`).
fn frontend() -> PathBuf {
    let packaged =
        std::env::current_exe().ok().and_then(|exe| exe.parent().map(|folder| folder.join("webui")));
    match packaged {
        Some(folder) if folder.join("app.html").is_file() => folder,
        _ => PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../src-tauri/frontend"),
    }
}

fn main() -> ExitCode {
    let arguments = match parse() {
        Parsed::Run(arguments) => arguments,
        Parsed::Exit(code) => return code,
    };
    let remote_selected = arguments.remote_origin.is_some() || arguments.access_key_file.is_some();
    if remote_selected && !arguments.webui {
        return usage_error("Private HTTPS options require --webui");
    }
    if arguments.remote_origin.is_none() != arguments.access_key_file.is_none() {
        return usage_error("Private HTTPS requires both --webui-remote-origin and --webui-access-key-file");
    }
    if !arguments.webui {
        println!("{USAGE}\n{HELP}");
        return ExitCode::SUCCESS;
    }
    let Some(data_root) = arguments.data_root else {
        return usage_error("--webui requires --data-root; choose a dataset explicitly");
    };
    if !(0..=65535).contains(&arguments.port) {
        return usage_error("--port must be from 0 to 65535");
    }
    let remote = match (&arguments.remote_origin, &arguments.access_key_file) {
        (Some(origin), Some(key_file)) => match RemoteAccess::from_file(origin, key_file) {
            Ok(remote) => Some(remote),
            Err(error) => {
                eprintln!("WebUI could not start: {error}");
                return ExitCode::from(1);
            }
        },
        _ => None,
    };
    let runtime =
        tokio::runtime::Builder::new_multi_thread().enable_all().build().expect("the WebUI runtime starts");
    let port = u16::try_from(arguments.port).expect("checked above");
    match runtime.block_on(host::serve(&data_root, port, remote, owner_program(), frontend())) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("WebUI could not start: {error}");
            ExitCode::from(1)
        }
    }
}
