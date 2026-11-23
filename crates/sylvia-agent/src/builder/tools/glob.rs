//! `Path.rglob(pattern)` as Python 3.12's `pathlib` runs it: the same selectors, in the same
//! order, so that a capped search sees the same files first.
//!
//! The order is the directory listing's (`os.scandir`, like `std::fs::read_dir`, reads the
//! platform's own order) through `pathlib`'s selectors: the recursive selector visits the start
//! directory, then every directory `Path.walk()` lists, each directory's children listed when
//! the walk reaches their parent. Names match as `fnmatch.translate` patterns, without regard to
//! case on Windows (`pathlib`'s default there).

use std::collections::HashSet;
use std::path::{Path, PathBuf};

/// A failure Python raises while building the selector.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GlobError {
    /// `ValueError("Invalid pattern: '**' can only be an entire path component")`.
    InvalidPattern,
    /// `NotImplementedError("Non-relative patterns are unsupported")`.
    NonRelative,
}

impl std::fmt::Display for GlobError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::InvalidPattern => "Invalid pattern: '**' can only be an entire path component",
            Self::NonRelative => "Non-relative patterns are unsupported",
        })
    }
}

const CASE_SENSITIVE: bool = !cfg!(windows);

#[derive(Debug, Clone)]
enum Item {
    Char(char),
    Range(char, char),
}

#[derive(Debug, Clone)]
enum Token {
    Star,
    Any,
    Literal(char),
    Class { negated: bool, items: Vec<Item> },
    Never,
}

/// One pattern component, as `fnmatch.translate` reads it.
#[derive(Debug, Clone)]
pub struct NamePattern(Vec<Token>);

/// Python's `[...]` handling in `fnmatch.translate`, as the regular-expression class it builds.
fn class(chars: &[char], start: usize, end: usize) -> Token {
    let stuff: Vec<char> = chars[start..end].to_vec();
    let mut parts: Vec<Vec<char>> = Vec::new();
    if !stuff.contains(&'-') {
        parts.push(stuff);
    } else {
        let mut i = start;
        let mut k = if chars[start] == '!' { start + 2 } else { start + 1 };
        loop {
            let found = (k..end).find(|&index| chars[index] == '-');
            let Some(found) = found else { break };
            parts.push(chars[i..found].to_vec());
            i = found + 1;
            k = found + 3;
            if k > end {
                break;
            }
        }
        let chunk: Vec<char> = chars[i..end].to_vec();
        if !chunk.is_empty() {
            parts.push(chunk);
        } else if let Some(last) = parts.last_mut() {
            last.push('-');
        }
        // Remove empty ranges, as Python does.
        let mut k = parts.len();
        while k > 1 {
            k -= 1;
            let (previous, current) = (parts[k - 1].last().copied(), parts[k].first().copied());
            if let (Some(previous), Some(current)) = (previous, current) {
                if previous > current {
                    let mut joined = parts[k - 1][..parts[k - 1].len() - 1].to_vec();
                    joined.extend_from_slice(&parts[k][1..]);
                    parts[k - 1] = joined;
                    parts.remove(k);
                }
            }
        }
    }
    // The class text Python builds: chunks joined by range hyphens, every other character literal.
    let flat: Vec<char> = parts.iter().flatten().copied().collect();
    if flat.is_empty() {
        return Token::Never;
    }
    if flat == ['!'] {
        return Token::Any;
    }
    let negated = flat[0] == '!';
    let mut items = Vec::new();
    for (index, part) in parts.iter().enumerate() {
        let mut part: &[char] = part;
        if index == 0 && negated {
            part = &part[1..];
        }
        let mut rest = part;
        if index > 0 {
            // The hyphen before this chunk is a range from the previous character to this
            // chunk's first, as `re` reads the class; after a range, or with nothing on either
            // side, it is a literal hyphen.
            match (items.last(), part.first()) {
                (Some(&Item::Char(previous)), Some(&first)) => {
                    items.pop();
                    items.push(Item::Range(previous, first));
                    rest = &part[1..];
                }
                _ => items.push(Item::Char('-')),
            }
        }
        items.extend(rest.iter().map(|&c| Item::Char(c)));
    }
    Token::Class { negated, items }
}

