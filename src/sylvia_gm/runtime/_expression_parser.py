"""Sandboxed expression parser — Lexer + Recursive Descent.

No eval(), no exec(). Uses a formal BNF grammar (see plan §6.2)
and produces a safe AST that the condition evaluator interprets.

Security:
- Hard limits on expression length (500 chars), nesting depth (20 nodes),
  string literals (200 chars).
- Whitelisted pure functions: max, min, abs, floor, ceil, len, merge.
- Forbidden identifiers blocked.
- All errors are typed ExpressionError with position information.
"""

from __future__ import annotations

import enum
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sylvia_gm.runtime._exceptions import ExpressionError

# ---------------------------------------------------------------------------
# Security limits
# ---------------------------------------------------------------------------

MAX_EXPRESSION_LENGTH: int = 500
MAX_NESTING_DEPTH: int = 20
MAX_STRING_LENGTH: int = 200

FORBIDDEN_IDENTIFIERS: frozenset[str] = frozenset({
    "__import__", "eval", "exec", "compile", "open",
    "__builtins__", "__class__", "__bases__", "__subclasses__",
    "__globals__", "__code__", "__dict__", "getattr", "setattr",
    "delattr", "breakpoint", "input", "__debug__",
    "type", "object", "super", "staticmethod", "classmethod",
    "property", "__name__", "__module__", "__qualname__",
    "_", "__", "___",
})

# ---------------------------------------------------------------------------
# Token types
# ---------------------------------------------------------------------------


class TokenType(enum.Enum):
    NUMBER = "NUMBER"
    STRING = "STRING"
    IDENT = "IDENT"
    TRUE = "TRUE"
    FALSE = "FALSE"
    NULL = "NULL"

    # Arithmetic operators
    PLUS = "+"
    MINUS = "-"
    STAR = "*"
    SLASH_SLASH = "//"
    PERCENT = "%"
    STAR_STAR = "**"

    # Comparison
    EQ_EQ = "=="
    NOT_EQ = "!="
    GE = ">="
    LE = "<="
    GT = ">"
    LT = "<"

    # Logic & membership
    AND = "and"
    OR = "or"
    NOT = "not"
    IN = "in"
    CONTAINS = "contains"

    # Delimiters
    DOT = "."
    LPAREN = "("
    RPAREN = ")"
    LBRACKET = "["
    RBRACKET = "]"
    COMMA = ","

    EOF = "EOF"


@dataclass
class Token:
    type: TokenType
    value: Any
    position: int

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, pos={self.position})"


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

# Ordered token patterns — longer patterns must come first
_TOKEN_SPEC_PAIRS: list[tuple[str, TokenType | None]] = [
    # Whitespace (skip)
    (r"[ \t\n\r]+", None),

    # Multi-char operators (must come before single-char)
    (r"//", TokenType.SLASH_SLASH),
    (r"\*\*", TokenType.STAR_STAR),
    (r"==", TokenType.EQ_EQ),
    (r"!=", TokenType.NOT_EQ),
    (r">=", TokenType.GE),
    (r"<=", TokenType.LE),

    # Single-char operators/delimiters
    (r"\+", TokenType.PLUS),
    (r"-", TokenType.MINUS),
    (r"\*", TokenType.STAR),
    (r"%", TokenType.PERCENT),
    (r">", TokenType.GT),
    (r"<", TokenType.LT),
    (r"\.", TokenType.DOT),
    (r"\(", TokenType.LPAREN),
    (r"\)", TokenType.RPAREN),
    (r"\[", TokenType.LBRACKET),
    (r"\]", TokenType.RBRACKET),
    (r",", TokenType.COMMA),

    # Numbers (integer or float)
    (r"[0-9]+(?:\.[0-9]+)?", TokenType.NUMBER),

    # Strings (double/single quoted)
    (r"\"[^\"]*\"", TokenType.STRING),
    (r"'[^']*'", TokenType.STRING),

    # Keywords and identifiers
    (r"true\b", TokenType.TRUE),
    (r"false\b", TokenType.FALSE),
    (r"null\b", TokenType.NULL),
    (r"and\b", TokenType.AND),
    (r"or\b", TokenType.OR),
    (r"not\b", TokenType.NOT),
    (r"in\b", TokenType.IN),
    (r"contains\b", TokenType.CONTAINS),
    (r"[a-zA-Z_][a-zA-Z0-9_]*", TokenType.IDENT),
]

