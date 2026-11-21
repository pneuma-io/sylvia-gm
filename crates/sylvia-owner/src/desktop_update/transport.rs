//! `application/desktop_update/transport.py`: the fixed official request rules and the
//! verification-only download staging.
//!
//! Ported here: `official_request`, the redirect and final-location rules of
//! `OfficialRedirects` and `HttpsTransport.open`, `content_length`, `metadata_bytes` and
//! `download_artifact`, over a [`Transport`] the caller supplies. The HTTPS client itself
//! (`HttpsTransport`) comes with the update controller.

use super::manifest::{Manifest, MANIFEST_LIMIT, OFFICIAL_RELEASES};
use super::storage::stage_identity;
use super::update_error;
use super::url::{has_bare_percent, unquote_strict, urlsplit};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use sylvia_base::{CodedError, CodedResult};

/// `_CHUNK`.
pub const CHUNK: usize = 256 * 1024;
/// `_REDIRECT_HOSTS`.
pub const REDIRECT_HOSTS: [&str; 2] = ["github.com", "release-assets.githubusercontent.com"];

/// A `threading.Event` used only as a cancellation flag.
#[derive(Debug, Clone, Default)]
pub struct Cancel(Arc<AtomicBool>);

impl Cancel {
    /// `event.set()`.
    pub fn set(&self) {
        self.0.store(true, Ordering::SeqCst);
    }

    /// `event.is_set()`.
    pub fn is_set(&self) -> bool {
        self.0.load(Ordering::SeqCst)
    }
}

/// An open response: its headers in the order received, and bounded reads of its body.
pub trait Response {
    /// The header fields as `(name, value)`, values as `http.client` stores them.
    fn headers(&self) -> &[(String, String)];
    /// `response.read(maximum)`: at most `maximum` bytes, empty at the end of the body.
    fn read(&mut self, maximum: usize) -> std::io::Result<Vec<u8>>;
}

/// `Transport.open(url, cancel)`. Implementations refuse with an `UpdateError`.
pub trait Transport: Send + Sync {
    fn open<'a>(&'a self, url: &str, cancel: &Cancel) -> CodedResult<Box<dyn Response + 'a>>;
}

/// `official_request(url)`: the fixed release index, or one asset download of one release.
pub fn official_request(url: &str) -> CodedResult<()> {
    if url == OFFICIAL_RELEASES {
        return Ok(());
    }
    let invalid = || update_error("untrusted_origin", "The update location is invalid.");
    let parsed = urlsplit(url).map_err(|_| invalid())?;
    let path = unquote_strict(&parsed.path).map_err(|_| invalid())?;
    let prefix = "/pneuma-io/sylvia-gm/releases/download/";
    let tail = path.strip_prefix(prefix).unwrap_or(&path);
    let pieces: Vec<&str> = tail.split('/').collect();
    if parsed.scheme != "https"
        || parsed.netloc != "github.com"
        || !parsed.query.is_empty()
        || !parsed.fragment.is_empty()
        || has_bare_percent(&parsed.path)
        || !path.starts_with(prefix)
        || path.contains('\\')
        || url.contains('\\')
        || url.chars().any(|c| (c as u32) <= 32)
        || path.chars().any(|c| (c as u32) < 32)
        || pieces.len() != 2
        || pieces.iter().any(|piece| matches!(*piece, "" | "." | ".."))
    {
        return Err(update_error("untrusted_origin", "Only official Sylvia release requests are allowed."));
    }
    Ok(())
}

fn unsafe_redirect() -> CodedError {
    update_error("unsafe_redirect", "The update service returned an unsafe redirect.")
}

/// `OfficialRedirects.redirect_request`'s check of `new_url`: HTTPS to a release host only,
/// and on `github.com` an official request.
pub fn check_redirect(new_url: &str) -> CodedResult<()> {
    let parsed = urlsplit(new_url).map_err(|_| unsafe_redirect())?;
    if parsed.scheme != "https"
        || !REDIRECT_HOSTS.contains(&parsed.netloc.as_str())
        || !parsed.fragment.is_empty()
        || new_url.contains('\\')
        || new_url.chars().any(|c| (c as u32) <= 32)
    {
        return Err(unsafe_redirect());
    }
    if parsed.netloc == "github.com" {
        official_request(new_url)?;
    }
    Ok(())
}

