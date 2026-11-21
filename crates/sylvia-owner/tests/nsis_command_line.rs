//! The installer's exact command line, as the child really receives it (Windows only).
//!
//! NSIS requires its `/D=` destination last and unquoted, even with spaces. This test copies
//! itself to a folder with spaces as a stand-in installer, starts it through the very
//! `Command` that `run_nsis` uses, and the copy reports the arguments Windows split from its
//! command line. A quoted `/D=` would arrive as one argument; the unquoted one splits at each
//! space. (`harness = false`: the copy must answer before any test harness reads its
//! arguments.)

const ECHO: &str = "SYLVIA_NSIS_COMMAND_ECHO";

fn main() {
    if std::env::var_os(ECHO).is_some() {
        let arguments: Vec<String> = std::env::args().skip(1).collect();
        println!("{}", serde_json::to_string(&arguments).unwrap());
        return;
    }
    if !cfg!(windows) {
        println!("nsis_command_line: Windows only, skipped");
        return;
    }
    let base = std::env::temp_dir().join(format!("sylvia nsis command {}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    let downloads = base.join("verified downloads");
    let live = base.join("Sylvia GM app with spaces");
    let temporary = downloads.join("installer-temp");
    for folder in [&downloads, &live, &temporary] {
        std::fs::create_dir_all(folder).unwrap();
    }
    let artifact = downloads.join("artifact.verified");
    std::fs::copy(std::env::current_exe().unwrap(), &artifact).unwrap();
    let mut command =
        sylvia_owner::desktop_update::installed::nsis_command(&artifact, &live, &temporary).unwrap();
    let output = command.env(ECHO, "1").stdout(std::process::Stdio::piped()).output().unwrap();
    let _ = std::fs::remove_dir_all(&base);
    assert!(output.status.success());
    let got: Vec<String> = serde_json::from_slice(&output.stdout).unwrap();
    let tail = format!("/D={}", live.display());
    let mut expected = vec!["/S".to_string(), "/UPDATE".into(), "/NS".into()];
    expected.extend(tail.split(' ').map(str::to_string));
    assert_eq!(got, expected, "the /D= destination must reach the installer unquoted");
    assert!(tail.contains(' '), "the test path must contain spaces");
    let line = sylvia_owner::desktop_update::installed::nsis_command_line(&artifact, &live).unwrap();
    assert!(line.ends_with(&format!(" /S /UPDATE /NS {tail}")), "{line}");
    println!("nsis_command_line: ok");
}
