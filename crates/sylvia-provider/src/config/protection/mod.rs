//! `config/_credential_protection.py`: current-user protection for owner-local provider
//! secrets.
//!
//! Windows uses current-user DPAPI ([`windows`]); Linux seals with AES-256-GCM under a
//! per-user key file in the state folder ([`linux`]). Each platform opens only its own envelope
//! kind; other platforms fail closed. The envelope is
//! `{"version": 1, "protection": ..., "data": base64}` over the JSON of the key and headers.

#[cfg(target_os = "linux")]
pub mod linux;
#[cfg(windows)]
pub mod windows;

use super::env::Environment;
use super::resolver::resolve_vars;
use super::ConfigFailure;
use sylvia_base::pybase64;
use sylvia_base::pyjson;
use sylvia_base::pyyaml::Data;
use sylvia_base::Json;

/// `UNAVAILABLE_MESSAGE`.
#[cfg(target_os = "linux")]
pub const UNAVAILABLE_MESSAGE: &str = "This computer could not unlock this connection's saved credentials. Re-enter its API key and private headers, or remove the connection.";
/// `UNAVAILABLE_MESSAGE`.
#[cfg(not(target_os = "linux"))]
pub const UNAVAILABLE_MESSAGE: &str = "Windows could not unlock this connection's saved credentials. Re-enter its API key and private headers, or remove the connection.";

/// `_PROTECTION`: the envelope kind this platform writes and opens.
#[cfg(target_os = "linux")]
pub const PROTECTION: &str = "linux-user-key-file";
/// `_PROTECTION`: the envelope kind this platform writes and opens.
#[cfg(not(target_os = "linux"))]
pub const PROTECTION: &str = "windows-dpapi-user";

/// `_VERIFY_FAILED`.
#[cfg(target_os = "linux")]
pub const VERIFY_FAILED: &str = "Saved credential protection could not be verified";
/// `_VERIFY_FAILED`.
#[cfg(not(target_os = "linux"))]
pub const VERIFY_FAILED: &str = "Windows credential protection could not be verified";

/// `_SECRET_BYTE_LIMIT`.
pub const SECRET_BYTE_LIMIT: usize = 128 * 1024;

/// `"Protected credentials exceed the supported size"`.
pub const OVERSIZE_MESSAGE: &str = "Protected credentials exceed the supported size";

/// `_platform_seal(value, decrypt=...)`: seal or open bytes with user-scoped protection.
pub trait Seal {
    fn seal(&self, value: &[u8], decrypt: bool) -> Result<Vec<u8>, ConfigFailure>;
}

/// This platform's protection: DPAPI on Windows, the key file under the user's state folder
/// on Linux, and on any other platform the refusal Python gives there.
#[derive(Debug, Clone, Copy, Default)]
pub struct PlatformSeal;

impl Seal for PlatformSeal {
    fn seal(&self, value: &[u8], decrypt: bool) -> Result<Vec<u8>, ConfigFailure> {
        #[cfg(windows)]
        {
            windows::dpapi(value, decrypt)
        }
        #[cfg(target_os = "linux")]
        {
            linux::KeyFile::current().seal(value, decrypt)
        }
        #[cfg(not(any(windows, target_os = "linux")))]
        {
            let _ = (value, decrypt);
            Err(ConfigFailure::config("Saving or unlocking protected credentials requires Windows"))
        }
    }
}

/// `protect_credentials(api_key, headers)`: seal the key and headers, then open the result to
/// verify it before any settings write.
pub fn protect_credentials(
    seal: &dyn Seal,
    api_key: &str,
    headers: &[(String, String)],
) -> Result<Data, ConfigFailure> {
    let mut material = sylvia_base::JsonMap::new();
    material.insert("api_key".into(), Json::String(api_key.to_owned()));
    material.insert("headers".into(), super::pyvalue::text_map_json(headers));
    let payload = pyjson::dumps(&Json::Object(material), pyjson::Dump::DEFAULT.ascii(false)).into_bytes();
    if payload.len() > SECRET_BYTE_LIMIT / 2 {
        return Err(ConfigFailure::config("Provider credentials exceed the supported size"));
    }
    let encrypted = seal.seal(&payload, false)?;
    if seal.seal(&encrypted, true)? != payload {
        return Err(ConfigFailure::config(VERIFY_FAILED));
    }
    Ok(Data::Dict(vec![
        (Data::Str("version".into()), Data::Int(1)),
        (Data::Str("protection".into()), Data::Str(PROTECTION.into())),
        (Data::Str("data".into()), Data::Str(pybase64::b64encode(&encrypted))),
    ]))
}

fn unavailable() -> ConfigFailure {
    ConfigFailure::config(UNAVAILABLE_MESSAGE)
}

/// `unprotect_credentials(envelope, resolve=...)`: one selected provider's key and headers, in
/// memory, or [`UNAVAILABLE_MESSAGE`] for any failure. With `resolve`, `${VAR}` references in
/// them are resolved as before protection existed.
pub fn unprotect_credentials(
    seal: &dyn Seal,
    envelope: &Data,
    resolve: bool,
    env: &Environment,
) -> Result<(String, Vec<(String, String)>), ConfigFailure> {
    let Some(pairs) = envelope.as_dict() else { return Err(unavailable()) };
    let keys: Vec<&str> = pairs.iter().filter_map(|(key, _)| key.as_str()).collect();
    let exactly =
        pairs.len() == 3 && ["version", "protection", "data"].iter().all(|wanted| keys.contains(wanted));
    if !exactly {
        return Err(unavailable());
    }
    let (Some(Data::Int(1)), Some(Data::Str(protection)), Some(Data::Str(data))) =
        (envelope.get_str("version"), envelope.get_str("protection"), envelope.get_str("data"))
    else {
        return Err(unavailable());
    };
    if protection != PROTECTION || data.chars().count() > SECRET_BYTE_LIMIT * 2 {
        return Err(unavailable());
    }
    let sealed = pybase64::b64decode_validate(data).map_err(|_| unavailable())?;
    let payload = seal.seal(&sealed, true).map_err(|_| unavailable())?;
    // json.loads(bytes): the payload is UTF-8 JSON written by protect_credentials.
    let text = String::from_utf8(payload).map_err(|_| unavailable())?;
    let material = pyjson::loads(&text).map_err(|_| unavailable())?;
    let Json::Object(material) = material else { return Err(unavailable()) };
    if material.len() != 2 || !material.contains_key("api_key") || !material.contains_key("headers") {
        return Err(unavailable());
    }
    let (Some(Json::String(key)), Some(Json::Object(headers))) =
        (material.get("api_key"), material.get("headers"))
    else {
        return Err(unavailable());
    };
    let mut header_pairs = Vec::with_capacity(headers.len());
    for (name, value) in headers {
        let Json::String(value) = value else { return Err(unavailable()) };
        header_pairs.push((name.clone(), value.clone()));
    }
    if !resolve {
        return Ok((key.clone(), header_pairs));
    }
    let key = super::resolver::resolve_string(key, "config", env).map_err(|_| unavailable())?;
    let header_data =
        Data::Dict(header_pairs.into_iter().map(|(n, v)| (Data::Str(n), Data::Str(v))).collect());
    let resolved = resolve_vars(&header_data, "config", env).map_err(|_| unavailable())?;
    let headers = resolved
        .as_dict()
        .expect("a dict stays a dict")
        .iter()
        .map(|(name, value)| {
            (name.as_str().unwrap_or_default().to_owned(), value.as_str().unwrap_or_default().to_owned())
        })
        .collect();
    Ok((key, headers))
}
