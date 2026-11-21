//! `sylvia-gm-owner`: the owner executable the desktop shell and the WebUI host start.

use std::io::{stdin, stdout, BufReader, Write};

fn main() {
    let arguments: Vec<std::ffi::OsString> = std::env::args_os().collect();
    let first = arguments.get(1).and_then(|argument| argument.to_str());
    // `sylvia_gm_owner_entry.py`: the two update modes take no further argument and open no
    // storage, provider or database. (Its `--webui` branch, checked first, is the separate
    // `sylvia-gm` executable here.)
    if !arguments.iter().skip(1).any(|argument| argument == "--webui") {
        match first {
            Some("--desktop-update-helper") => {
                if arguments.len() != 2 {
                    std::process::exit(2);
                }
                std::process::exit(sylvia_owner::desktop_update::helper::main());
            }
            Some("--desktop-update-identity") => {
                if arguments.len() != 2 {
                    std::process::exit(2);
                }
                let line = sylvia_owner::desktop_update::identity::identity_line();
                let mut output = stdout();
                let written = output.write_all(line.as_bytes()).and_then(|()| output.flush());
                std::process::exit(if written.is_ok() { 0 } else { 120 });
            }
            _ => {}
        }
    }
    // Python's entry point also serves `--ruleset-test-worker`. That mode is not ported yet.
    if first == Some("--ruleset-test-worker") {
        eprintln!("Sylvia GM owner: the ruleset test worker is not ported to Rust yet");
        std::process::exit(sylvia_owner::run::EXIT_STARTUP_FAILED);
    }
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .thread_name("sylvia-owner")
        .build()
        .expect("the owner runtime starts");
    let code = runtime.block_on(sylvia_owner::run(BufReader::new(stdin()), stdout()));
    // The stdin reader thread may still be blocked in a read; exit without waiting for it.
    std::process::exit(code);
}
