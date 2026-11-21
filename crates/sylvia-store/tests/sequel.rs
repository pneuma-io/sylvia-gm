//! `sqlite_sequel.verify_sequel` against Python (`tests/vectors/sequel`, written by
//! `generate_sequel_vectors.py`): a reviewed sequel Plan built as `campaign_sequels.append`
//! builds it, and one-field changes of it. Where Python raises `ValueError`, Rust must give the
//! same text; where it raises anything else, Rust must refuse too; where it accepts, Rust must.

mod common;

use sylvia_base::Json;
use sylvia_domain::session::{CampaignPlan, CampaignPlanFields, PlanAcceptanceStatus, PlanValidationOutcome};

fn plan(fields: &Json) -> CampaignPlan {
    let text = |key: &str| fields[key].as_str().unwrap().to_owned();
    CampaignPlan::new(CampaignPlanFields {
        plan_id: text("plan_id"),
        campaign_id: text("campaign_id"),
        package_lock_id: text("package_lock_id"),
        source_session_id: text("source_session_id"),
        content_sha256: text("content_sha256"),
        content: fields["content"].clone(),
        provenance_evidence_ids: fields["provenance_evidence_ids"]
            .as_array()
            .unwrap()
            .iter()
            .map(|id| id.as_str().unwrap().to_owned())
            .collect(),
        validation_outcome: PlanValidationOutcome::parse(&text("validation_outcome")).unwrap(),
        acceptance_status: PlanAcceptanceStatus::parse(&text("acceptance_status")).unwrap(),
        plan_revision: fields["plan_revision"].as_i64().unwrap(),
        resulting_campaign_revision: fields["resulting_campaign_revision"].as_i64().unwrap(),
        payload_sha256: text("payload_sha256"),
    })
    .unwrap()
}

#[test]
fn verify_sequel_matches_python() {
    let record = common::read_json("tests/vectors/sequel/sequel_vectors.json");
    let cases = record["cases"].as_array().unwrap();
    assert!(cases.len() > 30);
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let got = sylvia_store::sequel::verify_sequel(&plan(&case["plan"]), &plan(&case["previous"]));
        let want = &case["outcome"];
        match (got, want.get("error").and_then(Json::as_str)) {
            (Ok(()), None) => {}
            (Err(error), Some("ValueError")) => {
                let coded =
                    error.rejection().unwrap_or_else(|| panic!("{name}: Rust {error:?}, Python {want}"));
                assert_eq!(coded.message, want["message"].as_str().unwrap(), "{name}");
            }
            (Err(_), Some(_)) => {}
            (got, _) => panic!("{name}: Rust {got:?}, Python {want}"),
        }
    }
}
