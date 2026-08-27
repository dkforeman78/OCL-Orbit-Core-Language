from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .diagnostics import DiagnosticError, SourceLocation


class TokenKind(Enum):
    FN = auto()
    IF = auto()
    ELSE = auto()
    LET = auto()
    RETURN = auto()
    TRUE = auto()
    FALSE = auto()
    IDENTIFIER = auto()
    INTEGER = auto()
    LEFT_PAREN = auto()
    RIGHT_PAREN = auto()
    LEFT_BRACE = auto()
    RIGHT_BRACE = auto()
    ARROW = auto()
    COLON = auto()
    COMMA = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    EQUAL = auto()
    EQUAL_EQUAL = auto()
    BANG_EQUAL = auto()
    LESS = auto()
    LESS_EQUAL = auto()
    GREATER = auto()
    GREATER_EQUAL = auto()
    SEMICOLON = auto()
    EOF = auto()


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    lexeme: str
    location: SourceLocation


def lex(source: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    line = 1
    column = 1

    def location() -> SourceLocation:
        return SourceLocation(index, line, column)

    while index < len(source):
        char = source[index]
        if char in " \t\r":
            index += 1
            column += 1
            continue
        if char == "\n":
            index += 1
            line += 1
            column = 1
            continue

        start = location()
        compound = {
            "->": TokenKind.ARROW,
            "==": TokenKind.EQUAL_EQUAL,
            "!=": TokenKind.BANG_EQUAL,
            "<=": TokenKind.LESS_EQUAL,
            ">=": TokenKind.GREATER_EQUAL,
        }
        pair = source[index:index + 2]
        if pair in compound:
            tokens.append(Token(compound[pair], pair, start))
            index += 2
            column += 2
            continue
        single = {
            "(": TokenKind.LEFT_PAREN,
            ")": TokenKind.RIGHT_PAREN,
            "{": TokenKind.LEFT_BRACE,
            "}": TokenKind.RIGHT_BRACE,
            ":": TokenKind.COLON,
            ",": TokenKind.COMMA,
            "+": TokenKind.PLUS,
            "-": TokenKind.MINUS,
            "*": TokenKind.STAR,
            "=": TokenKind.EQUAL,
            "<": TokenKind.LESS,
            ">": TokenKind.GREATER,
            ";": TokenKind.SEMICOLON,
        }
        if char in single:
            tokens.append(Token(single[char], char, start))
            index += 1
            column += 1
            continue
        if char.isascii() and (char.isalpha() or char == "_"):
            end = index + 1
            while end < len(source) and source[end].isascii() and (source[end].isalnum() or source[end] == "_"):
                end += 1
            word = source[index:end]
            kind = {
                "else": TokenKind.ELSE,
                "false": TokenKind.FALSE,
                "fn": TokenKind.FN,
                "if": TokenKind.IF,
                "let": TokenKind.LET,
                "return": TokenKind.RETURN,
                "true": TokenKind.TRUE,
            }.get(word, TokenKind.IDENTIFIER)
            tokens.append(Token(kind, word, start))
            column += end - index
            index = end
            continue
        if char.isascii() and char.isdigit():
            end = index + 1
            while end < len(source) and source[end].isascii() and source[end].isdigit():
                end += 1
            tokens.append(Token(TokenKind.INTEGER, source[index:end], start))
            column += end - index
            index = end
            continue
        raise DiagnosticError("E0001", f"invalid token {char!r}", source, start)

    tokens.append(Token(TokenKind.EOF, "", location()))
    return tokens
