from __future__ import annotations

import sys
import threading

from .nodes import (
    I32_MAX,
    BinaryExpression,
    CallExpression,
    Expression,
    Function,
    IdentifierExpression,
    IntegerLiteral,
    LetStatement,
    Parameter,
    Program,
    ReturnStatement,
)
from .diagnostics import DiagnosticError
from .lexer import Token, TokenKind

_MAX_I32_DIGITS = len(str(I32_MAX))

# Recursive descent nests Python frames for calls and grouped expressions, so
# the accepted depth is bounded deliberately here rather than left to whatever
# the interpreter's stack happens to allow. Real source never approaches this;
# machine-generated source can, and must get a diagnostic instead of a crash.
MAX_EXPRESSION_DEPTH = 256

# Python frames consumed per level of expression nesting: _expression -> _term
# -> _primary, which then re-enters _expression. MAX_EXPRESSION_DEPTH is a
# language bound, but the promise attached to it — a diagnostic, never a
# RecursionError — only holds if the interpreter can actually reach that depth
# from wherever the caller happens to be. Adding a precedence tier raises this
# number; `test_frames_per_nesting_level_matches_the_parser` fails if it is
# stale, so the guard cannot silently stop working.
FRAMES_PER_LEVEL = 3

# Slack for the driver, the CLI and the tail of _primary beyond the recursive call.
_STACK_MARGIN = 96

# sys.setrecursionlimit affects the whole interpreter. Serialize parse calls so
# overlapping compiler invocations cannot restore each other's saved limits out
# of order. RLock also keeps a future same-thread nested parse from deadlocking.
_RECURSION_LIMIT_LOCK = threading.RLock()


