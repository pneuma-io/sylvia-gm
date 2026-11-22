//! `spatial/_interface.py`: `mutate_spatial_scene`, one neutral object patch that advances the
//! map revision.

use super::scene::{capture_payload, choice, hex, identity, integer, object, reject, Admit};
use super::{admit_spatial_scene, AdmittedSpatialScene, SpatialError, SPATIAL_ALGORITHM_VERSION};
use serde_json::json;
use sha2::{Digest, Sha256};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::package_library::canonical_json_bytes;

const MUTATION_KEYS: &[&str] = &["map_id", "expected_revision", "target", "patch"];
const MUTATION_TARGET_KEYS: &[&str] = &["kind", "id"];
const GEOMETRY_FIELDS: &[&str] =
    &["volume", "active", "movement", "occupies_cells", "visibility", "metadata"];
const ENTITY_FIELDS: &[&str] =
    &["position", "footprint", "height", "facing", "active", "movement", "visibility", "metadata"];
const MUTATION_INVALID: &str = "spatial_mutation_invalid";

/// `AdmittedSpatialMutation`: one validated scene replacement and its before/after evidence.
#[derive(Debug, Clone, PartialEq)]
pub struct AdmittedSpatialMutation {
    payload: JsonMap,
    /// `scene`: the advanced scene.
    pub scene: AdmittedSpatialScene,
}

impl AdmittedSpatialMutation {
    /// `as_payload()`: the mutation evidence.
    pub fn as_payload(&self) -> JsonMap {
        self.payload.clone()
    }
}

/// `_scene_sha256(scene)`.
fn scene_sha256(scene: &AdmittedSpatialScene) -> String {
    sha256(&Json::Object(scene.as_payload()))
}

fn sha256(value: &Json) -> String {
    hex(&Sha256::digest(canonical_json_bytes(value)))
}

/// The request's checks, in Python's order, before the scene is touched.
fn admit_request(scene: &AdmittedSpatialScene, value: &Json) -> Admit<(String, String, JsonMap)> {
    let code = MUTATION_INVALID;
    let captured = Json::Object(capture_payload(value, code)?);
    let request = object(&captured, MUTATION_KEYS, code, "$")?;
    if request["map_id"].as_str() != Some(scene.map_id.as_str()) {
        return reject("Spatial mutation belongs to another map", "spatial_cross_map", "$.map_id");
    }
    let expected = integer(&request["expected_revision"], 0, i64::MAX, code, "$.expected_revision")?;
    if expected != scene.geometry_revision {
        return reject(
            "Spatial mutation expected revision is stale",
            "spatial_stale_revision",
            "$.expected_revision",
        );
    }
    let target = object(&request["target"], MUTATION_TARGET_KEYS, code, "$.target")?;
    let kind = choice(&target["kind"], &["geometry", "entity"], code, "$.target.kind")?;
    let id = identity(&target["id"], code, "$.target.id")?;
    let patch = capture_payload(&request["patch"], code)?;
    let fields = if kind == "geometry" { GEOMETRY_FIELDS } else { ENTITY_FIELDS };
    if patch.is_empty() || patch.keys().any(|key| !fields.contains(&key.as_str())) {
        return reject("Spatial mutation patch uses unsupported fields", code, "$.patch");
    }
    Ok((kind, id, patch))
}

/// `mutate_spatial_scene(scene, value)`: apply one exact neutral object patch and advance the
/// authoritative map revision.
pub fn mutate_spatial_scene(
    scene: &AdmittedSpatialScene,
    value: &Json,
) -> Result<AdmittedSpatialMutation, SpatialError> {
    let (kind, id, patch) = admit_request(scene, value)?;
    let mut scene_payload = scene.as_payload();
    let collection_name = if kind == "geometry" { "geometry" } else { "entities" };
    let collection = scene_payload[collection_name].as_array_mut().expect("an admitted collection");
    // An admitted scene's identities are unique, so the target matches at most once.
    let Some(position) = collection.iter().position(|item| item["id"].as_str() == Some(id.as_str())) else {
        return Err(reject::<()>(
            "Spatial mutation target is missing",
            "spatial_target_missing",
            "$.target.id",
        )
        .unwrap_err()
        .into());
    };
    let before_target = collection[position].clone();
    // `{**before_target, **patch}`: patched fields keep their place.
    let mut replacement = before_target.as_object().expect("an admitted object").clone();
    for (key, item) in &patch {
        replacement.insert(key.clone(), item.clone());
    }
    collection[position] = Json::Object(replacement);
    // `scene.geometry_revision + 1`, which may pass the signed 64-bit range admission allows.
    let next_revision = scene.geometry_revision as u64 + 1;
    scene_payload["map"]["revision"] = json!(next_revision);
    let advanced = admit_spatial_scene(&Json::Object(scene_payload))?;
    let after_target = advanced.payload()[collection_name]
        .as_array()
        .expect("an admitted collection")
        .iter()
        .find(|item| item["id"].as_str() == Some(id.as_str()))
        .expect("the patched target")
        .clone();
    let mut keys: Vec<&String> = patch.keys().collect();
    keys.sort();
    let mut patched = JsonMap::new();
    for key in keys {
        patched.insert(key.clone(), after_target[key.as_str()].clone());
    }
    let result = json!({
        "kind": "scene_mutation",
        "map_id": scene.map_id,
        "geometry_revision": advanced.geometry_revision,
        "snapshot_id": advanced.snapshot_id,
        "algorithm_version": SPATIAL_ALGORITHM_VERSION,
        "target": {"kind": kind, "id": id},
        "patch": patched,
        "before": {
            "geometry_revision": scene.geometry_revision,
            "snapshot_id": scene.snapshot_id,
            "scene_sha256": scene_sha256(scene),
            "target_sha256": sha256(&before_target),
        },
        "after": {
            "geometry_revision": advanced.geometry_revision,
            "snapshot_id": advanced.snapshot_id,
            "scene_sha256": scene_sha256(&advanced),
            "target_sha256": sha256(&after_target),
        },
    });
    let Json::Object(payload) = result else { unreachable!("an object literal") };
    Ok(AdmittedSpatialMutation { payload, scene: advanced })
}
