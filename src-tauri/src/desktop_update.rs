//! Fixed retained-helper protocol. Only native-owned paths and existing process handles enter it.
use crate::desktop_update_process::{spawn_owned, HelperProcess};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Child;

pub(crate) fn valid_id(value: &str) -> bool {
    value.len() == 32
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

pub(crate) struct HelperSpec {
    pub operation: String,
    pub attempt: String,
    pub restart_nonce: Option<String>,
    data: PathBuf,
    live: PathBuf,
    portable: bool,
    size: u64,
    hash: String,
}

impl HelperSpec {
    pub fn from_prepared(
        data: &Path,
        live: &Path,
        portable: bool,
        row: &Value,
    ) -> Result<Self, String> {
        let object = row.as_object().ok_or("Invalid prepared helper metadata")?;
        if object.len() != 3 || !data.is_absolute() || !live.is_absolute() {
            return Err("Invalid prepared helper context".into());
        }
        let operation = row["operation_id"]
            .as_str()
            .ok_or("Missing update operation")?;
        let attempt = row["attempt"].as_str().ok_or("Missing update attempt")?;
        let helper = row["helper"]
            .as_object()
            .ok_or("Missing retained helper identity")?;
        let size = helper
            .get("size")
            .and_then(Value::as_u64)
            .ok_or("Missing helper size")?;
        let hash = helper
            .get("sha256")
            .and_then(Value::as_str)
            .ok_or("Missing helper hash")?;
        if !valid_id(operation)
            || !valid_id(attempt)
            || helper.len() != 2
            || size == 0
            || size > 1024 * 1024 * 1024
            || hash.len() != 64
            || !hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || (portable && data != live.join("isolated-data"))
        {
            return Err("Invalid retained helper identity".into());
        }
        Ok(Self {
            operation: operation.into(),
            attempt: attempt.into(),
            restart_nonce: None,
            data: data.into(),
            live: live.into(),
            portable,
            size,
            hash: hash.into(),
        })
    }

    pub fn from_recovery(
        data: &Path,
        live: &Path,
        portable: bool,
        row: &Value,
    ) -> Result<Self, String> {
        if row.as_object().map(|value| value.len()) != Some(4) {
            return Err("Invalid recovery helper metadata".into());
        }
        let nonce = match row.get("restart_nonce") {
            Some(Value::Null) => None,
            Some(Value::String(value)) if valid_id(value) => Some(value.clone()),
            _ => return Err("Invalid recovery restart identity".into()),
        };
        let mut ordinary = row.clone();
        ordinary
            .as_object_mut()
            .ok_or("Invalid recovery metadata")?
            .remove("restart_nonce");
        let mut spec = Self::from_prepared(data, live, portable, &ordinary)?;
        spec.restart_nonce = nonce;
        Ok(spec)
    }

    fn executable(&self) -> PathBuf {
        self.data
            .join(".application-updates")
            .join(&self.operation)
            .join("attempts")
            .join(&self.attempt)
            .join("helper")
            .join("sylvia-gm-owner.exe")
    }

    fn verify_helper(&self) -> Result<(PathBuf, File), String> {
        let path = self.executable();
        for ancestor in path.ancestors() {
            let metadata = std::fs::symlink_metadata(ancestor)
                .map_err(|_| "The retained helper location is unavailable")?;
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                if metadata.file_attributes() & 0x400 != 0 {
                    return Err("The retained helper location is aliased".into());
                }
            }
            if metadata.file_type().is_symlink() {
                return Err("The retained helper location is aliased".into());
            }
        }
        let mut options = OpenOptions::new();
        options.read(true);
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            options.share_mode(1); // Keep the checked executable read-only through process creation.
        }
        let mut file = options
            .open(&path)
            .map_err(|_| "The retained helper is unavailable")?;
        let before = file
            .metadata()
            .map_err(|_| "The retained helper identity is unavailable")?;
        if !before.is_file() || before.len() != self.size {
            return Err("The retained helper size changed".into());
        }
        let mut digest = Sha256::new();
        let mut count = 0_u64;
        let mut buffer = [0_u8; 256 * 1024];
        loop {
            let read = file
                .read(&mut buffer)
                .map_err(|_| "The retained helper could not be read")?;
            if read == 0 {
                break;
            }
            count += read as u64;
            if count > self.size {
                return Err("The retained helper grew while checking".into());
            }
            digest.update(&buffer[..read]);
        }
        let after = file
            .metadata()
            .map_err(|_| "The retained helper identity is unavailable")?;
        if count != self.size
            || hex::encode(digest.finalize()) != self.hash
            || after.len() != before.len()
            || after.modified().ok() != before.modified().ok()
        {
            return Err("The retained helper failed verification".into());
        }
        Ok((path, file))
    }
}

