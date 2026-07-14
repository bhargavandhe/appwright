"""Bounded, atomic interruption handling tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import timedelta
from threading import Event, Timer, get_ident
from time import monotonic
from typing import Any, TypeVar, cast

import pytest

from appwright.observations import Observation, parse_hierarchy
from appwright.operations import OperationDeadline
from appwright.screens.elements import ElementBinder, ElementDescriptor, by_id
from appwright.screens.interruptions import (
    InterruptionError,
    InterruptionEvent,
    InterruptionFailureReason,
    InterruptionManager,
)
from appwright.screens.model import (
    DeviceScope,
    DeviceScreen,
    Interruption,
    visible,
)

APP_PACKAGE = "com.example"
DEVICE_PACKAGE = "com.android.permissioncontroller"
AppControlT = TypeVar("AppControlT")
DeviceControlT = TypeVar("DeviceControlT")


class MutableClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class RecordingBinder:
    def __init__(self, label: str) -> None:
        self.label = label
        self.dismissals: list[type[Interruption[Any]]] = []

    def bind(
        self,
        descriptor: ElementDescriptor[AppControlT, DeviceControlT],
    ) -> AppControlT | DeviceControlT:
        del descriptor
        raise AssertionError("these interruption definitions do not bind descriptors")

    def record(self, interruption_type: type[Interruption[Any]]) -> None:
        self.dismissals.append(interruption_type)


def record_dismissal(interruption: Interruption[Any]) -> None:
    binder = cast(RecordingBinder, interruption.binder)
    binder.record(type(interruption))


class LowPriority(Interruption):
    ready = visible(by_id("low"))
    priority = 10

    def dismiss(self) -> None:
        record_dismissal(self)


class HighPriority(Interruption):
    ready = visible(by_id("high"))
    priority = 100

    def dismiss(self) -> None:
        record_dismissal(self)


class FirstTie(Interruption):
    ready = visible(by_id("first_tie"))
    priority = 50

    def dismiss(self) -> None:
        record_dismissal(self)


class SecondTie(Interruption):
    ready = visible(by_id("second_tie"))
    priority = 50

    def dismiss(self) -> None:
        record_dismissal(self)


class AsyncInterruption(Interruption):
    ready = visible(by_id("async"))
    priority = 75

    async def dismiss(self) -> None:
        record_dismissal(self)


class LifecycleBinder(RecordingBinder):
    def __init__(self, label: str) -> None:
        super().__init__(label)
        self.calls = 0
        self.started = Event()
        self.release = Event()
        self.async_started = asyncio.Event()
        self.async_release = asyncio.Event()
        self.dismiss_thread_ids: list[int] = []
        self.construction_failures = 0


class BlockingSyncInterruption(Interruption):
    ready = visible(by_id("blocking_sync"))
    priority = 90

    def dismiss(self) -> None:
        binder = cast(LifecycleBinder, self.binder)
        binder.calls += 1
        binder.dismiss_thread_ids.append(get_ident())
        binder.started.set()
        binder.release.wait()


class BlockingAsyncInterruption(Interruption):
    ready = visible(by_id("blocking_async"))
    priority = 90

    async def dismiss(self) -> None:
        binder = cast(LifecycleBinder, self.binder)
        binder.calls += 1
        binder.async_started.set()
        await binder.async_release.wait()


class ReturningAwaitableInterruption(Interruption):
    ready = visible(by_id("returning_awaitable"))
    priority = 90

    def dismiss(self) -> Awaitable[None]:
        binder = cast(LifecycleBinder, self.binder)

        async def dismiss_later() -> None:
            binder.calls += 1
            binder.async_started.set()
            await binder.async_release.wait()

        return dismiss_later()


class FlakyConstructionInterruption(Interruption):
    ready = visible(by_id("flaky_construction"))
    priority = 90

    def __init__(self, binder: ElementBinder) -> None:
        lifecycle_binder = cast(LifecycleBinder, binder)
        if lifecycle_binder.construction_failures:
            lifecycle_binder.construction_failures -= 1
            raise RuntimeError("construction failed")
        super().__init__(binder)

    def dismiss(self) -> None:
        binder = cast(LifecycleBinder, self.binder)
        binder.calls += 1


class DevicePermission(DeviceScreen, Interruption[DeviceScope]):
    ready = visible(
        by_id("com.android.permissioncontroller:id/permission_allow_foreground_only_button")
    )
    priority = 200

    def dismiss(self) -> None:
        record_dismissal(self)


class BinderFactory:
    def __init__(
        self,
        app_binder: RecordingBinder,
        device_binder: RecordingBinder | None = None,
    ) -> None:
        self.app_binder = app_binder
        self.device_binder = device_binder if device_binder is not None else app_binder
        self.requested: list[type[Interruption[Any]]] = []

    def __call__(
        self,
        interruption_type: type[Interruption[Any]],
    ) -> ElementBinder:
        self.requested.append(interruption_type)
        if interruption_type.scope is DeviceScope:
            return self.device_binder
        return self.app_binder


class FlakyBinderFactory(BinderFactory):
    def __init__(self, binder: RecordingBinder) -> None:
        super().__init__(binder)
        self.failures = 1

    def __call__(
        self,
        interruption_type: type[Interruption[Any]],
    ) -> ElementBinder:
        self.requested.append(interruption_type)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("factory failed")
        return self.app_binder


def observation(sequence: int, *resource_ids: str) -> Observation:
    nodes: list[str] = []
    for index, resource_id in enumerate(resource_ids):
        package = DEVICE_PACKAGE if resource_id.startswith(f"{DEVICE_PACKAGE}:id/") else APP_PACKAGE
        nodes.append(
            f'''<node index="{index}" text="" resource-id="{resource_id}"
                class="android.widget.Button" package="{package}" content-desc=""
                checkable="false" checked="false" clickable="true" enabled="true"
                focusable="true" focused="false" selected="false"
                bounds="[0,0][100,100]" displayed="true" />'''
        )
    xml = f'<hierarchy rotation="0">{"".join(nodes)}</hierarchy>'
    return parse_hierarchy(xml, sequence=sequence, package=None)


def parent_deadline(
    *,
    expires_at: float = 100.0,
    clock: MutableClock | None = None,
) -> OperationDeadline:
    selected_clock = clock if clock is not None else MutableClock()
    return OperationDeadline(
        started_at=0.0,
        expires_at=expires_at,
        clock=selected_clock,
    )


def manager(
    definitions: tuple[type[Interruption[Any]], ...],
    *,
    factory: BinderFactory,
    timeout: timedelta = timedelta(seconds=30),
    max_dismissals: int = 8,
) -> InterruptionManager:
    return InterruptionManager(
        definitions,
        binder_factory=factory,
        package=APP_PACKAGE,
        timeout=timeout,
        max_dismissals=max_dismissals,
    )


async def test_highest_priority_wins_and_factory_receives_exact_type() -> None:
    binder = RecordingBinder("app")
    factory = BinderFactory(binder)
    interruptions = manager((LowPriority, HighPriority), factory=factory)

    handled = await interruptions.handle(
        observation(1, f"{APP_PACKAGE}:id/low", f"{APP_PACKAGE}:id/high"),
        parent_deadline(),
    )

    assert handled
    assert factory.requested == [HighPriority]
    assert binder.dismissals == [HighPriority]


async def test_equal_priority_uses_registration_order() -> None:
    binder = RecordingBinder("app")
    factory = BinderFactory(binder)
    interruptions = manager((SecondTie, FirstTie), factory=factory)

    await interruptions.handle(
        observation(
            1,
            f"{APP_PACKAGE}:id/first_tie",
            f"{APP_PACKAGE}:id/second_tie",
        ),
        parent_deadline(),
    )

    assert factory.requested == [SecondTie]
    assert binder.dismissals == [SecondTie]


async def test_visible_interruption_is_dismissed_once_then_waited_out() -> None:
    binder = RecordingBinder("app")
    interruptions = manager((HighPriority,), factory=BinderFactory(binder))

    first = await interruptions.handle(
        observation(10, f"{APP_PACKAGE}:id/high"),
        parent_deadline(),
    )
    still_visible = await interruptions.handle(
        observation(11, f"{APP_PACKAGE}:id/high"),
        parent_deadline(),
    )
    disappeared = await interruptions.handle(observation(12), parent_deadline())

    assert first
    assert still_visible
    assert not disappeared
    assert binder.dismissals == [HighPriority]
    assert [
        (
            item.observation_sequence,
            item.interruption_type,
            item.priority,
            item.event,
        )
        for item in interruptions.history
    ] == [
        (10, HighPriority, 100, InterruptionEvent.DETECTED),
        (10, HighPriority, 100, InterruptionEvent.DISMISSED),
        (11, HighPriority, 100, InterruptionEvent.STILL_VISIBLE),
        (12, HighPriority, 100, InterruptionEvent.DISAPPEARED),
    ]


async def test_disappearance_allows_a_different_handler_from_same_observation() -> None:
    binder = RecordingBinder("app")
    interruptions = manager(
        (HighPriority, LowPriority),
        factory=BinderFactory(binder),
    )

    await interruptions.handle(
        observation(1, f"{APP_PACKAGE}:id/high"),
        parent_deadline(),
    )
    second_handled = await interruptions.handle(
        observation(2, f"{APP_PACKAGE}:id/low"),
        parent_deadline(),
    )
    clean = await interruptions.handle(observation(3), parent_deadline())

    assert second_handled
    assert not clean
    assert binder.dismissals == [HighPriority, LowPriority]
    assert [item.event for item in interruptions.history] == [
        InterruptionEvent.DETECTED,
        InterruptionEvent.DISMISSED,
        InterruptionEvent.DISAPPEARED,
        InterruptionEvent.DETECTED,
        InterruptionEvent.DISMISSED,
        InterruptionEvent.DISAPPEARED,
    ]


async def test_device_scope_uses_unfiltered_observation_and_device_binder() -> None:
    app_binder = RecordingBinder("app")
    device_binder = RecordingBinder("device")
    factory = BinderFactory(app_binder, device_binder)
    interruptions = manager((DevicePermission,), factory=factory)

    handled = await interruptions.handle(
        observation(
            1,
            f"{DEVICE_PACKAGE}:id/permission_allow_foreground_only_button",
        ),
        parent_deadline(),
    )

    assert handled
    assert factory.requested == [DevicePermission]
    assert app_binder.dismissals == []
    assert device_binder.dismissals == [DevicePermission]


async def test_sync_and_async_dismiss_methods_are_both_completed() -> None:
    sync_binder = RecordingBinder("sync")
    async_binder = RecordingBinder("async")
    sync_manager = manager((LowPriority,), factory=BinderFactory(sync_binder))
    async_manager = manager((AsyncInterruption,), factory=BinderFactory(async_binder))

    await sync_manager.handle(
        observation(1, f"{APP_PACKAGE}:id/low"),
        parent_deadline(),
    )
    await async_manager.handle(
        observation(2, f"{APP_PACKAGE}:id/async"),
        parent_deadline(),
    )

    assert sync_binder.dismissals == [LowPriority]
    assert async_binder.dismissals == [AsyncInterruption]


async def test_maximum_dismissals_is_enforced_before_second_dispatch() -> None:
    binder = RecordingBinder("app")
    interruptions = manager(
        (HighPriority, LowPriority),
        factory=BinderFactory(binder),
        max_dismissals=1,
    )

    await interruptions.handle(
        observation(1, f"{APP_PACKAGE}:id/high"),
        parent_deadline(),
    )

    with pytest.raises(InterruptionError) as raised:
        await interruptions.handle(
            observation(2, f"{APP_PACKAGE}:id/low"),
            parent_deadline(),
        )

    assert raised.value.reason is InterruptionFailureReason.MAX_DISMISSALS
    assert raised.value.history == interruptions.history
    assert binder.dismissals == [HighPriority]
    assert interruptions.history[-1].event is InterruptionEvent.MAX_DISMISSALS_EXCEEDED
    assert interruptions.history[-1].interruption_type is LowPriority


async def test_failure_exposes_total_count_beyond_bounded_history() -> None:
    binder = RecordingBinder("bounded-error")
    interruptions = InterruptionManager(
        (HighPriority, LowPriority),
        binder_factory=BinderFactory(binder),
        package=APP_PACKAGE,
        timeout=timedelta(seconds=30),
        max_dismissals=1,
        history_limit=2,
    )

    await interruptions.handle(
        observation(1, f"{APP_PACKAGE}:id/high"),
        parent_deadline(),
    )
    with pytest.raises(InterruptionError) as raised:
        await interruptions.handle(
            observation(2, f"{APP_PACKAGE}:id/low"),
            parent_deadline(),
        )

    assert raised.value.event_count == 5
    assert interruptions.event_count == 5
    assert raised.value.history == interruptions.history
    assert len(raised.value.history) == 2
    assert [entry.event for entry in raised.value.history] == [
        InterruptionEvent.DETECTED,
        InterruptionEvent.MAX_DISMISSALS_EXCEEDED,
    ]


async def test_a_b_a_recurrence_is_reported_as_a_cycle_without_redispatch() -> None:
    binder = RecordingBinder("app")
    interruptions = manager(
        (HighPriority, LowPriority),
        factory=BinderFactory(binder),
    )

    await interruptions.handle(
        observation(1, f"{APP_PACKAGE}:id/high"),
        parent_deadline(),
    )
    await interruptions.handle(
        observation(2, f"{APP_PACKAGE}:id/low"),
        parent_deadline(),
    )

    with pytest.raises(InterruptionError) as raised:
        await interruptions.handle(
            observation(3, f"{APP_PACKAGE}:id/high"),
            parent_deadline(),
        )

    assert raised.value.reason is InterruptionFailureReason.CYCLE
    assert binder.dismissals == [HighPriority, LowPriority]
    assert interruptions.history[-1].event is InterruptionEvent.CYCLE_DETECTED
    assert interruptions.history[-1].observation_sequence == 3


async def test_clean_observation_resets_cycle_detection_and_dismissal_count() -> None:
    binder = RecordingBinder("app")
    interruptions = manager(
        (HighPriority,),
        factory=BinderFactory(binder),
        max_dismissals=1,
    )

    await interruptions.handle(
        observation(1, f"{APP_PACKAGE}:id/high"),
        parent_deadline(),
    )
    assert not await interruptions.handle(observation(2), parent_deadline())
    await interruptions.handle(
        observation(3, f"{APP_PACKAGE}:id/high"),
        parent_deadline(),
    )

    assert binder.dismissals == [HighPriority, HighPriority]


async def test_stale_observation_after_reset_is_suppressed_without_redispatch() -> None:
    binder = RecordingBinder("stale")
    interruptions = manager((HighPriority,), factory=BinderFactory(binder))

    assert await interruptions.handle(
        observation(10, f"{APP_PACKAGE}:id/high"),
        parent_deadline(),
    )
    assert not await interruptions.handle(observation(11), parent_deadline())
    history_before_stale = interruptions.history

    stale_was_suppressed = await interruptions.handle(
        observation(10, f"{APP_PACKAGE}:id/high"),
        parent_deadline(),
    )

    assert stale_was_suppressed
    assert binder.dismissals == [HighPriority]
    assert interruptions.history == history_before_stale


async def test_child_deadline_is_capped_by_parent_and_expires_while_waiting() -> None:
    clock = MutableClock()
    binder = RecordingBinder("app")
    interruptions = manager(
        (HighPriority,),
        factory=BinderFactory(binder),
        timeout=timedelta(seconds=30),
    )
    parent = parent_deadline(expires_at=5.0, clock=clock)

    await interruptions.handle(
        observation(1, f"{APP_PACKAGE}:id/high"),
        parent,
    )
    clock.value = 5.0

    with pytest.raises(InterruptionError) as raised:
        await interruptions.handle(
            observation(2, f"{APP_PACKAGE}:id/high"),
            parent_deadline(expires_at=100.0, clock=clock),
        )

    assert raised.value.reason is InterruptionFailureReason.DEADLINE
    assert raised.value.history[-1].event is InterruptionEvent.DEADLINE_EXCEEDED
    assert binder.dismissals == [HighPriority]


async def test_child_deadline_is_not_restarted_for_sequential_handlers() -> None:
    clock = MutableClock()
    binder = RecordingBinder("app")
    interruptions = manager(
        (HighPriority, LowPriority),
        factory=BinderFactory(binder),
        timeout=timedelta(seconds=2),
    )

    await interruptions.handle(
        observation(1, f"{APP_PACKAGE}:id/high"),
        parent_deadline(clock=clock),
    )
    clock.value = 1.5
    await interruptions.handle(
        observation(2, f"{APP_PACKAGE}:id/low"),
        parent_deadline(clock=clock),
    )
    clock.value = 2.0

    with pytest.raises(InterruptionError) as raised:
        await interruptions.handle(
            observation(3, f"{APP_PACKAGE}:id/low"),
            parent_deadline(clock=clock),
        )

    assert raised.value.reason is InterruptionFailureReason.DEADLINE
    assert binder.dismissals == [HighPriority, LowPriority]


async def test_blocking_sync_dismissal_runs_off_loop_and_stops_at_child_deadline() -> None:
    binder = LifecycleBinder("blocking")
    interruptions = manager(
        (BlockingSyncInterruption,),
        factory=BinderFactory(binder),
        timeout=timedelta(milliseconds=30),
    )
    release_timer = Timer(0.2, binder.release.set)
    release_timer.start()
    started_at = monotonic()

    try:
        with pytest.raises(InterruptionError) as raised:
            await asyncio.wait_for(
                interruptions.handle(
                    observation(1, f"{APP_PACKAGE}:id/blocking_sync"),
                    OperationDeadline.start(timedelta(seconds=1)),
                ),
                timeout=0.5,
            )
    finally:
        binder.release.set()
        release_timer.cancel()

    assert raised.value.reason is InterruptionFailureReason.DEADLINE
    assert monotonic() - started_at < 0.15
    assert binder.calls == 1
    assert binder.dismiss_thread_ids[0] != get_ident()


async def test_async_dismissal_is_bounded_by_the_lifecycle_deadline() -> None:
    binder = LifecycleBinder("async")
    interruptions = manager(
        (BlockingAsyncInterruption,),
        factory=BinderFactory(binder),
        timeout=timedelta(milliseconds=20),
    )

    with pytest.raises(InterruptionError) as raised:
        await asyncio.wait_for(
            interruptions.handle(
                observation(1, f"{APP_PACKAGE}:id/blocking_async"),
                OperationDeadline.start(timedelta(seconds=1)),
            ),
            timeout=0.2,
        )

    assert raised.value.reason is InterruptionFailureReason.DEADLINE
    assert binder.calls == 1


async def test_sync_returning_awaitable_is_awaited_within_lifecycle_deadline() -> None:
    binder = LifecycleBinder("returning-awaitable")
    interruptions = manager(
        (ReturningAwaitableInterruption,),
        factory=BinderFactory(binder),
        timeout=timedelta(milliseconds=20),
    )

    with pytest.raises(InterruptionError) as raised:
        await asyncio.wait_for(
            interruptions.handle(
                observation(1, f"{APP_PACKAGE}:id/returning_awaitable"),
                OperationDeadline.start(timedelta(seconds=1)),
            ),
            timeout=0.2,
        )

    assert raised.value.reason is InterruptionFailureReason.DEADLINE
    assert binder.calls == 1


async def test_factory_failure_is_structured_and_does_not_poison_retry_state() -> None:
    binder = RecordingBinder("app")
    factory = FlakyBinderFactory(binder)
    interruptions = manager((HighPriority,), factory=factory)

    with pytest.raises(InterruptionError) as raised:
        await interruptions.handle(
            observation(1, f"{APP_PACKAGE}:id/high"),
            parent_deadline(),
        )

    handled = await interruptions.handle(
        observation(2, f"{APP_PACKAGE}:id/high"),
        parent_deadline(),
    )

    assert raised.value.reason is InterruptionFailureReason.DISMISSAL
    assert raised.value.history[-1].event is InterruptionEvent.DISMISSAL_FAILED
    assert handled
    assert factory.requested == [HighPriority, HighPriority]
    assert binder.dismissals == [HighPriority]


async def test_handler_construction_failure_is_structured_and_retryable() -> None:
    binder = LifecycleBinder("constructor")
    binder.construction_failures = 1
    interruptions = manager(
        (FlakyConstructionInterruption,),
        factory=BinderFactory(binder),
    )

    with pytest.raises(InterruptionError) as raised:
        await interruptions.handle(
            observation(1, f"{APP_PACKAGE}:id/flaky_construction"),
            parent_deadline(),
        )

    handled = await interruptions.handle(
        observation(2, f"{APP_PACKAGE}:id/flaky_construction"),
        parent_deadline(),
    )

    assert raised.value.reason is InterruptionFailureReason.DISMISSAL
    assert handled
    assert binder.calls == 1


async def test_overlapping_handle_calls_are_serialized_and_dismiss_exactly_once() -> None:
    binder = LifecycleBinder("concurrent")
    interruptions = manager(
        (BlockingAsyncInterruption,),
        factory=BinderFactory(binder),
        timeout=timedelta(seconds=1),
    )
    first = asyncio.create_task(
        interruptions.handle(
            observation(1, f"{APP_PACKAGE}:id/blocking_async"),
            OperationDeadline.start(timedelta(seconds=1)),
        )
    )
    await asyncio.wait_for(binder.async_started.wait(), timeout=0.2)
    second = asyncio.create_task(
        interruptions.handle(
            observation(2, f"{APP_PACKAGE}:id/blocking_async"),
            OperationDeadline.start(timedelta(seconds=1)),
        )
    )
    await asyncio.sleep(0)
    binder.async_release.set()

    first_handled, second_handled = await asyncio.gather(first, second)
    assert first_handled
    assert second_handled
    assert binder.calls == 1


async def test_waiting_for_handle_lock_is_bounded_by_parent_deadline() -> None:
    binder = LifecycleBinder("lock-deadline")
    interruptions = manager(
        (BlockingAsyncInterruption,),
        factory=BinderFactory(binder),
        timeout=timedelta(seconds=1),
    )
    first = asyncio.create_task(
        interruptions.handle(
            observation(1, f"{APP_PACKAGE}:id/blocking_async"),
            OperationDeadline.start(timedelta(seconds=1)),
        )
    )
    await asyncio.wait_for(binder.async_started.wait(), timeout=0.2)

    with pytest.raises(InterruptionError) as raised:
        await interruptions.handle(
            observation(2, f"{APP_PACKAGE}:id/blocking_async"),
            OperationDeadline.start(timedelta(milliseconds=20)),
        )

    binder.async_release.set()
    assert await first
    assert raised.value.reason is InterruptionFailureReason.DEADLINE
    assert binder.calls == 1


async def test_cancellation_during_possible_dispatch_prevents_redispatch() -> None:
    binder = LifecycleBinder("cancel")
    interruptions = manager(
        (BlockingAsyncInterruption,),
        factory=BinderFactory(binder),
        timeout=timedelta(seconds=1),
    )
    first = asyncio.create_task(
        interruptions.handle(
            observation(1, f"{APP_PACKAGE}:id/blocking_async"),
            OperationDeadline.start(timedelta(seconds=1)),
        )
    )
    await asyncio.wait_for(binder.async_started.wait(), timeout=0.2)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    handled = await interruptions.handle(
        observation(2, f"{APP_PACKAGE}:id/blocking_async"),
        OperationDeadline.start(timedelta(seconds=1)),
    )

    assert handled
    assert binder.calls == 1


async def test_cancelled_live_sync_dismissal_blocks_different_dispatch() -> None:
    binder = LifecycleBinder("cancel-sync-overlap")
    interruptions = manager(
        (BlockingSyncInterruption, LowPriority),
        factory=BinderFactory(binder),
        timeout=timedelta(seconds=1),
    )
    first = asyncio.create_task(
        interruptions.handle(
            observation(1, f"{APP_PACKAGE}:id/blocking_sync"),
            OperationDeadline.start(timedelta(seconds=1)),
        )
    )
    assert await asyncio.to_thread(binder.started.wait, 0.2)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(
        interruptions.handle(
            observation(2, f"{APP_PACKAGE}:id/low"),
            OperationDeadline.start(timedelta(seconds=1)),
        )
    )
    await asyncio.sleep(0.02)

    try:
        assert not second.done()
        assert binder.dismissals == []
    finally:
        binder.release.set()

    assert await asyncio.wait_for(second, timeout=0.2)
    assert binder.calls == 1
    assert binder.dismissals == [LowPriority]


async def test_cancelled_live_async_dismissal_blocks_different_dispatch() -> None:
    binder = LifecycleBinder("cancel-async-overlap")
    interruptions = manager(
        (BlockingAsyncInterruption, LowPriority),
        factory=BinderFactory(binder),
        timeout=timedelta(seconds=1),
    )
    first = asyncio.create_task(
        interruptions.handle(
            observation(1, f"{APP_PACKAGE}:id/blocking_async"),
            OperationDeadline.start(timedelta(seconds=1)),
        )
    )
    await asyncio.wait_for(binder.async_started.wait(), timeout=0.2)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(
        interruptions.handle(
            observation(2, f"{APP_PACKAGE}:id/low"),
            OperationDeadline.start(timedelta(seconds=1)),
        )
    )
    await asyncio.sleep(0.02)

    assert not second.done()
    assert binder.dismissals == []
    binder.async_release.set()

    assert await asyncio.wait_for(second, timeout=0.2)
    assert binder.calls == 1
    assert binder.dismissals == [LowPriority]


async def test_history_retention_is_bounded_across_many_clean_episodes() -> None:
    binder = RecordingBinder("history-stress")
    interruptions = InterruptionManager(
        (HighPriority,),
        binder_factory=BinderFactory(binder),
        package=APP_PACKAGE,
        timeout=timedelta(seconds=30),
        max_dismissals=1,
        history_limit=7,
    )

    for episode in range(300):
        sequence = episode * 2 + 1
        assert await interruptions.handle(
            observation(sequence, f"{APP_PACKAGE}:id/high"),
            parent_deadline(),
        )
        assert not await interruptions.handle(
            observation(sequence + 1),
            parent_deadline(),
        )

    assert interruptions.event_count == 900
    assert len(interruptions.history) == 7
    assert interruptions.history[-1].observation_sequence == 600


@pytest.mark.parametrize(
    ("timeout", "max_dismissals", "message"),
    [
        (timedelta(), 1, "timeout"),
        (timedelta(seconds=1), 0, "max_dismissals"),
    ],
)
def test_invalid_budgets_are_rejected(
    timeout: timedelta,
    max_dismissals: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        manager(
            (HighPriority,),
            factory=BinderFactory(RecordingBinder("app")),
            timeout=timeout,
            max_dismissals=max_dismissals,
        )


@pytest.mark.parametrize("history_limit", [0, True, cast(Any, 1.5)])
def test_invalid_history_limit_is_rejected(history_limit: int) -> None:
    expected = (
        TypeError if history_limit is True or isinstance(history_limit, float) else ValueError
    )
    with pytest.raises(expected, match="history_limit"):
        InterruptionManager(
            (HighPriority,),
            binder_factory=BinderFactory(RecordingBinder("app")),
            package=APP_PACKAGE,
            timeout=timedelta(seconds=1),
            max_dismissals=1,
            history_limit=history_limit,
        )
