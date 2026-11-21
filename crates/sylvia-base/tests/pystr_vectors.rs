//! Compare sylvia_base::pystr with vectors written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_pystr_vectors.py` (needs the snapshot's Python).

use serde_json::Value;
use sylvia_base::pystr;

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/pystr_vectors.json")).expect("vector file is valid JSON")
}

#[test]
fn isspace_accepts_exactly_python_whitespace() {
    let vectors = vectors();
    assert_eq!(vectors["unicode"], "15.0.0");
    let expected: Vec<u32> =
        vectors["whitespace"].as_array().unwrap().iter().map(|c| c.as_u64().unwrap() as u32).collect();
    let actual: Vec<u32> = (0..=0x10ffff_u32)
        .filter_map(char::from_u32)
        .filter(|c| pystr::is_space(*c))
        .map(u32::from)
        .collect();
    assert_eq!(actual, expected);
}

#[test]
fn strip_and_isspace_match_python() {
    let vectors = vectors();
    let strings = vectors["strings"].as_array().unwrap();
    assert!(strings.len() > 2000);
    for case in strings {
        let text = case["text"].as_str().unwrap();
        assert_eq!(pystr::strip(text), case["strip"].as_str().unwrap(), "strip {text:?}");
        assert_eq!(pystr::lstrip(text), case["lstrip"].as_str().unwrap(), "lstrip {text:?}");
        assert_eq!(pystr::rstrip(text), case["rstrip"].as_str().unwrap(), "rstrip {text:?}");
        assert_eq!(pystr::isspace(text), case["isspace"].as_bool().unwrap(), "isspace {text:?}");
    }
}

#[test]
fn casefold_upper_and_len_match_python() {
    let vectors = vectors();
    let cases = vectors["cased"].as_array().unwrap();
    assert!(cases.len() > 1500);
    for case in cases {
        let text = case["text"].as_str().unwrap();
        assert_eq!(pystr::casefold(text), case["casefold"].as_str().unwrap(), "casefold {text:?}");
        assert_eq!(pystr::upper(text), case["upper"].as_str().unwrap(), "upper {text:?}");
        assert_eq!(pystr::len(text) as u64, case["len"].as_u64().unwrap(), "len {text:?}");
    }
}
