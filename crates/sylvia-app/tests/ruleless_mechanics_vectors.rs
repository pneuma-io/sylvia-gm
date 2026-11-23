//! Compare the No Ruleset mechanics check on held replies with Python.
//!
//! Regenerate with `tests/vectors/generate_ruleless_mechanics_vectors.py`. Every finding and
//! correction was read back from `application/ruleless_play.py`.

use serde_json::Value as J;
use sylvia_app::ruleless_play::{mechanics_in_response, ruleless_mechanics_rejection};
use sylvia_base::JsonMap;

#[test]
fn mechanics_findings_and_corrections_match_python() {
    let vectors: J = serde_json::from_str(include_str!("vectors/ruleless_mechanics_vectors.json"))
        .expect("vector file is valid JSON");
    let cases = vectors["cases"].as_array().expect("cases");
    assert_eq!(cases.len(), 88);
    for (index, case) in cases.iter().enumerate() {
        let blocks: Option<Vec<JsonMap>> = case["blocks"]
            .as_array()
            .map(|blocks| blocks.iter().map(|block| block.as_object().cloned().expect("a block")).collect());
        let findings = mechanics_in_response(blocks.as_deref(), case["text"].as_str());
        assert_eq!(J::from(findings.clone()), case["findings"], "case {index}: {:?}", case["text"]);
        let rejection =
            if findings.is_empty() { J::Null } else { J::Object(ruleless_mechanics_rejection(&findings)) };
        assert_eq!(
            serde_json::to_string(&rejection).expect("serialisable"),
            serde_json::to_string(&case["rejection"]).expect("serialisable"),
            "case {index}"
        );
    }
}
