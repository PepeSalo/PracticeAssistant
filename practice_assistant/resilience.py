from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TypeVar

T = TypeVar("T")


class AppError(RuntimeError):
    def __init__(
        self, message: str, *, retryable: bool = False, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0, min(60, float(value)))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            return max(0, min(60, (date - datetime.now(timezone.utc)).total_seconds()))
        except (ValueError, TypeError, OverflowError):
            return None


def backoff(attempt: int, random_fn: Callable[[], float] = random.random) -> float:
    return min(8, 0.5 * 2**attempt) + random_fn() * 0.25


async def retry(
    operation: Callable[[], Awaitable[T]],
    retries: int,
    notify: Callable[[str], None],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    random_fn: Callable[[], float] = random.random,
) -> T:
    for attempt in range(retries + 1):
        try:
            return await operation()
        except AppError as exc:
            if not exc.retryable or attempt == retries:
                raise
            delay = (
                exc.retry_after
                if exc.retry_after is not None
                else backoff(attempt, random_fn)
            )
            notify(f"{exc} Retrying in {delay:.2f}s ({attempt + 1}/{retries}).")
            await sleep(delay)
    raise AssertionError("Retry loop did not execute.")
