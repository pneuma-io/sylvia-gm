//! `domain/model_capability.py`: the ordered Sylvia GM capability rubric, exact model
//! placements and the owner-computable model guidance decision.

use crate::authority::{str_enum, validate_authority_identity, validate_authority_identity_value};
use crate::error::{DomainError, DomainResult};
use std::collections::HashMap;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson;
use sylvia_base::{Json, JsonMap};

/// `MODEL_CAPABILITY_REGISTRY_VERSION`.
pub const MODEL_CAPABILITY_REGISTRY_VERSION: &str = "sylvia.capability.registry.v1";

/// One stable member of the ordered capability rubric (`CapabilityTier`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CapabilityTier {
    pub tier_id: String,
    pub rank: u8,
    pub label: String,
}

/// `CAPABILITY_TIERS`: `sylvia.capability.t00` to `t09`.
pub fn capability_tiers() -> Vec<CapabilityTier> {
    (0..10)
        .map(|rank| CapabilityTier {
            tier_id: format!("sylvia.capability.t{rank:02}"),
            rank,
            label: format!("Capability tier {rank}"),
        })
        .collect()
}

/// The rank of an admitted tier identity.
fn rank(tier_id: &str) -> Option<u8> {
    capability_tiers().into_iter().find(|tier| tier.tier_id == tier_id).map(|tier| tier.rank)
}

/// `validate_capability_tier_id(value)`: one admitted stable tier identity.
pub fn validate_capability_tier_id(value: &Json) -> DomainResult<&str> {
    let tier_id = validate_authority_identity_value(value, "Model capability tier", false)?;
    if rank(tier_id).is_none() {
        return Err(DomainError::value("Model capability tier is unsupported"));
    }
    Ok(tier_id)
}

/// `validate_capability_tier_id` for text already known to be a `str`.
pub fn validate_capability_tier_text(value: &str) -> DomainResult<&str> {
    let tier_id = validate_authority_identity(value, "Model capability tier", false)?;
    if rank(tier_id).is_none() {
        return Err(DomainError::value("Model capability tier is unsupported"));
    }
    Ok(tier_id)
}

/// `_canonical_bytes(value)`: sorted keys, `,`/`:` separators, UTF-8.
fn canonical_sha256(value: &Json) -> String {
    sha256_hex(pyjson::canonical_bytes(value))
}

/// One exact model placement backed by benchmark evidence (`ModelCapabilityPlacement`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelCapabilityPlacement {
    model_id: String,
    tier_id: String,
    benchmark_suite_id: String,
    benchmark_suite_version: String,
    benchmark_suite_sha256: String,
    result_sha256: String,
}

impl ModelCapabilityPlacement {
    /// `ModelCapabilityPlacement(...)` with Python's checks, in Python's order.
    pub fn new(
        model_id: &str,
        tier_id: &str,
        benchmark_suite_id: &str,
        benchmark_suite_version: &str,
        benchmark_suite_sha256: &str,
        result_sha256: &str,
    ) -> DomainResult<Self> {
        Ok(Self {
            model_id: validate_authority_identity(model_id, "Model identity", false)?.to_owned(),
            tier_id: validate_capability_tier_text(tier_id)?.to_owned(),
            benchmark_suite_id: validate_authority_identity(
                benchmark_suite_id,
                "Benchmark suite identity",
                false,
            )?
            .to_owned(),
            benchmark_suite_version: validate_authority_identity(
                benchmark_suite_version,
                "Benchmark suite version",
                false,
            )?
            .to_owned(),
            benchmark_suite_sha256: validate_authority_identity(
                benchmark_suite_sha256,
                "Benchmark suite SHA-256",
                true,
            )?
            .to_owned(),
            result_sha256: validate_authority_identity(result_sha256, "Benchmark result SHA-256", true)?
                .to_owned(),
        })
    }

    pub fn model_id(&self) -> &str {
        &self.model_id
    }
    pub fn tier_id(&self) -> &str {
        &self.tier_id
    }

    /// `evidence_sha256`: the canonical hash of every placement field.
    pub fn evidence_sha256(&self) -> String {
        canonical_sha256(&serde_json::json!({
            "benchmark_suite_id": self.benchmark_suite_id,
            "benchmark_suite_sha256": self.benchmark_suite_sha256,
            "benchmark_suite_version": self.benchmark_suite_version,
            "model_id": self.model_id,
            "result_sha256": self.result_sha256,
            "tier_id": self.tier_id,
        }))
    }
}

str_enum! {
    /// Whether a model meets a Ruleset's minimum tier.
    ModelGuidanceStatus {
        NotApplicable = "not_applicable",
        Unrated = "unrated",
        Below = "below",
        Meets = "meets",
    }
}

/// The fields of a [`ModelGuidanceDecision`], before validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelGuidanceFields {
    pub registry_version: String,
    pub model_id: String,
    pub minimum_tier_id: Option<String>,
    pub placement_tier_id: Option<String>,
    pub placement_evidence_sha256: Option<String>,
    pub status: ModelGuidanceStatus,
    pub warning_code: Option<String>,
    pub override_accepted: bool,
}