/// `HttpsTransport.open`'s check of the final location after redirects.
pub fn check_final_location(url: &str) -> CodedResult<()> {
    let untrusted = || update_error("unsafe_redirect", "The final update location is not trusted.");
    // Python lets a `ValueError` from `urlsplit` here escape the transport; the controller then
    // reports its generic failure. Every redirect was checked already, so it cannot happen.
    let parsed = urlsplit(url).map_err(|_| update_failed())?;
    if parsed.scheme != "https"
        || !(REDIRECT_HOSTS.contains(&parsed.netloc.as_str()) || parsed.netloc == "api.github.com")
    {
        return Err(untrusted());
    }
    Ok(())
}

/// The controller's report of a failure that is not an `UpdateError`.
pub fn update_failed() -> CodedError {
    update_error("update_failed", "The update operation failed. The current application is unchanged.")
}

/// `headers.get(name)`: the first field of that name, compared case-insensitively.
fn header<'a>(headers: &'a [(String, String)], name: &str) -> Option<&'a str> {
    headers.iter().find(|(key, _)| key.eq_ignore_ascii_case(name)).map(|(_, value)| value.as_str())
}

/// `content_length(response)`: the declared length, `None` when there is none. A length of
/// up to 20 digits can exceed 64 bits, so it is a `u128`.
pub fn content_length(response: &dyn Response) -> CodedResult<Option<u128>> {
    let headers = response.headers();
    let encoding = header(headers, "Content-Encoding").unwrap_or("identity");
    if encoding.to_lowercase() != "identity" {
        return Err(update_error("invalid_length", "Encoded update responses are unsupported."));
    }
    let lengths = headers.iter().filter(|(key, _)| key.eq_ignore_ascii_case("Content-Length")).count();
    if lengths > 1 {
        return Err(update_error("invalid_length", "The update response has conflicting lengths."));
    }
    let Some(length) = header(headers, "Content-Length") else {
        return Ok(None);
    };
    if length.is_empty() || !length.bytes().all(|byte| byte.is_ascii_digit()) || length.len() > 20 {
        return Err(update_error("invalid_length", "The update response length is invalid."));
    }
    Ok(Some(length.parse().expect("twenty ASCII digits fit in 128 bits")))
}

fn cancelled_error() -> CodedError {
    update_error("cancelled", "The update download was cancelled. The current app is unchanged.")
}

/// `_cancelled(cancel)`.
pub fn check_cancelled(cancel: &Cancel) -> CodedResult<()> {
    if cancel.is_set() {
        Err(cancelled_error())
    } else {
        Ok(())
    }
}

/// `_read(response, maximum, cancel)`.
fn read(response: &mut dyn Response, maximum: usize, cancel: &Cancel) -> CodedResult<Vec<u8>> {
    check_cancelled(cancel)?;
    let result = match response.read(maximum) {
        Ok(chunk) => chunk,
        Err(_) => {
            check_cancelled(cancel)?;
            return Err(update_error(
                "network_unavailable",
                "The update connection was interrupted. You can retry safely.",
            ));
        }
    };
    check_cancelled(cancel)?;
    Ok(result)
}

/// `metadata_bytes(transport, url, cancel)`: a whole small metadata response.
pub fn metadata_bytes(transport: &dyn Transport, url: &str, cancel: &Cancel) -> CodedResult<Vec<u8>> {
    check_cancelled(cancel)?;
    let mut response = transport.open(url, cancel)?;
    let length = content_length(response.as_ref())?;
    let too_large = || update_error("metadata_limit", "Release metadata exceeds its size limit.");
    if length.is_some_and(|length| length > MANIFEST_LIMIT as u128) {
        return Err(too_large());
    }
    let mut result = Vec::new();
    loop {
        check_cancelled(cancel)?;
        let chunk = read(response.as_mut(), CHUNK.min(MANIFEST_LIMIT - result.len() + 1), cancel)?;
        if chunk.is_empty() {
            break;
        }
        result.extend_from_slice(&chunk);
        if result.len() > MANIFEST_LIMIT {
            return Err(too_large());
        }
    }
    if length.is_some_and(|length| result.len() as u128 != length) {
        return Err(update_error("download_incomplete", "Release metadata was interrupted. You can retry."));
    }
    Ok(result)
}

