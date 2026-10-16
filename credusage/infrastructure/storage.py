"""Private atomic JSON writes and strict typed, lossless serialization."""

from __future__ import annotations

import json
import os
import tempfile
import types
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Union, get_args, get_origin, get_type_hints

from credusage.domain.normalization import number, utc_timestamp

MAX_JSON_BYTES = 4 * 1024 * 1024


def to_data(value):
    if is_dataclass(value):
        return {f.name: to_data(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite decimal")
        return str(value)
    if isinstance(value, datetime):
        return utc_timestamp(value).isoformat()
    if isinstance(value, (tuple, list)):
        return [to_data(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_data(item) for key, item in value.items()}
    return value


def from_data(kind, value):
    origin, args = get_origin(kind), get_args(kind)
    if origin in (types.UnionType, Union):
        for candidate in args:
            try:
                return from_data(candidate, value)
            except (ValueError, TypeError, KeyError):
                continue
        raise ValueError("Invalid optional value")
    if kind is type(None):
        if value is not None:
            raise ValueError("Expected null")
        return None
    if origin is tuple:
        if not isinstance(value, list):
            raise ValueError("Expected list")
        return tuple(from_data(args[0], item) for item in value)
    if kind is Decimal:
        result = number(value, allow_negative=True)
        if result is None:
            raise ValueError("Expected decimal")
        return result
    if kind is datetime:
        result = utc_timestamp(value)
        if result is None:
            raise ValueError("Expected datetime")
        return result
    if isinstance(kind, type) and issubclass(kind, Enum):
        return kind(value)
    if isinstance(kind, type) and is_dataclass(kind):
        if not isinstance(value, dict):
            raise ValueError("Expected object")
        hints = get_type_hints(kind)
        allowed = {f.name for f in fields(kind)}
        if set(value) - allowed:
            raise ValueError("Unknown record fields")
        return kind(**{key: from_data(hints[key], item) for key, item in value.items()})
    if kind in (bool, int, str, float):
        if type(value) is not kind:
            raise ValueError("Wrong scalar type")
        if isinstance(value, str) and len(value) > 16384:
            raise ValueError("Oversized string")
        return value
    raise ValueError("Unsupported record type")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Refusing symlink destination")
    payload = json.dumps(to_data(value), ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path):
    if path.is_symlink() or path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("Unsafe or oversized JSON file")

    def reject_constant(_):
        raise ValueError("Non-finite JSON value")

    value = json.loads(path.read_text(), parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def quarantine(path: Path) -> None:
    if path.exists() and not path.is_symlink():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        os.replace(path, path.with_name(path.name + ".invalid-" + stamp))
