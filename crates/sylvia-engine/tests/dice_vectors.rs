//! Compare sylvia_engine's dice, dice expressions and RNG queue with the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_dice_vectors.py` (needs the snapshot's Python).

use serde_json::{json, Value};
use std::cell::RefCell;
use std::rc::Rc;
use std::sync::OnceLock;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::testing::{assert_same_json, json_difference};
use sylvia_engine::dice::{Die, RollResult, RollSource};
use sylvia_engine::dice_expr::{parse_dice_expr, roll_initiative_expr};
use sylvia_engine::rng_queue::RngQueue;
use sylvia_engine::{Dice, PyError, PyRandom, PyResult};

fn vectors() -> &'static Value {
    static VECTORS: OnceLock<Value> = OnceLock::new();
    VECTORS
        .get_or_init(|| serde_json::from_str(include_str!("vectors/dice_vectors.json")).expect("valid JSON"))
}

fn int(value: &Value) -> i64 {
    value.as_i64().unwrap_or_else(|| panic!("integer expected, got {value}"))
}

fn opt_int(value: &Value) -> Option<i64> {
    (!value.is_null()).then(|| int(value))
}

/// Python's integer text as a compared value: beyond `i64` it is the bound on its side.
fn bound(text: &Value) -> i64 {
    let text = text.as_str().unwrap();
    text.parse().unwrap_or(if text.starts_with('-') { i64::MIN } else { i64::MAX })
}

fn error_value(error: &PyError) -> Value {
    json!({"error": error.kind.name(), "message": error.message})
}

fn dict_outcome(result: PyResult<Value>) -> Value {
    match result {
        Ok(payload) => json!({
            "ok": payload,
            "json": pyjson::dumps(&payload, Dump::DEFAULT),
            "canonical": String::from_utf8(pyjson::canonical_bytes(&payload)).unwrap(),
        }),
        Err(error) => error_value(&error),
    }
}

fn die(sides: i64) -> Die {
    match sides {
        4 => Die::D4,
        6 => Die::D6,
        8 => Die::D8,
        10 => Die::D10,
        12 => Die::D12,
        20 => Die::D20,
        100 => Die::D100,
        other => panic!("no die with {other} sides"),
    }
}

fn run(dice: &mut Dice, call: &Value) -> Value {
    let i = |key: &str| int(&call[key]);
    match call["fn"].as_str().unwrap() {
        "roll" => dict_outcome(dice.roll(i("sides"), i("count"), i("modifier")).map(|r| r.to_dict())),
        "die" => dict_outcome(dice.die(die(i("sides")), i("modifier")).map(|r| r.to_dict())),
        "ndn" => dict_outcome(dice.ndn(i("n"), i("sides"), i("modifier")).map(|r| r.to_dict())),
        "advantage" => dict_outcome(dice.advantage(i("modifier")).map(|r| r.to_dict())),
        "disadvantage" => dict_outcome(dice.disadvantage(i("modifier")).map(|r| r.to_dict())),
        "check" => dict_outcome(
            dice.check(
                i("dc"),
                i("modifier"),
                call["advantage"].as_bool().unwrap(),
                call["disadvantage"].as_bool().unwrap(),
            )
            .map(|r| r.to_dict()),
        ),
        "explode" => dict_outcome(
            dice.explode(
                i("sides"),
                i("count"),
                i("modifier"),
                opt_int(&call["explode_on"]),
                i("max_explosions"),
            )
            .map(|r| r.to_dict()),
        ),
        "count_successes" => dict_outcome(
            dice.count_successes(i("sides"), i("count"), i("threshold"), i("modifier")).map(|r| r.to_dict()),
        ),
        "reroll" => {
            let prior = &call["previous"];
            let previous = RollResult {
                sides: int(&prior["sides"]),
                count: int(&prior["count"]),
                results: prior["results"].as_array().unwrap().iter().map(int).collect(),
                total: int(&prior["total"]),
                modifier: int(&prior["modifier"]),
            };
            dict_outcome(
                dice.reroll(
                    &previous,
                    call["predicate"].as_str().unwrap(),
                    i("modifier"),
                    opt_int(&call["threshold"]),
                )
                .map(|r| r.to_dict()),
            )
        }
        "weighted_pick" => {
            let roll = opt_int(&call["roll_total"]).map(|total| RollResult {
                sides: 100,
                count: 1,
                results: vec![total],
                total,
                modifier: 0,
            });
            let items = call["items"].as_array().unwrap();
            match dice.weighted_pick(items, call["field"].as_str().unwrap(), roll.as_ref()) {
                Ok(index) => json!({"ok": index}),
                Err(error) => error_value(&error),
            }
        }
        "hunger_roll" => dict_outcome(
            dice.hunger_roll(i("pool"), i("hunger"), i("sides"), i("threshold"), i("modifier"))
                .map(|r| r.to_dict()),
        ),
        other => panic!("unknown call {other}"),
    }
}

