//! `sylvia-gm-owner`: the owner executable the desktop shell and the WebUI host start.

use std::io::{stdin, stdout, BufReader};

fn main() {
    // Python's entry point also serves `--ruleset-test-worker`. That mode is not ported yet.
    if std::env::args().nth(1).as_deref() == Some("--ruleset-test-worker") {
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
