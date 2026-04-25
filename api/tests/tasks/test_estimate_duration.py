"""Unit tests for `task_service.estimate_duration_ms`.

Covers two paths: the historical-p50 branch (≥5 prior completed tasks)
and the hardcoded-default branch. `export_zip` has motion-count-aware
defaults that we exercise separately.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.services import task_service


@pytest.mark.asyncio
async def test_hardcoded_default_when_no_history(
    db_session: Any, seeded_user: dict[str, Any]
) -> None:
    estimate = await task_service.estimate_duration_ms(
        db_session,
        task_type="create_checkpoint",
        input_payload={},
    )
    assert estimate == task_service.DEFAULT_ESTIMATES_MS["create_checkpoint"]


@pytest.mark.asyncio
async def test_export_zip_default_scales_with_motion_count(
    db_session: Any, seeded_user: dict[str, Any]
) -> None:
    estimate = await task_service.estimate_duration_ms(
        db_session,
        task_type="export_zip",
        input_payload={"motion_count": 8},
    )
    # 10s base + 2s/motion × 8 = 26s
    assert estimate == 10_000 + 2_000 * 8


@pytest.mark.asyncio
async def test_export_zip_treats_invalid_motion_count_as_zero(
    db_session: Any, seeded_user: dict[str, Any]
) -> None:
    estimate = await task_service.estimate_duration_ms(
        db_session,
        task_type="export_zip",
        input_payload={"motion_count": "not-a-number"},
    )
    assert estimate == task_service.DEFAULT_ESTIMATES_MS["export_zip"]


@pytest.mark.asyncio
async def test_uses_historical_median_when_enough_samples(
    db_session: Any, seeded_user: dict[str, Any]
) -> None:
    """Seed 6 completed tasks with known durations and assert the
    estimator returns the median, not the hardcoded default."""
    from app.models.task import Task

    durations_ms = [10_000, 12_000, 14_000, 16_000, 18_000, 20_000]
    base_started = datetime.now(UTC) - timedelta(hours=1)
    for i, dur in enumerate(durations_ms):
        started = base_started + timedelta(seconds=i * 10)
        completed = started + timedelta(milliseconds=dur)
        db_session.add(
            Task(
                id=uuid.uuid4(),
                user_id=seeded_user["id"],
                task_type="create_checkpoint",
                status="completed",
                input_payload={},
                started_at=started,
                completed_at=completed,
            )
        )
    await db_session.commit()

    estimate = await task_service.estimate_duration_ms(
        db_session,
        task_type="create_checkpoint",
        input_payload={},
    )
    # Median of [10000,12000,14000,16000,18000,20000] = (14000+16000)/2 = 15000
    assert estimate == 15_000


@pytest.mark.asyncio
async def test_falls_back_to_default_with_too_few_samples(
    db_session: Any, seeded_user: dict[str, Any]
) -> None:
    """Threshold is 5 — 4 samples isn't enough."""
    from app.models.task import Task

    base_started = datetime.now(UTC) - timedelta(hours=1)
    for i in range(4):
        started = base_started + timedelta(seconds=i)
        db_session.add(
            Task(
                id=uuid.uuid4(),
                user_id=seeded_user["id"],
                task_type="create_alias",
                status="completed",
                input_payload={},
                started_at=started,
                completed_at=started + timedelta(milliseconds=999_999),
            )
        )
    await db_session.commit()

    estimate = await task_service.estimate_duration_ms(
        db_session,
        task_type="create_alias",
        input_payload={},
    )
    # Should use hardcoded default, NOT the seeded 999_999.
    assert estimate == task_service.DEFAULT_ESTIMATES_MS["create_alias"]
