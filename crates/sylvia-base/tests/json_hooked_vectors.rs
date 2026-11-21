//! `sylvia_base::pyjson::loads_hooked` against CPython's `json.loads` with
//! `object_pairs_hook=<refuse duplicates>` and `parse_constant=<refuse NaN/Infinity>`, under all
//! four combinations of the two hooks (`tests/vectors/generate_json_hooked_vectors.py`).
//!
//! The first failure must be the same, in CPython's scan order: the `JSONDecodeError` text, line
//! and column; the refused literal; or the duplicate key. A successful read must give the same
//! value (non-finite numbers as `null`) and the same `non_finite`. Where Python raises anything
//! else (a `RecursionError`), Rust must refuse. Where Python reads what Rust cannot hold, only a
//! declared-limit refusal is allowed.

use serde_json::Value;
use sylvia_base::pyjson::{self, Dump, HookFailure, HookPolicy};

#[test]
fn hooked_reads_match_python() {
    let record: Value = serde_json::from_str(include_str!("vectors/json_hooked_vectors.json")).unwrap();
    let cases = record["cases"].as_array().unwrap();
    assert!(cases.len() > 700, "expected the full corpus, got {}", cases.len());
    let mut failures = Vec::new();
    let mut checked = 0;
    for case in cases {
        let text = case["text"].as_str().unwrap();
        for (key, want) in case["outcomes"].as_object().unwrap() {
            let policy =
                HookPolicy { refuse_duplicates: &key[..1] == "1", refuse_constants: &key[1..] == "1" };
            let got = pyjson::loads_hooked(text, policy);
            checked += 1;
            let agrees = match (&got, want) {
                (Err(HookFailure::Decode(error)), want) if want.get("decode").is_some() => {
                    want["decode"].as_str() == Some(error.to_string().as_str())
                        && want["lineno"].as_u64() == Some(error.lineno as u64)
                        && want["colno"].as_u64() == Some(error.colno as u64)
                }
                (Err(HookFailure::Constant(literal)), want) if want.get("constant").is_some() => {
                    want["constant"].as_str() == Some(literal)
                }
                (Err(HookFailure::Duplicate(found)), want) if want.get("duplicate").is_some() => {
                    let encoded = want["duplicate"].as_str().unwrap_or_default();
                    // A key holding a lone surrogate cannot be a Rust string (declared row 3).
                    encoded == pyjson::dumps(&Value::String(found.clone()), Dump::DEFAULT)
                        || (found.contains('\u{fffd}')
                            && (encoded.contains("\\ud8") || encoded.contains("\\udc")))
                }
                (Err(_), want) if want.get("other").is_some() => true,
                (Ok(hooked), want) if want.get("ok").is_some() => {
                    want["ok"].as_str()
                        == Some(pyjson::dumps(&hooked.value, Dump::DEFAULT.sorted(true)).as_str())
                        && want["non_finite"].as_bool() == Some(hooked.non_finite)
                }
                // Python reads it; Rust may only refuse it for a declared limit.
                (Err(HookFailure::Unsupported(_)), want) => want.get("ok").is_some(),
                _ => false,
            };
            if !agrees {
                failures.push(format!("{text:?} [{key}]: python {want}, rust {got:?}"));
            }
        }
    }
    assert!(failures.is_empty(), "{} of {checked} reads differ:\n{}", failures.len(), failures.join("\n"));
}
