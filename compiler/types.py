from __future__ import annotations


INTEGER_TYPES = frozenset(("i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64"))


def is_integer(type_name) -> bool:
    return str(type_name) in INTEGER_TYPES


def integer_width(type_name) -> int:
    if not is_integer(type_name):
        raise ValueError(f"not an integer type: {type_name}")
    return int(str(type_name)[1:])


def integer_is_signed(type_name) -> bool:
    if not is_integer(type_name):
        raise ValueError(f"not an integer type: {type_name}")
    return str(type_name).startswith("i")


def integer_minimum(type_name) -> int:
    width = integer_width(type_name)
    return -(1 << (width - 1)) if integer_is_signed(type_name) else 0


def integer_maximum(type_name) -> int:
    width = integer_width(type_name)
    return (1 << (width - 1)) - 1 if integer_is_signed(type_name) else (1 << width) - 1


def wrap_integer(value: int, type_name) -> int:
    width = integer_width(type_name)
    value &= (1 << width) - 1
    if integer_is_signed(type_name) and value >= (1 << (width - 1)):
        value -= 1 << width
    return value


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


class EnumType(TypeRef):
    def __new__(cls, name: str):
        value = str.__new__(cls, name)
        value.name = name
        return value
