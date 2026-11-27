//! `sylvia-gm-owner claude-settings` against Python's `claude_settings.main()`
//! (`tests/vectors/generate_claude_settings_cli_vectors.py`, one vector file per platform): each
//! case runs the built owner in a fresh work folder that is also its home and profile, offline,
//! and compares the exit code and the exact bytes of stdout and stderr. The cases cover the
//! usage and help, every option, prefixes and their ambiguity, `--option=value`, the order
//! errors are found in, Python's `int()`, the `-h` forms, unrecognized words, the `--config`
//! name check, and the actions refused before Sylvia's copy is installed.

use std::path::Path;
use std::process::Command;

fn vectors() -> serde_json::Value {
    let platform = if cfg!(windows) { "windows" } else { "linux" };
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(format!("tests/vectors/claude_settings_cli_vectors.{platform}.json"));
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", path.display()));
    serde_json::from_str(&text).unwrap()
}

/// The `/`-separated work folder, as the generator writes `<work>` into arguments.
fn posix(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

#[test]
fn claude_settings_answers_as_python_main_does() {
    let vectors = vectors();
    assert_eq!(vectors["prog"], sylvia_owner::claude_settings_cli::PROG);
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() >= 50, "{} cases", cases.len());
    let base = std::env::temp_dir().join(format!("sylvia-cli-{}", std::process::id()));
    if let Err(error) = std::fs::remove_dir_all(&base) {
        assert_eq!(
            error.kind(),
            std::io::ErrorKind::NotFound,
            "cannot clear the old work folder {}: {error}",
            base.display()
        );
    }
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let work = base.join(index.to_string());
        std::fs::create_dir_all(work.join("data")).unwrap();
        let work_text = posix(&work);
        let arguments: Vec<String> = case["args"]
            .as_array()
            .unwrap()
            .iter()
            .map(|argument| argument.as_str().unwrap().replace("<work>", &work_text))
            .collect();
        let output = Command::new(env!("CARGO_BIN_EXE_sylvia-gm-owner"))
            .arg("claude-settings")
            .args(&arguments)
            .current_dir(&work)
            .env_clear()
            .envs(
                ["SYSTEMROOT", "PATH", "COMSPEC"]
                    .iter()
                    .filter_map(|key| Some((*key, std::env::var_os(key)?))),
            )
            .env("HOME", &work)
            .env("USERPROFILE", &work)
            .env("APPDATA", &work)
            .env("LOCALAPPDATA", &work)
            .env("TEMP", &work)
            .env("TMP", &work)
            .env("XDG_CONFIG_HOME", work.join("xdg"))
            .output()
            .unwrap();
        let scrub = |bytes: &[u8]| {
            let mut text = String::from_utf8(bytes.to_vec()).unwrap();
            for form in [work_text.clone(), work.to_string_lossy().into_owned()] {
                text = text.replace(&form, "<work>");
            }
            text
        };
        let got = (output.status.code(), scrub(&output.stdout), scrub(&output.stderr));
        let want = (
            case["exit"].as_i64().map(|code| code as i32),
            case["stdout"].as_str().unwrap().to_owned(),
            case["stderr"].as_str().unwrap().to_owned(),
        );
        if got != want {
            failures.push(format!("#{index} {:?}\n  Rust   {got:?}\n  Python {want:?}", case["args"]));
        }
    }
    let _ = std::fs::remove_dir_all(&base);
    assert!(failures.is_empty(), "{} cases differ:\n{}", failures.len(), failures.join("\n"));
}
