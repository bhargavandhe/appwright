"""Serialized, deadline-aware hierarchy capture."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from time import monotonic
from typing import Any

from appwright.backends.base import AutomationBackend
from appwright.observations.models import Observation
from appwright.observations.parser import parse_hierarchy
from appwright.operations import OperationDeadline


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _consume_future_outcome(future: asyncio.Future[Any]) -> None:
    if future.cancelled():
        return
    try:
        future.exception()
    except BaseException:
        return


def _cancel_detached(future: asyncio.Future[Any]) -> None:
    future.cancel()
    future.add_done_callback(_consume_future_outcome)


class _BackendDeadlineExpired(Exception):
    """Internal signal that a backend observation outlived its root deadline."""


def _release_capture_lock(
    lock: asyncio.Lock,
    future: asyncio.Future[Any],
) -> None:
    """Consume detached backend work and release its serialized session slot."""

    _consume_future_outcome(future)
    if lock.locked():
        lock.release()


def _detach_backend_observation(
    future: asyncio.Future[Any],
    lock: asyncio.Lock,
) -> None:
    """Request cancellation while retaining the lock until work truly terminates."""

    future.cancel()
    future.add_done_callback(partial(_release_capture_lock, lock))


async def _await_backend_before_deadline(
    future: asyncio.Future[Any],
    timeout: timedelta,
) -> Any:
    timeout_seconds = timeout.total_seconds()
    if timeout_seconds <= 0:
        raise _BackendDeadlineExpired
    done, pending = await asyncio.wait((future,), timeout=timeout_seconds)
    del pending
    if not done:
        raise _BackendDeadlineExpired
    return future.result()


async def _acquire_before_deadline(
    lock: asyncio.Lock,
    deadline: OperationDeadline,
) -> None:
    """Acquire a serialization lock without leaking it at timeout races."""

    if not lock.locked():
        await lock.acquire()
        return
    remaining = deadline.remaining().total_seconds()
    if remaining <= 0:
        raise TimeoutError("observation deadline expired waiting for capture lock")
    future = asyncio.ensure_future(lock.acquire())
    try:
        done, pending = await asyncio.wait((future,), timeout=remaining)
        del pending
    except BaseException:
        if future.done() and not future.cancelled() and future.result():
            lock.release()
        else:
            _cancel_detached(future)
        raise
    if not done:
        _cancel_detached(future)
        raise TimeoutError("observation deadline expired waiting for capture lock")
    if not future.result():
        raise RuntimeError("asyncio lock acquisition returned false")


class ObservationEngine:
    """Capture immutable hierarchy observations for one backend session."""

    def __init__(
        self,
        backend: AutomationBackend,
        *,
        monotonic_clock: Callable[[], float] = monotonic,
        utc_now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.backend = backend
        self.monotonic_clock = monotonic_clock
        self.utc_now = utc_now
        self.capture_lock = asyncio.Lock()
        self.next_sequence = 1

    async def capture(
        self,
        package: str | None,
        deadline: OperationDeadline,
    ) -> Observation:
        """Capture and parse one hierarchy within ``deadline``.

        Sequence numbers are reserved immediately before the backend command and
        never reused, including when observation or parsing fails.
        """

        await _acquire_before_deadline(self.capture_lock, deadline)
        lock_transferred = False
        try:
            remaining = deadline.remaining()
            if remaining <= timedelta():
                raise TimeoutError("observation deadline expired")

            sequence = self.next_sequence
            self.next_sequence += 1
            started_at = self.monotonic_clock()
            backend_future = asyncio.create_task(self.backend.observe(remaining))
            try:
                source = await _await_backend_before_deadline(
                    backend_future,
                    remaining,
                )
            except _BackendDeadlineExpired:
                lock_transferred = True
                _detach_backend_observation(backend_future, self.capture_lock)
                raise TimeoutError(
                    "observation deadline expired during backend observation"
                ) from None
            except asyncio.CancelledError:
                lock_transferred = True
                _detach_backend_observation(backend_future, self.capture_lock)
                raise
            captured_at = self.utc_now()
            if deadline.expired():
                raise TimeoutError("observation deadline expired during backend observation")
            elapsed = timedelta(seconds=max(self.monotonic_clock() - started_at, 0.0))
            observation = parse_hierarchy(
                source.content,
                sequence=sequence,
                package=package,
                captured_at=captured_at,
                elapsed=elapsed,
            )
            if deadline.expired():
                raise TimeoutError("observation deadline expired during hierarchy parsing")
            return observation
        finally:
            if not lock_transferred:
                self.capture_lock.release()
