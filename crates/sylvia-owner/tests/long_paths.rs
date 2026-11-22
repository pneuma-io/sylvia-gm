//! The owner executables' application manifest (`sylvia-gm-owner.manifest`, embedded by
//! `build.rs`) and what it is for: with `LongPathsEnabled=1`, the owner works on a storage path
//! longer than MAX_PATH (260 characters), as Python's `python.exe` (also `longPathAware`) does.
//!
//! The manifest is read back from the built executables' resources, with the PE format parsed
//! here. Windows only.

#[cfg(windows)]
mod windows {
    use serde_json::json;
    use std::io::{BufRead, BufReader, Read, Write};
    use std::path::{Path, PathBuf};
    use std::process::{Command, Stdio};

    fn u16_at(bytes: &[u8], at: usize) -> usize {
        usize::from(u16::from_le_bytes([bytes[at], bytes[at + 1]]))
    }

    fn u32_at(bytes: &[u8], at: usize) -> usize {
        u32::from_le_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]]) as usize
    }

    /// The `RT_MANIFEST` resource of a PE file: the resource directory's type 24, its first
    /// name, its first language, then that data entry's bytes.
    pub fn manifest_of(executable: &Path) -> String {
        let bytes = std::fs::read(executable).expect("read the executable");
        let pe = u32_at(&bytes, 0x3c);
        assert_eq!(&bytes[pe..pe + 4], b"PE\0\0", "a PE file");
        let sections = u16_at(&bytes, pe + 6);
        let optional = pe + 24;
        let optional_size = u16_at(&bytes, pe + 20);
        // PE32+: the data directories start 112 bytes in; the resource table is the third.
        assert_eq!(u16_at(&bytes, optional), 0x20b, "PE32+");
        let resource_rva = u32_at(&bytes, optional + 112 + 2 * 8);
        assert_ne!(
            resource_rva,
            0,
            "{}: the executable has no resources, so no manifest",
            executable.display()
        );
        let table = optional + optional_size;
        let offset_of = |rva: usize| -> usize {
            (0..sections)
                .map(|index| table + index * 40)
                .find_map(|section| {
                    let virtual_address = u32_at(&bytes, section + 12);
                    let size = u32_at(&bytes, section + 8).max(u32_at(&bytes, section + 16));
                    let raw = u32_at(&bytes, section + 20);
                    (virtual_address..virtual_address + size)
                        .contains(&rva)
                        .then(|| rva - virtual_address + raw)
                })
                .expect("the address is in a section")
        };
        let root = offset_of(resource_rva);
        // Each directory: 16 bytes, then named entries, then id entries (8 bytes each).
        let entry = |directory: usize, wanted: Option<usize>| -> usize {
            let count = u16_at(&bytes, directory + 12) + u16_at(&bytes, directory + 14);
            (0..count)
                .map(|index| directory + 16 + index * 8)
                .find(|entry| wanted.is_none_or(|id| u32_at(&bytes, *entry) == id))
                .map(|entry| u32_at(&bytes, entry + 4))
                .expect("the resource entry")
        };
        let types = entry(root, Some(24));
        let names = entry(root + (types & 0x7fff_ffff), None);
        let languages = entry(root + (names & 0x7fff_ffff), None);
        let data = root + languages;
        let (rva, size) = (u32_at(&bytes, data), u32_at(&bytes, data + 4));
        let start = offset_of(rva);
        String::from_utf8_lossy(&bytes[start..start + size]).into_owned()
    }

    /// `LongPathsEnabled` in `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem`, through `reg`.
    pub fn long_paths_enabled() -> bool {
        let output = Command::new("reg")
            .args(["query", r"HKLM\SYSTEM\CurrentControlSet\Control\FileSystem", "/v", "LongPathsEnabled"])
            .output()
            .expect("run reg");
        String::from_utf8_lossy(&output.stdout).contains("0x1")
    }

    /// A fresh folder whose full path is longer than `length` characters.
    pub fn long_folder(length: usize) -> PathBuf {
        let base = std::env::temp_dir().join(format!("sylvia-long-paths-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let mut folder = base.clone();
        let mut part = 0;
        while folder.as_os_str().len() <= length {
            folder.push(format!("a-folder-to-make-the-path-long-{part:02}"));
            part += 1;
        }
        std::fs::create_dir_all(&folder).expect("std creates long folders itself");
        folder
    }

    /// Launch the built owner on `database`: its first stdout line.
    pub fn launch(database: &Path) -> String {
        let mut child = Command::new(env!("CARGO_BIN_EXE_sylvia-gm-owner"))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("the owner starts");
        let mut stdin = child.stdin.take().unwrap();
        let envelope = json!({
            "type": "launch", "secret_hex": "00".repeat(32),
            "database_path": database, "owner_id": "long-paths-test",
        });
        stdin.write_all(format!("{envelope}\n").as_bytes()).unwrap();
        stdin.flush().unwrap();
        let mut first = String::new();
        BufReader::new(child.stdout.take().unwrap()).read_line(&mut first).unwrap();
        drop(stdin);
        let mut stderr = String::new();
        child.stderr.take().unwrap().read_to_string(&mut stderr).unwrap();
        child.wait().unwrap();
        if !first.contains(r#""type":"ready""#) {
            eprintln!("owner stderr: {stderr}");
        }
        first
    }
}

#[cfg(windows)]
#[test]
fn the_executables_declare_long_paths_and_common_controls_6() {
    for executable in [env!("CARGO_BIN_EXE_sylvia-gm-owner"), env!("CARGO_BIN_EXE_sylvia-gm")] {
        let manifest = windows::manifest_of(std::path::Path::new(executable));
        assert!(manifest.contains("<ws2:longPathAware>true</ws2:longPathAware>"), "{executable}: {manifest}");
        assert!(manifest.contains("http://schemas.microsoft.com/SMI/2016/WindowsSettings"), "{executable}");
        assert!(
            manifest.contains(r#"name="Microsoft.Windows.Common-Controls" version="6.0.0.0""#),
            "{executable}: {manifest}"
        );
    }
}

#[cfg(windows)]
#[test]
fn the_owner_starts_on_storage_longer_than_max_path() {
    if !windows::long_paths_enabled() {
        // Without the system setting, `longPathAware` changes nothing; Python would fail too.
        eprintln!("LongPathsEnabled is not 1 on this machine: skipped");
        return;
    }
    let folder = windows::long_folder(300);
    let database = folder.join("authority.sqlite3");
    assert!(database.as_os_str().len() > 300, "{}", database.display());
    let first = windows::launch(&database);
    assert!(first.contains(r#""type":"ready""#), "the owner starts on a long path: {first}");
    assert!(database.exists(), "the database is on the long path");
    // Startup's own files on the long path: the ownership lock and the diagnostics folder.
    assert!(folder.join("authority.sqlite3.authority-owner.owner.lock").exists(), "the owner lock");
    assert!(folder.join("diagnostics").is_dir(), "the diagnostics folder");
    let _ = std::fs::remove_dir_all(
        std::env::temp_dir().join(format!("sylvia-long-paths-{}", std::process::id())),
    );
}
