from __future__ import annotations


class TypeRef(str):
    """Structured type identity with string compatibility for the 0.x AST API."""


class ScalarType(TypeRef):
    def __new__(cls, name: str):
        value = str.__new__(cls, name)
        value.name = name
        return value


class ArrayType(TypeRef):
    def __new__(cls, element: TypeRef, length: int):
        value = str.__new__(cls, f"[{element}; {length}]")
        value.element = element
        value.length = length
        return value


class StructType(TypeRef):
    def __new__(cls, name: str):
        value = str.__new__(cls, name)
        value.name = name
        return value
