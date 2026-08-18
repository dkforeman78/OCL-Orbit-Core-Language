from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceLocation:
    offset: int
    line: int
    column: int


class DiagnosticError(Exception):
    def __init__(self, code: str, message: str, source: str, location: SourceLocation):
        self.code = code
        self.message = message
        self.source = source
        self.location = location
        super().__init__(message)

    def render(self, filename: str) -> str:
        lines = self.source.splitlines()
        text = lines[self.location.line - 1] if self.location.line <= len(lines) else ""
        gutter = str(self.location.line)
        caret = " " * (self.location.column - 1) + "^"
        return (
            f"error[{self.code}]: {self.message}\n"
            f" --> {filename}:{self.location.line}:{self.location.column}\n\n"
            f"{gutter} | {text}\n"
            f"{' ' * len(gutter)} | {caret}"
        )
