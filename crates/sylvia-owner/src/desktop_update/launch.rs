//! `application/desktop_update/launch.py`: the immutable installation context the native
//! parent supplies once, on its private owner pipe.

use super::manifest::{Version, APP_VERSION};
use super::storage::{directory_identity, same_path, Identity};
use super::update_error;
use std::path::{Path, PathBuf};
use sylvia_base::{CodedResult, Json};

/// `Installation`: one application copy and its data folder, with their folder identities
/// taken when the owner started.
#[derive(Debug, Clone, PartialEq)]
pub struct Installation {
    pub live: PathBuf,
    pub data: PathBuf,
    pub distribution: String,
    pub target: String,
    pub app_identity: Identity,
    pub data_identity: Identity,
}

impl Installation {
    /// `Installation.from_launch(value, data)`: `None` for a source or engineering owner,
    /// which never gets an install capability.
    pub fn from_launch(value: Option<&Json>, data: &Path) -> CodedResult<Option<Installation>> {
        Self::parse(value, data, APP_VERSION)
    }

    /// `Installation.for_recovery(value, data)`: a mixed old and new pair may offer recovery,
    /// never ordinary play or install admission.
    pub fn for_recovery(value: Option<&Json>, data: &Path) -> CodedResult<Option<Installation>> {
        let version =
            value.and_then(Json::as_object).and_then(|object| object.get("version")).unwrap_or(&Json::Null);
        let expected = Version::parse(version)?.text;
        Self::parse(value, data, &expected)
    }

    fn parse(value: Option<&Json>, data: &Path, expected_version: &str) -> CodedResult<Option<Installation>> {
        let Some(value) = value.filter(|value| !value.is_null()) else {
            return Ok(None);
        };
        let invalid = || update_error("invalid_launch_context", "The native update context is invalid.");
        let Json::Object(value) = value else {
            return Err(invalid());
        };
        let keys = ["live", "data_root", "version", "target", "distribution"];
        if value.len() != keys.len() || !keys.iter().all(|key| value.contains_key(*key)) {
            return Err(invalid());
        }
        let location = |name: &str| -> Option<PathBuf> {
            let text = value[name].as_str()?;
            let length = text.chars().count();
            // `Path(text)`: rebuilt from its components, so separators, repeated or trailing
            // slashes and `.` parts are normalized as `pathlib` normalizes them.
            ((1..=4096).contains(&length) && Path::new(text).is_absolute())
                .then(|| Path::new(text).components().collect::<PathBuf>())
        };
        let (live, selected) = match (location("live"), location("data_root")) {
            (Some(live), Some(selected))
                if value["version"] == expected_version
                    && value["target"] == "windows-x86_64"
                    && matches!(value["distribution"].as_str(), Some("installed" | "portable")) =>
            {
                (live, selected)
            }
            _ => return Err(invalid()),
        };
        let distribution = value["distribution"].as_str().unwrap_or_default().to_string();
        if !same_path(&selected, data)
            || (distribution == "portable" && !same_path(&selected, &live.join("isolated-data")))
        {
            return Err(update_error("wrong_data_root", "The native update data folder is inconsistent."));
        }
        let app_identity = directory_identity(&live)?;
        let data_identity = directory_identity(&selected)?;
        Ok(Some(Installation {
            live,
            data: selected,
            distribution,
            target: value["target"].as_str().unwrap_or_default().to_string(),
            app_identity,
            data_identity,
        }))
    }

    /// `check(target, distribution)`: the same copy, in the same folders, as at startup.
    pub fn check(&self, target: &str, distribution: &str) -> CodedResult<()> {
        if (target, distribution) != (self.target.as_str(), self.distribution.as_str())
            || directory_identity(&self.live)? != self.app_identity
            || directory_identity(&self.data)? != self.data_identity
        {
            return Err(update_error("changed_update_context", "The selected application copy changed."));
        }
        Ok(())
    }
}
