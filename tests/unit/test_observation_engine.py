"""Atomic hierarchy capture tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from xml.etree import ElementTree

import pytest

import appwright.observations.engine as observation_engine_module
import appwright.operations.engine as deadline_module
from appwright.backends.base import AutomationBackend
from appwright.models.data import HierarchySource
from appwright.observations import Observation, ObservationEngine
from appwright.observations.parser import parse_hierarchy
from appwright.operations import OperationDeadline

XML = """\
<hierarchy rotation="0">
  <node text="Continue" resource-id="com.example:id/submit"
        class="android.widget.Button" package="com.example" content-desc="Primary action"
        enabled="true" clickable="true" bounds="[10,20][110,70]" displayed="true" />
</hierarchy>
"""


class StepClock:
    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def __call__(self) -> float:
        if not self.values:
            raise AssertionError("clock called more times than expected")
        return self.values.pop(0)


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class RecordingBackend:
    def __init__(self, *outcomes: HierarchySource | BaseException) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0
        self.timeouts: list[timedelta] = []

    async def observe(self, timeout: timedelta) -> HierarchySource:
        self.calls += 1
        self.timeouts.append(timeout)
        if not self.outcomes:
            raise AssertionError("unexpected observation call")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class ControlledBackend:
    def __init__(self) -> None:
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.calls = 0
        self.active_calls = 0
        self.maximum_active_calls = 0

    async def observe(self, timeout: timedelta) -> HierarchySource:
        del timeout
        self.calls += 1
        call_number = self.calls
        self.active_calls += 1
        self.maximum_active_calls = max(self.maximum_active_calls, self.active_calls)
        try:
            if call_number == 1:
                self.first_started.set()
                await self.release_first.wait()
            return HierarchySource(content=XML)
        finally:
            self.active_calls -= 1


class TimestampOrderingBackend:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def observe(self, timeout: timedelta) -> HierarchySource:
        del timeout
        self.events.append("backend_return")
        return HierarchySource(content=XML)


class CancellationResistantBackend:
    def __init__(self) -> None:
        self.first_started = asyncio.Event()
        self.first_cancelled = asyncio.Event()
        self.release_first = asyncio.Event()
        self.first_finished = asyncio.Event()
        self.calls = 0
        self.active_calls = 0
        self.maximum_active_calls = 0

    async def observe(self, timeout: timedelta) -> HierarchySource:
        del timeout
        self.calls += 1
        call_number = self.calls
        self.active_calls += 1
        self.maximum_active_calls = max(self.maximum_active_calls, self.active_calls)
        try:
            if call_number == 1:
                self.first_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.first_cancelled.set()
                    await self.release_first.wait()
                finally:
                    self.first_finished.set()
            return HierarchySource(content=XML)
        finally:
            self.active_calls -= 1


def as_backend(
    backend: (
        RecordingBackend
        | ControlledBackend
        | TimestampOrderingBackend
        | CancellationResistantBackend
    ),
) -> AutomationBackend:
    return cast(AutomationBackend, backend)


async def test_capture_observes_once_with_parent_budget_and_parses_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deadline_module, "monotonic", lambda: 101.25)
    deadline = OperationDeadline(started_at=100.0, expires_at=105.0)
    backend = RecordingBackend(HierarchySource(content=XML))
    captured_at = datetime(2026, 7, 11, 12, 30, tzinfo=UTC)
    timestamp_calls = 0

    def utc_now() -> datetime:
        nonlocal timestamp_calls
        timestamp_calls += 1
        return captured_at

    engine = ObservationEngine(
        as_backend(backend),
        monotonic_clock=StepClock(20.0, 20.4),
        utc_now=utc_now,
    )

    observation = await engine.capture(package="com.example", deadline=deadline)

    assert backend.calls == 1
    assert backend.timeouts == [timedelta(seconds=3.75)]
    assert observation.sequence == 1
    assert observation.captured_at == captured_at
    assert observation.captured_at.utcoffset() is not None
    assert observation.elapsed == timedelta(milliseconds=400)
    assert observation.package == "com.example"
    assert observation.elements[0].snapshot.resource_id == "com.example:id/submit"
    assert timestamp_calls == 1


async def test_capture_timestamp_is_sampled_after_backend_returns() -> None:
    events: list[str] = []
    captured_at = datetime(2026, 7, 11, 12, 30, tzinfo=UTC)
    backend = TimestampOrderingBackend(events)

    def utc_now() -> datetime:
        events.append("timestamp")
        return captured_at

    engine = ObservationEngine(
        as_backend(backend),
        monotonic_clock=StepClock(20.0, 20.1),
        utc_now=utc_now,
    )

    observation = await engine.capture(
        package=None,
        deadline=OperationDeadline.start(timedelta(seconds=1)),
    )

    assert events == ["backend_return", "timestamp"]
    assert observation.captured_at == captured_at


async def test_successful_captures_receive_increasing_sequences() -> None:
    backend = RecordingBackend(
        HierarchySource(content=XML),
        HierarchySource(content=XML),
    )
    engine = ObservationEngine(
        as_backend(backend),
        monotonic_clock=StepClock(1.0, 1.1, 2.0, 2.2),
    )
    deadline = OperationDeadline.start(timedelta(seconds=1))

    first = await engine.capture(package=None, deadline=deadline)
    second = await engine.capture(package=None, deadline=deadline)

    assert (first.sequence, second.sequence) == (1, 2)
    assert first.elements[0].snapshot.identity == "observation-1-0"
    assert second.elements[0].snapshot.identity == "observation-2-0"


async def test_backend_failure_consumes_reserved_sequence_without_reuse() -> None:
    backend = RecordingBackend(
        RuntimeError("capture failed"),
        HierarchySource(content=XML),
    )
    engine = ObservationEngine(
        as_backend(backend),
        monotonic_clock=StepClock(1.0, 2.0, 2.1),
    )
    deadline = OperationDeadline.start(timedelta(seconds=1))

    with pytest.raises(RuntimeError, match="capture failed"):
        await engine.capture(package=None, deadline=deadline)

    observation = await engine.capture(package=None, deadline=deadline)

    assert observation.sequence == 2
    assert backend.calls == 2


async def test_parse_failure_consumes_reserved_sequence_without_reuse() -> None:
    backend = RecordingBackend(
        HierarchySource(content="<not-xml"),
        HierarchySource(content=XML),
    )
    engine = ObservationEngine(
        as_backend(backend),
        monotonic_clock=StepClock(1.0, 1.1, 2.0, 2.1),
    )
    deadline = OperationDeadline.start(timedelta(seconds=1))

    with pytest.raises(ElementTree.ParseError):
        await engine.capture(package=None, deadline=deadline)

    observation = await engine.capture(package=None, deadline=deadline)

    assert observation.sequence == 2
    assert backend.calls == 2


async def test_backend_capture_crossing_deadline_is_rejected_and_consumes_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadline_clock = StepClock(101.0, 106.0)
    monkeypatch.setattr(deadline_module, "monotonic", deadline_clock)
    backend = RecordingBackend(
        HierarchySource(content=XML),
        HierarchySource(content=XML),
    )
    engine = ObservationEngine(
        as_backend(backend),
        monotonic_clock=StepClock(1.0, 2.0, 2.1),
    )
    deadline = OperationDeadline(started_at=100.0, expires_at=105.0)

    with pytest.raises(TimeoutError, match="during backend observation"):
        await engine.capture(package=None, deadline=deadline)

    monkeypatch.setattr(deadline_module, "monotonic", lambda: 101.0)
    observation = await engine.capture(package=None, deadline=deadline)

    assert backend.timeouts == [timedelta(seconds=4), timedelta(seconds=4)]
    assert observation.sequence == 2


async def test_parsing_crossing_deadline_is_rejected_and_consumes_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadline_clock = MutableClock(101.0)
    monkeypatch.setattr(deadline_module, "monotonic", deadline_clock)
    backend = RecordingBackend(
        HierarchySource(content=XML),
        HierarchySource(content=XML),
    )
    engine = ObservationEngine(
        as_backend(backend),
        monotonic_clock=StepClock(1.0, 1.1, 2.0, 2.1),
    )
    deadline = OperationDeadline(started_at=100.0, expires_at=105.0)
    real_parse_hierarchy = parse_hierarchy

    def parse_after_deadline(
        source: str,
        *,
        sequence: int,
        package: str | None,
        captured_at: datetime | None = None,
        elapsed: timedelta = timedelta(),
    ) -> Observation:
        deadline_clock.value = 106.0
        return real_parse_hierarchy(
            source,
            sequence=sequence,
            package=package,
            captured_at=captured_at,
            elapsed=elapsed,
        )

    monkeypatch.setattr(observation_engine_module, "parse_hierarchy", parse_after_deadline)

    with pytest.raises(TimeoutError, match="during hierarchy parsing"):
        await engine.capture(package=None, deadline=deadline)

    deadline_clock.value = 101.0
    monkeypatch.setattr(observation_engine_module, "parse_hierarchy", real_parse_hierarchy)
    observation = await engine.capture(package=None, deadline=deadline)

    assert backend.calls == 2
    assert observation.sequence == 2


async def test_expired_deadline_does_not_observe_or_consume_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend(HierarchySource(content=XML))
    engine = ObservationEngine(
        as_backend(backend),
        monotonic_clock=StepClock(1.0, 1.1),
    )
    deadline = OperationDeadline(started_at=9.0, expires_at=10.0)
    monkeypatch.setattr(deadline_module, "monotonic", lambda: 10.0)

    with pytest.raises(TimeoutError, match="observation deadline expired"):
        await engine.capture(package=None, deadline=deadline)

    assert backend.calls == 0

    monkeypatch.setattr(deadline_module, "monotonic", lambda: 9.0)
    observation = await engine.capture(package=None, deadline=deadline)

    assert observation.sequence == 1
    assert backend.timeouts == [timedelta(seconds=1)]


async def test_concurrent_captures_are_serialized_per_engine() -> None:
    backend = ControlledBackend()
    engine = ObservationEngine(
        as_backend(backend),
        monotonic_clock=StepClock(1.0, 1.1, 2.0, 2.1),
    )
    deadline = OperationDeadline.start(timedelta(seconds=1))

    first_task = asyncio.create_task(engine.capture(package=None, deadline=deadline))
    await backend.first_started.wait()
    second_task = asyncio.create_task(engine.capture(package=None, deadline=deadline))
    await asyncio.sleep(0)
    calls_while_first_was_blocked = backend.calls
    backend.release_first.set()

    first, second = await asyncio.gather(first_task, second_task)

    assert calls_while_first_was_blocked == 1
    assert backend.calls == 2
    assert backend.maximum_active_calls == 1
    assert (first.sequence, second.sequence) == (1, 2)


async def test_waiting_for_capture_lock_honors_deadline_without_consuming_sequence() -> None:
    backend = ControlledBackend()
    engine = ObservationEngine(as_backend(backend))
    first = asyncio.create_task(
        engine.capture(
            package=None,
            deadline=OperationDeadline.start(timedelta(seconds=1)),
        )
    )
    await backend.first_started.wait()

    started_at = asyncio.get_running_loop().time()
    try:
        with pytest.raises(TimeoutError, match="observation deadline expired"):
            await engine.capture(
                package=None,
                deadline=OperationDeadline.start(timedelta(milliseconds=10)),
            )
        elapsed = asyncio.get_running_loop().time() - started_at
    finally:
        backend.release_first.set()
        first_observation = await first

    assert elapsed < 0.08
    assert backend.calls == 1
    assert first_observation.sequence == 1

    second_observation = await engine.capture(
        package=None,
        deadline=OperationDeadline.start(timedelta(seconds=1)),
    )
    assert second_observation.sequence == 2


async def test_backend_timeout_returns_promptly_but_retains_lock_until_backend_finishes() -> None:
    backend = CancellationResistantBackend()
    engine = ObservationEngine(as_backend(backend))
    loop = asyncio.get_running_loop()
    safety_release = loop.call_later(0.3, backend.release_first.set)
    started_at = loop.time()

    try:
        with pytest.raises(TimeoutError, match="during backend observation"):
            await asyncio.wait_for(
                engine.capture(
                    package=None,
                    deadline=OperationDeadline.start(timedelta(milliseconds=20)),
                ),
                timeout=0.2,
            )
        elapsed = loop.time() - started_at

        assert elapsed < 0.08
        await asyncio.wait_for(backend.first_cancelled.wait(), timeout=0.05)
        assert not backend.first_finished.is_set()
        assert backend.calls == 1

        second = asyncio.create_task(
            engine.capture(
                package=None,
                deadline=OperationDeadline.start(timedelta(seconds=1)),
            )
        )
        await asyncio.sleep(0.02)
        assert not second.done()
        assert backend.calls == 1
        assert backend.maximum_active_calls == 1
    finally:
        safety_release.cancel()
        backend.release_first.set()

    observation = await asyncio.wait_for(second, timeout=0.2)
    assert observation.sequence == 2
    assert backend.calls == 2
    assert backend.maximum_active_calls == 1


async def test_external_cancellation_returns_promptly_and_retains_backend_lock() -> None:
    backend = CancellationResistantBackend()
    engine = ObservationEngine(as_backend(backend))
    loop = asyncio.get_running_loop()
    capture = asyncio.create_task(
        engine.capture(
            package=None,
            deadline=OperationDeadline.start(timedelta(seconds=1)),
        )
    )
    await backend.first_started.wait()
    safety_release = loop.call_later(0.3, backend.release_first.set)
    started_at = loop.time()

    capture.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await capture
        elapsed = loop.time() - started_at

        assert elapsed < 0.08
        assert backend.first_cancelled.is_set()
        assert not backend.first_finished.is_set()

        second = asyncio.create_task(
            engine.capture(
                package=None,
                deadline=OperationDeadline.start(timedelta(seconds=1)),
            )
        )
        await asyncio.sleep(0.02)
        assert not second.done()
        assert backend.calls == 1
    finally:
        safety_release.cancel()
        backend.release_first.set()

    observation = await asyncio.wait_for(second, timeout=0.2)
    assert observation.sequence == 2
    assert backend.maximum_active_calls == 1


async def test_immediate_backend_exception_preserves_identity_and_cause() -> None:
    cause = RuntimeError("backend root cause")
    error = ValueError("backend failed immediately")
    try:
        raise error from cause
    except ValueError as raised_error:
        backend = RecordingBackend(raised_error)
    engine = ObservationEngine(as_backend(backend))

    with pytest.raises(ValueError, match="backend failed immediately") as captured:
        await engine.capture(
            package=None,
            deadline=OperationDeadline.start(timedelta(seconds=1)),
        )

    assert captured.value is error
    assert captured.value.__cause__ is cause
