//! `application/desktop_update/discovery.py`: select one eligible official release. Only its
//! signed manifest carries update authority; the release index only points at it.

use super::manifest::{
    bounded_json, parse_current_recovery_manifest, parse_manifest, release_url, Context, Manifest, Version,
    MANIFEST_LIMIT, OFFICIAL_RELEASES, RELEASE_CHANNEL,
};
use super::transport::{metadata_bytes, Cancel, Transport};
use super::update_error;
use sylvia_base::{CodedResult, Json, JsonMap};

/// `retain_current_manifest(...)`: the fixed official manifest of the running version, which
/// an installed copy keeps for recovery. The compiled version selects it, never a request.
pub fn retain_current_manifest(
    transport: &dyn Transport,
    cancel: &Cancel,
    context: Context<'_>,
) -> CodedResult<Manifest> {
    if context.distribution != "installed" {
        return Err(update_error("wrong_distribution", "Only NSIS recovery needs a previous installer."));
    }
    let current = &context.current.text;
    let name = format!("sylvia-gm-{current}-{}-installed-update.json", context.target);
    let url = release_url(
        &Json::String(format!("https://github.com/pneuma-io/sylvia-gm/releases/download/v{current}/{name}")),
        current,
        None,
    )?;
    parse_current_recovery_manifest(&metadata_bytes(transport, &url, cancel)?, context)
}

/// `type(value) is bool`.
fn boolean(release: &JsonMap, key: &str) -> Option<bool> {
    release.get(key).and_then(Json::as_bool)
}

/// `discover(...)`: the newest eligible release's manifest, or `None` when this build is current.
pub fn discover(
    transport: &dyn Transport,
    cancel: &Cancel,
    context: Context<'_>,
) -> CodedResult<Option<Manifest>> {
    let index = bounded_json(&metadata_bytes(transport, OFFICIAL_RELEASES, cancel)?, MANIFEST_LIMIT)?;
    let invalid = || update_error("invalid_metadata", "The official release index is invalid.");
    let releases = match &index {
        Json::Array(releases) if releases.len() <= 30 => releases,
        _ => return Err(invalid()),
    };
    let mut candidates: Vec<(Version, &JsonMap)> = Vec::new();
    for release in releases {
        let Json::Object(release) = release else {
            return Err(invalid());
        };
        let (Some(draft), Some(prerelease)) = (boolean(release, "draft"), boolean(release, "prerelease"))
        else {
            return Err(update_error("invalid_metadata", "The official release index is incomplete."));
        };
        if draft {
            continue;
        }
        let Some(Json::String(tag)) = release.get("tag_name") else {
            continue;
        };
        let Some(text) = tag.strip_prefix('v') else {
            continue;
        };
        let Ok(version) = Version::parse_text(text) else {
            continue;
        };
        let channel: Option<&str> = if !version.prerelease.is_empty() {
            Some(version.channel())
        } else if prerelease {
            // A plain vX.Y.Z tag flagged as a prerelease belongs to the declared non-stable channel.
            (RELEASE_CHANNEL != "stable").then_some(RELEASE_CHANNEL)
        } else {
            Some("stable")
        };
        if version <= *context.current || channel != Some(RELEASE_CHANNEL) {
            continue;
        }
        if !version.prerelease.is_empty() && !prerelease {
            return Err(update_error("invalid_metadata", "The official release channel is inconsistent."));
        }
        candidates.push((version, release));
    }
    if candidates.is_empty() {
        return Ok(None);
    }
    // A broken newest release is an error, never a reason to select a silently older payload.
    // `max` keeps the first of equal maxima.
    let mut best = 0;
    for (index, (version, _)) in candidates.iter().enumerate() {
        if *version > candidates[best].0 {
            best = index;
        }
    }
    let (version, release) = &candidates[best];
    if candidates.iter().filter(|(candidate, _)| candidate == version).count() != 1 {
        return Err(update_error("invalid_metadata", "The official release version is ambiguous."));
    }
    let name = format!("sylvia-gm-{}-{}-{}-update.json", version.text, context.target, context.distribution);
    let assets = match release.get("assets") {
        Some(Json::Array(assets)) if assets.len() <= 128 => assets,
        _ => return Err(update_error("invalid_metadata", "The official release asset list is incomplete.")),
    };
    let matches: Vec<&JsonMap> = assets
        .iter()
        .filter_map(Json::as_object)
        .filter(|row| row.get("name") == Some(&Json::String(name.clone())))
        .collect();
    if matches.len() != 1 {
        return Err(update_error(
            "missing_manifest",
            "This release has no compatible signed update manifest.",
        ));
    }
    let location = matches[0].get("browser_download_url").cloned().unwrap_or(Json::Null);
    let url = release_url(&location, &version.text, None)?;
    let manifest = parse_manifest(&metadata_bytes(transport, &url, cancel)?, context)?;
    if manifest.version.text != version.text {
        return Err(update_error("wrong_version", "The signed update does not match the selected release."));
    }
    Ok(Some(manifest))
}