class Parser:
    def __init__(self, tokens: list[Token], source: str):
        self.tokens = tokens
        self.source = source
        self.current = 0
        self.depth = 0

    def parse(self) -> Program:
        functions: list[Function] = []
        while not self._at(TokenKind.EOF):
            functions.append(self._function())
        return Program(tuple(functions))

    def _function(self) -> Function:
        start = self._expect(TokenKind.FN, "expected 'fn' to begin a function")
        name = self._expect(TokenKind.IDENTIFIER, "expected function name")
        self._expect(TokenKind.LEFT_PAREN, "expected '(' after function name")
        parameters = self._parameters()
        self._expect(TokenKind.RIGHT_PAREN, "expected ')' after parameters")
        self._expect(TokenKind.ARROW, "expected '->' before return type")
        return_type = self._expect(TokenKind.IDENTIFIER, "expected return type")
        self._expect(TokenKind.LEFT_BRACE, "expected '{' to begin function body")
        statements: list[LetStatement | ReturnStatement] = []
        while not self._at(TokenKind.RIGHT_BRACE) and not self._at(TokenKind.EOF):
            if self._at(TokenKind.LET):
                statements.append(self._let_statement())
            else:
                statements.append(self._return_statement())
        self._expect(TokenKind.RIGHT_BRACE, "expected '}' to close function body")
        return Function(name.lexeme, return_type.lexeme, tuple(statements), start.location, tuple(parameters))

    def _let_statement(self) -> LetStatement:
        self._expect(TokenKind.LET, "expected 'let'")
        name = self._expect(TokenKind.IDENTIFIER, "expected local name after 'let'")
        self._expect(TokenKind.COLON, "expected ':' after local name")
        type_name = self._expect(TokenKind.IDENTIFIER, "expected local type")
        self._expect(TokenKind.EQUAL, "expected '=' before local initializer")
        initializer = self._expression()
        self._expect(TokenKind.SEMICOLON, "expected ';' after local binding")
        return LetStatement(name.lexeme, type_name.lexeme, initializer, name.location)

    def _parameters(self) -> list[Parameter]:
        parameters: list[Parameter] = []
        if self._at(TokenKind.RIGHT_PAREN):
            return parameters
        while True:
            name = self._expect(TokenKind.IDENTIFIER, "expected parameter name")
            self._expect(TokenKind.COLON, "expected ':' after parameter name")
            type_name = self._expect(TokenKind.IDENTIFIER, "expected parameter type")
            parameters.append(Parameter(name.lexeme, type_name.lexeme, name.location))
            if not self._match(TokenKind.COMMA):
                return parameters
            if self._at(TokenKind.RIGHT_PAREN):
                raise DiagnosticError("E0100", "expected parameter after ','", self.source, self.tokens[self.current].location)

    def _return_statement(self) -> ReturnStatement:
        start = self._expect(TokenKind.RETURN, "expected 'return' statement")
        expression = self._expression()
        self._expect(TokenKind.SEMICOLON, "expected ';' after return value")
        return ReturnStatement(expression, start.location)

    def _expression(self) -> Expression:
        self.depth += 1
        if self.depth > MAX_EXPRESSION_DEPTH:
            raise DiagnosticError(
                "E0101",
                f"expression is nested too deeply; OCL 0.3 allows at most {MAX_EXPRESSION_DEPTH} levels",
                self.source,
                self.tokens[self.current].location,
            )
        try:
            expression = self._term()
            while self._match(TokenKind.PLUS) or self._match(TokenKind.MINUS):
                operator = self.tokens[self.current - 1]
                expression = BinaryExpression(expression, operator.lexeme, self._term(), operator.location)
            return expression
        finally:
            self.depth -= 1

    def _term(self) -> Expression:
        expression = self._primary()
        while self._match(TokenKind.STAR):
            operator = self.tokens[self.current - 1]
            expression = BinaryExpression(expression, operator.lexeme, self._primary(), operator.location)
        return expression

    def _primary(self) -> Expression:
        if self._at(TokenKind.INTEGER):
            value = self._expect(TokenKind.INTEGER, "expected integer literal")
            return self._integer(value)
        if self._at(TokenKind.IDENTIFIER):
            name = self._expect(TokenKind.IDENTIFIER, "expected identifier")
            if not self._match(TokenKind.LEFT_PAREN):
                return IdentifierExpression(name.lexeme, name.location)
            arguments: list[Expression] = []
            if not self._at(TokenKind.RIGHT_PAREN):
                while True:
                    arguments.append(self._expression())
                    if not self._match(TokenKind.COMMA):
                        break
                    if self._at(TokenKind.RIGHT_PAREN):
                        raise DiagnosticError("E0100", "expected argument after ','", self.source, self.tokens[self.current].location)
            self._expect(TokenKind.RIGHT_PAREN, "expected ')' after arguments")
            return CallExpression(name.lexeme, tuple(arguments), name.location)
        if self._match(TokenKind.LEFT_PAREN):
            expression = self._expression()
            self._expect(TokenKind.RIGHT_PAREN, "expected ')' after parenthesized expression")
            return expression
        token = self.tokens[self.current]
        raise DiagnosticError("E0100", "expected expression", self.source, token.location)

    def _integer(self, value: Token) -> IntegerLiteral:
        # Reject over-long literals before int() runs: CPython refuses to convert
        # strings past its digit limit, and that must not surface as a crash.
        significant = value.lexeme.lstrip("0") or "0"
        if len(significant) > _MAX_I32_DIGITS:
            raise DiagnosticError("E0203", "integer literal does not fit in i32", self.source, value.location)
        return IntegerLiteral(int(significant), value.location)

    def _at(self, kind: TokenKind) -> bool:
        return self.tokens[self.current].kind is kind

    def _expect(self, kind: TokenKind, message: str) -> Token:
        token = self.tokens[self.current]
        if token.kind is not kind:
            raise DiagnosticError("E0100", message, self.source, token.location)
        self.current += 1
        return token

    def _match(self, kind: TokenKind) -> bool:
        if not self._at(kind):
            return False
        self.current += 1
        return True


def _stack_depth() -> int:
    depth = 0
    frame: object = sys._getframe()
    while frame is not None:
        depth += 1
        frame = frame.f_back
    return depth


def parse(tokens: list[Token], source: str) -> Program:
    """Parse a token stream, guaranteeing E0101 rather than a RecursionError.

    The depth guard is expressed in parser levels, but it is enforced by Python
    frames. A caller that is already deep — an embedding tool, a language server,
    a future self-hosted driver — would otherwise exhaust the stack before the
    guard could fire. Reserving the frames the bound actually needs keeps the
    documented limit deterministic instead of dependent on the call site.
    """
    with _RECURSION_LIMIT_LOCK:
        required = MAX_EXPRESSION_DEPTH * FRAMES_PER_LEVEL + _STACK_MARGIN
        previous_limit = sys.getrecursionlimit()
        stack_depth = _stack_depth()
        if previous_limit - stack_depth < required:
            sys.setrecursionlimit(stack_depth + required)
        try:
            return Parser(tokens, source).parse()
        finally:
            sys.setrecursionlimit(previous_limit)