impl NamePattern {
    /// `fnmatch.translate(pattern)`, as tokens.
    pub fn new(pattern: &str) -> Self {
        let chars: Vec<char> = pattern.chars().collect();
        let (mut tokens, mut i, n) = (Vec::new(), 0, chars.len());
        while i < n {
            let c = chars[i];
            i += 1;
            match c {
                '*' => {
                    if !matches!(tokens.last(), Some(Token::Star)) {
                        tokens.push(Token::Star);
                    }
                }
                '?' => tokens.push(Token::Any),
                '[' => {
                    let mut j = i;
                    if j < n && chars[j] == '!' {
                        j += 1;
                    }
                    if j < n && chars[j] == ']' {
                        j += 1;
                    }
                    while j < n && chars[j] != ']' {
                        j += 1;
                    }
                    if j >= n {
                        tokens.push(Token::Literal('['));
                    } else {
                        tokens.push(class(&chars, i, j));
                        i = j + 1;
                    }
                }
                other => tokens.push(Token::Literal(other)),
            }
        }
        Self(tokens)
    }

    /// The whole of `name` matches.
    pub fn matches(&self, name: &str) -> bool {
        let name: Vec<char> = name.chars().collect();
        let mut memo = HashSet::new();
        matches_from(&self.0, &name, 0, 0, &mut memo)
    }
}

fn same(a: char, b: char) -> bool {
    a == b || (!CASE_SENSITIVE && variants(a).any(|x| variants(b).any(|y| x == y)))
}

/// A character and its simple case mappings, for `re.IGNORECASE`.
fn variants(c: char) -> impl Iterator<Item = char> {
    let mut lower = c.to_lowercase();
    let mut upper = c.to_uppercase();
    let lower = if lower.len() == 1 { lower.next() } else { None };
    let upper = if upper.len() == 1 { upper.next() } else { None };
    [Some(c), lower, upper].into_iter().flatten()
}

fn in_class(items: &[Item], c: char) -> bool {
    let test = |x: char| {
        items.iter().any(|item| match *item {
            Item::Char(member) => member == x,
            Item::Range(low, high) => low <= x && x <= high,
        })
    };
    if CASE_SENSITIVE {
        test(c)
    } else {
        variants(c).any(test)
    }
}

fn matches_from(
    tokens: &[Token],
    name: &[char],
    t: usize,
    at: usize,
    memo: &mut HashSet<(usize, usize)>,
) -> bool {
    if !memo.insert((t, at)) {
        return false;
    }
    let Some(token) = tokens.get(t) else { return at == name.len() };
    match token {
        Token::Star => (at..=name.len()).any(|next| matches_from(tokens, name, t + 1, next, memo)),
        _ if at >= name.len() => false,
        Token::Any => matches_from(tokens, name, t + 1, at + 1, memo),
        Token::Literal(c) => same(*c, name[at]) && matches_from(tokens, name, t + 1, at + 1, memo),
        Token::Class { negated, items } => {
            in_class(items, name[at]) != *negated && matches_from(tokens, name, t + 1, at + 1, memo)
        }
        Token::Never => false,
    }
}

/// `Path._parse_path(pattern)`'s relative parts, or the refusal of a rooted pattern.
fn pattern_parts(pattern: &str) -> Result<Vec<String>, GlobError> {
    let separators: &[char] = if cfg!(windows) { &['\\', '/'] } else { &['/'] };
    let rooted = pattern.starts_with(separators)
        || (cfg!(windows) && {
            let mut chars = pattern.chars();
            matches!((chars.next(), chars.next()), (Some(drive), Some(':')) if drive.is_ascii_alphabetic())
        });
    if rooted {
        return Err(GlobError::NonRelative);
    }
    let mut parts: Vec<String> = pattern
        .split(separators)
        .filter(|part| !part.is_empty() && *part != ".")
        .map(str::to_owned)
        .collect();
    if pattern.ends_with(separators) {
        parts.push(String::new());
    }
    Ok(parts)
}

