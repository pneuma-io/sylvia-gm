//! Compare `select_campaign_memory` with the frozen Python reference, ranked by lane 3's port of
//! `application/knowledge_search.rank_references` (`sylvia_owner::preflight::rank_references`).
//!
//! Regenerate with `tests/vectors/generate_campaign_memory_vectors.py`.

use serde_json::Value as J;
use sylvia_agent::campaign_memory::select_with;
use sylvia_owner::preflight::rank_references;

#[test]
fn memory_selection_matches_python() {
    let vectors: J = serde_json::from_str(include_str!("vectors/campaign_memory_vectors.json")).unwrap();
    for case in vectors["cases"].as_array().unwrap() {
        let facts: Vec<_> =
            case["facts"].as_array().unwrap().iter().map(|fact| fact.as_object().unwrap().clone()).collect();
        let queries: Vec<String> = case["queries"]
            .as_array()
            .unwrap()
            .iter()
            .map(|query| query.as_str().unwrap().to_owned())
            .collect();
        let (selected, summary) =
            select_with(&facts, &queries, &rank_references).expect("the ranking succeeds");
        let selected = J::Array(selected.into_iter().map(J::Object).collect());
        assert_eq!(
            serde_json::to_string(&selected).unwrap(),
            serde_json::to_string(&case["selected"]).unwrap(),
            "{} {:?}",
            case["name"],
            queries
        );
        assert_eq!(
            serde_json::to_string(&J::Object(summary)).unwrap(),
            serde_json::to_string(&case["summary"]).unwrap()
        );
    }
}
