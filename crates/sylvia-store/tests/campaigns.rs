//! `campaign_index` against Python's records for the same database
//! (`tests/vectors/campaigns`, written by `generate_campaign_index.py`).

mod common;

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_base::Json;
use sylvia_domain::model_capability::model_guidance_payload;
use sylvia_store::campaigns::CampaignIndexRecord;
use sylvia_store::AuthorityStore;

fn record_json(record: &CampaignIndexRecord) -> Json {
    let campaign = &record.campaign;
    json!({
        "campaign": {
            "campaign_id": campaign.campaign_id,
            "display_name": campaign.display_name,
            "lifecycle": campaign.lifecycle.as_str(),
            "writer": campaign.writer.as_str(),
            "revision": campaign.revision,
        },
        "package_lock": record.package_lock.as_ref().map(|lock| {
            lock.entries()
                .iter()
                .map(|entry| {
                    json!({
                        "package_id": entry.package_id(),
                        "version": entry.version(),
                        "sha256": entry.sha256(),
                        "role": entry.role().as_str(),
                        "load_order": entry.load_order(),
                        "dependencies": entry.dependencies().iter().map(|d| json!([
                            d.package_id(), d.version(), d.sha256(), d.kind(), d.namespace()
                        ])).collect::<Vec<_>>(),
                        "capabilities": entry.capabilities(),
                    })
                })
                .collect::<Vec<_>>()
        }),
        "package_labels": record.package_labels.iter().map(|(id, label)| json!([id, label])).collect::<Vec<_>>(),
        "session_activity_ordinal": record.session_activity_ordinal,
        "turns": record.turns.iter().map(|(id, status)| json!([id, status.as_str()])).collect::<Vec<_>>(),
        "top_level_sessions": record.top_level_sessions.iter().map(|session| {
            let s = session.fields();
            json!({
                "session_id": s.session_id,
                "campaign_id": s.campaign_id,
                "role": s.role.as_str(),
                "provider_id": s.provider_id,
                "model_id": s.model_id,
                "status": s.status.as_str(),
                "parent_session_id": s.parent_session_id,
                "selected_skill": s.selected_skill,
                "scope": s.scope,
                "reasoning_effort": s.reasoning_effort,
                "reasoning_effort_once": s.reasoning_effort_once,
            })
        }).collect::<Vec<_>>(),
        "handoffs": record.handoffs.iter().map(|handoff| {
            let h = handoff.fields();
            json!({
                "handoff_id": h.handoff_id,
                "campaign_id": h.campaign_id,
                "builder_session_id": h.builder_session_id,
                "dm_session_id": h.dm_session_id,
                "package_lock_id": h.package_lock_id,
                "package_candidate_id": h.package_candidate_id,
                "plan_id": h.plan_id,
                "plan_revision": h.plan_revision,
                "plan_content_sha256": h.plan_content_sha256,
                "expected_revision": h.expected_revision,
                "resulting_campaign_revision": h.resulting_campaign_revision,
                "chronology_ordinal": h.chronology_ordinal,
                "completed_turn_id": h.completed_turn_id,
                "model_guidance": Json::Object(model_guidance_payload(&h.model_guidance)),
                "payload_sha256": h.payload_sha256,
            })
        }).collect::<Vec<_>>(),
        "interview_name": record.interview_name,
    })
}

fn entry_json(entry: &sylvia_store::authority::CampaignIndexEntry) -> Json {
    json!({
        "campaign_id": entry.campaign_id,
        "display_name": entry.display_name,
        "lifecycle": entry.lifecycle,
        "writer": entry.writer,
        "revision": entry.revision,
        "ruleset_summary": entry.ruleset_summary,
        "package_summary": entry.package_summary,
        "package_count": entry.package_count,
        "playable": entry.playable,
        "availability": entry.availability,
        "ruleset_id": entry.ruleset_id,
        "ruleset_label": entry.ruleset_label,
        "campaign_setting_id": entry.campaign_setting_id,
        "campaign_setting_label": entry.campaign_setting_label,
        "session_activity_ordinal": entry.session_activity_ordinal,
        "turn_id": entry.turn_id,
        "turn_status": entry.turn_status,
        "session_id": entry.session_id,
        "session_status": entry.session_status,
        "provider_id": entry.provider_id,
        "model_id": entry.model_id,
        "sessions": entry.sessions.iter().map(|session| json!({
            "session_id": session.session_id,
            "role": session.role,
            "status": session.status,
            "provider_id": session.provider_id,
            "model_id": session.model_id,
            "selected_skill": session.selected_skill,
            "model_guidance": session.model_guidance.as_ref().map(|guidance| Json::Object(model_guidance_payload(guidance))),
        })).collect::<Vec<_>>(),
    })
}

fn open_seed(name: &str) -> (AuthorityStore, std::path::PathBuf) {
    let folder = common::work_folder(name);
    let database = folder.join("authority.sqlite3");
    let seed = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/campaigns/seed.sqlite3.gz");
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(seed).unwrap()).read_to_end(&mut bytes).unwrap();
    std::fs::write(&database, bytes).unwrap();
    (AuthorityStore::open_for_application_owner(&database, None).unwrap(), database)
}

#[test]
fn campaign_index_entries_match_the_python_owner() {
    let expected = common::read_json("tests/vectors/campaigns/campaign_index.json");
    let (mut store, _) = open_seed("campaign-index-entries");
    let entries = store.campaign_index_entries().unwrap();
    store.close();
    let want = expected["campaign_index_entries"].as_array().unwrap();
    assert_eq!(entries.len(), want.len(), "entry count");
    for (got, want) in entries.iter().map(entry_json).zip(want) {
        // Every key, in Python's declaration order.
        let got_keys: Vec<&String> = got.as_object().unwrap().keys().collect();
        let want_keys: Vec<&String> = want.as_object().unwrap().keys().collect();
        assert_eq!(got_keys, want_keys, "field order");
        assert_eq!(&got, want, "entry {}", want["campaign_id"]);
    }
    assert!(store.campaign_index_entries().is_err(), "a closed owner refuses the read");
}

#[test]
fn campaign_index_matches_python() {
    let expected = common::read_json("tests/vectors/campaigns/campaign_index.json");
    let folder = common::work_folder("campaign-index");
    let database = folder.join("authority.sqlite3");
    let seed = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/campaigns/seed.sqlite3.gz");
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(seed).unwrap()).read_to_end(&mut bytes).unwrap();
    std::fs::write(&database, bytes).unwrap();
    let mut store = AuthorityStore::open_for_application_owner(&database, None).unwrap();
    let records = store.campaign_index().unwrap();
    store.close();
    let got: Vec<Json> = records.iter().map(record_json).collect();
    let want = expected["campaign_index"].as_array().unwrap();
    assert_eq!(got.len(), want.len(), "campaign count");
    for (got, want) in got.iter().zip(want) {
        assert_eq!(got, want, "campaign {}", want["campaign"]["campaign_id"]);
    }
}
