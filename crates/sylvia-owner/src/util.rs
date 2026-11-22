//! Small Python standard-library behaviours the owner relies on.

use std::fs::{File, OpenOptions};
use std::path::{Path, PathBuf};

fn random_bytes<const N: usize>() -> [u8; N] {
    let mut bytes = [0u8; N];
    getrandom::getrandom(&mut bytes).expect("the operating system's random source is available");
    bytes
}

/// `uuid.uuid4().hex`: 32 lowercase hex digits with the version 4 and RFC 4122 variant bits.
pub fn uuid4_hex() -> String {
    let mut bytes: [u8; 16] = random_bytes();
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    hex::encode(bytes)
}

/// `round(value, 1)` for a finite float. Rust's fixed-precision formatting is correctly
/// rounded with ties to even, as Python's `round` is.
pub fn python_round1(value: f64) -> f64 {
    format!("{value:.1}").parse().unwrap_or(value)
}

/// `tempfile.NamedTemporaryFile(mode="wb", dir=, prefix=, suffix=, delete=False)`: a new
/// file with eight random characters from Python's alphabet between prefix and suffix,
/// created exclusively.
pub fn named_temporary(directory: &Path, prefix: &str, suffix: &str) -> std::io::Result<(PathBuf, File)> {
    const ALPHABET: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789_";
    // `tempfile.TMP_MAX` attempts before giving up.
    let mut last = None;
    for _ in 0..10_000 {
        let name: String = random_bytes::<8>()
            .iter()
            .map(|byte| ALPHABET[usize::from(*byte) % ALPHABET.len()] as char)
            .collect();
        let path = directory.join(format!("{prefix}{name}{suffix}"));
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => return Ok((path, file)),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => last = Some(error),
            Err(error) => return Err(error),
        }
    }
    Err(last.unwrap_or_else(|| std::io::Error::other("no temporary name was free")))
}

/// A path without Windows' `\\?\` prefix, as Python prints it.
pub fn plain_path(path: &Path) -> String {
    let text = path.to_string_lossy().into_owned();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{rest}")
    } else {
        text.strip_prefix(r"\\?\").map(str::to_owned).unwrap_or(text)
    }
}

/// `Path(text).name`: the last component of the path as pathlib reports it. `..` is a name;
/// a drive or root alone has none.
pub fn path_name(path: &Path) -> String {
    use std::path::Component;
    match path.components().next_back() {
        Some(Component::Normal(name)) => name.to_string_lossy().into_owned(),
        Some(Component::ParentDir) => "..".to_owned(),
        _ => String::new(),
    }
}

/// The source checkout the owner runs from: the repository root, two levels above this crate.
/// The bundled package sources and other shipped files are found under it. Packaging replaces it
/// with the installed resource root.
pub fn source_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uuid4_hex_has_the_version_and_variant() {
        let identity = uuid4_hex();
        assert_eq!(identity.len(), 32);
        assert_eq!(&identity[12..13], "4");
        assert!("89ab".contains(&identity[16..17]));
    }

    #[test]
    fn rounds_like_python() {
        // round(0.25, 1) == 0.2, round(0.35, 1) == 0.3 (0.35 is below .35 in binary),
        // round(2.675, 1) == 2.7, round(12.345, 1) == 12.3.
        assert_eq!(python_round1(0.25), 0.2);
        assert_eq!(python_round1(0.35), 0.3);
        assert_eq!(python_round1(2.675), 2.7);
        assert_eq!(python_round1(12.345), 12.3);
    }

    #[test]
    fn names_like_pathlib() {
        assert_eq!(path_name(Path::new("/a/report.json")), "report.json");
        assert_eq!(path_name(Path::new("/a/..")), "..");
        assert_eq!(path_name(Path::new("/")), "");
    }
}
