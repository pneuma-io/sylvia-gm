//! The lexer of `runtime/_expression_parser.py` (`tokenize`).
//!
//! Python matches one composite regular expression of alternatives in a fixed order at each
//! position and takes the first alternative that matches (not the longest). This lexer tries the
//! same alternatives in the same order. Positions and lengths count code points, as Python does.

use super::error::ExpressionError;
use sylvia_base::pyjson::float_repr;
use sylvia_base::pystr::repr;

// Generated; kept exactly as the generator writes it.
#[rustfmt::skip]
#[path = "word_table.rs"]
mod table;

/// `MAX_EXPRESSION_LENGTH`.
pub const MAX_EXPRESSION_LENGTH: usize = 500;
/// `MAX_NESTING_DEPTH`.
pub const MAX_NESTING_DEPTH: usize = 20;
/// `MAX_STRING_LENGTH`.
pub const MAX_STRING_LENGTH: usize = 200;

/// `FORBIDDEN_IDENTIFIERS`.
pub const FORBIDDEN_IDENTIFIERS: &[&str] = &[
    "__import__",
    "eval",
    "exec",
    "compile",
    "open",
    "__builtins__",
    "__class__",
    "__bases__",
    "__subclasses__",
    "__globals__",
    "__code__",
    "__dict__",
    "getattr",
    "setattr",
    "delattr",
    "breakpoint",
    "input",
    "__debug__",
    "type",
    "object",
    "super",
    "staticmethod",
    "classmethod",
    "property",
    "__name__",
    "__module__",
    "__qualname__",
    "_",
    "__",
    "___",
];

/// `TokenType`. [`TokenType::name`] is the enum member's name, which error messages show.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TokenType {
    Number,
    String,
    Ident,
    True,
    False,
    Null,
    Plus,
    Minus,
    Star,
    SlashSlash,
    Percent,
    StarStar,
    EqEq,
    NotEq,
    Ge,
    Le,
    Gt,
    Lt,
    And,
    Or,
    Not,
    In,
    Contains,
    Dot,
    LParen,
    RParen,
    LBracket,
    RBracket,
    Comma,
    Eof,
}

impl TokenType {
    /// `token_type.name`.
    pub const fn name(self) -> &'static str {
        match self {
            Self::Number => "NUMBER",
            Self::String => "STRING",
            Self::Ident => "IDENT",
            Self::True => "TRUE",
            Self::False => "FALSE",
            Self::Null => "NULL",
            Self::Plus => "PLUS",
            Self::Minus => "MINUS",
            Self::Star => "STAR",
            Self::SlashSlash => "SLASH_SLASH",
            Self::Percent => "PERCENT",
            Self::StarStar => "STAR_STAR",
            Self::EqEq => "EQ_EQ",
            Self::NotEq => "NOT_EQ",
            Self::Ge => "GE",
            Self::Le => "LE",
            Self::Gt => "GT",
            Self::Lt => "LT",
            Self::And => "AND",
            Self::Or => "OR",
            Self::Not => "NOT",
            Self::In => "IN",
            Self::Contains => "CONTAINS",
            Self::Dot => "DOT",
            Self::LParen => "LPAREN",
            Self::RParen => "RPAREN",
            Self::LBracket => "LBRACKET",
            Self::RBracket => "RBRACKET",
            Self::Comma => "COMMA",
            Self::Eof => "EOF",
        }
    }
}

/// An integer literal: its value, or `None` when it lies beyond `i64` (Python keeps it exact;
/// evaluating it is refused, `differences.md` row 5). `digits` is the source text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IntLiteral {
    /// The value when it fits in `i64`.
    pub value: Option<i64>,
    /// The ASCII digits as written.
    pub digits: String,
}

impl IntLiteral {
    /// `repr(int(digits))`: the digits without leading zeros.
    pub fn repr(&self) -> String {
        match self.value {
            Some(value) => value.to_string(),
            None => self.digits.trim_start_matches('0').to_owned(),
        }
    }
}

