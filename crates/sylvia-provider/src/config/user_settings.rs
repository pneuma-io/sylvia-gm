//! `config/_user_settings.py`: the one owner-local settings file, written atomically, with
//! secrets protected even in the file.

use super::protection::{protect_credentials, Seal};
use super::pyvalue::truthy;
use super::{ConfigFailure, CONFIG_LOCK};
use std::io::Write;
use std::path::{Path, PathBuf};
use sylvia_base::pyyaml::{self, Data};

/// `_MIGRATION_RETRY`.
#[cfg(target_os = "linux")]
pub const MIGRATION_RETRY: &str =
    "retry after saved-credential protection and settings-file access are available.";
/// `_MIGRATION_RETRY`.
#[cfg(not(target_os = "linux"))]
pub const MIGRATION_RETRY: &str = "retry after Windows protection and settings-file access are available.";

/// Why the atomic replacement failed: `OSError`, or a `ConfigError` from protection.
enum WriteFailure {
    Os,
    Failure(ConfigFailure),
}

impl From<std::io::Error> for WriteFailure {
    fn from(_: std::io::Error) -> Self {
        WriteFailure::Os
    }
}

fn str_key(text: &str) -> Data {
    Data::Str(text.to_owned())
}

/// `protected_document(document)`: a copy where every provider's plaintext `api_key` and
/// `headers` are sealed into `protected_credentials` and removed. The flag is whether anything
/// changed (`protected != document`).
pub fn protected_document(seal: &dyn Seal, document: &Data) -> Result<(Data, bool), ConfigFailure> {
    let mut result = document.clone();
    let mut changed = false;
    let Some(providers) = result.as_dict_mut().and_then(|pairs| {
        pairs
            .iter_mut()
            .find(|(key, _)| sylvia_base::pyyaml::data_key_eq(key, &str_key("providers")))
            .map(|(_, v)| v)
    }) else {
        return Ok((result, false));
    };
    let Some(providers) = providers.as_dict_mut() else { return Ok((result, false)) };
    for (_, provider) in providers.iter_mut() {
        let Some(pairs) = provider.as_dict_mut() else { continue };
        let find = |pairs: &Vec<(Data, Data)>, name: &str| {
            pairs.iter().position(|(key, _)| sylvia_base::pyyaml::data_key_eq(key, &str_key(name)))
        };
        let key = find(pairs, "api_key").map_or(Data::Str(String::new()), |index| pairs[index].1.clone());
        let headers = find(pairs, "headers").map_or(Data::Dict(Vec::new()), |index| pairs[index].1.clone());
        let text_headers: Option<Vec<(String, String)>> = headers.as_dict().and_then(|items| {
            items
                .iter()
                .map(|(name, value)| Some((name.as_str()?.to_owned(), value.as_str()?.to_owned())))
                .collect()
        });
        let (Data::Str(key_text), Some(header_pairs)) = (&key, text_headers) else {
            return Err(ConfigFailure::config("Provider credentials must contain text values"));
        };
        if truthy(&key) || truthy(&headers) {
            if find(pairs, "protected_credentials").is_some() {
                return Err(ConfigFailure::config(
                    "Provider settings contain conflicting credential formats",
                ));
            }
            let envelope = protect_credentials(seal, key_text, &header_pairs)?;
            pairs.push((str_key("protected_credentials"), envelope));
            changed = true;
        }
        if find(pairs, "protected_credentials").is_some() {
            for name in ["api_key", "headers"] {
                if let Some(index) = find(pairs, name) {
                    pairs.remove(index);
                    changed = true;
                }
            }
        }
    }
    Ok((result, changed))
}

