from __future__ import annotations

from .nodes import (
    I32_MAX,
    BinaryExpression,
    CallExpression,
    Expression,
    Function,
    IdentifierExpression,
    IntegerLiteral,
    Parameter,
    Program,
    ReturnStatement,
)
from .diagnostics import DiagnosticError
from .lexer import Token, TokenKind

_MAX_I32_DIGITS = len(str(I32_MAX))

# Recursive descent nests one Python frame per level of parenthesised call, so
# the accepted depth is bounded deliberately here rather than left to whatever
# the interpreter's stack happens to allow. Real source never approaches this;
# machine-generated source can, and must get a diagnostic instead of a crash.
MAX_EXPRESSION_DEPTH = 256


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
        statements: list[ReturnStatement] = []
        while not self._at(TokenKind.RIGHT_BRACE) and not self._at(TokenKind.EOF):
            statements.append(self._return_statement())
        self._expect(TokenKind.RIGHT_BRACE, "expected '}' to close function body")
        return Function(name.lexeme, return_type.lexeme, tuple(statements), start.location, tuple(parameters))

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
                f"expression is nested too deeply; OCL 0.2 allows at most {MAX_EXPRESSION_DEPTH} levels",
                self.source,
                self.tokens[self.current].location,
            )
        try:
            # Addition is folded iteratively, so a long chain costs no depth;
            # only nested calls actually nest.
            expression = self._primary()
            while self._match(TokenKind.PLUS):
                operator = self.tokens[self.current - 1]
                expression = BinaryExpression(expression, operator.lexeme, self._primary(), operator.location)
            return expression
        finally:
            self.depth -= 1

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
        token = self.tokens[self.current]
        raise DiagnosticError("E0100", "expected expression after 'return'", self.source, token.location)

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


def parse(tokens: list[Token], source: str) -> Program:
    return Parser(tokens, source).parse()
