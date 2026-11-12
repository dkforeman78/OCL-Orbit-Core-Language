from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceLocation:
    offset: int
    line: int
    column: int


class InternalCompilerError(Exception):
    """A compiler invariant was violated, not a defect in the user's source."""


class DiagnosticError(Exception):
    def __init__(self, code: str, message: str, source: str, location: SourceLocation):
        self.code = code
        self.message = message
        self.source = source
        self.location = location
        super().__init__(message)

    def render(self, filename: str) -> str:
        text = self.source_line()
        prefix = text[:max(0, self.location.column - 1)].expandtabs(4)
        text = text.expandtabs(4)
        gutter = str(self.location.line)
        caret = " " * len(prefix) + "^"
        return (
            f"error[{self.code}]: {self.message}\n"
            f" --> {filename}:{self.location.line}:{self.location.column}\n\n"
            f"{gutter} | {text}\n"
            f"{' ' * len(gutter)} | {caret}"
        )

    def source_line(self) -> str:
        # Match the lexer's LF-only line accounting; Unicode separators inside
        # comments are ordinary characters, not extra source lines.
        lines = self.source.split("\n")
        return lines[self.location.line - 1].rstrip("\r") if 1 <= self.location.line <= len(lines) else ""

    def as_dict(self, filename: str) -> dict:
        return diagnostic_record(
            "source", self.message, code=self.code, filename=filename,
            location={"offset": self.location.offset, "line": self.location.line,
                      "column": self.location.column}, source_line=self.source_line(),
        )


def diagnostic_record(kind: str, message: str, *, code=None, filename=None,
                      location=None, source_line=None) -> dict:
    return {"schema_version": 1, "severity": "error", "kind": kind,
            "code": code, "message": message, "file": filename,
            "location": location, "source_line": source_line}
