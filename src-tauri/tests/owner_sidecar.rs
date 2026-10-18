use serde_json::json;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};
use sylvia_gm::OwnerBridge;

fn test_directory() -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_nanos();
    std::env::temp_dir().join(format!(
        "sylvia-gm-owner-rust-{}-{nonce}",
        std::process::id()
    ))
}

#[test]
fn rust_host_handshakes_and_reopens_authority_child() {
    let root = test_directory();
    std::fs::create_dir_all(&root).expect("create test directory");
    let database = root.join("authority.sqlite3");
    let python = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repository root")
        .join(".venv")
        .join("Scripts")
        .join("python.exe");

    {
        let bridge = OwnerBridge::spawn_python(&python, &database)
            .expect("Rust host launches verified Python owner");
        assert_eq!(
            bridge
                .request("campaign_index", json!({}))
                .expect("empty index"),
            json!([])
        );
        let confirmed = bridge
            .request(
                "confirm_new_campaign",
                json!({
                    "operation_id": "confirm-rust-sidecar",
                    "display_name": "Rust Sidecar",
                    "turn_id": "turn-rust-sidecar",
                    "candidate_id": null,
                    "provider_id": "test.rust-sidecar",
                    "model_id": "test.rust-sidecar/model0"
                }),
            )
            .expect("confirm canonical no-rules campaign");
        let campaign_id = confirmed["campaign_id"]
            .as_str()
            .expect("owner-assigned campaign identity");
        bridge
            .request(
                "begin_campaign_construction",
                json!({
                    "operation_id": "begin-rust-sidecar",
                    "campaign_id": campaign_id,
                    "expected_revision": confirmed["resulting_revision"],
                    "lane": "quick_start",
                    "canonical_input": {"test_fixture": "rust-sidecar"}
                }),
            )
            .expect("begin canonical campaign construction");
        let plan_path = root
            .join("campaigns")
            .join(campaign_id)
            .join("campaign-plan.md");
        std::fs::create_dir_all(plan_path.parent().expect("campaign plan parent"))
            .expect("create campaign plan directory");
        std::fs::write(&plan_path, "# Rust Sidecar Plan\n")
            .expect("write Builder campaign-plan fixture");
        let finalized = bridge
            .request(
                "finalize_campaign_construction",
                json!({
                    "campaign_id": campaign_id,
                    "dm_provider_id": "test.rust-sidecar",
                    "dm_model_id": "test.rust-sidecar/model0"
                }),
            )
            .expect("finalize canonical campaign construction");
        assert_eq!(finalized["handoff"]["campaign_id"], campaign_id);
        bridge.shutdown().expect("clean owner shutdown");
    }

    {
        let reopened = OwnerBridge::spawn_python(&python, &database)
            .expect("Rust host reopens verified Python owner");
        let index = reopened
            .request("campaign_index", json!({}))
            .expect("reopened campaign index");
        assert_eq!(index[0]["display_name"], "Rust Sidecar");
        assert_eq!(index[0]["playable"], true);
        assert_eq!(index[0]["provider_id"], "test.rust-sidecar");
        assert_eq!(index[0]["model_id"], "test.rust-sidecar/model0");
        reopened.shutdown().expect("clean reopened owner shutdown");
    }

    std::fs::remove_dir_all(&root).expect("remove test directory");
}
