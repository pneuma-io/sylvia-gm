//! The owner executable's two update modes, as `sylvia_gm_owner_entry.py` serves them (and as
//! `test_desktop_update_identity.py` and `test_desktop_update_helper.py` check them): no
//! storage, provider or database is opened, and nothing is written in the working folder.

use std::process::{Command, Stdio};

fn owner(arguments: &[&str], folder: &std::path::Path) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_sylvia-gm-owner"))
        .args(arguments)
        .current_dir(folder)
        .stdin(Stdio::null())
        .output()
        .unwrap()
}

fn folder(name: &str) -> std::path::PathBuf {
    let path = std::env::temp_dir().join(format!("sylvia-owner-modes-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&path);
    std::fs::create_dir_all(&path).unwrap();
    path
}

#[test]
fn the_identity_mode_reports_only_public_metadata() {
    let place = folder("identity");
    let output = owner(&["--desktop-update-identity"], &place);
    assert!(output.status.success());
    assert!(output.stderr.is_empty());
    let newline: &[u8] = if cfg!(windows) { b"\r\n" } else { b"\n" };
    assert!(output.stdout.ends_with(newline));
    let text = String::from_utf8(output.stdout).unwrap();
    let target = if cfg!(windows) { "windows-x86_64" } else { "linux-x86_64" };
    assert_eq!(
        text.trim_end(),
        format!(
            r#"{{"format_version":1,"product":"Sylvia GM","component":"owner","version":"{}","storage_compatibility":1,"target":"{target}"}}"#,
            env!("CARGO_PKG_VERSION")
        )
    );
    assert_eq!(std::fs::read_dir(&place).unwrap().count(), 0);
    let _ = std::fs::remove_dir_all(&place);
}

#[test]
fn either_mode_with_an_extra_argument_exits_2_silently() {
    let place = folder("extra");
    for mode in ["--desktop-update-identity", "--desktop-update-helper"] {
        let output = owner(&[mode, "unaccepted"], &place);
        assert_eq!(output.status.code(), Some(2));
        assert!(output.stdout.is_empty() && output.stderr.is_empty());
    }
    assert_eq!(std::fs::read_dir(&place).unwrap().count(), 0);
    let _ = std::fs::remove_dir_all(&place);
}

#[test]
fn the_unconfigured_helper_refuses_without_owner_or_database() {
    let place = folder("helper");
    let output = owner(&["--desktop-update-helper"], &place);
    assert_eq!(output.status.code(), Some(2));
    assert!(output.stderr.is_empty());
    let newline = if cfg!(windows) { "\r\n" } else { "\n" };
    assert_eq!(
        String::from_utf8(output.stdout).unwrap(),
        format!("{{\"type\":\"error\",\"code\":\"trust_not_configured\"}}{newline}")
    );
    assert_eq!(std::fs::read_dir(&place).unwrap().count(), 0);
    let _ = std::fs::remove_dir_all(&place);
}