pub(crate) struct Handoff {
    child: Option<HelperProcess>,
    input: Option<File>,
    output: BufReader<File>,
    operation: String,
    attempt: String,
    nonce: String,
}

impl Handoff {
    pub fn spawn(spec: HelperSpec, owner: &Child) -> Result<Self, String> {
        Self::spawn_selected(spec, owner, None)
    }

    pub fn spawn_recovery(spec: HelperSpec, owner: &Child, choice: &str) -> Result<Self, String> {
        let confirmation = match choice {
            "keep" => "Keep current data",
            "restore" => "Restore pre-update data",
            _ => return Err("Invalid recovery choice".into()),
        };
        Self::spawn_selected(spec, owner, Some(confirmation))
    }

    fn spawn_selected(
        spec: HelperSpec,
        owner: &Child,
        choice: Option<&str>,
    ) -> Result<Self, String> {
        if !cfg!(windows) {
            return Err("Update helper handoff is supported on Windows only".into());
        }
        if std::env::var_os("SYLVIA_GM_OWNER_EXECUTABLE").is_some()
            || std::env::var_os("SYLVIA_GM_AUTHORITY_DATABASE").is_some()
        {
            return Err("Update handoff cannot use engineering launch overrides".into());
        }
        let (executable, _verified_file) = spec.verify_helper()?;
        let (child, input, output, processes) = spawn_owned(&executable, owner)?;
        let mut result = Self {
            child: Some(child),
            input: Some(input),
            output: BufReader::new(output),
            operation: spec.operation.clone(),
            attempt: spec.attempt.clone(),
            nonce: String::new(),
        };
        let mut request = json!({
            "type": "desktop_update_apply", "operation_id": spec.operation,
            "attempt": spec.attempt, "data_root": spec.data, "live": spec.live,
            "distribution": if spec.portable { "portable" } else { "installed" },
            "processes": processes,
        });
        if let Some(choice) = choice {
            request["type"] = json!("desktop_update_recover");
            request["choice"] = json!(choice);
            request["restart_nonce"] = json!(spec.restart_nonce);
        }
        result.send(&request)?;
        let ready = result.receive()?;
        let nonce = ready["helper_nonce"]
            .as_str()
            .ok_or("The helper did not become ready")?;
        if ready.as_object().map(|row| row.len()) != Some(4)
            || ready["type"] != "ready"
            || ready["operation_id"] != result.operation
            || ready["attempt"] != result.attempt
            || !valid_id(nonce)
        {
            return Err("The update helper returned a different operation".into());
        }
        result.nonce = nonce.into();
        Ok(result)
    }

    fn send(&mut self, value: &Value) -> Result<(), String> {
        let encoded = serde_json::to_vec(value).map_err(|_| "Invalid helper message")?;
        if encoded.len() > 65536 {
            return Err("Helper message exceeds its bound".into());
        }
        let input = self.input.as_mut().ok_or("The helper input is closed")?;
        input
            .write_all(&encoded)
            .and_then(|_| input.write_all(b"\n"))
            .and_then(|_| input.flush())
            .map_err(|_| "The helper did not accept its prepared request".into())
    }

