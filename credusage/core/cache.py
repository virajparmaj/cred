"""Whole-observation merging with explicit completeness and account isolation."""

from dataclasses import replace

from credusage.domain.contracts import CollectionResult, Completeness
from credusage.domain.models import UsageSnapshot


def merge_snapshot(previous, result: CollectionResult, integration_id, collected_at):
    same_context = (
        previous is not None
        and result.binding.is_verified
        and previous.binding == result.binding
        and previous.generation == result.generation
        and previous.source_epoch == result.source_epoch
    )
    if result.completeness == Completeness.UNCHANGED:
        return previous if same_context else None
    existing = {item.bucket_key: item for item in previous.observations} if same_context else {}
    merged = existing.copy() if result.completeness == Completeness.PARTIAL else {}
    incoming = set()
    for item in result.observations:
        if item.bucket_key in incoming:
            raise ValueError("Duplicate incoming bucket")
        incoming.add(item.bucket_key)
        if (
            item.connection_id != result.connection_id
            or item.generation != result.generation
            or item.binding != result.binding
        ):
            raise ValueError("Mismatched observation identity")
        older = existing.get(item.bucket_key)
        if older is not None:
            if older.unit != item.unit or older.metric != item.metric:
                raise ValueError("Incompatible bucket units")
            if (older.provenance.event_at or older.provenance.fetched_at) > (
                item.provenance.event_at or item.provenance.fetched_at
            ):
                merged[item.bucket_key] = older
                continue
        merged[item.bucket_key] = item
    for key in result.removed_keys:
        merged.pop(key, None)
    return UsageSnapshot(
        result.connection_id,
        integration_id,
        result.generation,
        result.binding,
        tuple(merged.values()),
        collected_at,
        result.source_epoch,
        result.plan,
        result.capabilities,
    )


def mark_historical(snapshot, now):
    if snapshot is None:
        return None
    observations = tuple(
        replace(item, fresh_until=min(item.fresh_until, now) if item.fresh_until else None)
        for item in snapshot.observations
    )
    return replace(snapshot, observations=observations)