enum Selector {
    Terminating,
    Parent(Box<Selector>),
    Wildcard { pattern: NamePattern, dironly: bool, successor: Box<Selector> },
    Recursive { successor: Box<Selector>, dedupe: bool },
}

fn selector(parts: &[String]) -> Result<Selector, GlobError> {
    let Some(first) = parts.first() else { return Ok(Selector::Terminating) };
    if first.is_empty() {
        return Ok(Selector::Terminating);
    }
    if first == "**" {
        let mut index = 1;
        while index < parts.len() && parts[index] == "**" {
            index += 1;
        }
        let child = &parts[index..];
        let dedupe = child.iter().any(|part| part == "**");
        return Ok(Selector::Recursive { successor: Box::new(selector(child)?), dedupe });
    }
    let child = &parts[1..];
    if first == ".." {
        return Ok(Selector::Parent(Box::new(selector(child)?)));
    }
    if first.contains("**") {
        return Err(GlobError::InvalidPattern);
    }
    Ok(Selector::Wildcard {
        pattern: NamePattern::new(first),
        dironly: !child.is_empty(),
        successor: Box::new(selector(child)?),
    })
}

/// `os.scandir(path)`'s entries: names, and whether each is a directory (`follow` symlinks or
/// not). An unreadable directory has none.
fn listing(path: &Path, follow: bool) -> Vec<(String, bool)> {
    let Ok(entries) = std::fs::read_dir(path) else { return Vec::new() };
    entries
        .filter_map(Result::ok)
        .map(|entry| {
            let name = entry.file_name().to_string_lossy().into_owned();
            let is_dir = if follow {
                std::fs::metadata(entry.path()).is_ok_and(|metadata| metadata.is_dir())
            } else {
                entry.file_type().is_ok_and(|kind| kind.is_dir())
            };
            (name, is_dir)
        })
        .collect()
}

/// `_RecursiveWildcardSelector._iterate_directories(parent)`.
fn directories(parent: &Path) -> Vec<PathBuf> {
    let mut found = vec![parent.to_path_buf()];
    let mut stack = vec![parent.to_path_buf()];
    while let Some(path) = stack.pop() {
        let names: Vec<String> =
            listing(&path, false).into_iter().filter(|(_, dir)| *dir).map(|(name, _)| name).collect();
        found.extend(names.iter().map(|name| path.join(name)));
        stack.extend(names.iter().rev().map(|name| path.join(name)));
    }
    found
}

fn select(selector: &Selector, parent: &Path, out: &mut Vec<PathBuf>) {
    match selector {
        Selector::Terminating => out.push(parent.to_path_buf()),
        Selector::Parent(successor) => select(successor, &parent.join(".."), out),
        Selector::Wildcard { pattern, dironly, successor } => {
            for (name, is_dir) in listing(parent, true) {
                if *dironly && !is_dir {
                    continue;
                }
                if pattern.matches(&name) {
                    select(successor, &parent.join(&name), out);
                }
            }
        }
        Selector::Recursive { successor, dedupe } => {
            let mut seen = HashSet::new();
            for start in directories(parent) {
                let mut found = Vec::new();
                select(successor, &start, &mut found);
                for path in found {
                    if !*dedupe || seen.insert(path.clone()) {
                        out.push(path);
                    }
                }
            }
        }
    }
}

/// `root.rglob(pattern)`: every match, in Python's order.
pub fn rglob(root: &Path, pattern: &str) -> Result<Vec<PathBuf>, GlobError> {
    let mut parts = vec!["**".to_owned()];
    parts.extend(pattern_parts(pattern)?);
    let selector = selector(&parts)?;
    let mut out = Vec::new();
    if root.is_dir() {
        select(&selector, root, &mut out);
    }
    Ok(out)
}