/// The warning decision for one exact model and Ruleset pairing (`ModelGuidanceDecision`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelGuidanceDecision {
    fields: ModelGuidanceFields,
}

impl ModelGuidanceDecision {
    /// `ModelGuidanceDecision(...)` with Python's checks, in Python's order.
    pub fn new(fields: ModelGuidanceFields) -> DomainResult<Self> {
        let value = |message: &str| Err(DomainError::value(message));
        if fields.registry_version != MODEL_CAPABILITY_REGISTRY_VERSION {
            return value("Model guidance registry version is unsupported");
        }
        validate_authority_identity(&fields.model_id, "Model identity", false)?;
        let Some(minimum) = &fields.minimum_tier_id else {
            if fields.placement_tier_id.is_some()
                || fields.placement_evidence_sha256.is_some()
                || fields.status != ModelGuidanceStatus::NotApplicable
                || fields.warning_code.is_some()
                || fields.override_accepted
            {
                return value("No-rules model guidance decision is inconsistent");
            }
            return Ok(Self { fields });
        };
        let minimum_rank = rank(validate_capability_tier_text(minimum)?).expect("validated tier");
        if fields.status == ModelGuidanceStatus::Unrated {
            if fields.placement_tier_id.is_some()
                || fields.placement_evidence_sha256.is_some()
                || fields.warning_code.as_deref() != Some("model_capability_unrated")
            {
                return value("Unrated model guidance decision is inconsistent");
            }
            return Ok(Self { fields });
        }
        if !matches!(fields.status, ModelGuidanceStatus::Below | ModelGuidanceStatus::Meets) {
            return value("Ruleset model guidance status is inconsistent");
        }
        let (Some(placement), Some(evidence)) =
            (&fields.placement_tier_id, &fields.placement_evidence_sha256)
        else {
            return value("Rated model guidance evidence is incomplete");
        };
        let placement_rank = rank(validate_capability_tier_text(placement)?).expect("validated tier");
        validate_authority_identity(evidence, "Model capability placement evidence SHA-256", true)?;
        let below = placement_rank < minimum_rank;
        let expected_status = if below { ModelGuidanceStatus::Below } else { ModelGuidanceStatus::Meets };
        let expected_warning = below.then_some("model_below_ruleset_minimum");
        if fields.status != expected_status || fields.warning_code.as_deref() != expected_warning {
            return value("Rated model guidance comparison is inconsistent");
        }
        if fields.override_accepted && !below {
            return value("Model guidance override is not applicable");
        }
        Ok(Self { fields })
    }

    pub fn fields(&self) -> &ModelGuidanceFields {
        &self.fields
    }
    pub fn model_id(&self) -> &str {
        &self.fields.model_id
    }
    pub fn status(&self) -> ModelGuidanceStatus {
        self.fields.status
    }

    /// `warning_required`.
    pub fn warning_required(&self) -> bool {
        matches!(self.fields.status, ModelGuidanceStatus::Below | ModelGuidanceStatus::Unrated)
    }

    /// `may_continue`.
    pub fn may_continue(&self) -> bool {
        !self.warning_required() || self.fields.override_accepted
    }
}

/// The exact-model registry behind owner-side guidance (`ModelCapabilityRegistry`).
#[derive(Debug, Clone, Default)]
pub struct ModelCapabilityRegistry {
    placements: HashMap<String, ModelCapabilityPlacement>,
}

impl ModelCapabilityRegistry {
    /// `ModelCapabilityRegistry(placements)`: model identities are unique.
    pub fn new(placements: Vec<ModelCapabilityPlacement>) -> DomainResult<Self> {
        let mut by_model = HashMap::new();
        for placement in placements {
            if by_model.contains_key(&placement.model_id) {
                return Err(DomainError::value(
                    "Model capability registry contains a duplicate model identity",
                ));
            }
            by_model.insert(placement.model_id.clone(), placement);
        }
        Ok(Self { placements: by_model })
    }

    /// `EMPTY_MODEL_CAPABILITY_REGISTRY`.
    pub fn empty() -> Self {
        Self::default()
    }

    /// `placement(model_id)`.
    pub fn placement(&self, model_id: &str) -> DomainResult<Option<&ModelCapabilityPlacement>> {
        let exact = validate_authority_identity(model_id, "Model identity", false)?;
        Ok(self.placements.get(exact))
    }