#[test]
fn every_call_matches_python_under_every_seed() {
    let vectors = vectors();
    let script = vectors["script"].as_array().unwrap();
    let runs = vectors["runs"].as_array().unwrap();
    assert!(script.len() > 900 && runs.len() >= 12, "expected the full corpus");
    let mut failures = Vec::new();
    for case in runs {
        let seed: i128 = case["seed"].as_str().unwrap().parse().unwrap();
        let results = case["results"].as_array().unwrap();
        assert_eq!(results.len(), script.len());
        let mut dice = Dice::with_default(PyRandom::from_u64(0));
        dice.seeded_rolls(Some(seed), |dice| {
            for (index, (call, expected)) in script.iter().zip(results).enumerate() {
                let actual = run(dice, call);
                if json_difference(&actual, expected).is_some() {
                    failures
                        .push(format!("seed {seed} call {index} {call}: python {expected}, rust {actual}"));
                    break;
                }
            }
        });
    }
    assert!(
        failures.is_empty(),
        "{} seeds differ, first: {:?}",
        failures.len(),
        &failures[..failures.len().min(3)]
    );
}

/// The scripted source of the vectors: faces from a list, every call logged.
struct Scripted {
    faces: Vec<i128>,
    position: usize,
    calls: Rc<RefCell<Vec<Value>>>,
}

impl RollSource for Scripted {
    fn randint(&mut self, a: i128, b: i128) -> PyResult<i128> {
        self.calls.borrow_mut().push(json!([a as i64, b as i64]));
        let face = self.faces[self.position % self.faces.len()];
        self.position += 1;
        Ok(a + (face - 1).rem_euclid(b - a + 1))
    }
}

#[test]
fn context_streams_behave_like_python_context_variables() {
    let expected = &vectors()["context"];
    let mut dice = Dice::with_default(PyRandom::from_u64(0));
    let d20 = |dice: &mut Dice| dice.die(Die::D20, 0).unwrap().total;

    let mut rolls = Vec::new();
    dice.seeded_rolls(Some(11), |dice| {
        rolls.push(d20(dice));
        dice.seeded_rolls(Some(22), |dice| {
            rolls.push(d20(dice));
            dice.seeded_rolls(None, |dice| rolls.push(d20(dice)));
        });
        rolls.push(d20(dice));
        dice.set_seed(33);
        rolls.push(d20(dice));
    });
    assert_same_json(&json!(rolls), &expected["nested"], "nested");

    dice.set_seed(5);
    let mut default = vec![d20(&mut dice)];
    dice.seeded_rolls(Some(6), |dice| default.push(d20(dice)));
    default.push(d20(&mut dice));
    assert_same_json(
        &json!({"rolls": default, "seed": dice.get_seed().map(|s| s as i64)}),
        &expected["default_after_tool"],
        "default_after_tool",
    );

    let scripted = &expected["scripted"];
    let calls = Rc::new(RefCell::new(Vec::new()));
    let faces = scripted["faces"].as_array().unwrap().iter().map(|f| i128::from(int(f))).collect();
    let source = Box::new(Scripted { faces, position: 0, calls: Rc::clone(&calls) });
    let (results, _) = dice.use_roll_source(source, |dice| {
        let mut out = vec![json!(d20(dice))];
        dice.seeded_rolls(Some(99), |dice| {
            out.push(dice.roll(6, 3, 0).unwrap().to_dict());
            out.push(dice.advantage(0).unwrap().to_dict());
        });
        let items = [json!({"weight": 2}), json!({"weight": 5})];
        // Python returns the picked row itself; Rust returns its index.
        out.push(items[dice.weighted_pick(&items, "weight", None).unwrap().unwrap()].clone());
        out
    });
    assert_same_json(&json!(results), &scripted["results"], "scripted results");
    assert_same_json(&json!(*calls.borrow()), &scripted["calls"], "scripted calls");
}

#[test]
fn rng_queue_matches_python() {
    let vectors = vectors();
    for case in vectors["queues"].as_array().unwrap() {
        let seed = int(&case["seed"]);
        let mut queue = RngQueue::new(int(&case["size"]), Some(seed)).unwrap();
        assert_same_json(&queue.to_dict(), &case["first"], format!("seed {seed}"));
        assert_eq!(RngQueue::from_dict(&queue.to_dict()).as_ref(), Some(&queue));
        let consumed: Vec<Value> = (0..=queue.size).map(|_| queue.consume().to_dict()).collect();
        assert_same_json(&json!(consumed), &case["consumed"], "consumed");
        assert_eq!(queue.to_context_block(), case["block"].as_str().unwrap());
        assert_eq!(queue.remaining(), int(&case["remaining"]));
        queue.regenerate(None);
        assert_same_json(&queue.to_dict(), &case["again"], "again");
        queue.regenerate(Some(seed + 1));
        assert_eq!(queue.to_dict(), case["reseeded"]);
    }
    for case in vectors["queue_errors"].as_array().unwrap() {
        let error = RngQueue::new(int(&case["size"]), Some(1)).unwrap_err();
        assert_eq!(error.message, case["message"].as_str().unwrap());
    }
}