/// `tempfile.mkstemp(prefix=..., suffix=..., dir=...)`: a new file of mode 0600 with a random
/// name from `tempfile`'s alphabet.
fn create_temporary(folder: &Path) -> std::io::Result<(std::fs::File, PathBuf)> {
    const ALPHABET: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789_";
    for _ in 0..100 {
        let mut random = [0u8; 8];
        getrandom::getrandom(&mut random).map_err(|_| std::io::Error::other("no random bytes"))?;
        let name: String =
            random.iter().map(|b| char::from(ALPHABET[usize::from(*b) % ALPHABET.len()])).collect();
        let path = folder.join(format!("provider-settings-{name}.tmp"));
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        match options.open(&path) {
            Ok(file) => return Ok((file, path)),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    Err(std::io::Error::from(std::io::ErrorKind::AlreadyExists))
}

/// `_replace(path, document)`: write the YAML to a temporary file beside the settings, sync it,
/// then move it over them; a failure removes the temporary file and keeps the original.
fn replace(path: &Path, document: &Data) -> Result<(), WriteFailure> {
    let loaded = document.to_loaded();
    let payload = pyyaml::safe_dump(&loaded).map_err(|error| {
        WriteFailure::Failure(ConfigFailure::python(error.class_name(), error.to_string()))
    })?;
    let folder = path.parent().map(Path::to_path_buf).unwrap_or_default();
    std::fs::create_dir_all(&folder)?;
    let (mut file, temporary) = create_temporary(&folder)?;
    let result = (|| -> std::io::Result<()> {
        file.write_all(payload.as_bytes())?;
        file.flush()?;
        file.sync_all()?;
        drop(file);
        std::fs::rename(&temporary, path)
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    Ok(result?)
}

/// `write_user_settings(path, document)`: protect every new secret, then replace the file.
pub fn write_user_settings(seal: &dyn Seal, path: &Path, document: &Data) -> Result<(), ConfigFailure> {
    let _guard = CONFIG_LOCK.acquire();
    let failed = || {
        ConfigFailure::config("Could not protect and save provider settings; the previous file is unchanged")
    };
    let protected = match protected_document(seal, document) {
        Ok((protected, _)) => protected,
        Err(ConfigFailure::Config(_)) => return Err(failed()),
        Err(other) => return Err(other),
    };
    match replace(path, &protected) {
        Ok(()) => Ok(()),
        Err(WriteFailure::Os | WriteFailure::Failure(ConfigFailure::Config(_))) => Err(failed()),
        Err(WriteFailure::Failure(other)) => Err(other),
    }
}

/// `read_user_settings(path)`: the settings as a dict, `{}` when the file is absent or empty.
/// Plaintext credentials are migrated to protection first, atomically; a failed migration
/// never returns plaintext.
pub fn read_user_settings(seal: &dyn Seal, path: &Path) -> Result<Data, ConfigFailure> {
    let _guard = CONFIG_LOCK.acquire();
    if !path.exists() {
        return Ok(Data::Dict(Vec::new()));
    }
    let unreadable = || ConfigFailure::config("The local provider settings file could not be read");
    let bytes = std::fs::read(path).map_err(|_| unreadable())?;
    let text =
        pyyaml::read_text(&bytes).map_err(|message| ConfigFailure::python("UnicodeDecodeError", message))?;
    let loaded = match pyyaml::safe_load(&text) {
        Ok(loaded) => loaded,
        Err(error) if error.is_yaml_error() => return Err(unreadable()),
        Err(error) => return Err(ConfigFailure::python(error.class_name(), error.to_string())),
    };
    let document = match loaded.to_data() {
        Ok(None) | Ok(Some(Data::None)) => return Ok(Data::Dict(Vec::new())),
        Ok(Some(document)) => document,
        // A document that contains itself: Python's comparison with its copy recurses without end.
        Err(_) => {
            return Err(ConfigFailure::python(
                "RecursionError",
                "maximum recursion depth exceeded in comparison",
            ))
        }
    };
    if document.as_dict().is_none() {
        return Err(ConfigFailure::config("The local provider settings file must contain a mapping"));
    }
    let migration_failed = || {
        ConfigFailure::config(format!(
            "Saved credential migration did not complete. The original settings are unchanged; {MIGRATION_RETRY}"
        ))
    };
    let (protected, changed) = match protected_document(seal, &document) {
        Ok(result) => result,
        Err(ConfigFailure::Config(_)) => return Err(migration_failed()),
        Err(other) => return Err(other),
    };
    if changed {
        match replace(path, &protected) {
            Ok(()) => {}
            Err(WriteFailure::Os | WriteFailure::Failure(ConfigFailure::Config(_))) => {
                return Err(migration_failed())
            }
            Err(WriteFailure::Failure(other)) => return Err(other),
        }
    }
    Ok(protected)
}
