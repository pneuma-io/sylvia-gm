//! Compare sylvia_engine::expr's tokenizer and parser with the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_expression_parse_vectors.py` (needs the snapshot's
//! Python). Each case is a text and Python's AST (or the SHA-256 of its canonical JSON, for the
//! two large groups) or Python's `ExpressionError` message.

use serde_json::{json, Value};
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, float_repr};
use sylvia_base::testing::json_difference;
use sylvia_engine::expr::{parse_expression, Literal, Node, UnaryOp};

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/expression_parse_vectors.json")).expect("valid JSON")
}

/// The generator's compact dump of an AST.
fn dump(node: &Node) -> Value {
    match node {
        Node::Literal(Literal::None) => json!(["n"]),
        Node::Literal(Literal::Bool(flag)) => json!(["b", flag]),
        Node::Literal(Literal::Int(literal)) => json!(["i", literal.repr()]),
        Node::Literal(Literal::Float(value)) => json!(["f", float_repr(*value)]),
        Node::Literal(Literal::Str(text)) => json!(["s", text]),
        Node::Field(segments) => {
            let mut out = vec![json!("F")];
            for (name, index) in segments {
                out.push(json!([name, index.as_ref().map_or(Value::Null, |node| dump(node))]));
            }
            Value::Array(out)
        }
        Node::Unary(op, operand) => {
            let symbol = match op {
                UnaryOp::Neg => "-",
                UnaryOp::Not => "not",
            };
            json!(["U", symbol, dump(operand)])
        }
        Node::Binary(op, left, right) => json!(["B", op.symbol(), dump(left), dump(right)]),
        Node::Call(name, args) => json!(["C", name, args.iter().map(dump).collect::<Vec<_>>()]),
    }
}

fn check_case(case: &Value) -> Option<String> {
    let text = case["text"].as_str().unwrap();
    let actual = parse_expression(text);
    let same = match (&actual, case.get("error")) {
        (Ok(node), None) => {
            let tree = dump(node);
            match case.get("ast_sha256").and_then(Value::as_str) {
                Some(digest) => sha256_hex(pyjson::canonical_bytes(&tree))[..16] == *digest,
                None => json_difference(&tree, &case["ast"]).is_none(),
            }
        }
        (Err(error), Some(expected)) => {
            // The generator writes `'<text>'` inside a message as `'\0'`.
            let expected = expected.as_str().unwrap().replace("'\\0'", &format!("'{text}'"));
            error.message == expected && error.class_name() == case["class"].as_str().unwrap()
        }
        _ => false,
    };
    (!same).then(|| format!("{text:?}: python {case}, rust {actual:?}"))
}

#[test]
fn every_group_parses_as_python_does() {
    let vectors = vectors();
    let groups = vectors["groups"].as_object().unwrap();
    for (name, minimum) in [("validated", 9000), ("ruleset_strings", 50000), ("edges", 120), ("soup", 5000)] {
        let cases = groups[name].as_array().unwrap();
        assert!(cases.len() >= minimum, "{name}: expected the full corpus, got {}", cases.len());
        let failures: Vec<String> = cases.iter().filter_map(check_case).collect();
        assert!(
            failures.is_empty(),
            "{name}: {} of {} differ, first: {:?}",
            failures.len(),
            cases.len(),
            &failures[..failures.len().min(5)]
        );
    }
}
