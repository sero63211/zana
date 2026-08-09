"""Bounded streaming batches: limits, oversized items, no materialization."""

from __future__ import annotations

import pytest

from tests.resources.helpers import request
from zana_core.resources.batching import (
    BatchLimitError,
    iter_batches,
    plan_batch,
    validate_batch_limits,
)
from zana_core.resources.models import OperationCategory, ResourcePolicy


def test_iter_batches_respects_item_and_byte_limits():
    batches = list(
        iter_batches(
            (b"x" * 100 for _ in range(25)),
            max_items=10,
            max_bytes=1000,
        )
    )
    assert [len(batch) for batch, _ in batches] == [10, 10, 5]
    assert all(size <= 1000 for _, size in batches)


def test_iter_batches_never_materializes_whole_input():
    consumed = 0

    def items():
        nonlocal consumed
        for index in range(1000):
            consumed += 1
            yield index

    generator = iter_batches(items(), max_items=10, max_bytes=1000)
    first_batch, first_size = next(generator)
    assert len(first_batch) == 10
    # A one-item lookahead is allowed; the whole input is never materialized.
    assert consumed < 100
    remaining = list(generator)
    assert len(remaining) == 99


def test_oversized_single_item_fails_before_growth():
    with pytest.raises(BatchLimitError):
        list(iter_batches([b"x" * 5000], max_items=10, max_bytes=100))


def test_max_total_items_bounds_stream():
    with pytest.raises(BatchLimitError):
        list(
            iter_batches(
                range(50),
                max_items=10,
                max_bytes=1000,
                max_total_items=30,
            )
        )


def test_negative_size_provider_fails():
    with pytest.raises(BatchLimitError):
        list(iter_batches([1], max_items=1, max_bytes=10, size_provider=lambda _: -1))


def test_batch_plan_derived_from_request_and_limit():
    policy = ResourcePolicy()
    governor_limit = policy.category_limit(OperationCategory.TINY)
    req = request(category="tiny", request_id="r", items=500, byte_count=10)
    plan = plan_batch(req, governor_limit)
    assert plan.max_items_per_batch == governor_limit.max_items
    assert plan.max_bytes_per_batch == governor_limit.max_bytes
    assert plan.declared_items == 500


def test_validate_batch_limits_reports_violations():
    policy = ResourcePolicy()
    limit = policy.category_limit(OperationCategory.TINY)
    req = request(category="tiny", request_id="r", items=limit.max_items + 1)
    assert validate_batch_limits(req, limit)
    ok = request(category="tiny", request_id="r", items=10)
    assert validate_batch_limits(ok, limit) == []


def test_bytes_are_referenced_not_copied():
    payload = bytearray(b"a" * 1000)
    batches = list(iter_batches([payload], max_items=1, max_bytes=4096))
    assert batches[0][0][0] is payload
