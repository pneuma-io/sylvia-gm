//! `spatial/_engine.py`: `SpatialEngine`, the public query interface over an authoritative scene.

use super::engine::{compile, resolve, CompiledScene};
use super::{
    admit_spatial_query, admit_spatial_result, admit_spatial_scene, AdmittedSpatialResult,
    AdmittedSpatialScene, SpatialError, SpatialValidationError,
};
use indexmap::IndexMap;
use sylvia_base::Json;
use sylvia_domain::package_library::canonical_json_bytes;

/// `_CACHE_LIMIT`: compiled scenes and query results each keep at most this many entries.
const CACHE_LIMIT: usize = 256;

/// `SpatialEngine`: deterministic spatial facts over one map's authoritative scenes.
///
/// It keeps the compiled scenes it has seen and the results it has resolved, each in insertion
/// order with Python's `OrderedDict` evictions, so later queries answer from the same work.
pub struct SpatialEngine {
    current: CompiledScene,
    compiled: IndexMap<String, CompiledScene>,
    cache: IndexMap<(String, Vec<u8>), AdmittedSpatialResult>,
}

impl SpatialEngine {
    /// `SpatialEngine(scene)`.
    pub fn new(scene: &Json) -> Result<Self, SpatialError> {
        let current = compile(&admit_spatial_scene(scene)?);
        let mut compiled = IndexMap::new();
        compiled.insert(current.admitted.snapshot_id.clone(), current.clone());
        Ok(Self { current, compiled, cache: IndexMap::new() })
    }

    /// `snapshot`: the scene unsnapshotted queries use.
    pub fn snapshot(&self) -> &AdmittedSpatialScene {
        &self.current.admitted
    }

    /// `replace_scene(scene)`: advance the current geometry, keeping earlier snapshots queryable.
    pub fn replace_scene(&mut self, scene: &Json) -> Result<AdmittedSpatialScene, SpatialError> {
        let admitted = admit_spatial_scene(scene)?;
        if admitted.map_id != self.current.admitted.map_id {
            return Err(SpatialValidationError {
                message: "Replacement scene belongs to another map",
                code: "spatial_cross_map",
                path: "$.map.id".into(),
            }
            .into());
        }
        if admitted.geometry_revision <= self.current.admitted.geometry_revision {
            return Err(SpatialValidationError {
                message: "Replacement geometry revision must advance",
                code: "spatial_revision_invalid",
                path: "$.map.revision".into(),
            }
            .into());
        }
        let compiled = compile(&admitted);
        self.current = compiled.clone();
        // `OrderedDict` assignment keeps an existing key where it is.
        self.compiled.insert(admitted.snapshot_id.clone(), compiled);
        while self.compiled.len() > CACHE_LIMIT {
            self.compiled.shift_remove_index(0);
        }
        Ok(admitted)
    }

    /// `query(request, snapshot=...)`: resolve one closed query against one immutable scene.
    pub fn query(
        &mut self,
        request: &Json,
        snapshot: Option<&AdmittedSpatialScene>,
    ) -> Result<AdmittedSpatialResult, SpatialError> {
        let admitted_scene = snapshot.cloned().unwrap_or_else(|| self.current.admitted.clone());
        if !self.compiled.contains_key(&admitted_scene.snapshot_id) {
            self.compiled.insert(admitted_scene.snapshot_id.clone(), compile(&admitted_scene));
        }
        let query = admit_spatial_query(&admitted_scene, request)?;
        let cache_key =
            (admitted_scene.snapshot_id.clone(), canonical_json_bytes(&Json::Object(query.as_payload())));
        if let Some(index) = self.cache.get_index_of(&cache_key) {
            let last = self.cache.len() - 1;
            self.cache.move_index(index, last);
            return Ok(self.cache[last].clone());
        }
        let compiled = &self.compiled[&admitted_scene.snapshot_id];
        let resolved = resolve(compiled, query.payload())?;
        let result = admit_spatial_result(&admitted_scene, &Json::Object(resolved))?;
        self.cache.insert(cache_key, result.clone());
        while self.cache.len() > CACHE_LIMIT {
            self.cache.shift_remove_index(0);
        }
        Ok(result)
    }

    /// The number of compiled scenes and cached results held (for the eviction vectors).
    pub fn cache_sizes(&self) -> (usize, usize) {
        (self.compiled.len(), self.cache.len())
    }
}
