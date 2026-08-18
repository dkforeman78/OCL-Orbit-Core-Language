from __future__ import annotations

from .ast import I32_MAX, Function, IntegerLiteral, Program, ReturnStatement
from .diagnostics import DiagnosticError
from .lexer import Token, TokenKind

_MAX_I32_DIGITS = len(str(I32_MAX))


class Parser:
    def __init__(self, tokens: list[Token], source: str):
        self.tokens = tokens
        self.source = source
        self.current = 0

    def parse(self) -> Program:
        functions: list[Function] = []
        while not self._at(TokenKind.EOF):
            functions.append(self._function())
        return Program(tuple(functions))

    def _function(self) -> Function:
        start = self._expect(TokenKind.FN, "expected 'fn' to begin a function")
        name = self._expect(TokenKind.IDENTIFIER, "expected function name")
        self._expect(TokenKind.LEFT_PAREN, "expected '(' after function name")
        self._expect(TokenKind.RIGHT_PAREN, "expected ')' (parameters are not supported in OCL 0.1)")
        self._expect(TokenKind.ARROW, "expected '->' before return type")
        return_type = self._expect(TokenKind.IDENTIFIER, "expected return type")
        self._expect(TokenKind.LEFT_BRACE, "expected '{' to begin function body")
        statements: list[ReturnStatement] = []
        while not self._at(TokenKind.RIGHT_BRACE) and not self._at(TokenKind.EOF):
            statements.append(self._return_statement())
        self._expect(TokenKind.RIGHT_BRACE, "expected '}' to close function body")
        return Function(name.lexeme, return_type.lexeme, tuple(statements), start.location)

    def _return_statement(self) -> ReturnStatement:
        start = self._expect(TokenKind.RETURN, "expected 'return' statement")
        value = self._expect(TokenKind.INTEGER, "expected integer literal after 'return'")
        self._expect(TokenKind.SEMICOLON, "expected ';' after return value")
        # Reject over-long literals before int() runs: CPython refuses to convert
        # strings past its digit limit, and that must not surface as a crash.
        if len(value.lexeme.lstrip("0")) > _MAX_I32_DIGITS:
            raise DiagnosticError("E0203", "integer literal does not fit in i32", self.source, value.location)
        return ReturnStatement(IntegerLiteral(int(value.lexeme), value.location), start.location)

    def _at(self, kind: TokenKind) -> bool:
        return self.tokens[self.current].kind is kind

    def _expect(self, kind: TokenKind, message: str) -> Token:
        token = self.tokens[self.current]
        if token.kind is not kind:
            raise DiagnosticError("E0100", message, self.source, token.location)
        self.current += 1
        return token


def parse(tokens: list[Token], source: str) -> Program:
    return Parser(tokens, source).parse()