# Build a composite regex
_TOKEN_REGEX = "|".join(
    f"(?P<G{i}>{pattern})" for i, (pattern, _) in enumerate(_TOKEN_SPEC_PAIRS)
)
_TOKEN_TYPES: list[TokenType | None] = [tok_type for _, tok_type in _TOKEN_SPEC_PAIRS]
_TOKEN_PATTERN = re.compile(_TOKEN_REGEX)


def tokenize(expr: str) -> list[Token]:
    """Convert an expression string into a list of tokens.

    Raises ExpressionError on invalid characters or string over-length.
    """
    if len(expr) > MAX_EXPRESSION_LENGTH:
        raise ExpressionError(
            f"Expression exceeds {MAX_EXPRESSION_LENGTH} character limit "
            f"({len(expr)} chars).",
            expr=expr,
        )

    tokens: list[Token] = []
    pos = 0

    while pos < len(expr):
        match = _TOKEN_PATTERN.match(expr, pos)
        if not match:
            raise ExpressionError(
                f"Unexpected character {expr[pos]!r}",
                expr=expr,
                position=pos,
            )

        pos = match.end()

        # Find the FIRST group that matched (not lastindex which gives the last match)
        group_idx: int | None = None
        for i in range(len(_TOKEN_TYPES)):
            if match.group(f"G{i}") is not None:
                group_idx = i
                break

        if group_idx is None:
            continue
        tok_type = _TOKEN_TYPES[group_idx]
        if tok_type is None:
            continue  # Whitespace

        raw_value: Any = match.group()
        value: Any = raw_value

        # Parse value based on token type
        if tok_type == TokenType.NUMBER:
            value = float(raw_value) if "." in raw_value else int(raw_value)
        elif tok_type == TokenType.STRING:
            value = value[1:-1]  # Strip quotes
            if len(value) > MAX_STRING_LENGTH:
                raise ExpressionError(
                    f"String literal exceeds {MAX_STRING_LENGTH} char limit "
                    f"({len(value)} chars).",
                    expr=expr,
                    position=match.start(),
                )
        elif tok_type == TokenType.TRUE:
            value = True
        elif tok_type == TokenType.FALSE:
            value = False
        elif tok_type == TokenType.NULL:
            value = None
        elif tok_type == TokenType.IDENT and value in FORBIDDEN_IDENTIFIERS:
            raise ExpressionError(
                f"Identifier '{value}' is forbidden.",
                expr=expr,
                position=match.start(),
            )

        tokens.append(Token(tok_type, value, match.start()))

    tokens.append(Token(TokenType.EOF, None, pos))
    return tokens


# ---------------------------------------------------------------------------
# AST Node types
# ---------------------------------------------------------------------------


@dataclass
class ASTNode:
    """Base class for expression AST nodes."""


@dataclass
class LiteralNode(ASTNode):
    """Integer, float, string, bool, or None literal."""
    value: Any


@dataclass
class FieldAccessNode(ASTNode):
    """Field path access like foo.bar.baz or foo.bar[0].

    segments: list of (field_name, optional_index_expression) pairs.
    For `_attack_roll.results[0]`:
        segments = [("_attack_roll", None), ("results", LiteralNode(0))]
    For `target.combat.hp`:
        segments = [("target", None), ("combat", None), ("hp", None)]
    """
    segments: list[tuple[str, ASTNode | None]]


@dataclass
class UnaryOpNode(ASTNode):
    """Unary negation or logical not."""
    operator: str  # '-' or 'not'
    operand: ASTNode


@dataclass
class BinaryOpNode(ASTNode):
    """Binary operation (arithmetic, comparison, logic, membership)."""
    operator: str  # '+', '-', '*', '//', '%', '**', '==', '!=', etc.
    left: ASTNode
    right: ASTNode


@dataclass
class FunctionCallNode(ASTNode):
    """Whitelisted pure function call."""
    name: str
    args: list[ASTNode]


# ---------------------------------------------------------------------------
# Recursive Descent Parser
# ---------------------------------------------------------------------------