    fn receive(&mut self) -> Result<Value, String> {
        let mut raw = Vec::new();
        (&mut self.output)
            .take(65537)
            .read_until(b'\n', &mut raw)
            .map_err(|_| "The update helper response could not be read")?;
        if raw.len() > 65536 || raw.last() != Some(&b'\n') {
            return Err("The update helper response is incomplete".into());
        }
        serde_json::from_slice(&raw).map_err(|_| "The update helper response is invalid".into())
    }

    pub fn commit(mut self) -> Result<HelperProcess, String> {
        self.send(&json!({"action": "commit", "operation_id": self.operation,
                          "attempt": self.attempt, "helper_nonce": self.nonce}))?;
        self.input.take();
        self.child
            .take()
            .ok_or_else(|| "The committed helper handle is unavailable".into())
    }
}

impl Drop for Handoff {
    fn drop(&mut self) {
        self.input.take();
        if let Some(child) = self.child.take() {
            // Uncommitted helper EOF exits normally without apply. Never kill another process.
            let _ = child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn metadata() -> Value {
        json!({"operation_id": "a".repeat(32), "attempt": "b".repeat(32),
               "helper": {"size": 1024, "sha256": "c".repeat(64)}})
    }

    #[test]
    fn helper_path_is_derived_only_from_owned_roots_and_bounded_ids() {
        let live = std::env::temp_dir().join("sylvia-inert-fixture");
        let data = live.join("isolated-data");
        let spec = HelperSpec::from_prepared(&data, &live, true, &metadata()).unwrap();
        assert_eq!(
            spec.executable(),
            data.join(".application-updates")
                .join("a".repeat(32))
                .join("attempts")
                .join("b".repeat(32))
                .join("helper/sylvia-gm-owner.exe")
        );
        assert!(HelperSpec::from_prepared(&live, &data, true, &metadata()).is_err());
    }

    #[test]
    fn helper_metadata_rejects_commands_paths_and_wrong_identity_before_any_spawn() {
        let live = std::env::temp_dir().join("sylvia-inert-fixture");
        let data = live.join("isolated-data");
        for field in ["path", "command", "url", "public_key"] {
            let mut row = metadata();
            row[field] = json!("untrusted");
            assert!(HelperSpec::from_prepared(&data, &live, true, &row).is_err());
        }
        for (field, value) in [
            ("operation_id", "../outside"),
            ("attempt", "A"),
            ("attempt", ""),
        ] {
            let mut row = metadata();
            row[field] = json!(value);
            assert!(HelperSpec::from_prepared(&data, &live, true, &row).is_err());
        }
        let mut row = metadata();
        row["helper"]["size"] = json!(true);
        assert!(HelperSpec::from_prepared(&data, &live, true, &row).is_err());
    }

    #[test]
    fn recovery_metadata_requires_an_explicit_bounded_nonce_and_keeps_apply_strict() {
        let live = std::env::temp_dir().join("sylvia-inert-fixture");
        let data = live.join("isolated-data");
        let mut row = metadata();
        assert!(HelperSpec::from_recovery(&data, &live, true, &row).is_err());
        for nonce in [Value::Null, json!("d".repeat(32))] {
            row["restart_nonce"] = nonce;
            let spec = HelperSpec::from_recovery(&data, &live, true, &row).unwrap();
            assert_eq!(spec.restart_nonce.as_deref(), row["restart_nonce"].as_str());
            assert!(HelperSpec::from_prepared(&data, &live, true, &row).is_err());
        }
        for nonce in [
            json!(true),
            json!("../other"),
            json!("D".repeat(32)),
            json!(12),
        ] {
            row["restart_nonce"] = nonce;
            assert!(HelperSpec::from_recovery(&data, &live, true, &row).is_err());
        }
    }
}
