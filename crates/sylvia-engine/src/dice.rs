//! `src/sylvia_gm/dice.py`: number-only dice, drawn from Python-identical random streams.
//!
//! Python keeps three streams in context variables: a scripted roll source
//! (`use_roll_source`), the isolated stream of the seeded tool invocation in progress
//! (`seeded_rolls`) and a module-wide default seeded from `os.urandom(16)`. Every roll asks
//! `_roll_rng()` for the innermost one. Rust keeps the same three in a [`Dice`] value that the
//! caller owns and passes down, so the engine stays free of globals and threads.
//!
//! Every draw is `randint`, as in Python, so a scripted [`RollSource`] sees exactly the calls
//! Python's `ScenarioDice` sees. Results serialise with [`RollResult::to_dict`] and friends in
//! Python's key order with integer values; error messages are copied from `dice.py`.
//!
//! Python's integers are exact at any size. Every sum, total plus modifier, margin and success
//! count here is checked instead, and a result beyond `i64` is refused with
//! `integer result exceeds the supported 64-bit range` (`differences.md` row 5).

use crate::pyerror::{self, PyError, PyResult};
use crate::pynum::{py_sum, PyNumber};
use crate::pyrandom::PyRandom;
use sylvia_base::{Json, JsonMap};

/// `_MAX_SIDES`.
pub const MAX_SIDES: i64 = 1000;
/// `_MAX_COUNT`.
pub const MAX_COUNT: i64 = 1000;

/// Something `_roll_rng()` can return: answers `randint(a, b)`.
///
/// `PyRandom` is the ordinary stream. A test harness supplies scripted faces the way Python's
/// `ScenarioDice(random.Random)` overrides `randint`.
pub trait RollSource {
    /// `rng.randint(a, b)`.
    fn randint(&mut self, a: i128, b: i128) -> PyResult<i128>;
}

impl RollSource for PyRandom {
    fn randint(&mut self, a: i128, b: i128) -> PyResult<i128> {
        PyRandom::randint(self, a, b)
    }
}

/// The dice streams of one thread of execution (Python's context variables plus `_rng`).
pub struct Dice {
    default: PyRandom,
    current_seed: Option<i128>,
    /// `_tool_dice`: one frame per `seeded_rolls` in progress, innermost last.
    tool_frames: Vec<Option<PyRandom>>,
    /// `_roll_source`: one per `use_roll_source` in progress, innermost last.
    sources: Vec<Box<dyn RollSource>>,
}

impl std::fmt::Debug for Dice {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Dice")
            .field("current_seed", &self.current_seed)
            .field("tool_frames", &self.tool_frames.len())
            .field("sources", &self.sources.len())
            .finish_non_exhaustive()
    }
}

impl Default for Dice {
    fn default() -> Self {
        Self::new()
    }
}

impl Dice {
    /// A fresh set of streams: the default one from OS entropy, as at module import.
    pub fn new() -> Self {
        Self::with_default(PyRandom::from_entropy())
    }

    /// Streams whose default is the given generator (for tests and replays).
    pub fn with_default(default: PyRandom) -> Self {
        Self { default, current_seed: None, tool_frames: Vec::new(), sources: Vec::new() }
    }

    /// `with use_roll_source(source): ...`. Returns the closure's result and the source, so the
    /// caller can inspect it afterwards (`ScenarioDice.assert_finished`).
    pub fn use_roll_source<R>(
        &mut self,
        source: Box<dyn RollSource>,
        body: impl FnOnce(&mut Self) -> R,
    ) -> (R, Box<dyn RollSource>) {
        self.sources.push(source);
        let result = body(self);
        let source = self.sources.pop().expect("the source pushed above");
        (result, source)
    }

    /// `with seeded_rolls(seed): ...`: an isolated stream for one tool invocation.
    ///
    /// No seed, or a scripted source in force, leaves the streams as they are.
    pub fn seeded_rolls<R>(&mut self, seed: Option<i128>, body: impl FnOnce(&mut Self) -> R) -> R {
        let Some(seed) = seed else { return body(self) };
        if !self.sources.is_empty() {
            return body(self);
        }
        self.tool_frames.push(None);
        self.set_seed(seed);
        let result = body(self);
        self.tool_frames.pop();
        result
    }

