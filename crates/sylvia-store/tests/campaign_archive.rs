//! The frozen portable archive ABI against Python (`tests/vectors/campaign_archive`, written by
//! `generate_campaign_archive_vectors.py`): the portable tables, every source schema claim, the
//! expected lineages, and `validate_migration_provenance` on valid and damaged provenance.

mod common;

use serde_json::json;
use sylvia_base::Json;
use sylvia_store::campaign_archive::{
    self, EXPECTED_SCHEMA_EXTENSION_PROVENANCE, PURGE_SCHEMA_EXTENSION_PROVENANCE, SCHEMA_EXTENSION_FIELDS,
    TABLES,
};
use sylvia_store::StoreError;

fn record() -> Json {
    common::read_json("tests/vectors/campaign_archive/archive_abi.json")
}

fn extension(row: &(&str, &str, &str, &str)) -> Json {
    let values = [row.0, row.1, row.2, row.3];
    Json::Object(
        SCHEMA_EXTENSION_FIELDS
            .iter()
            .zip(values)
            .map(|(key, value)| ((*key).to_owned(), json!(value)))
            .collect(),
    )
}

#[test]
fn tables_claims_and_lineages_are_pythons() {
    let record = record();
    let tables: Vec<Json> =
        TABLES.iter().map(|table| json!([table.name, table.columns, table.keys])).collect();
    assert_eq!(Json::Array(tables), record["tables"]);
    for version in campaign_archive::SOURCE_AUTHORITY_SCHEMA_VERSIONS {
        assert_eq!(
            json!(campaign_archive::source_authority_schema_claim_sha256(version)),
            record["schema_claims"][version.to_string()],
            "schema claim {version}"
        );
        let lineage: Vec<Json> = campaign_archive::expected_migrations(version)
            .iter()
            .map(|(number, name, sha256)| json!({"version": number, "applied_name": name, "sql_sha256": sha256}))
            .collect();
        assert_eq!(
            Json::Array(lineage),
            record["migration_lineages"][version.to_string()],
            "lineage {version}"
        );
    }
    let extensions: Vec<Json> = EXPECTED_SCHEMA_EXTENSION_PROVENANCE.iter().map(extension).collect();
    assert_eq!(Json::Array(extensions), record["extension_lineage"]);
    assert_eq!(extension(&PURGE_SCHEMA_EXTENSION_PROVENANCE), record["purge_extension"]);
}

#[test]
fn migration_provenance_is_validated_as_python_validates_it() {
    let mut failures = Vec::new();
    for case in record()["provenance_cases"].as_array().unwrap() {
        let enforce = case["enforce"].as_bool().unwrap();
        let label = format!("{} (enforce {enforce})", case["name"]);
        let got = match campaign_archive::validate_migration_provenance(
            &case["value"],
            enforce,
            &case["version"],
        ) {
            Ok(()) => json!({"ok": null}),
            Err(StoreError::Other(_)) => json!({"error": "TypeError", "code": null}),
            Err(error) => match error.rejection() {
                Some(coded) => json!({"error": coded.error_type.unwrap_or_default(), "code": coded.code}),
                None => json!({"error": format!("{error:?}")}),
            },
        };
        if got != case["outcome"] {
            failures.push(format!("{label}: Rust {got}, Python {}", case["outcome"]));
        }
    }
    assert!(failures.is_empty(), "{} cases differ:\n{}", failures.len(), failures.join("\n"));
}
