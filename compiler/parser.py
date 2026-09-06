from __future__ import annotations

from .nodes import (
    I32_MAX,
    AssignmentStatement,
    ArrayLiteral,
    BinaryExpression,
    BlockStatement,
    BreakStatement,
    BooleanLiteral,
    CallExpression,
    CastExpression,
    ContinueStatement,
    ConstDeclaration,
    Expression,
    Function,
    IdentifierExpression,
    IfExpression,
    EnumDeclaration,
    EnumVariant,
    EnumVariantExpression,
    MatchArm,
    MatchExpression,
    FieldAssignmentStatement,
    FieldExpression,
    IndexAssignmentStatement,
    IndexExpression,
    IntegerLiteral,
    LetStatement,
    Parameter,
    Program,
    ReturnStatement,
    Statement,
    UnaryExpression,
    StructDeclaration,
    StructField,
    StructLiteral,
    StructLiteralField,
    VarStatement,
    WhileStatement,
)
from .diagnostics import DiagnosticError
from .lexer import Token, TokenKind
from .stack import RECURSION_LIMIT_LOCK, reserved
from .types import INTEGER_TYPES, ArrayType, EnumType, ScalarType, StructType, TypeRef

_MAX_I32_DIGITS = len(str(I32_MAX))

# Recursive descent nests Python frames for calls and grouped expressions, so
# the accepted depth is bounded deliberately here rather than left to whatever
# the interpreter's stack happens to allow. Real source never approaches this;
# machine-generated source can, and must get a diagnostic instead of a crash.
MAX_EXPRESSION_DEPTH = 256
MAX_BLOCK_DEPTH = 256

# Python frames consumed per block level by the recursive statement walks in
# `semantic._analyze_statements` and `codegen._lower_statements`. Those walks run
# after parsing, so they reserve stack of their own;
# `test_frames_per_block_level_matches_the_statement_walkers` fails if this is
# stale, so their guard cannot silently stop working either.
FRAMES_PER_BLOCK_LEVEL = 1

# Python frames consumed per level of expression nesting: _expression through
# the precedence tiers to _primary, which then re-enters _expression. The count
# is measured by a mutation-resistant test. MAX_EXPRESSION_DEPTH is a
# language bound, but the promise attached to it — a diagnostic, never a
# RecursionError — only holds if the interpreter can actually reach that depth
# from wherever the caller happens to be. Adding a precedence tier raises this
# number; `test_frames_per_nesting_level_matches_the_parser` fails if it is
# stale, so the guard cannot silently stop working.
FRAMES_PER_LEVEL = 14

# Re-exported so the serialization guarantee is one lock across every phase that
# temporarily changes the interpreter-global recursion limit.
_RECURSION_LIMIT_LOCK = RECURSION_LIMIT_LOCK