    /// `set_seed(seed)`: seed the active tool stream, or the default stream outside a tool.
    pub fn set_seed(&mut self, seed: i128) {
        match self.tool_frames.last_mut() {
            None => {
                self.default = PyRandom::from_i128(seed);
                self.current_seed = Some(seed);
            }
            Some(frame) => *frame = Some(PyRandom::from_i128(seed)),
        }
    }

    /// `get_seed()`: the last seed given to the default stream, `None` after `reseed()`.
    pub fn get_seed(&self) -> Option<i128> {
        self.current_seed
    }

    /// `reseed()`: a new default stream from OS entropy.
    pub fn reseed(&mut self) {
        self.current_seed = None;
        self.default = PyRandom::from_entropy();
    }

    /// `_roll_rng().randint(a, b)`.
    pub fn randint(&mut self, a: i64, b: i64) -> PyResult<i64> {
        let value = self.randint_wide(i128::from(a), i128::from(b))?;
        Ok(i64::try_from(value).expect("randint result lies between its i64 bounds"))
    }

    /// `_roll_rng().randint(a, b)` for bounds beyond `i64` (a sum of large table weights).
    pub fn randint_wide(&mut self, a: i128, b: i128) -> PyResult<i128> {
        let source: &mut dyn RollSource = if let Some(source) = self.sources.last_mut() {
            source.as_mut()
        } else if let Some(Some(tool)) = self.tool_frames.last_mut() {
            tool
        } else {
            &mut self.default
        };
        source.randint(a, b)
    }
}

/// `Die`: the standard die types.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Die {
    /// d4
    D4 = 4,
    /// d6
    D6 = 6,
    /// d8
    D8 = 8,
    /// d10
    D10 = 10,
    /// d12
    D12 = 12,
    /// d20
    D20 = 20,
    /// d100
    D100 = 100,
}

impl Die {
    /// `Die.value`.
    pub const fn sides(self) -> i64 {
        self as i64
    }
}

fn int_list(values: &[i64]) -> Json {
    Json::Array(values.iter().map(|v| Json::from(*v)).collect())
}

/// `RollResult`. `modifier` is stored, never added to `total`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RollResult {
    /// Faces on each die.
    pub sides: i64,
    /// Dice rolled (the original count for exploding dice).
    pub count: i64,
    /// Every die's result in roll order.
    pub results: Vec<i64>,
    /// Sum, or the kept die for advantage and disadvantage.
    pub total: i64,
    /// Flat modifier kept for the consumer.
    pub modifier: i64,
}

impl RollResult {
    /// `to_dict()`.
    pub fn to_dict(&self) -> Json {
        let mut map = JsonMap::new();
        map.insert("sides".into(), self.sides.into());
        map.insert("count".into(), self.count.into());
        map.insert("results".into(), int_list(&self.results));
        map.insert("total".into(), self.total.into());
        map.insert("modifier".into(), self.modifier.into());
        Json::Object(map)
    }
}

/// `CheckResult`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CheckResult {
    /// `effective >= dc`.
    pub success: bool,
    /// The underlying roll.
    pub roll_result: RollResult,
    /// The target.
    pub dc: i64,
    /// `effective - dc`.
    pub margin: i64,
}

impl CheckResult {
    /// `to_dict()`.
    pub fn to_dict(&self) -> Json {
        let mut map = JsonMap::new();
        map.insert("success".into(), self.success.into());
        map.insert("dc".into(), self.dc.into());
        map.insert("margin".into(), self.margin.into());
        map.insert("roll_result".into(), self.roll_result.to_dict());
        Json::Object(map)
    }
}

/// `PoolResult`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PoolResult {
    /// Faces on each die.
    pub sides: i64,
    /// Dice rolled.
    pub count: i64,
    /// Every die's result.
    pub results: Vec<i64>,
    /// Lowest success face.
    pub threshold: i64,
    /// Dice at or above the threshold, plus the modifier, at least 0.
    pub successes: i64,
    /// Dice below the threshold.
    pub failures: i64,
    /// Dice showing 1.
    pub botches: i64,
    /// Flat bonus successes.
    pub modifier: i64,
}

