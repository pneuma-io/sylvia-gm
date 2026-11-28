//! Compare sylvia_engine::pyrandom with draw sequences written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_pyrandom_vectors.py` (needs the snapshot's Python).

use serde_json::Value;
use sylvia_base::pyjson;
use sylvia_base::testing::json_difference;
use sylvia_engine::pyrandom::{seed_from_basis, PyRandom};
use sylvia_engine::PyError;

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/pyrandom_vectors.json")).expect("valid JSON")
}

fn int(value: &Value) -> i128 {
    value.as_str().expect("integer as decimal string").parse().expect("fits in i128")
}

fn hex_limbs(hex: &str) -> Vec<u32> {
    let digits = hex.trim_start_matches('-');
    let bytes: Vec<u8> = digits.bytes().rev().collect();
    bytes
        .chunks(8)
        .map(|chunk| {
            let text: String = chunk.iter().rev().map(|b| *b as char).collect();
            u32::from_str_radix(&text, 16).expect("hex limb")
        })
        .collect()
}

fn error_value(error: &PyError) -> Value {
    serde_json::json!({"error": error.kind.name(), "message": error.message})
}

fn run(rng: &mut PyRandom, op: &Value) -> Value {
    let text = |result: Result<i128, PyError>| match result {
        Ok(value) => Value::String(value.to_string()),
        Err(error) => error_value(&error),
    };
    match op["op"].as_str().unwrap() {
        "randint" => text(rng.randint(int(&op["a"]), int(&op["b"]))),
        "randrange" => {
            let (start, stop) = (int(&op["start"]), int(&op["stop"]));
            if op["step"].is_null() {
                text(rng.randrange(start, stop))
            } else {
                text(rng.randrange_step(start, stop, int(&op["step"])))
            }
        }
        "randrange_to" => text(rng.randrange_to(int(&op["stop"]))),
        "getrandbits" => {
            let k = u32::try_from(int(&op["k"])).unwrap();
            Value::String(rng.getrandbits(k).to_string())
        }
        "choice" => {
            let len = usize::try_from(int(&op["len"])).unwrap();
            let items: Vec<usize> = (0..len).collect();
            match rng.choice(&items) {
                Ok(item) => Value::String(item.to_string()),
                Err(error) => error_value(&error),
            }
        }
        "shuffle" => {
            let len = usize::try_from(int(&op["len"])).unwrap();
            let mut items: Vec<usize> = (0..len).collect();
            rng.shuffle(&mut items);
            serde_json::json!(items)
        }
        "random" => Value::String(pyjson::float_repr(rng.random())),
        other => panic!("unknown operation {other}"),
    }
}

fn seeded(seed: &Value) -> PyRandom {
    match seed["kind"].as_str().unwrap() {
        "int" => PyRandom::from_int_limbs(&hex_limbs(seed["hex"].as_str().unwrap())),
        "str" => PyRandom::from_str_seed(seed["value"].as_str().unwrap()),
        "bytes" => PyRandom::from_bytes(&hex::decode(seed["value"].as_str().unwrap()).unwrap()),
        other => panic!("unknown seed kind {other}"),
    }
}

#[test]
fn every_seed_reproduces_the_python_script() {
    let vectors = vectors();
    let script = vectors["script"].as_array().unwrap();
    let seeds = vectors["seeds"].as_array().unwrap();
    assert!(script.len() > 1500 && seeds.len() > 50, "expected the full corpus");
    let mut failures = Vec::new();
    for seed in seeds {
        let mut rng = seeded(seed);
        assert_eq!(seed["results"].as_array().unwrap().len(), script.len());
        for (index, (op, expected)) in script.iter().zip(seed["results"].as_array().unwrap()).enumerate() {
            let actual = run(&mut rng, op);
            if json_difference(&actual, expected).is_some() {
                failures.push(format!(
                    "seed {seed_id} op {index} {op}: python {expected}, rust {actual}",
                    seed_id = seed.get("hex").or(seed.get("value")).unwrap()
                ));
                break;
            }
        }
    }
    assert!(
        failures.is_empty(),
        "{} seeds differ, first: {:?}",
        failures.len(),
        &failures[..failures.len().min(5)]
    );
}

#[test]
fn integer_seed_helpers_agree_with_limbs() {
    for seed in [0i64, 1, -1, 42, -42, i64::MAX, i64::MIN, 1 << 32, -(1 << 40)] {
        let mut by_i64 = PyRandom::from_i64(seed);
        let magnitude = seed.unsigned_abs();
        #[allow(clippy::cast_possible_truncation)]
        let mut by_limbs = PyRandom::from_int_limbs(&[magnitude as u32, (magnitude >> 32) as u32]);
        for _ in 0..700 {
            assert_eq!(by_i64.next_u32(), by_limbs.next_u32(), "seed {seed}");
        }
    }
}

#[test]
fn derived_tool_seeds_match_python() {
    let vectors = vectors();
    for case in vectors["derived"].as_array().unwrap() {
        let canonical = String::from_utf8(pyjson::canonical_bytes(&case["basis"])).unwrap();
        assert_eq!(canonical, case["canonical"].as_str().unwrap());
        let seed = seed_from_basis(&case["basis"]);
        assert_eq!(seed.to_string(), case["seed"].as_str().unwrap());
        let mut rng = PyRandom::from_u64(seed);
        let d20s: Vec<i128> = (0..20).map(|_| rng.randint(1, 20).unwrap()).collect();
        let expected: Vec<i128> =
            case["d20s"].as_array().unwrap().iter().map(|v| i128::from(v.as_i64().unwrap())).collect();
        assert_eq!(d20s, expected, "seed {seed}");
    }
}