/// What a failed file operation inside `download_artifact` reports, after cancellation.
fn file_failure(error: &std::io::Error, cancel: &Cancel) -> CodedError {
    if cancel.is_set() {
        return cancelled_error();
    }
    match error.kind() {
        std::io::ErrorKind::StorageFull => {
            update_error("disk_full", "There is not enough space to download this update.")
        }
        std::io::ErrorKind::PermissionDenied => {
            update_error("permission_denied", "The update staging folder is not writable.")
        }
        _ => update_error("download_failed", "The update download failed. The current app is unchanged."),
    }
}

/// `checked_stage()`.
fn checked_stage(stage: &Path) -> CodedResult<super::storage::Identity> {
    match stage_identity(stage) {
        Ok(Some(identity)) => Ok(identity),
        _ => Err(update_error("unsafe_staging", "The update staging folder is unsafe.")),
    }
}

/// `download_artifact(transport, manifest, stage, cancel, progress)`: only a complete,
/// matching artifact is published as `artifact.verified`. Like Python, a refused or broken
/// download leaves `artifact.partial` behind, and a later retry refuses with
/// `staging_exists` until it is reconciled.
pub fn download_artifact(
    transport: &dyn Transport,
    manifest: &Manifest,
    stage: &Path,
    cancel: &Cancel,
    progress: &mut dyn FnMut(u64, Option<u128>),
) -> CodedResult<PathBuf> {
    if !stage.is_dir() || stage.is_symlink() {
        return Err(update_error("unsafe_staging", "The update staging folder is unavailable."));
    }
    let identity = checked_stage(stage)?;
    let partial = stage.join("artifact.partial");
    let complete = stage.join("artifact.verified");
    if partial.exists() || complete.exists() {
        return Err(update_error(
            "staging_exists",
            "Previous update files need reconciliation before retry.",
        ));
    }
    check_cancelled(cancel)?;
    let mut response = transport.open(&manifest.artifact_url, cancel)?;
    let length = content_length(response.as_ref())?;
    if length.is_some_and(|length| length != u128::from(manifest.artifact_size)) {
        return Err(update_error("invalid_length", "The download length does not match the signed release."));
    }
    if checked_stage(stage)? != identity {
        return Err(update_error("unsafe_staging", "The update staging folder changed during the request."));
    }
    let mut stream = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&partial)
        .map_err(|error| file_failure(&error, cancel))?;
    use sha2::Digest;
    let mut digest = sha2::Sha256::new();
    let mut count = 0u64;
    progress(0, length);
    loop {
        check_cancelled(cancel)?;
        let wanted = (manifest.artifact_size - count + 1).min(CHUNK as u64) as usize;
        let chunk = read(response.as_mut(), wanted, cancel)?;
        if chunk.is_empty() {
            break;
        }
        count += chunk.len() as u64;
        if count > manifest.artifact_size {
            return Err(update_error("invalid_length", "The download exceeds its signed length."));
        }
        stream.write_all(&chunk).map_err(|error| file_failure(&error, cancel))?;
        digest.update(&chunk);
        progress(count, length);
    }
    check_cancelled(cancel)?;
    if count != manifest.artifact_size || hex::encode(digest.finalize()) != manifest.artifact_sha256 {
        return Err(update_error("artifact_mismatch", "The download does not match the signed release."));
    }
    stream.flush().map_err(|error| file_failure(&error, cancel))?;
    stream.sync_all().map_err(|error| file_failure(&error, cancel))?;
    drop(stream);
    drop(response);
    if complete.exists() {
        return Err(update_error("staging_exists", "Update completion files changed unexpectedly."));
    }
    if checked_stage(stage)? != identity {
        return Err(update_error("unsafe_staging", "The update staging folder changed during download."));
    }
    // `Path.rename` never replaces an existing file on Windows; the check above stands in for
    // that, as `std::fs::rename` would replace one.
    std::fs::rename(&partial, &complete).map_err(|error| file_failure(&error, cancel))?;
    Ok(complete)
}