/// `Token.value`.
#[derive(Debug, Clone, PartialEq)]
pub enum TokenValue {
    /// `None` (`null`, end of input).
    None,
    /// `True` / `False`.
    Bool(bool),
    /// An integer `NUMBER`.
    Int(IntLiteral),
    /// A `NUMBER` with a point.
    Float(f64),
    /// A string literal's text, an identifier, or an operator's source text.
    Text(String),
}

impl TokenValue {
    /// `repr(token.value)`, as `!r` shows it in messages.
    pub fn repr(&self) -> String {
        match self {
            Self::None => "None".to_owned(),
            Self::Bool(true) => "True".to_owned(),
            Self::Bool(false) => "False".to_owned(),
            Self::Int(literal) => literal.repr(),
            Self::Float(value) => float_repr(*value),
            Self::Text(text) => repr(text),
        }
    }

    /// The text of a `Text` value.
    pub fn text(&self) -> &str {
        match self {
            Self::Text(text) => text,
            other => panic!("token value {other:?} is not text"),
        }
    }
}

/// `Token`: type, value and start position (in code points).
#[derive(Debug, Clone, PartialEq)]
pub struct Token {
    /// `token.type`.
    pub kind: TokenType,
    /// `token.value`.
    pub value: TokenValue,
    /// `token.position`.
    pub position: usize,
}

/// `\w` in a Python `str` pattern.
fn is_word(c: char) -> bool {
    let code = u32::from(c);
    let index = table::WORD.partition_point(|(start, _)| *start <= code);
    index > 0 && code <= table::WORD[index - 1].1
}

const OPERATORS: [(&str, TokenType); 18] = [
    ("//", TokenType::SlashSlash),
    ("**", TokenType::StarStar),
    ("==", TokenType::EqEq),
    ("!=", TokenType::NotEq),
    (">=", TokenType::Ge),
    ("<=", TokenType::Le),
    ("+", TokenType::Plus),
    ("-", TokenType::Minus),
    ("*", TokenType::Star),
    ("%", TokenType::Percent),
    (">", TokenType::Gt),
    ("<", TokenType::Lt),
    (".", TokenType::Dot),
    ("(", TokenType::LParen),
    (")", TokenType::RParen),
    ("[", TokenType::LBracket),
    ("]", TokenType::RBracket),
    (",", TokenType::Comma),
];

const KEYWORDS: [(&str, TokenType); 8] = [
    ("true", TokenType::True),
    ("false", TokenType::False),
    ("null", TokenType::Null),
    ("and", TokenType::And),
    ("or", TokenType::Or),
    ("not", TokenType::Not),
    ("in", TokenType::In),
    ("contains", TokenType::Contains),
];

fn starts_with(chars: &[char], at: usize, text: &str) -> bool {
    (at..).zip(text.chars()).all(|(index, expected)| chars.get(index) == Some(&expected))
}

