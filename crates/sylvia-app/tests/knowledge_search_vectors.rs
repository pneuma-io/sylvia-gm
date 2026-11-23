//! `sylvia_app::knowledge_search` against Python (`tests/vectors/knowledge_search_vectors.json`,
//! written by `generate_knowledge_search_vectors.py`): every word list, ranking and page must be
//! Python's, in Python's order, or Python's refusal.

use serde_json::json;
use std::path::Path;
use sylvia_app::knowledge_search::{
    bounded_search_page, knowledge_terms, rank_references, KnowledgeReference,
};
use sylvia_base::{Json, JsonMap};

fn record() -> Json {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/knowledge_search_vectors.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn maps(value: &Json) -> Vec<JsonMap> {
    value.as_array().unwrap().iter().map(|item| item.as_object().unwrap().clone()).collect()
}

#[test]
fn terms_rankings_and_pages_match_python() {
    let record = record();
    for case in record["terms"].as_array().unwrap() {
        let got: Vec<Json> =
            knowledge_terms(case["value"].as_str().unwrap()).into_iter().map(Json::from).collect();
        assert_eq!(Json::Array(got), case["terms"], "terms of {}", case["value"]);
    }
    let references: Vec<KnowledgeReference> = record["references"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| KnowledgeReference {
            result: item["result"].as_object().unwrap().clone(),
            identity: item["identity"].as_str().unwrap().into(),
            body: item["body"].as_str().unwrap().into(),
        })
        .collect();
    for case in record["rankings"].as_array().unwrap() {
        let got = rank_references(case["query"].as_str().unwrap(), &references).unwrap();
        let got = Json::Array(got.into_iter().map(Json::Object).collect());
        assert_eq!(got.to_string(), case["results"].to_string(), "ranking of {}", case["query"]);
    }
    assert_eq!(rank_references("harbour", &[]).unwrap(), Vec::<JsonMap>::new());
    let matches = maps(&record["page_matches"]);
    let mut metadata = JsonMap::new();
    metadata.insert("query".into(), "harbour".into());
    metadata.insert("total".into(), matches.len().into());
    for case in record["pages"].as_array().unwrap() {
        let got = match bounded_search_page(
            &metadata,
            &matches,
            case["offset"].as_i64().unwrap(),
            case["limit"].as_i64().unwrap(),
            case["maximum_bytes"].as_i64().unwrap(),
        ) {
            Ok(page) => json!({ "ok": page }),
            Err(error) => {
                let coded = error.rejection().unwrap();
                json!({"error": coded.error_type.unwrap_or("CommandRejected"), "code": coded.code, "message": coded.message})
            }
        };
        assert_eq!(got.to_string(), case["outcome"].to_string(), "page {case}");
    }
}
