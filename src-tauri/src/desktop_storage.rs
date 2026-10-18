use serde::Deserialize;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

const PORTABLE_MARKER: &str = "sylvia-portable.json";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PortableMarker {
    version: u32,
}

// Only the portable distribution carries this marker. Installed copies retain their data location.
pub(crate) fn portable_data(executable: &Path) -> Result<Option<PathBuf>, String> {
    let directory = executable
        .parent()
        .ok_or_else(|| "Sylvia GM executable directory is unavailable".to_string())?;
    let marker = directory.join(PORTABLE_MARKER);
    let bytes = match std::fs::read(&marker) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("Cannot read {}: {error}", marker.display())),
    };
    let config: PortableMarker = serde_json::from_slice(&bytes)
        .map_err(|error| format!("Invalid {}: {error}", marker.display()))?;
    if config.version != 1 {
        return Err(format!(
            "Unsupported portable format {} in {}",
            config.version,
            marker.display()
        ));
    }
    Ok(Some(directory.join("isolated-data")))
}

pub(crate) fn database_path(
    portable: Option<&Path>,
    explicit_database: Option<OsString>,
    installed_data: impl FnOnce() -> Result<PathBuf, String>,
) -> Result<PathBuf, String> {
    if let Some(database) = explicit_database {
        return Ok(PathBuf::from(database));
    }
    let directory = match portable {
        Some(directory) => directory.to_path_buf(),
        None => installed_data()?,
    };
    Ok(directory.join("authority.sqlite3"))
}

// Set process-local paths before any WebView or owner starts, including detached panel windows.
pub(crate) fn configure_portable(data: &Path) -> Result<(), String> {
    for directory in [data.to_path_buf(), data.join("webview"), data.join("temp")] {
        std::fs::create_dir_all(&directory).map_err(|error| {
            format!(
                "Cannot write portable data at {}: {error}. Move the complete Sylvia GM folder to a writable local folder.",
                directory.display()
            )
        })?;
    }
    std::env::set_var("WEBVIEW2_USER_DATA_FOLDER", data.join("webview"));
    for name in ["TEMP", "TMP", "TMPDIR"] {
        std::env::set_var(name, data.join("temp"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    struct Fixture(PathBuf);

    impl Fixture {
        fn new() -> Self {
            static NEXT: AtomicU64 = AtomicU64::new(0);
            let path = std::env::temp_dir().join(format!(
                "sylvia-portable-{}-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir(&path).unwrap();
            Self(path)
        }

        fn marker(&self, bytes: &[u8]) {
            std::fs::write(self.0.join(PORTABLE_MARKER), bytes).unwrap();
        }

        fn executable(&self) -> PathBuf {
            self.0.join("Sylvia GM.exe")
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            std::fs::remove_dir_all(&self.0).unwrap();
        }
    }

    #[test]
    fn installed_copy_without_marker_keeps_per_user_storage() {
        let fixture = Fixture::new();
        let installed = fixture.0.join("per-user");
        assert_eq!(portable_data(&fixture.executable()).unwrap(), None);
        assert_eq!(
            database_path(None, None, || Ok(installed.clone())).unwrap(),
            installed.join("authority.sqlite3")
        );
        assert!(!installed.exists());
    }

    #[test]
    fn marked_copy_selects_adjacent_data_without_installed_lookup() {
        let fixture = Fixture::new();
        fixture.marker(br#"{"version":1}"#);
        let data = portable_data(&fixture.executable()).unwrap().unwrap();
        assert_eq!(data, fixture.0.join("isolated-data"));
        assert_eq!(
            database_path(Some(&data), None, || panic!("installed lookup")).unwrap(),
            data.join("authority.sqlite3")
        );
        assert!(!data.exists());
    }

    #[test]
    fn explicit_database_override_remains_available() {
        let path = PathBuf::from("explicit-test").join("selected.sqlite3");
        assert_eq!(
            database_path(
                Some(Path::new("portable")),
                Some(path.clone().into()),
                || { panic!("installed lookup") }
            )
            .unwrap(),
            path
        );
    }

    #[test]
    fn malformed_or_unsupported_marker_never_falls_back() {
        let fixture = Fixture::new();
        for bytes in [
            &b""[..],
            &b"not json"[..],
            &br#"{"version":2}"#[..],
            &br#"{"version":1,"data":"elsewhere"}"#[..],
        ] {
            fixture.marker(bytes);
            assert!(portable_data(&fixture.executable()).is_err());
        }
    }

    #[test]
    fn marker_directory_is_an_error() {
        let fixture = Fixture::new();
        std::fs::create_dir(fixture.0.join(PORTABLE_MARKER)).unwrap();
        assert!(portable_data(&fixture.executable()).is_err());
    }

    #[test]
    fn moving_complete_folder_preserves_and_rebases_existing_data() {
        let fixture = Fixture::new();
        let original = fixture.0.join("original with spaces");
        std::fs::create_dir_all(original.join("isolated-data")).unwrap();
        std::fs::write(original.join(PORTABLE_MARKER), br#"{"version":1}"#).unwrap();
        let bytes = b"selected synthetic save";
        std::fs::write(original.join("isolated-data/authority.sqlite3"), bytes).unwrap();
        let moved = fixture.0.join("moved with spaces");
        std::fs::rename(&original, &moved).unwrap();
        let data = portable_data(&moved.join("Sylvia GM.exe"))
            .unwrap()
            .unwrap();
        assert_eq!(data, moved.join("isolated-data"));
        assert_eq!(
            std::fs::read(data.join("authority.sqlite3")).unwrap(),
            bytes
        );
        assert!(!original.exists());
    }
}