# Grammar (operator precedence lowest → highest):
#
# expression   → logic_expr
# logic_expr   → comparison (("and" | "or") comparison)*
# comparison   → arithmetic (("==" | "!=" | ">=" | "<=" | ">" | "<") arithmetic)*
#              | arithmetic ("in" | "not" "in" | "contains") arithmetic
# arithmetic   → term (("+" | "-") term)*
# term         → power (("*" | "//" | "%") power)*
# power        → unary ("**" power)?     # right-associative
# unary        → ("not" | "-") unary
#              | primary
# primary      → NUMBER | STRING | true | false | null
#              | field_access
#              | "(" expression ")"
#              | function_call
# field_access → IDENT ("." IDENT | "[" expression "]")*
# function_call→ IDENT "(" (expression ("," expression)*)? ")"


_FUNCTION_WHITELIST: frozenset[str] = frozenset(
    {"max", "min", "abs", "floor", "ceil", "len", "merge"}
)


class Parser:
    """Recursive descent parser for the expression grammar."""

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0
        self._depth = 0

    def _enter(self) -> None:
        self._depth += 1
        if self._depth > MAX_NESTING_DEPTH:
            raise ExpressionError(
                f"Expression exceeds maximum nesting depth of {MAX_NESTING_DEPTH}."
            )

    def _leave(self) -> None:
        self._depth -= 1

    def parse(self) -> ASTNode:
        """Parse the full expression."""
        node = self._expression()
        self._expect(TokenType.EOF)
        return node

    def _peek(self) -> Token:
        return self.tokens[self.pos]

    def _advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _expect(self, typ: TokenType) -> Token:
        tok = self._advance()
        if tok.type != typ:
            raise ExpressionError(
                f"Expected {typ.name} but got {tok.type.name} ({tok.value!r})",
                position=tok.position,
            )
        return tok

    def _match(self, *types: TokenType) -> Token | None:
        tok = self._peek()
        if tok.type in types:
            return self._advance()
        return None

    # ── Grammar rules ──

    def _expression(self) -> ASTNode:
        self._enter()
        try:
            return self._logic_expr()
        finally:
            self._leave()

    def _logic_expr(self) -> ASTNode:
        left = self._comparison()
        while True:
            op = self._match(TokenType.AND, TokenType.OR)
            if op is None:
                break
            right = self._comparison()
            left = BinaryOpNode(operator=op.value, left=left, right=right)
        return left

    def _comparison(self) -> ASTNode:
        left = self._arithmetic()

        tok = self._peek()

        # Comparison operators
        if tok.type in (
            TokenType.EQ_EQ, TokenType.NOT_EQ, TokenType.GE,
            TokenType.LE, TokenType.GT, TokenType.LT,
        ):
            op = self._advance()
            right = self._arithmetic()
            return BinaryOpNode(operator=op.value, left=left, right=right)

        # "in" operator
        if tok.type == TokenType.IN:
            self._advance()
            right = self._arithmetic()
            return BinaryOpNode(operator="in", left=left, right=right)

        # "not in" operator
        if tok.type == TokenType.NOT and (self.pos + 1 < len(self.tokens)
                and self.tokens[self.pos + 1].type == TokenType.IN):
            self._advance()  # not
            self._advance()  # in
            right = self._arithmetic()
            return BinaryOpNode(operator="not_in", left=left, right=right)

        # "contains" operator
        if tok.type == TokenType.CONTAINS:
            self._advance()
            right = self._arithmetic()
            return BinaryOpNode(operator="contains", left=left, right=right)

        return left

    def _arithmetic(self) -> ASTNode:
        left = self._term()
        while True:
            op = self._match(TokenType.PLUS, TokenType.MINUS)
            if op is None:
                break
            right = self._term()
            left = BinaryOpNode(operator=op.value, left=left, right=right)
        return left

    def _term(self) -> ASTNode:
        left = self._power()
        while True:
            op = self._match(TokenType.STAR, TokenType.SLASH_SLASH, TokenType.PERCENT)
            if op is None:
                break
            right = self._power()
            left = BinaryOpNode(operator=op.value, left=left, right=right)
        return left

    def _power(self) -> ASTNode:
        left = self._unary()
        op = self._match(TokenType.STAR_STAR)
        if op:
            right = self._power()  # right-associative
            left = BinaryOpNode(operator="**", left=left, right=right)
        return left

    def _unary(self) -> ASTNode:
        op = self._match(TokenType.NOT, TokenType.MINUS)
        if op:
            operand = self._unary()
            return UnaryOpNode(operator=op.value, operand=operand)
        return self._primary()

    def _primary(self) -> ASTNode:
        self._enter()
        try:
            return self._primary_inner()
        finally:
            self._leave()

    def _primary_inner(self) -> ASTNode:
        tok = self._peek()

        # Parenthesized expression
        if tok.type == TokenType.LPAREN:
            self._advance()
            node = self._expression()
            self._expect(TokenType.RPAREN)
            return node

        # Literals
        if tok.type == TokenType.NUMBER:
            self._advance()
            return LiteralNode(value=tok.value)

        if tok.type == TokenType.STRING:
            self._advance()
            return LiteralNode(value=tok.value)

        if tok.type == TokenType.TRUE:
            self._advance()
            return LiteralNode(value=True)

        if tok.type == TokenType.FALSE:
            self._advance()
            return LiteralNode(value=False)

        if tok.type == TokenType.NULL:
            self._advance()
            return LiteralNode(value=None)

        # Field access or function call (starts with IDENT)
        if tok.type == TokenType.IDENT:
            ident_tok = self._advance()

            # Function call: IDENT "(" ...
            if self._peek().type == TokenType.LPAREN:
                return self._function_call(ident_tok.value)

            # Field access: start parsing segments
            return self._field_access(ident_tok.value)

        raise ExpressionError(
            f"Unexpected token {tok.type.name} ({tok.value!r})",
            position=tok.position,
        )

    def _field_access(self, first_part: str) -> ASTNode:
        """Parse field path: IDENT (. IDENT | [ expr ])*."""
        segments: list[tuple[str, ASTNode | None]] = [(first_part, None)]

        while True:
            tok = self._peek()

            if tok.type == TokenType.DOT:
                self._advance()
                ident_tok = self._expect(TokenType.IDENT)
                segments.append((ident_tok.value, None))

            elif tok.type == TokenType.LBRACKET:
                self._advance()
                index_node = self._expression()
                self._expect(TokenType.RBRACKET)
                # Replace last segment's index with this one
                last_name, _ = segments[-1]
                segments[-1] = (last_name, index_node)

                # After bracket, can continue with .more or [more]
                continue

            else:
                break

        return FieldAccessNode(segments=segments)

    def _function_call(self, func_name: str) -> ASTNode:
        """Parse function call: func_name(arg1, arg2, ...)."""
        if func_name not in _FUNCTION_WHITELIST:
            raise ExpressionError(
                f"'{func_name}' is not an available function. "
                f"Available: {', '.join(sorted(_FUNCTION_WHITELIST))}."
            )

        self._expect(TokenType.LPAREN)

        args: list[ASTNode] = []
        if self._peek().type != TokenType.RPAREN:
            args.append(self._expression())
            while self._match(TokenType.COMMA):
                args.append(self._expression())

        self._expect(TokenType.RPAREN)
        return FunctionCallNode(name=func_name, args=args)


# ---------------------------------------------------------------------------
# Convenience: parse string → AST
# ---------------------------------------------------------------------------


def parse_expression(expr: str) -> ASTNode:
    """Parse an expression string into an AST.

    Raises ExpressionError with position info on failure.

    >>> parse_expression("1 + 2")
    BinaryOpNode(operator='+', left=LiteralNode(1), right=LiteralNode(2))
    """
    tokens = tokenize(expr)
    parser = Parser(tokens)
    return parser.parse()


# ---------------------------------------------------------------------------
# Sandboxed function implementations (for the evaluator)
# ---------------------------------------------------------------------------

def _collection_length(value: object) -> int:
    """Count a data collection without accepting arbitrary objects with a length method."""
    if not isinstance(value, (list, tuple, dict, str)):
        raise TypeError("len() requires an array, object or string")
    return len(value)


def _merge_objects(original: Any, changes: Any) -> dict[str, Any]:
    """Return a shallow object update without mutating either expression input."""
    if not isinstance(original, dict) or not isinstance(changes, dict):
        raise ValueError("merge requires two objects")
    return {**original, **changes}


_SANDBOXED_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "max": max,
    "min": min,
    "abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
    "len": _collection_length,
    "merge": _merge_objects,
}