    /// `assess(model_id=..., minimum_tier_id=..., override_accepted=...)`.
    pub fn assess(
        &self,
        model_id: &str,
        minimum_tier_id: Option<&str>,
        override_accepted: bool,
    ) -> DomainResult<ModelGuidanceDecision> {
        let exact_model_id = validate_authority_identity(model_id, "Model identity", false)?.to_owned();
        let Some(minimum) = minimum_tier_id else {
            return ModelGuidanceDecision::new(ModelGuidanceFields {
                registry_version: MODEL_CAPABILITY_REGISTRY_VERSION.into(),
                model_id: exact_model_id,
                minimum_tier_id: None,
                placement_tier_id: None,
                placement_evidence_sha256: None,
                status: ModelGuidanceStatus::NotApplicable,
                warning_code: None,
                override_accepted: false,
            });
        };
        let exact_minimum = validate_capability_tier_text(minimum)?.to_owned();
        let (status, warning_code, placement_tier_id, placement_evidence_sha256) =
            match self.placements.get(&exact_model_id) {
                None => (ModelGuidanceStatus::Unrated, Some("model_capability_unrated"), None, None),
                Some(placement) if rank(&placement.tier_id) < rank(&exact_minimum) => (
                    ModelGuidanceStatus::Below,
                    Some("model_below_ruleset_minimum"),
                    Some(placement.tier_id.clone()),
                    Some(placement.evidence_sha256()),
                ),
                Some(placement) => (
                    ModelGuidanceStatus::Meets,
                    None,
                    Some(placement.tier_id.clone()),
                    Some(placement.evidence_sha256()),
                ),
            };
        let warning_required = matches!(status, ModelGuidanceStatus::Below | ModelGuidanceStatus::Unrated);
        ModelGuidanceDecision::new(ModelGuidanceFields {
            registry_version: MODEL_CAPABILITY_REGISTRY_VERSION.into(),
            model_id: exact_model_id,
            minimum_tier_id: Some(exact_minimum),
            placement_tier_id,
            placement_evidence_sha256,
            status,
            warning_code: warning_code.map(str::to_owned),
            override_accepted: override_accepted && warning_required,
        })
    }
}

/// `model_guidance_payload(decision)`: the canonical safe payload persisted with a handoff.
pub fn model_guidance_payload(decision: &ModelGuidanceDecision) -> JsonMap {
    let fields = &decision.fields;
    let text = |value: &Option<String>| value.as_deref().map(Json::from).unwrap_or(Json::Null);
    let mut payload = JsonMap::new();
    payload.insert("minimum_tier_id".into(), text(&fields.minimum_tier_id));
    payload.insert("model_id".into(), Json::from(fields.model_id.as_str()));
    payload.insert("override_accepted".into(), Json::Bool(fields.override_accepted));
    payload.insert("placement_evidence_sha256".into(), text(&fields.placement_evidence_sha256));
    payload.insert("placement_tier_id".into(), text(&fields.placement_tier_id));
    payload.insert("registry_version".into(), Json::from(fields.registry_version.as_str()));
    payload.insert("status".into(), Json::from(fields.status.as_str()));
    payload.insert("warning_code".into(), text(&fields.warning_code));
    payload
}

const GUIDANCE_KEYS: &[&str] = &[
    "minimum_tier_id",
    "model_id",
    "override_accepted",
    "placement_evidence_sha256",
    "placement_tier_id",
    "registry_version",
    "status",
    "warning_code",
];

/// `model_guidance_from_payload(value)`: reconstruct and validate one stored decision.
pub fn model_guidance_from_payload(value: &Json) -> DomainResult<ModelGuidanceDecision> {
    let invalid = || DomainError::value("Stored model guidance payload is invalid");
    let map = match value {
        Json::Object(map)
            if map.len() == GUIDANCE_KEYS.len() && GUIDANCE_KEYS.iter().all(|k| map.contains_key(*k)) =>
        {
            map
        }
        _ => return Err(invalid()),
    };
    let nullable = |key: &str| -> DomainResult<Option<String>> {
        match &map[key] {
            Json::Null => Ok(None),
            Json::String(text) => Ok(Some(text.clone())),
            _ => Err(invalid()),
        }
    };
    let minimum_tier_id = nullable("minimum_tier_id")?;
    let placement_evidence_sha256 = nullable("placement_evidence_sha256")?;
    let placement_tier_id = nullable("placement_tier_id")?;
    let warning_code = nullable("warning_code")?;
    let (
        Json::String(registry_version),
        Json::String(model_id),
        Json::String(status),
        Json::Bool(override_accepted),
    ) = (&map["registry_version"], &map["model_id"], &map["status"], &map["override_accepted"])
    else {
        return Err(invalid());
    };
    let status = ModelGuidanceStatus::parse(status)
        .ok_or_else(|| DomainError::value("Stored model guidance status is invalid"))?;
    ModelGuidanceDecision::new(ModelGuidanceFields {
        registry_version: registry_version.clone(),
        model_id: model_id.clone(),
        minimum_tier_id,
        placement_tier_id,
        placement_evidence_sha256,
        status,
        warning_code,
        override_accepted: *override_accepted,
    })
}

/// `model_guidance_payload_sha256(decision)`.
pub fn model_guidance_payload_sha256(decision: &ModelGuidanceDecision) -> String {
    canonical_sha256(&Json::Object(model_guidance_payload(decision)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn tiers_match_python() {
        let tiers = capability_tiers();
        assert_eq!(tiers.len(), 10);
        assert_eq!(tiers[3].tier_id, "sylvia.capability.t03");
        assert_eq!(tiers[3].label, "Capability tier 3");
        let message = |v: Json| validate_capability_tier_id(&v).unwrap_err().to_string();
        assert_eq!(message(json!("sylvia.capability.t10")), "Model capability tier is unsupported");
        assert_eq!(message(json!(3)), "Model capability tier must be a non-whitespace string");
    }
}