class Parser:
    def __init__(self, tokens: list[Token], source: str):
        self.tokens = tokens
        self.source = source
        self.current = 0
        self.depth = 0
        self.block_depth = 0
        self.enum_names = {
            tokens[index + 1].lexeme
            for index, token in enumerate(tokens[:-1])
            if token.kind is TokenKind.ENUM and tokens[index + 1].kind is TokenKind.IDENTIFIER
        }

    def parse(self) -> Program:
        functions: list[Function] = []
        structures: list[StructDeclaration] = []
        enumerations: list[EnumDeclaration] = []
        constants: list[ConstDeclaration] = []
        while not self._at(TokenKind.EOF):
            if self._at(TokenKind.STRUCT):
                structures.append(self._struct_declaration())
            elif self._at(TokenKind.ENUM):
                enumerations.append(self._enum_declaration())
            elif self._at(TokenKind.CONST):
                constants.append(self._const_declaration())
            else:
                functions.append(self._function())
        return Program(tuple(functions), tuple(structures), tuple(enumerations), tuple(constants))

    def _const_declaration(self) -> ConstDeclaration:
        start = self._expect(TokenKind.CONST, "expected 'const'")
        name = self._expect(TokenKind.IDENTIFIER, "expected constant name")
        self._expect(TokenKind.COLON, "expected ':' after constant name")
        type_name = self._type_name("expected constant type")
        self._expect(TokenKind.EQUAL, "expected '=' before constant initializer")
        initializer = self._expression()
        self._expect(TokenKind.SEMICOLON, "expected ';' after constant declaration")
        return ConstDeclaration(name.lexeme, type_name, initializer, start.location)

    def _enum_declaration(self) -> EnumDeclaration:
        start = self._expect(TokenKind.ENUM, "expected 'enum'")
        name = self._expect(TokenKind.IDENTIFIER, "expected enumeration name")
        self._expect(TokenKind.LEFT_BRACE, "expected '{' after enumeration name")
        variants: list[EnumVariant] = []
        while not self._at(TokenKind.RIGHT_BRACE) and not self._at(TokenKind.EOF):
            variant = self._expect(TokenKind.IDENTIFIER, "expected enumeration variant")
            variants.append(EnumVariant(variant.lexeme, variant.location))
            if not self._match(TokenKind.COMMA):
                break
        self._expect(TokenKind.RIGHT_BRACE, "expected '}' after enumeration variants")
        return EnumDeclaration(name.lexeme, tuple(variants), start.location)

    def _struct_declaration(self) -> StructDeclaration:
        start = self._expect(TokenKind.STRUCT, "expected 'struct'")
        name = self._expect(TokenKind.IDENTIFIER, "expected structure name")
        self._expect(TokenKind.LEFT_BRACE, "expected '{' after structure name")
        fields: list[StructField] = []
        while not self._at(TokenKind.RIGHT_BRACE) and not self._at(TokenKind.EOF):
            field = self._expect(TokenKind.IDENTIFIER, "expected field name")
            self._expect(TokenKind.COLON, "expected ':' after field name")
            type_name = self._expect(TokenKind.IDENTIFIER, "expected field type")
            fields.append(StructField(field.lexeme, ScalarType(type_name.lexeme), field.location))
            if not self._match(TokenKind.COMMA):
                break
        self._expect(TokenKind.RIGHT_BRACE, "expected '}' after structure fields")
        return StructDeclaration(name.lexeme, tuple(fields), start.location)

    def _function(self) -> Function:
        start = self._expect(TokenKind.FN, "expected 'fn' to begin a function")
        name = self._expect(TokenKind.IDENTIFIER, "expected function name")
        self._expect(TokenKind.LEFT_PAREN, "expected '(' after function name")
        parameters = self._parameters()
        self._expect(TokenKind.RIGHT_PAREN, "expected ')' after parameters")
        self._expect(TokenKind.ARROW, "expected '->' before return type")
        return_type = self._expect(TokenKind.IDENTIFIER, "expected return type")
        body = self._block("expected '{' to begin function body")
        return Function(name.lexeme, self._named_type(return_type.lexeme), body.statements, start.location, tuple(parameters))

    def _block(self, message: str = "expected '{' to begin block") -> BlockStatement:
        start = self._expect(TokenKind.LEFT_BRACE, message)
        self.block_depth += 1
        if self.block_depth > MAX_BLOCK_DEPTH:
            self.block_depth -= 1
            raise DiagnosticError("E0102", f"block is nested too deeply; OCL 0.12 allows at most {MAX_BLOCK_DEPTH} levels", self.source, start.location)
        try:
            statements: list[Statement] = []
            while not self._at(TokenKind.RIGHT_BRACE) and not self._at(TokenKind.EOF):
                statements.append(self._statement())
            self._expect(TokenKind.RIGHT_BRACE, "expected '}' to close block")
            return BlockStatement(tuple(statements), start.location)
        finally:
            self.block_depth -= 1

    def _statement(self) -> Statement:
        if self._at(TokenKind.LET):
            return self._binding(False)
        if self._at(TokenKind.VAR):
            return self._binding(True)
        if self._at(TokenKind.RETURN):
            return self._return_statement()
        if self._at(TokenKind.BREAK) or self._at(TokenKind.CONTINUE):
            token = self.tokens[self.current]
            self.current += 1
            self._expect(TokenKind.SEMICOLON, f"expected ';' after '{token.lexeme}'")
            return BreakStatement(token.location) if token.kind is TokenKind.BREAK else ContinueStatement(token.location)
        if self._at(TokenKind.WHILE):
            start = self._expect(TokenKind.WHILE, "expected 'while'")
            condition = self._expression()
            return WhileStatement(condition, self._block("expected '{' after while condition"), start.location)
        if self._at(TokenKind.LEFT_BRACE):
            return self._block()
        name = self._expect(TokenKind.IDENTIFIER, "expected statement")
        field_name = None
        if self._match(TokenKind.DOT):
            field_name = self._expect(TokenKind.IDENTIFIER, "expected field name after '.'")
        index = None
        if field_name is None and self._match(TokenKind.LEFT_BRACKET):
            index = self._expression()
            self._expect(TokenKind.RIGHT_BRACKET, "expected ']' after array index")
        self._expect(TokenKind.EQUAL, "expected '=' in assignment")
        expression = self._expression()
        self._expect(TokenKind.SEMICOLON, "expected ';' after assignment")
        if field_name is not None:
            return FieldAssignmentStatement(name.lexeme, field_name.lexeme, expression, name.location)
        if index is not None:
            return IndexAssignmentStatement(name.lexeme, index, expression, name.location)
        return AssignmentStatement(name.lexeme, expression, name.location)

    def _let_statement(self) -> LetStatement:
        return self._binding(False)

    def _binding(self, mutable: bool) -> LetStatement | VarStatement:
        keyword = "var" if mutable else "let"
        self._expect(TokenKind.VAR if mutable else TokenKind.LET, f"expected '{keyword}'")
        name = self._expect(TokenKind.IDENTIFIER, f"expected local name after '{keyword}'")
        self._expect(TokenKind.COLON, "expected ':' after local name")
        type_name = self._type_name("expected local type")
        self._expect(TokenKind.EQUAL, "expected '=' before local initializer")
        initializer = self._expression()
        self._expect(TokenKind.SEMICOLON, "expected ';' after local binding")
        binding = VarStatement if mutable else LetStatement
        return binding(name.lexeme, type_name, initializer, name.location)

    def _type_name(self, message: str) -> TypeRef:
        if not self._match(TokenKind.LEFT_BRACKET):
            name = self._expect(TokenKind.IDENTIFIER, message).lexeme
            return self._named_type(name)
        element = self._expect(TokenKind.IDENTIFIER, "expected array element type")
        self._expect(TokenKind.SEMICOLON, "expected ';' before array length")
        length = self._expect(TokenKind.INTEGER, "expected array length")
        self._expect(TokenKind.RIGHT_BRACKET, "expected ']' after array type")
        significant = length.lexeme.lstrip("0") or "0"
        # The semantic implementation cap is 256. Avoid feeding an arbitrarily
        # long source numeral to int(), while still routing it to E0219.
        normalized = int(significant) if len(significant) <= 3 else 257
        return ArrayType(ScalarType(element.lexeme), normalized)

    def _parameters(self) -> list[Parameter]:
        parameters: list[Parameter] = []
        if self._at(TokenKind.RIGHT_PAREN):
            return parameters
        while True:
            name = self._expect(TokenKind.IDENTIFIER, "expected parameter name")
            self._expect(TokenKind.COLON, "expected ':' after parameter name")
            type_name = self._expect(TokenKind.IDENTIFIER, "expected parameter type")
            parameters.append(Parameter(name.lexeme, self._named_type(type_name.lexeme), name.location))
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
                f"expression is nested too deeply; OCL 0.12 allows at most {MAX_EXPRESSION_DEPTH} levels",
                self.source,
                self.tokens[self.current].location,
            )
        try:
            return self._logical_or()
        finally:
            self.depth -= 1

    def _logical_or(self) -> Expression:
        expression = self._logical_and()
        while self._match(TokenKind.OR_OR):
            operator = self.tokens[self.current - 1]
            expression = BinaryExpression(expression, operator.lexeme, self._logical_and(), operator.location)
        return expression

    def _logical_and(self) -> Expression:
        expression = self._equality()
        while self._match(TokenKind.AND_AND):
            operator = self.tokens[self.current - 1]
            expression = BinaryExpression(expression, operator.lexeme, self._equality(), operator.location)
        return expression

    def _bitwise_or(self) -> Expression:
        expression = self._bitwise_xor()
        while self._match(TokenKind.PIPE):
            operator = self.tokens[self.current - 1]
            expression = BinaryExpression(expression, operator.lexeme, self._bitwise_xor(), operator.location)
        return expression

    def _bitwise_xor(self) -> Expression:
        expression = self._bitwise_and()
        while self._match(TokenKind.CARET):
            operator = self.tokens[self.current - 1]
            expression = BinaryExpression(expression, operator.lexeme, self._bitwise_and(), operator.location)
        return expression

    def _bitwise_and(self) -> Expression:
        expression = self._comparison()
        while self._match(TokenKind.AMPERSAND):
            operator = self.tokens[self.current - 1]
            expression = BinaryExpression(expression, operator.lexeme, self._comparison(), operator.location)
        return expression

    def _equality(self) -> Expression:
        expression = self._bitwise_or()
        while self._match(TokenKind.EQUAL_EQUAL) or self._match(TokenKind.BANG_EQUAL):
            operator = self.tokens[self.current - 1]
            expression = BinaryExpression(expression, operator.lexeme, self._bitwise_or(), operator.location)
        return expression

    def _comparison(self) -> Expression:
        expression = self._shift()
        while (self._match(TokenKind.LESS) or self._match(TokenKind.LESS_EQUAL)
               or self._match(TokenKind.GREATER) or self._match(TokenKind.GREATER_EQUAL)):
            operator = self.tokens[self.current - 1]
            expression = BinaryExpression(expression, operator.lexeme, self._shift(), operator.location)
        return expression

    def _shift(self) -> Expression:
        expression = self._sum()
        while self._match(TokenKind.SHIFT_LEFT) or self._match(TokenKind.SHIFT_RIGHT):
            operator = self.tokens[self.current - 1]
            expression = BinaryExpression(expression, operator.lexeme, self._sum(), operator.location)
        return expression

    def _sum(self) -> Expression:
        expression = self._term()
        while self._match(TokenKind.PLUS) or self._match(TokenKind.MINUS):
            operator = self.tokens[self.current - 1]
            expression = BinaryExpression(expression, operator.lexeme, self._term(), operator.location)
        return expression

    def _term(self) -> Expression:
        expression = self._unary()
        while self._match(TokenKind.STAR) or self._match(TokenKind.SLASH) or self._match(TokenKind.PERCENT):
            operator = self.tokens[self.current - 1]
            expression = BinaryExpression(expression, operator.lexeme, self._unary(), operator.location)
        return expression

    def _unary(self) -> Expression:
        if self._match(TokenKind.BANG) or self._match(TokenKind.MINUS) or self._match(TokenKind.TILDE):
            operator = self.tokens[self.current - 1]
            self.depth += 1
            if self.depth > MAX_EXPRESSION_DEPTH:
                self.depth -= 1
                raise DiagnosticError(
                    "E0101",
                    f"expression is nested too deeply; OCL 0.12 allows at most {MAX_EXPRESSION_DEPTH} levels",
                    self.source,
                    operator.location,
                )
            try:
                if operator.kind is TokenKind.MINUS and self._at(TokenKind.INTEGER):
                    value = self._expect(TokenKind.INTEGER, "expected integer literal")
                    significant = value.lexeme.lstrip("0") or "0"
                    if len(significant) > len(str(I32_MAX + 1)) or int(significant) > I32_MAX + 1:
                        raise DiagnosticError("E0203", "integer literal does not fit in i32", self.source, value.location)
                    return self._postfix(IntegerLiteral(-int(significant), operator.location))
                return UnaryExpression(operator.lexeme, self._unary(), operator.location)
            finally:
                self.depth -= 1
        return self._postfix(self._primary())

    def _postfix(self, expression: Expression) -> Expression:
        while True:
            if self._match(TokenKind.LEFT_BRACKET):
                start = self.tokens[self.current - 1]
                index = self._expression()
                self._expect(TokenKind.RIGHT_BRACKET, "expected ']' after array index")
                expression = IndexExpression(expression, index, start.location)
                continue
            if self._match(TokenKind.DOT):
                start = self.tokens[self.current - 1]
                field = self._expect(TokenKind.IDENTIFIER, "expected field name after '.'")
                expression = FieldExpression(expression, field.lexeme, start.location)
                continue
            if self._match(TokenKind.AS):
                start = self.tokens[self.current - 1]
                target = self._expect(TokenKind.IDENTIFIER, "expected integer type after 'as'")
                expression = CastExpression(expression, self._named_type(target.lexeme), start.location)
                continue
            break
        return expression

    def _primary(self) -> Expression:
        if self._at(TokenKind.MATCH):
            return self._match_expression()
        if self._at(TokenKind.TRUE) or self._at(TokenKind.FALSE):
            value = self.tokens[self.current]
            self.current += 1
            return BooleanLiteral(value.kind is TokenKind.TRUE, value.location)
        if self._at(TokenKind.INTEGER):
            value = self._expect(TokenKind.INTEGER, "expected integer literal")
            return self._integer(value)
        if self._match(TokenKind.LEFT_BRACKET):
            start = self.tokens[self.current - 1]
            elements: list[Expression] = []
            if not self._at(TokenKind.RIGHT_BRACKET):
                while True:
                    elements.append(self._expression())
                    if not self._match(TokenKind.COMMA):
                        break
                    if self._at(TokenKind.RIGHT_BRACKET):
                        raise DiagnosticError("E0100", "expected array element after ','", self.source, self.tokens[self.current].location)
            self._expect(TokenKind.RIGHT_BRACKET, "expected ']' after array literal")
            return ArrayLiteral(tuple(elements), start.location)
        if self._at(TokenKind.IDENTIFIER):
            name = self._expect(TokenKind.IDENTIFIER, "expected identifier")
            if name.lexeme in self.enum_names and self._match(TokenKind.DOT):
                variant = self._expect(TokenKind.IDENTIFIER, "expected enumeration variant after '.'")
                return EnumVariantExpression(name.lexeme, variant.lexeme, name.location)
            # A named-field literal is distinguishable from the block that
            # follows an `if`/`while` condition by its `field:` prefix.
            if (self._at(TokenKind.LEFT_BRACE)
                    and self.current + 2 < len(self.tokens)
                    and self.tokens[self.current + 1].kind is TokenKind.IDENTIFIER
                    and self.tokens[self.current + 2].kind is TokenKind.COLON):
                self.current += 1
                fields: list[StructLiteralField] = []
                if not self._at(TokenKind.RIGHT_BRACE):
                    while True:
                        field = self._expect(TokenKind.IDENTIFIER, "expected field name in structure literal")
                        self._expect(TokenKind.COLON, "expected ':' after structure literal field")
                        fields.append(StructLiteralField(field.lexeme, self._expression(), field.location))
                        if not self._match(TokenKind.COMMA):
                            break
                        if self._at(TokenKind.RIGHT_BRACE):
                            break
                self._expect(TokenKind.RIGHT_BRACE, "expected '}' after structure literal")
                return StructLiteral(StructType(name.lexeme), tuple(fields), name.location)
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
        if self._at(TokenKind.IF):
            return self._if_expression()
        token = self.tokens[self.current]
        raise DiagnosticError("E0100", "expected expression", self.source, token.location)

    def _match_expression(self) -> MatchExpression:
        start = self._expect(TokenKind.MATCH, "expected 'match'")
        scrutinee = self._expression()
        self._expect(TokenKind.LEFT_BRACE, "expected '{' before match arms")
        arms: list[MatchArm] = []
        while not self._at(TokenKind.RIGHT_BRACE) and not self._at(TokenKind.EOF):
            enum_name = self._expect(TokenKind.IDENTIFIER, "expected enumeration name in match arm")
            self._expect(TokenKind.DOT, "expected '.' in match arm")
            variant = self._expect(TokenKind.IDENTIFIER, "expected enumeration variant in match arm")
            self._expect(TokenKind.FAT_ARROW, "expected '=>' in match arm")
            arms.append(MatchArm(enum_name.lexeme, variant.lexeme, self._expression(), enum_name.location))
            if not self._match(TokenKind.COMMA):
                break
        self._expect(TokenKind.RIGHT_BRACE, "expected '}' after match arms")
        return MatchExpression(scrutinee, tuple(arms), start.location)

    def _named_type(self, name: str) -> TypeRef:
        if name == "bool" or name in INTEGER_TYPES:
            return ScalarType(name)
        if name in self.enum_names:
            return EnumType(name)
        return StructType(name)

    def _if_expression(self) -> IfExpression:
        start = self._expect(TokenKind.IF, "expected 'if'")
        condition = self._expression()
        self._expect(TokenKind.LEFT_BRACE, "expected '{' before if branch")
        then_expression = self._expression()
        self._expect(TokenKind.RIGHT_BRACE, "expected '}' after if branch")
        self._expect(TokenKind.ELSE, "expected 'else' after if branch")
        self._expect(TokenKind.LEFT_BRACE, "expected '{' before else branch")
        else_expression = self._expression()
        self._expect(TokenKind.RIGHT_BRACE, "expected '}' after else branch")
        return IfExpression(condition, then_expression, else_expression, start.location)

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
    """Parse a token stream, guaranteeing E0101 rather than a RecursionError.

    The depth guard is expressed in parser levels, but it is enforced by Python
    frames. A caller that is already deep — an embedding tool, a language server,
    a future self-hosted driver — would otherwise exhaust the stack before the
    guard could fire. Reserving the frames the bound actually needs keeps the
    documented limit deterministic instead of dependent on the call site.
    """
    with reserved(MAX_EXPRESSION_DEPTH * FRAMES_PER_LEVEL):
        return Parser(tokens, source).parse()