impl PoolResult {
    /// `to_dict()`.
    pub fn to_dict(&self) -> Json {
        let mut map = JsonMap::new();
        map.insert("sides".into(), self.sides.into());
        map.insert("count".into(), self.count.into());
        map.insert("results".into(), int_list(&self.results));
        map.insert("threshold".into(), self.threshold.into());
        map.insert("successes".into(), self.successes.into());
        map.insert("failures".into(), self.failures.into());
        map.insert("botches".into(), self.botches.into());
        map.insert("modifier".into(), self.modifier.into());
        Json::Object(map)
    }
}

/// `HungerPoolResult`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HungerPoolResult {
    /// Normal dice.
    pub regular_results: Vec<i64>,
    /// Hunger dice.
    pub hunger_results: Vec<i64>,
    /// Dice at or above the threshold, plus the modifier, at least 0.
    pub successes: i64,
    /// Dice below the threshold.
    pub failures: i64,
    /// Hunger dice showing 1.
    pub bestial_failures: i64,
    /// 1 when a hunger die shows 10 and another die does too.
    pub messy_criticals: i64,
    /// Lowest success face.
    pub threshold: i64,
    /// Flat bonus successes.
    pub modifier: i64,
}

impl HungerPoolResult {
    /// `to_dict()`.
    pub fn to_dict(&self) -> Json {
        let mut map = JsonMap::new();
        map.insert("regular_results".into(), int_list(&self.regular_results));
        map.insert("hunger_results".into(), int_list(&self.hunger_results));
        map.insert("successes".into(), self.successes.into());
        map.insert("failures".into(), self.failures.into());
        map.insert("bestial_failures".into(), self.bestial_failures.into());
        map.insert("messy_criticals".into(), self.messy_criticals.into());
        map.insert("threshold".into(), self.threshold.into());
        map.insert("modifier".into(), self.modifier.into());
        Json::Object(map)
    }
}

/// `_resolve_sides` for an `int`. (A `Die` is always in range; pass [`Die::sides`].)
fn resolve_sides(sides: i64) -> PyResult<i64> {
    if sides < 2 {
        return Err(PyError::value("Die must have at least 2 sides"));
    }
    if sides > MAX_SIDES {
        return Err(PyError::value(format!("Die sides exceeds maximum ({MAX_SIDES})")));
    }
    Ok(sides)
}

/// `_validate_count` for an `int`.
fn validate_count(count: i64) -> PyResult<()> {
    if count < 1 {
        return Err(PyError::value("Must roll at least 1 die"));
    }
    if count > MAX_COUNT {
        return Err(PyError::value(format!("Die count exceeds maximum ({MAX_COUNT})")));
    }
    Ok(())
}

impl Dice {
    /// `roll(sides, count, modifier)`.
    pub fn roll(&mut self, sides: i64, count: i64, modifier: i64) -> PyResult<RollResult> {
        let sides = resolve_sides(sides)?;
        validate_count(count)?;
        let results = (0..count).map(|_| self.randint(1, sides)).collect::<PyResult<Vec<_>>>()?;
        let total = pyerror::sum(&results)?;
        Ok(RollResult { sides, count, results, total, modifier })
    }

    /// `ndn(n, sides, modifier)`, which is `roll(sides, n, modifier)`.
    pub fn ndn(&mut self, n: i64, sides: i64, modifier: i64) -> PyResult<RollResult> {
        self.roll(sides, n, modifier)
    }

    /// `d4`, `d6`, ... `d100`: `roll(die, 1, modifier)`.
    pub fn die(&mut self, die: Die, modifier: i64) -> PyResult<RollResult> {
        self.roll(die.sides(), 1, modifier)
    }

    /// `advantage(modifier)`: 2d20, results high then low, total the higher.
    pub fn advantage(&mut self, modifier: i64) -> PyResult<RollResult> {
        let a = self.randint(1, 20)?;
        let b = self.randint(1, 20)?;
        let (higher, lower) = if a >= b { (a, b) } else { (b, a) };
        Ok(RollResult { sides: 20, count: 2, results: vec![higher, lower], total: higher, modifier })
    }

