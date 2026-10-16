"""Validate every accepted observation, including unpersisted local readings."""

import re
from datetime import datetime
from decimal import Decimal

from .models import Unit, UsageSnapshot


def _text(value, *, maximum=256):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("Invalid display or identity field")
    if any(ord(char) < 32 for char in value):
        raise ValueError("Control character in display or identity field")


def _timestamp(value):
    if value is not None and (
        not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
    ):
        raise ValueError("Timezone-aware timestamp required")


def validate_snapshot(snapshot: UsageSnapshot) -> None:
    _text(snapshot.connection_id)
    _text(snapshot.integration_id)
    _timestamp(snapshot.collected_at)
    if type(snapshot.generation) is not int or snapshot.generation < 0:
        raise ValueError("Invalid connection generation")
    if len(snapshot.observations) > 1024:
        raise ValueError("Too many quota buckets")
    keys = set()
    for item in snapshot.observations:
        _text(item.bucket_key, maximum=512)
        _text(item.label)
        _text(item.provenance.source_id)
        version = item.provenance.tool_version
        if version is not None and (
            not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
        ):
            raise ValueError("Invalid tool version")
        if item.bucket_key in keys:
            raise ValueError("Duplicate quota bucket")
        keys.add(item.bucket_key)
        if (
            item.connection_id != snapshot.connection_id
            or item.generation != snapshot.generation
            or item.binding != snapshot.binding
        ):
            raise ValueError("Observation binding mismatch")
        for name in ("used", "limit", "remaining", "used_percent"):
            value = getattr(item, name)
            negative_allowed = name == "remaining" and item.unit in (Unit.CREDITS, Unit.CURRENCY)
            if value is not None and (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or (value < 0 and not negative_allowed)
            ):
                raise ValueError("Invalid quota quantity")
        for value in (
            item.provenance.fetched_at,
            item.provenance.event_at,
            item.fresh_until,
            item.window.starts_at,
            item.window.ends_at,
            item.window.resets_at,
        ):
            _timestamp(value)
        if item.unit == Unit.CURRENCY:
            _text(item.currency, maximum=16)
        if item.shared_pool_id:
            _text(item.shared_pool_id)
            _text(item.sharing_evidence, maximum=2048)
        for model in item.model_ids:
            _text(model)
