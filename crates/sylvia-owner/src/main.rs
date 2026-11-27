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
            // `python -m sylvia_gm.config.claude_settings`: the headless Claude Code set-up.
            Some("claude-settings") => {
                std::process::exit(sylvia_owner::claude_settings_cli::main(&arguments[2..]));
            }
            // The speech worker takes the model folder; the runtime is the owner's own build.
            Some("--dictation-worker") => {
                std::process::exit(dictation_worker(&arguments[2..]));
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
    // Python's `atexit` hooks, last registered first: each ChatGPT account's connection closes
    // and a pending Claude sign-in is cancelled (both registered when first used), then the
    // kept Claude Code processes and ChatGPT threads close (registered at import): the processes
    // remove their private files, and the threads close without collecting usage.
    sylvia_provider::config::chatgpt_settings::close_all();
    sylvia_provider::config::claude_settings::cancel_all();
    sylvia_provider::claude_kept::close_all();
    sylvia_provider::chatgpt::close_all_kept_threads();
    // The stdin reader thread may still be blocked in a read; exit without waiting for it.
    std::process::exit(code);
}

/// The speech worker on the shipped libraries (Windows, the `dictation` feature), else the
/// build without the speech runtime.
fn dictation_worker(arguments: &[std::ffi::OsString]) -> i32 {
    #[cfg(all(windows, feature = "dictation"))]
    if let Some(folder) = sylvia_owner::dictation::speech::SpeechRuntime::natives() {
        let mut runtime = sylvia_owner::dictation::speech::SpeechRuntime::new(&folder);
        return sylvia_owner::dictation::worker::main(arguments, &mut runtime);
    }
    let mut runtime = sylvia_owner::dictation::Absent::default();
    sylvia_owner::dictation::worker::main(arguments, &mut runtime)
}