/// The fixed refusal for an integer beyond `i64` (differences.md row 5).
fn refusal() -> Value {
    json!({"error": "ValueError", "message": sylvia_base::pystr::INT_RANGE_MESSAGE})
}

/// Python's decimal text of an integer, or `None` when it lies beyond `i64`.
fn exact(text: &Value) -> Option<i64> {
    text.as_str().unwrap().parse().ok()
}

#[test]
fn parse_dice_expr_matches_python() {
    let mut failures = Vec::new();
    let mut refused = 0;
    let cases = vectors()["parse_dice_expr"].as_array().unwrap();
    assert!(cases.len() > 3000);
    for case in cases {
        let text = case["text"].as_str().unwrap();
        let parts = parse_dice_expr(text);
        let python = &case["parts"];
        // Counts and sides are only compared with limits: beyond i64 they are the bound.
        let counts = (bound(&python["count"]), bound(&python["sides"]));
        let modifier = match exact(&python["modifier_delta"]) {
            Some(value) => parts.modifier_delta() == Ok(value),
            None => {
                refused += 1;
                parts.modifier_delta().map_err(|e| error_value(&e)) == Err(refusal())
            }
        };
        if (parts.count, parts.sides) != counts || !modifier {
            failures.push(format!("{text:?}: python {python}, rust {parts:?}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{} differ, first: {:?}",
        failures.len(),
        &failures[..failures.len().min(5)]
    );
    assert!(refused >= 4, "expected modifiers beyond i64 in the corpus, got {refused}");
}

#[test]
fn roll_initiative_expr_matches_python() {
    let mut failures = Vec::new();
    let mut refused = 0;
    for case in vectors()["initiative"].as_array().unwrap() {
        let text = case["text"].as_str().unwrap();
        let seed: i128 = case["seed"].as_str().unwrap().parse().unwrap();
        let mut dice = Dice::with_default(PyRandom::from_u64(0));
        let actual = dice.seeded_rolls(Some(seed), |dice| roll_initiative_expr(dice, text));
        let expected = &case["result"];
        // Python's total or stored modifier beyond i64 is the declared refusal.
        let beyond = expected.get("total").is_some_and(|total| exact(total).is_none())
            || expected["roll"].get("modifier").is_some_and(|modifier| !modifier.is_i64());
        let same = if beyond {
            refused += 1;
            actual.as_ref().map_err(error_value).err() == Some(refusal())
        } else {
            match &actual {
                Ok((total, roll)) => {
                    expected.get("total").and_then(exact) == Some(*total)
                        && expected["roll"] == roll.clone().unwrap_or(Value::Null)
                }
                Err(error) => *expected == error_value(error),
            }
        };
        if !same {
            failures.push(format!("{text:?}: python {expected}, rust {actual:?}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{} differ, first: {:?}",
        failures.len(),
        &failures[..failures.len().min(5)]
    );
    assert!(refused >= 5, "expected totals or modifiers beyond i64, got {refused}");
}

#[test]
fn integer_edges_match_python_or_refuse() {
    let edges = vectors()["edges"].as_array().unwrap();
    assert!(edges.len() > 80);
    let mut failures = Vec::new();
    let mut refused = 0;
    for (index, case) in edges.iter().enumerate() {
        let call = &case["call"];
        let mut dice = Dice::with_default(PyRandom::from_u64(0));
        let actual = dice.seeded_rolls(Some(7000 + index as i128), |dice| run(dice, call));
        let expected = if case["refuse"].as_bool().unwrap() {
            refused += 1;
            refusal()
        } else {
            case["result"].clone()
        };
        if actual != expected {
            failures.push(format!("{call}: expected {expected}, rust {actual}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{} differ, first: {:?}",
        failures.len(),
        &failures[..failures.len().min(5)]
    );
    assert!(refused >= 20, "expected declared refusals among the edges, got {refused}");
}

#[test]
fn lower_matches_python() {
    use sylvia_engine::dice_expr::lower;
    let cases = &vectors()["lower"];
    let mut failures = Vec::new();
    for pair in cases["chars"].as_array().unwrap() {
        let c = char::from_u32(u32::try_from(int(&pair[0])).unwrap()).unwrap();
        let actual = lower(&c.to_string());
        if actual != pair[1].as_str().unwrap() {
            failures.push(format!("U+{:04X}: python {:?}, rust {actual:?}", u32::from(c), pair[1]));
        }
    }
    for pair in cases["texts"].as_array().unwrap() {
        let actual = lower(pair[0].as_str().unwrap());
        if actual != pair[1].as_str().unwrap() {
            failures.push(format!("{:?}: python {:?}, rust {actual:?}", pair[0], pair[1]));
        }
    }
    assert!(cases["chars"].as_array().unwrap().len() > 1400);
    assert!(
        failures.is_empty(),
        "{} differ, first: {:?}",
        failures.len(),
        &failures[..failures.len().min(8)]
    );
}