    /// `disadvantage(modifier)`: 2d20, results low then high, total the lower.
    pub fn disadvantage(&mut self, modifier: i64) -> PyResult<RollResult> {
        let a = self.randint(1, 20)?;
        let b = self.randint(1, 20)?;
        let (lower, higher) = if a <= b { (a, b) } else { (b, a) };
        Ok(RollResult { sides: 20, count: 2, results: vec![lower, higher], total: lower, modifier })
    }

    /// `check(dc, modifier, advantage, disadvantage)`. Both flags cancel to a plain d20.
    pub fn check(
        &mut self,
        dc: i64,
        modifier: i64,
        advantage: bool,
        disadvantage: bool,
    ) -> PyResult<CheckResult> {
        let roll_result = if advantage && !disadvantage {
            self.advantage(modifier)?
        } else if disadvantage && !advantage {
            self.disadvantage(modifier)?
        } else {
            self.die(Die::D20, modifier)?
        };
        let effective = pyerror::add(roll_result.total, roll_result.modifier)?;
        let margin = pyerror::sub(effective, dc)?;
        Ok(CheckResult { success: effective >= dc, roll_result, dc, margin })
    }

    /// `explode(sides, count, modifier, explode_on, max_explosions)`.
    pub fn explode(
        &mut self,
        sides: i64,
        count: i64,
        modifier: i64,
        explode_on: Option<i64>,
        max_explosions: i64,
    ) -> PyResult<RollResult> {
        let sides = resolve_sides(sides)?;
        validate_count(count)?;
        let explode_value = explode_on.unwrap_or(sides);
        if explode_value < 1 || explode_value > sides {
            return Err(PyError::value(format!(
                "explode_on must be between 1 and {sides}, got {explode_value}"
            )));
        }
        if max_explosions < 0 {
            return Err(PyError::value("max_explosions must be non-negative"));
        }
        let mut results = Vec::new();
        for _ in 0..count {
            results.push(self.randint(1, sides)?);
        }
        let mut extra = 0;
        let mut index = 0;
        while index < results.len() && extra < max_explosions {
            if results[index] >= explode_value {
                extra += 1;
                results.push(self.randint(1, sides)?);
            }
            index += 1;
        }
        let total = pyerror::sum(&results)?;
        Ok(RollResult { sides, count, results, total, modifier })
    }

    /// `count_successes(sides, count, threshold, modifier)`.
    pub fn count_successes(
        &mut self,
        sides: i64,
        count: i64,
        threshold: i64,
        modifier: i64,
    ) -> PyResult<PoolResult> {
        let sides = resolve_sides(sides)?;
        validate_count(count)?;
        if threshold < 1 {
            return Err(PyError::value("threshold must be at least 1"));
        }
        if threshold > sides + 1 {
            return Err(PyError::value(format!("threshold ({threshold}) exceeds die maximum ({sides})")));
        }
        let results = (0..count).map(|_| self.randint(1, sides)).collect::<PyResult<Vec<_>>>()?;
        let successes = pyerror::add(count_where(&results, |r| r >= threshold), modifier)?;
        let failures = count_where(&results, |r| r < threshold);
        let botches = count_where(&results, |r| r == 1);
        Ok(PoolResult {
            sides,
            count,
            results,
            threshold,
            successes: successes.max(0),
            failures,
            botches,
            modifier,
        })
    }

    /// `reroll(previous_result, reroll_predicate, modifier, threshold)`.
    pub fn reroll(
        &mut self,
        previous: &RollResult,
        predicate: &str,
        modifier: i64,
        threshold: Option<i64>,
    ) -> PyResult<RollResult> {
        let keep: Vec<bool> = match predicate {
            "all" => vec![false; previous.results.len()],
            "ones" => previous.results.iter().map(|r| *r != 1).collect(),
            "failures" => {
                let Some(threshold) = threshold else {
                    return Err(PyError::value("threshold is required when reroll_predicate='failures'"));
                };
                previous.results.iter().map(|r| *r >= threshold).collect()
            }
            other => {
                return Err(PyError::value(format!(
                    "Unknown reroll_predicate '{other}'. Use 'all', 'ones', or 'failures'."
                )))
            }
        };
        let mut results = Vec::with_capacity(keep.len());
        for (index, kept) in keep.iter().enumerate() {
            if *kept {
                results.push(previous.results[index]);
            } else {
                results.push(self.randint(1, previous.sides)?);
            }
        }
        let total = pyerror::sum(&results)?;
        let count = i64::try_from(results.len()).expect("dice count fits in i64");
        Ok(RollResult { sides: previous.sides, count, results, total, modifier })
    }

