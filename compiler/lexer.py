from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .diagnostics import DiagnosticError, SourceLocation


class TokenKind(Enum):
    FN = auto()
    RETURN = auto()
    IDENTIFIER = auto()
    INTEGER = auto()
    LEFT_PAREN = auto()
    RIGHT_PAREN = auto()
    LEFT_BRACE = auto()
    RIGHT_BRACE = auto()
    ARROW = auto()
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
        single = {
            "(": TokenKind.LEFT_PAREN,
            ")": TokenKind.RIGHT_PAREN,
            "{": TokenKind.LEFT_BRACE,
            "}": TokenKind.RIGHT_BRACE,
            ";": TokenKind.SEMICOLON,
        }
        if char in single:
            tokens.append(Token(single[char], char, start))
            index += 1
            column += 1
            continue
        if source.startswith("->", index):
            tokens.append(Token(TokenKind.ARROW, "->", start))
            index += 2
            column += 2
            continue
        if char.isascii() and (char.isalpha() or char == "_"):
            end = index + 1
            while end < len(source) and source[end].isascii() and (source[end].isalnum() or source[end] == "_"):
                end += 1
            word = source[index:end]
            kind = {"fn": TokenKind.FN, "return": TokenKind.RETURN}.get(word, TokenKind.IDENTIFIER)
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
