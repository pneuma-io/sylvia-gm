//! The migration SQL, digests and identities against `sql/schema_vectors.json`, which
//! `sql/generate_sql.py` wrote from the frozen Python modules.

mod common;

use sylvia_base::Json;
use sylvia_store::pytext::{casefold_to_ascii, PY_WHITESPACE};
use sylvia_store::schema::{self, identity_digest, Known};

fn vectors() -> Json {
    common::read_json("sql/schema_vectors.json")
}

#[test]
fn bundled_sqlite_is_the_python_reference_library() {
    // Python 3.12.11 in the reference environment ships SQLite 3.49.1. A dependency bump that
    // changes the bundled library fails here instead of silently changing stored schema text.
    assert_eq!(sylvia_store::sqlite_version(), "3.49.1");
    assert_eq!(vectors()["sqlite_version"], "3.49.1");
}

#[test]
fn migration_names_and_digests_match_python() {
    let vectors = vectors();
    let expected = vectors["migrations"].as_array().unwrap();
    assert_eq!(expected.len(), 12);
    for row in expected {
        let version = row[0].as_i64().unwrap();
        let (got_version, name, sha256) = schema::migration_row(version);
        assert_eq!(got_version, version);
        assert_eq!(name, row[1], "migration {version} name");
        assert_eq!(sha256, row[2], "migration {version} digest");
    }
    assert_eq!(schema::PREDECESSOR_MIGRATION_5_SHA256, vectors["predecessor_migration_5_sha256"]);
    let extensions = &vectors["extensions"];
    let digests = schema::digests();
    assert_eq!(extensions["campaign_identity"][0], schema::CAMPAIGN_IDENTITY_EXTENSION_NAME);
    assert_eq!(extensions["campaign_identity"][1], digests.campaign_identity_delta.as_str());
    assert_eq!(extensions["lifecycle"][0], schema::LIFECYCLE_EXTENSION_NAME);
    assert_eq!(extensions["lifecycle"][1], digests.lifecycle_extension.as_str());
    assert_eq!(extensions["recovery"][0], schema::RECOVERY_EXTENSION_NAME);
    assert_eq!(extensions["recovery"][1], digests.recovery_extension.as_str());
    assert_eq!(extensions["purge"][0], schema::PURGE_EXTENSION_NAME);
    assert_eq!(extensions["purge"][1], digests.purge_extension.as_str());
}

#[test]
fn expected_extension_rows_match_python() {
    let vectors = vectors();
    for recovery in [false, true] {
        for purge in [false, true] {
            let key = format!(
                "recovery={},purge={}",
                if recovery { "True" } else { "False" },
                if purge { "True" } else { "False" }
            );
            let expected: Vec<Vec<String>> =
                serde_json::from_value(vectors["extension_rows"][&key].clone()).unwrap();
            let got: Vec<Vec<String>> = schema::expected_campaign_extension_rows(recovery, purge)
                .into_iter()
                .map(|row| row.to_vec())
                .collect();
            assert_eq!(got, expected, "{key}");
        }
    }
}

#[test]
fn every_expected_schema_identity_matches_python() {
    let vectors = vectors();
    let expected = vectors["schema_identities"].as_object().unwrap();
    assert_eq!(expected.len(), Known::ALL.len());
    for known in Known::ALL {
        let entry = &expected[known.vector_key()];
        let (tables, sha256) = identity_digest(known.identity());
        assert_eq!(tables as u64, entry["tables"].as_u64().unwrap(), "{known:?} table count");
        assert_eq!(sha256, entry["sha256"].as_str().unwrap(), "{known:?} identity");
    }
}

#[test]
fn trigger_identity_matches_python() {
    let expected: Vec<(String, String)> =
        serde_json::from_value(vectors()["trigger_identity"].clone()).unwrap();
    assert_eq!(schema::expected_trigger_identity(), &expected);
}

#[test]
fn python_whitespace_and_casefold_tables_match_python() {
    let vectors = vectors();
    let whitespace: Vec<u32> = serde_json::from_value(vectors["python_whitespace"].clone()).unwrap();
    assert_eq!(PY_WHITESPACE.iter().map(|c| *c as u32).collect::<Vec<_>>(), whitespace);
    let table = vectors["casefold_to_ascii"].as_object().unwrap();
    let mut listed = 0;
    for code in 0x80..=0x10FFFF_u32 {
        let Some(c) = char::from_u32(code) else { continue };
        let expected = table.get(&format!("{code:04X}")).and_then(Json::as_str);
        assert_eq!(casefold_to_ascii(c), expected, "U+{code:04X}");
        listed += usize::from(expected.is_some());
    }
    assert_eq!(listed, table.len());
}
