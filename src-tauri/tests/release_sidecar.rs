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
        "sylvia-gm-owner-release-{}-{nonce}",
        std::process::id()
    ))
}

#[test]
#[ignore = "release gate: build sidecar and set SYLVIA_GM_TEST_OWNER_EXECUTABLE"]
fn standalone_owner_executable_handshakes_and_reopens_without_python_command() {
    let executable = std::env::var_os("SYLVIA_GM_TEST_OWNER_EXECUTABLE")
        .map(PathBuf::from)
        .expect("SYLVIA_GM_TEST_OWNER_EXECUTABLE must name the built standalone sidecar");
    assert!(
        executable.is_file(),
        "standalone owner executable is missing"
    );

    let root = test_directory();
    std::fs::create_dir_all(&root).expect("create test directory");
    let database = root.join("authority.sqlite3");

    {
        let bridge = OwnerBridge::spawn_packaged(&executable, &database)
            .expect("Rust host launches verified standalone owner");
        let confirmed = bridge
            .request(
                "confirm_new_campaign",
                json!({
                    "operation_id": "confirm-release-sidecar",
                    "display_name": "Release Sidecar",
                    "turn_id": "turn-release-sidecar",
                    "candidate_id": null,
                    "provider_id": "test.release-sidecar",
                    "model_id": "test.release-sidecar/model0"
                }),
            )
            .expect("standalone owner confirms canonical campaign");
        let campaign_id = confirmed["campaign_id"]
            .as_str()
            .expect("owner-assigned campaign identity");
        bridge
            .request(
                "begin_campaign_construction",
                json!({
                    "operation_id": "begin-release-sidecar",
                    "campaign_id": campaign_id,
                    "expected_revision": confirmed["resulting_revision"],
                    "lane": "quick_start",
                    "canonical_input": {"test_fixture": "release-sidecar"}
                }),
            )
            .expect("standalone owner begins campaign construction");
        let plan_path = root
            .join("campaigns")
            .join(campaign_id)
            .join("campaign-plan.md");
        std::fs::create_dir_all(plan_path.parent().expect("campaign plan parent"))
            .expect("create campaign plan directory");
        std::fs::write(&plan_path, "# Release Sidecar Plan\n")
            .expect("write Builder campaign-plan fixture");
        let finalized = bridge
            .request(
                "finalize_campaign_construction",
                json!({
                    "campaign_id": campaign_id,
                    "dm_provider_id": "test.release-sidecar",
                    "dm_model_id": "test.release-sidecar/model0"
                }),
            )
            .expect("standalone owner finalizes canonical campaign");
        assert_eq!(finalized["handoff"]["campaign_id"], campaign_id);
        bridge.shutdown().expect("clean standalone owner shutdown");
    }

    {
        let reopened = OwnerBridge::spawn_packaged(&executable, &database)
            .expect("Rust host reopens verified standalone owner");
        let index = reopened
            .request("campaign_index", json!({}))
            .expect("standalone owner reconstructs campaign index");
        assert_eq!(index[0]["display_name"], "Release Sidecar");
        assert_eq!(index[0]["playable"], true);
        assert_eq!(index[0]["provider_id"], "test.release-sidecar");
        assert_eq!(index[0]["model_id"], "test.release-sidecar/model0");
        reopened
            .shutdown()
            .expect("clean reopened standalone owner shutdown");
    }

    std::fs::remove_dir_all(&root).expect("remove release-sidecar test directory");
}
