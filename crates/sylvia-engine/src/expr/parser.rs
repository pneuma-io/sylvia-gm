//! The recursive-descent parser of `runtime/_expression_parser.py` (`Parser`,
//! `parse_expression`) and its AST.
//!
//! Ported as it is, quirks included: comparisons do not chain (`1 < 2 < 3` is a parse error),
//! unary `not` binds tighter than a comparison, and a second index on a field segment replaces
//! the first (`a[0][1]` reads `a[1]`).

use super::error::ExpressionError;
use super::token::{tokenize, IntLiteral, Token, TokenType, TokenValue, MAX_NESTING_DEPTH};

/// The collection functions of `runtime/_collections.py` (`FUNCTIONS`).
pub const COLLECTION_FUNCTIONS: [&str; 12] = [
    "indices",
    "portion",
    "joined",
    "replaced",
    "without",
    "occurrences",
    "text",
    "total",
    "partitions",
    "one",
    "ordered",
    "completions",
];

/// `_FUNCTION_WHITELIST`, sorted as the parser's error message lists it.
pub fn function_whitelist() -> Vec<&'static str> {
    let mut names: Vec<&'static str> = ["max", "min", "abs", "floor", "ceil", "len", "merge"]
        .into_iter()
        .chain(COLLECTION_FUNCTIONS)
        .collect();
    names.sort_unstable();
    names
}

/// A literal: `LiteralNode.value`.
#[derive(Debug, Clone, PartialEq)]
pub enum Literal {
    /// `null`.
    None,
    /// `true` / `false`.
    Bool(bool),
    /// An integer; beyond `i64` it is refused when evaluated.
    Int(IntLiteral),
    /// A number with a point.
    Float(f64),
    /// A string literal.
    Str(String),
}

/// Binary operators, by `BinaryOpNode.operator`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BinaryOp {
    And,
    Or,
    Add,
    Sub,
    Mul,
    FloorDiv,
    Mod,
    Pow,
    Eq,
    Ne,
    Ge,
    Le,
    Gt,
    Lt,
    In,
    NotIn,
    Contains,
}

impl BinaryOp {
    /// The operator string Python stores.
    pub const fn symbol(self) -> &'static str {
        match self {
            Self::And => "and",
            Self::Or => "or",
            Self::Add => "+",
            Self::Sub => "-",
            Self::Mul => "*",
            Self::FloorDiv => "//",
            Self::Mod => "%",
            Self::Pow => "**",
            Self::Eq => "==",
            Self::Ne => "!=",
            Self::Ge => ">=",
            Self::Le => "<=",
            Self::Gt => ">",
            Self::Lt => "<",
            Self::In => "in",
            Self::NotIn => "not_in",
            Self::Contains => "contains",
        }
    }

    fn from_token(kind: TokenType) -> Self {
        match kind {
            TokenType::And => Self::And,
            TokenType::Or => Self::Or,
            TokenType::Plus => Self::Add,
            TokenType::Minus => Self::Sub,
            TokenType::Star => Self::Mul,
            TokenType::SlashSlash => Self::FloorDiv,
            TokenType::Percent => Self::Mod,
            TokenType::EqEq => Self::Eq,
            TokenType::NotEq => Self::Ne,
            TokenType::Ge => Self::Ge,
            TokenType::Le => Self::Le,
            TokenType::Gt => Self::Gt,
            TokenType::Lt => Self::Lt,
            other => unreachable!("{other:?} is not a binary operator token"),
        }
    }
}

/// Unary operators, by `UnaryOpNode.operator`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UnaryOp {
    /// `-`.
    Neg,
    /// `not`.
    Not,
}

/// One segment of a field path: a name and an optional index expression.
pub type Segment = (String, Option<Box<Node>>);

/// An AST node.
#[derive(Debug, Clone, PartialEq)]
pub enum Node {
    /// `LiteralNode`.
    Literal(Literal),
    /// `FieldAccessNode`.
    Field(Vec<Segment>),
    /// `UnaryOpNode`.
    Unary(UnaryOp, Box<Node>),
    /// `BinaryOpNode`.
    Binary(BinaryOp, Box<Node>, Box<Node>),
    /// `FunctionCallNode`.
    Call(String, Vec<Node>),
}

struct Parser {
    tokens: Vec<Token>,
    pos: usize,
    depth: usize,
}

type ParseResult<T> = Result<T, ExpressionError>;

impl Parser {
    fn enter(&mut self) -> ParseResult<()> {
        self.depth += 1;
        if self.depth > MAX_NESTING_DEPTH {
            return Err(ExpressionError::new(format!(
                "Expression exceeds maximum nesting depth of {MAX_NESTING_DEPTH}."
            )));
        }
        Ok(())
    }

    fn peek(&self) -> &Token {
        &self.tokens[self.pos]
    }

    fn advance(&mut self) -> Token {
        let token = self.tokens[self.pos].clone();
        self.pos += 1;
        token
    }

    fn expect(&mut self, kind: TokenType) -> ParseResult<Token> {
        let token = self.advance();
        if token.kind != kind {
            return Err(ExpressionError::at_position(
                format!("Expected {} but got {} ({})", kind.name(), token.kind.name(), token.value.repr()),
                token.position,
            ));
        }
        Ok(token)
    }

    fn take(&mut self, kinds: &[TokenType]) -> Option<Token> {
        kinds.contains(&self.peek().kind).then(|| self.advance())
    }

    fn expression(&mut self) -> ParseResult<Node> {
        self.enter()?;
        let result = self.logic();
        self.depth -= 1;
        result
    }