/// `tokenize(expr)`.
pub fn tokenize(expr: &str) -> Result<Vec<Token>, ExpressionError> {
    let chars: Vec<char> = expr.chars().collect();
    if chars.len() > MAX_EXPRESSION_LENGTH {
        return Err(ExpressionError::in_expression(
            format!("Expression exceeds {MAX_EXPRESSION_LENGTH} character limit ({} chars).", chars.len()),
            expr,
        ));
    }
    let mut tokens = Vec::new();
    let mut pos = 0;
    let text_of = |start: usize, end: usize| chars[start..end].iter().collect::<String>();
    'scan: while pos < chars.len() {
        let start = pos;
        let c = chars[pos];
        // Whitespace (skipped).
        if matches!(c, ' ' | '\t' | '\n' | '\r') {
            while pos < chars.len() && matches!(chars[pos], ' ' | '\t' | '\n' | '\r') {
                pos += 1;
            }
            continue;
        }
        for (operator, kind) in OPERATORS {
            if starts_with(&chars, pos, operator) {
                pos += operator.chars().count();
                tokens.push(Token { kind, value: TokenValue::Text(operator.to_owned()), position: start });
                continue 'scan;
            }
        }
        if c.is_ascii_digit() {
            while pos < chars.len() && chars[pos].is_ascii_digit() {
                pos += 1;
            }
            let mut is_float = false;
            if chars.get(pos) == Some(&'.') && chars.get(pos + 1).is_some_and(char::is_ascii_digit) {
                is_float = true;
                pos += 1;
                while pos < chars.len() && chars[pos].is_ascii_digit() {
                    pos += 1;
                }
            }
            let raw = text_of(start, pos);
            let value = if is_float {
                // `float(raw)`: Rust's parser is correctly rounded, as CPython's is.
                TokenValue::Float(raw.parse().expect("ASCII decimal"))
            } else {
                TokenValue::Int(IntLiteral { value: raw.parse().ok(), digits: raw })
            };
            tokens.push(Token { kind: TokenType::Number, value, position: start });
            continue;
        }
        if c == '"' || c == '\'' {
            if let Some(offset) = chars[pos + 1..].iter().position(|other| *other == c) {
                let end = pos + 1 + offset;
                let value = text_of(pos + 1, end);
                let length = end - pos - 1;
                if length > MAX_STRING_LENGTH {
                    return Err(ExpressionError::at(
                        format!("String literal exceeds {MAX_STRING_LENGTH} char limit ({length} chars)."),
                        expr,
                        start,
                    ));
                }
                pos = end + 1;
                tokens.push(Token {
                    kind: TokenType::String,
                    value: TokenValue::Text(value),
                    position: start,
                });
                continue;
            }
        }
        for (keyword, kind) in KEYWORDS {
            if starts_with(&chars, pos, keyword) {
                let end = pos + keyword.len();
                if !chars.get(end).is_some_and(|next| is_word(*next)) {
                    pos = end;
                    let value = match kind {
                        TokenType::True => TokenValue::Bool(true),
                        TokenType::False => TokenValue::Bool(false),
                        TokenType::Null => TokenValue::None,
                        _ => TokenValue::Text(keyword.to_owned()),
                    };
                    tokens.push(Token { kind, value, position: start });
                    continue 'scan;
                }
            }
        }
        if c.is_ascii_alphabetic() || c == '_' {
            while pos < chars.len() && (chars[pos].is_ascii_alphanumeric() || chars[pos] == '_') {
                pos += 1;
            }
            let name = text_of(start, pos);
            if FORBIDDEN_IDENTIFIERS.contains(&name.as_str()) {
                return Err(ExpressionError::at(format!("Identifier '{name}' is forbidden."), expr, start));
            }
            tokens.push(Token { kind: TokenType::Ident, value: TokenValue::Text(name), position: start });
            continue;
        }
        return Err(ExpressionError::at(format!("Unexpected character {}", repr(&c.to_string())), expr, pos));
    }
    tokens.push(Token { kind: TokenType::Eof, value: TokenValue::None, position: pos });
    Ok(tokens)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn word_table_is_python_3_12() {
        assert_eq!(table::UNICODE_VERSION, "15.0.0");
        assert!(is_word('a') && is_word('é') && is_word('_') && is_word('٣') && !is_word('-'));
    }

    #[test]
    fn keywords_need_a_word_boundary() {
        let kinds = |text: &str| tokenize(text).unwrap().iter().map(|t| t.kind).collect::<Vec<_>>();
        assert_eq!(kinds("true"), [TokenType::True, TokenType::Eof]);
        assert_eq!(kinds("true_"), [TokenType::Ident, TokenType::Eof]);
        assert_eq!(kinds("in(x)")[0], TokenType::In);
        assert!(tokenize("trueé").is_err());
    }
}