    /// `weighted_pick(items, weight_field, roll_result)`: the index of the picked item.
    ///
    /// `items` are the table rows (JSON objects). The total is Python's `sum()` (see
    /// [`crate::pynum::py_sum`]); the running total is plain `+=`, as in `dice.py`. A float
    /// total with no supplied roll is the `TypeError` `randint` raises. `None` for no items.
    pub fn weighted_pick(
        &mut self,
        items: &[Json],
        weight_field: &str,
        roll_result: Option<&RollResult>,
    ) -> PyResult<Option<usize>> {
        if items.is_empty() {
            return Ok(None);
        }
        let zero = Json::from(0);
        let weight = |item: &'_ Json| -> Json { item.get(weight_field).unwrap_or(&zero).clone() };
        let weights: Vec<Json> = items.iter().map(weight).collect();
        let total = py_sum(&weights)?;
        if total.is_zero() {
            let last = i128::try_from(items.len() - 1).expect("table length fits in i128");
            let index = self.randint_wide(0, last)?;
            return Ok(Some(usize::try_from(index).expect("index within the table")));
        }
        let roll_value = match roll_result {
            Some(result) => i128::from(result.total),
            None => match total {
                PyNumber::Int(total) => self.randint_wide(1, total)?,
                PyNumber::Float(_) => {
                    return Err(PyError::type_error("'float' object cannot be interpreted as an integer"))
                }
            },
        };
        let mut cumulative = PyNumber::Int(0);
        for (index, weight) in weights.iter().enumerate() {
            cumulative = cumulative.add_json(weight)?;
            if cumulative.int_at_most(roll_value) {
                return Ok(Some(index));
            }
        }
        Ok(Some(items.len() - 1))
    }

    /// `hunger_roll(pool_size, hunger, sides, threshold, modifier)` (V5 style).
    pub fn hunger_roll(
        &mut self,
        pool_size: i64,
        hunger: i64,
        sides: i64,
        threshold: i64,
        modifier: i64,
    ) -> PyResult<HungerPoolResult> {
        let sides = resolve_sides(sides)?;
        if pool_size < 1 {
            return Err(PyError::value("pool_size must be at least 1"));
        }
        if hunger < 0 {
            return Err(PyError::value("hunger must be non-negative"));
        }
        if hunger > pool_size {
            return Err(PyError::value(format!("hunger ({hunger}) cannot exceed pool_size ({pool_size})")));
        }
        if threshold < 1 {
            return Err(PyError::value("threshold must be at least 1"));
        }
        let regular_count = pool_size - hunger;
        let regular = (0..regular_count).map(|_| self.randint(1, sides)).collect::<PyResult<Vec<_>>>()?;
        let hungry = (0..hunger).map(|_| self.randint(1, sides)).collect::<PyResult<Vec<_>>>()?;
        let all: Vec<i64> = regular.iter().chain(hungry.iter()).copied().collect();
        let successes = pyerror::add(count_where(&all, |r| r >= threshold), modifier)?;
        let failures = count_where(&all, |r| r < threshold);
        let bestial_failures = count_where(&hungry, |r| r == 1);
        let has_hunger_ten = hungry.contains(&10);
        let total_tens = count_where(&regular, |r| r == 10) + count_where(&hungry, |r| r == 10);
        let messy_criticals = i64::from(has_hunger_ten && total_tens >= 2);
        Ok(HungerPoolResult {
            regular_results: regular,
            hunger_results: hungry,
            successes: successes.max(0),
            failures,
            bestial_failures,
            messy_criticals,
            threshold,
            modifier,
        })
    }
}

fn count_where(values: &[i64], predicate: impl Fn(i64) -> bool) -> i64 {
    let count = values.iter().filter(|value| predicate(**value)).count();
    i64::try_from(count).expect("dice count fits in i64")
}