    fn logic(&mut self) -> ParseResult<Node> {
        let mut left = self.comparison()?;
        while let Some(op) = self.take(&[TokenType::And, TokenType::Or]) {
            let right = self.comparison()?;
            left = Node::Binary(BinaryOp::from_token(op.kind), Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    fn comparison(&mut self) -> ParseResult<Node> {
        let left = self.arithmetic()?;
        let kind = self.peek().kind;
        let op = match kind {
            TokenType::EqEq
            | TokenType::NotEq
            | TokenType::Ge
            | TokenType::Le
            | TokenType::Gt
            | TokenType::Lt => {
                self.advance();
                BinaryOp::from_token(kind)
            }
            TokenType::In => {
                self.advance();
                BinaryOp::In
            }
            TokenType::Not if self.tokens.get(self.pos + 1).is_some_and(|t| t.kind == TokenType::In) => {
                self.advance();
                self.advance();
                BinaryOp::NotIn
            }
            TokenType::Contains => {
                self.advance();
                BinaryOp::Contains
            }
            _ => return Ok(left),
        };
        let right = self.arithmetic()?;
        Ok(Node::Binary(op, Box::new(left), Box::new(right)))
    }

    fn arithmetic(&mut self) -> ParseResult<Node> {
        let mut left = self.term()?;
        while let Some(op) = self.take(&[TokenType::Plus, TokenType::Minus]) {
            let right = self.term()?;
            left = Node::Binary(BinaryOp::from_token(op.kind), Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    fn term(&mut self) -> ParseResult<Node> {
        let mut left = self.power()?;
        while let Some(op) = self.take(&[TokenType::Star, TokenType::SlashSlash, TokenType::Percent]) {
            let right = self.power()?;
            left = Node::Binary(BinaryOp::from_token(op.kind), Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    fn power(&mut self) -> ParseResult<Node> {
        let left = self.unary()?;
        if self.take(&[TokenType::StarStar]).is_some() {
            let right = self.power()?;
            return Ok(Node::Binary(BinaryOp::Pow, Box::new(left), Box::new(right)));
        }
        Ok(left)
    }

    fn unary(&mut self) -> ParseResult<Node> {
        if let Some(op) = self.take(&[TokenType::Not, TokenType::Minus]) {
            let operand = self.unary()?;
            let op = if op.kind == TokenType::Not { UnaryOp::Not } else { UnaryOp::Neg };
            return Ok(Node::Unary(op, Box::new(operand)));
        }
        self.primary()
    }

    fn primary(&mut self) -> ParseResult<Node> {
        self.enter()?;
        let result = self.primary_inner();
        self.depth -= 1;
        result
    }

    fn primary_inner(&mut self) -> ParseResult<Node> {
        let token = self.peek().clone();
        let literal = match (&token.kind, &token.value) {
            (TokenType::LParen, _) => {
                self.advance();
                let node = self.expression()?;
                self.expect(TokenType::RParen)?;
                return Ok(node);
            }
            (TokenType::Number, TokenValue::Int(literal)) => Literal::Int(literal.clone()),
            (TokenType::Number, TokenValue::Float(value)) => Literal::Float(*value),
            (TokenType::String, TokenValue::Text(text)) => Literal::Str(text.clone()),
            (TokenType::True, _) => Literal::Bool(true),
            (TokenType::False, _) => Literal::Bool(false),
            (TokenType::Null, _) => Literal::None,
            (TokenType::Ident, TokenValue::Text(name)) => {
                self.advance();
                if self.peek().kind == TokenType::LParen {
                    return self.function_call(name);
                }
                return self.field_access(name.clone());
            }
            _ => {
                return Err(ExpressionError::at_position(
                    format!("Unexpected token {} ({})", token.kind.name(), token.value.repr()),
                    token.position,
                ))
            }
        };
        self.advance();
        Ok(Node::Literal(literal))
    }

    fn field_access(&mut self, first: String) -> ParseResult<Node> {
        let mut segments: Vec<Segment> = vec![(first, None)];
        loop {
            match self.peek().kind {
                TokenType::Dot => {
                    self.advance();
                    let name = self.expect(TokenType::Ident)?;
                    segments.push((name.value.text().to_owned(), None));
                }
                TokenType::LBracket => {
                    self.advance();
                    let index = self.expression()?;
                    self.expect(TokenType::RBracket)?;
                    // Python replaces the last segment's index with this one.
                    segments.last_mut().expect("a first segment").1 = Some(Box::new(index));
                }
                _ => break,
            }
        }
        Ok(Node::Field(segments))
    }

    fn function_call(&mut self, name: &str) -> ParseResult<Node> {
        let whitelist = function_whitelist();
        if !whitelist.contains(&name) {
            return Err(ExpressionError::new(format!(
                "'{name}' is not an available function. Available: {}.",
                whitelist.join(", ")
            )));
        }
        self.expect(TokenType::LParen)?;
        let mut args = Vec::new();
        if self.peek().kind != TokenType::RParen {
            args.push(self.expression()?);
            while self.take(&[TokenType::Comma]).is_some() {
                args.push(self.expression()?);
            }
        }
        self.expect(TokenType::RParen)?;
        Ok(Node::Call(name.to_owned(), args))
    }
}

/// `parse_expression(expr)`.
pub fn parse_expression(expr: &str) -> Result<Node, ExpressionError> {
    let tokens = tokenize(expr)?;
    let mut parser = Parser { tokens, pos: 0, depth: 0 };
    let node = parser.expression()?;
    parser.expect(TokenType::Eof)?;
    Ok(node)
}
