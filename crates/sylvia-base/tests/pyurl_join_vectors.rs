//! `urlparse`, `urlunparse`, `urljoin` and `quote(..., "iso-8859-1")` against vectors from the
//! Python reference (`vectors/generate_pyurl_join_vectors.py`, CPython 3.12.11).

use serde_json::{json, Value};
use sylvia_base::pyurl::{quote_latin1, urljoin, urlparse, urlunparse, ParsedUrl, PUNCTUATION};

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/pyurl_join_vectors.json")).expect("vector file is valid JSON")
}

fn parts(parsed: &ParsedUrl) -> Value {
    json!([parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment])
}

fn outcome<T>(
    result: Result<T, sylvia_base::pyurl::UrlError>,
    ok: impl FnOnce(T) -> Value,
    error_type: &str,
) -> Value {
    match result {
        Ok(value) => json!({"ok": ok(value)}),
        Err(error) => json!({"error": [error_type, error.0]}),
    }
}

fn without_input(case: &Value, keys: &[&str]) -> Value {
    let mut case = case.clone();
    for key in keys {
        case.as_object_mut().unwrap().remove(*key);
    }
    case
}

#[test]
fn punctuation_is_pythons() {
    assert_eq!(vectors()["punctuation"], PUNCTUATION);
}

#[test]
fn urlparse_urlunparse_and_urljoin_match_python() {
    let vectors = vectors();
    let mut failures = Vec::new();
    for case in vectors["urlparse"].as_array().unwrap() {
        let (url, scheme) = (case["url"].as_str().unwrap(), case["scheme"].as_str().unwrap());
        let got = outcome(urlparse(url, scheme), |p| parts(&p), "ValueError");
        if got != without_input(case, &["url", "scheme"]) {
            failures.push(format!("urlparse({url:?}, {scheme:?}): {got}, expected {case}"));
        }
    }
    for case in vectors["urlunparse"].as_array().unwrap() {
        let p = case["parts"].as_array().unwrap();
        let text = |i: usize| p[i].as_str().unwrap().to_string();
        let parsed = ParsedUrl {
            scheme: text(0),
            netloc: text(1),
            path: text(2),
            params: text(3),
            query: text(4),
            fragment: text(5),
        };
        if urlunparse(&parsed) != case["url"].as_str().unwrap() {
            failures.push(format!("urlunparse({p:?}): {:?}, expected {}", urlunparse(&parsed), case["url"]));
        }
    }
    for case in vectors["urljoin"].as_array().unwrap() {
        let (base, url) = (case["base"].as_str().unwrap(), case["url"].as_str().unwrap());
        let got = outcome(urljoin(base, url), Value::String, "ValueError");
        if got != without_input(case, &["base", "url"]) {
            failures.push(format!("urljoin({base:?}, {url:?}): {got}, expected {case}"));
        }
    }
    assert!(failures.is_empty(), "{} differ:\n{}", failures.len(), failures.join("\n"));
}

#[test]
fn quote_latin1_matches_python() {
    let mut failures = Vec::new();
    for case in vectors()["quote_latin1"].as_array().unwrap() {
        let text = case["text"].as_str().unwrap();
        let got = outcome(quote_latin1(text, PUNCTUATION), Value::String, "UnicodeEncodeError");
        if got != without_input(case, &["text"]) {
            failures.push(format!("quote({text:?}): {got}, expected {case}"));
        }
    }
    assert!(failures.is_empty(), "{} differ:\n{}", failures.len(), failures.join("\n"));
}
