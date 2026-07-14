"""Atomic screen readiness and transition contracts."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import timedelta
from threading import Event, Timer, get_ident
from time import monotonic
from typing import TypeVar, assert_type, cast

import pytest

from appwright.models.config import RetryPolicy, Timeouts
from appwright.observations import Observation, parse_hierarchy
from appwright.operations import OperationDeadline
from appwright.screens import (
    DeviceScope,
    DeviceScreen,
    ElementBinder,
    ElementDescriptor,
    Readiness,
    Screen,
    ScreenChoice,
    all_of,
    any_of,
    by_id,
    one_of,
    visible,
)
from appwright.screens.readiness import evaluate_readiness
from appwright.screens.targets import ScreenDefinition
from appwright.screens.transitions import (
    ScreenTimeoutError,
    TransitionEngine,
    TransitionObservation,
)


def observation(source: str, *, sequence: int = 1) -> Observation:
    return parse_hierarchy(source, sequence=sequence, package=None)


def node(resource_id: str, package: str, *, displayed: bool = True) -> str:
    bounds = "[0,0][10,10]" if displayed else "[0,0][0,0]"
    return (
        f'<node resource-id="{resource_id}" package="{package}" '
        f'class="android.widget.TextView" bounds="{bounds}" displayed="true" />'
    )


def observed(*nodes: str, sequence: int) -> Observation:
    return observation(f"<hierarchy>{''.join(nodes)}</hierarchy>", sequence=sequence)


class Home(Screen):
    ready = visible(by_id("home"))


class Login(Screen):
    ready = visible(by_id("login"))


class Permission(DeviceScreen):
    ready = visible(by_id("com.android.permissioncontroller:id/permission_allow_button"))


async def transition_return_type_contract(
    transitions: TransitionEngine,
    existing: Home,
    parent: OperationDeadline,
) -> None:
    assert_type(await transitions.wait_for(Home, deadline=parent), Home)
    assert_type(
        await transitions.wait_for_any(one_of(Home, Permission), deadline=parent),
        ScreenChoice[Home | Permission],
    )
    assert_type(await transitions.settle(existing, deadline=parent), Home)


AppControlT = TypeVar("AppControlT")
DeviceControlT = TypeVar("DeviceControlT")


class RecordingBinder:
    def __init__(self, name: str) -> None:
        self.name = name

    def bind(
        self,
        descriptor: ElementDescriptor[AppControlT, DeviceControlT],
    ) -> AppControlT | DeviceControlT:
        del descriptor
        raise AssertionError("transition tests do not bind element descriptors")


class RecordingBinderFactory:
    def __init__(self) -> None:
        self.app = RecordingBinder("app")
        self.device = RecordingBinder("device")
        self.calls: list[type[ScreenDefinition]] = []

    def __call__(self, screen_type: type[ScreenDefinition]) -> ElementBinder:
        self.calls.append(screen_type)
        selected = self.device if screen_type.scope is DeviceScope else self.app
        return cast(ElementBinder, selected)


class BlockingBinderFactory(RecordingBinderFactory):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()
        self.completed = Event()
        self.thread_ids: list[int] = []

    def __call__(self, screen_type: type[ScreenDefinition]) -> ElementBinder:
        self.thread_ids.append(get_ident())
        self.started.set()
        try:
            self.release.wait()
            return super().__call__(screen_type)
        finally:
            self.completed.set()


class FailingBinderFactory(RecordingBinderFactory):
    def __init__(self) -> None:
        super().__init__()
        self.cause = RuntimeError("binder dependency root cause")
        self.error = ValueError("binder dependency failed")

    def __call__(self, screen_type: type[ScreenDefinition]) -> ElementBinder:
        del screen_type
        raise self.error from self.cause


class BlockingOnceFailingBinderFactory(RecordingBinderFactory):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()
        self.completed = Event()
        self.invocation_count = 0
        self.thread_ids: list[int] = []
        self.cause = RuntimeError("late binder dependency root cause")
        self.error = ValueError("late binder dependency failed")

    def __call__(self, screen_type: type[ScreenDefinition]) -> ElementBinder:
        self.invocation_count += 1
        self.thread_ids.append(get_ident())
        if self.invocation_count == 1:
            self.started.set()
            self.release.wait()
            try:
                raise self.error from self.cause
            finally:
                self.completed.set()
        return super().__call__(screen_type)


class ManualClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.value

    async def sleep(self, delay: float) -> None:
        assert delay > 0
        self.sleeps.append(delay)
        self.value += delay


class CancellationResistantSleep:
    def __init__(self, *, error_after_cancellation: bool = False) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.completed = asyncio.Event()
        self.error_after_cancellation = error_after_cancellation
        self.cause = RuntimeError("sleep dependency root cause")
        self.error = ValueError("sleep failed after cancellation")

    async def __call__(self, delay: float) -> None:
        del delay
        self.started.set()
        try:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.release.wait()
            if self.error_after_cancellation:
                raise self.error from self.cause
        finally:
            self.completed.set()


class SequenceSource:
    def __init__(self, *observations: Observation) -> None:
        if not observations:
            raise ValueError("at least one observation is required")
        self.observations = observations
        self.index = 0
        self.calls: list[tuple[str | None, OperationDeadline]] = []
        self.active = 0
        self.maximum_active = 0

    async def capture(
        self,
        package: str | None,
        deadline: OperationDeadline,
    ) -> Observation:
        self.calls.append((package, deadline))
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            selected = self.observations[min(self.index, len(self.observations) - 1)]
            self.index += 1
            return selected
        finally:
            self.active -= 1


class SequenceHook:
    def __init__(self, *handled_sequences: int) -> None:
        self.handled_sequences = set(handled_sequences)
        self.calls: list[tuple[int, OperationDeadline]] = []

    async def handle(
        self,
        observation: Observation,
        parent_deadline: OperationDeadline,
    ) -> bool:
        self.calls.append((observation.sequence, parent_deadline))
        return observation.sequence in self.handled_sequences


class HungHook:
    def __init__(self) -> None:
        self.cancelled = False
        self.cancelled_event = asyncio.Event()

    async def handle(
        self,
        observation: Observation,
        parent_deadline: OperationDeadline,
    ) -> bool:
        del observation, parent_deadline
        try:
            await asyncio.Event().wait()
            return False
        except asyncio.CancelledError:
            self.cancelled = True
            self.cancelled_event.set()
            raise


class CancellationResistantHook:
    def __init__(self, *, error_after_timeout: bool = False) -> None:
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.completed = asyncio.Event()
        self.error = RuntimeError("hook failed after root cancellation")
        self.error_after_timeout = error_after_timeout

    async def handle(
        self,
        observation: Observation,
        parent_deadline: OperationDeadline,
    ) -> bool:
        del observation, parent_deadline
        try:
            await asyncio.Event().wait()
            return False
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()
            if self.error_after_timeout:
                raise self.error from None
            return False
        finally:
            self.completed.set()


class ImmediateRuntimeErrorHook:
    def __init__(self) -> None:
        self.cause = LookupError("hook runtime dependency root cause")
        self.error = RuntimeError("hook runtime dependency failed")

    async def handle(
        self,
        observation: Observation,
        parent_deadline: OperationDeadline,
    ) -> bool:
        del observation, parent_deadline
        raise self.error from self.cause


class ImmediateTimeoutHook:
    def __init__(self) -> None:
        self.cause = RuntimeError("hook dependency root cause")
        self.error = TimeoutError("hook dependency timed out early")
        self.parent_was_unexpired = False

    async def handle(
        self,
        observation: Observation,
        parent_deadline: OperationDeadline,
    ) -> bool:
        del observation
        self.parent_was_unexpired = not parent_deadline.expired()
        raise self.error from self.cause


def timeouts(
    *,
    transition: timedelta = timedelta(seconds=1),
    stability: timedelta = timedelta(milliseconds=100),
    delay: timedelta = timedelta(milliseconds=100),
    maximum_delay: timedelta | None = None,
) -> Timeouts:
    return Timeouts(
        transition=transition,
        stability=stability,
        retry=RetryPolicy(
            initial_delay=delay,
            multiplier=1,
            maximum_delay=delay if maximum_delay is None else maximum_delay,
        ),
    )


def engine(
    source: SequenceSource,
    binders: RecordingBinderFactory,
    clock: ManualClock,
    *,
    configured_timeouts: Timeouts | None = None,
    hook: SequenceHook | None = None,
    history_limit: int = 50,
) -> TransitionEngine:
    return TransitionEngine(
        source,
        binder_factory=binders,
        app_package="com.example",
        timeouts=configured_timeouts or timeouts(),
        monotonic_clock=clock,
        sleep=clock.sleep,
        interruption_hook=hook,
        history_limit=history_limit,
    )


def test_readiness_recurses_and_requires_one_visible_scoped_match() -> None:
    captured = observation(
        """\
<hierarchy>
  <node resource-id="com.example:id/unique" package="com.example"
        class="android.widget.TextView" bounds="[0,0][10,10]" displayed="true" />
  <node resource-id="com.example:id/duplicate" package="com.example"
        class="android.widget.TextView" bounds="[0,0][10,10]" displayed="true" />
  <node resource-id="com.example:id/duplicate" package="com.example"
        class="android.widget.TextView" bounds="[0,10][10,20]" displayed="true" />
  <node resource-id="com.example:id/hidden" package="com.example"
        class="android.widget.TextView" bounds="[0,20][0,20]" displayed="true" />
  <node resource-id="com.other:id/unique" package="com.other"
        class="android.widget.TextView" bounds="[0,20][10,30]" displayed="true" />
</hierarchy>
"""
    )
    readiness = all_of(
        visible(by_id("unique")),
        any_of(visible(by_id("duplicate")), visible(by_id("hidden"))),
    )

    result = evaluate_readiness(captured, readiness, package="com.example")

    assert not result.ready
    assert (result.matched, result.total) == (1, 3)
    assert result.diagnostics[0].children[1].children[0].match_count == 2
    assert result.diagnostics[0].children[1].children[0].reason == "duplicate matches"
    assert result.diagnostics[0].children[1].children[1].visible_count == 0
    with pytest.raises(FrozenInstanceError):
        result.ready = True  # type: ignore[misc]


@pytest.mark.parametrize("readiness", [all_of(), any_of()])
def test_empty_composite_readiness_is_never_trivially_ready(readiness: object) -> None:
    captured = observed(sequence=2)

    result = evaluate_readiness(
        captured,
        cast(Readiness, readiness),
        package="com.example",
    )

    assert not result.ready
    assert (result.matched, result.total) == (0, 0)
    assert result.diagnostics[0].reason == "readiness has no conditions"


async def test_wait_for_any_evaluates_candidate_race_from_one_sequence() -> None:
    source = SequenceSource(
        observed(
            node("com.example:id/login", "com.example"),
            sequence=7,
        )
    )
    binders = RecordingBinderFactory()
    clock = ManualClock()

    choice = await engine(source, binders, clock).wait_for_any(one_of(Home, Login))

    assert isinstance(choice.screen, Login)
    assert choice.observation_sequence == 7
    assert len(source.calls) == 1
    assert source.calls[0][0] is None
    assert binders.calls == [Login]


async def test_wait_for_any_uses_declaration_order_when_both_are_ready() -> None:
    source = SequenceSource(
        observed(
            node("com.example:id/home", "com.example"),
            node("com.example:id/login", "com.example"),
            sequence=11,
        )
    )
    binders = RecordingBinderFactory()
    clock = ManualClock()

    choice = await engine(source, binders, clock).wait_for_any(one_of(Login, Home))

    assert isinstance(choice.screen, Login)
    assert choice.observation_sequence == 11
    assert len(source.calls) == 1


async def test_app_and_device_candidates_use_scope_and_concrete_binders() -> None:
    permission = observed(
        node(
            "com.android.permissioncontroller:id/permission_allow_button",
            "com.android.permissioncontroller",
        ),
        node("com.other:id/home", "com.other"),
        sequence=21,
    )
    home = observed(
        node("com.example:id/home", "com.example"),
        sequence=22,
    )
    source = SequenceSource(permission, home)
    binders = RecordingBinderFactory()
    clock = ManualClock()
    transitions = engine(source, binders, clock)

    permission_choice = await transitions.wait_for_any(one_of(Home, Permission))
    bound_home = await transitions.wait_for(Home)

    assert isinstance(permission_choice.screen, Permission)
    assert permission_choice.screen.binder is binders.device
    assert type(bound_home) is Home
    assert bound_home.binder is binders.app
    assert binders.calls == [Permission, Home]
    assert all(call[0] is None for call in source.calls)


async def test_wait_for_retries_a_transient_missing_target() -> None:
    source = SequenceSource(
        observed(sequence=31),
        observed(node("com.example:id/home", "com.example"), sequence=32),
    )
    binders = RecordingBinderFactory()
    clock = ManualClock()

    home = await engine(source, binders, clock).wait_for(Home)

    assert type(home) is Home
    assert [item.sequence for item in source.observations[: source.index]] == [31, 32]
    assert clock.sleeps == [pytest.approx(0.1)]


async def test_timeout_retains_bounded_typed_diagnostics() -> None:
    source = SequenceSource(
        observed(sequence=41),
        observed(sequence=42),
    )
    binders = RecordingBinderFactory()
    clock = ManualClock()
    transitions = engine(
        source,
        binders,
        clock,
        configured_timeouts=timeouts(
            transition=timedelta(milliseconds=50),
            delay=timedelta(milliseconds=20),
        ),
        history_limit=1,
    )

    with pytest.raises(ScreenTimeoutError) as captured_error:
        await transitions.wait_for(Home)

    error = captured_error.value
    assert error.operation == "wait_for"
    assert error.candidate_screens == ("Home",)
    assert error.observation_count == 3
    assert len(error.history) == 1
    assert isinstance(error.history[0], TransitionObservation)
    assert error.history[0].sequence == 42
    assert error.history[0].candidates[0].screen == "Home"
    assert error.history[0].candidates[0].matched == 0
    assert error.history[0].candidates[0].total == 1
    assert "Home" in str(error)


async def test_settle_resets_stability_when_target_disappears() -> None:
    home_node = node("com.example:id/home", "com.example")
    source = SequenceSource(
        observed(home_node, sequence=51),
        observed(sequence=52),
        observed(home_node, sequence=53),
        observed(home_node, sequence=54),
        observed(home_node, sequence=55),
    )
    binders = RecordingBinderFactory()
    foreign_binders = RecordingBinderFactory()
    clock = ManualClock()
    transitions = engine(source, binders, clock)
    existing = Home(cast(ElementBinder, foreign_binders.app))

    settled = await transitions.settle(
        existing,
        stable_for=timedelta(milliseconds=150),
    )

    assert settled is not existing
    assert settled.binder is binders.app
    assert source.index == 5
    assert clock.sleeps == [pytest.approx(0.1)] * 4
    assert binders.calls == [Home]


async def test_settle_ignores_pre_dismissal_home_and_resets_stability() -> None:
    home_node = node("com.example:id/home", "com.example")
    source = SequenceSource(
        observed(home_node, sequence=61),
        observed(home_node, sequence=62),
        observed(home_node, sequence=63),
        observed(home_node, sequence=64),
    )
    binders = RecordingBinderFactory()
    clock = ManualClock()
    hook = SequenceHook(62)
    transitions = engine(source, binders, clock, hook=hook)

    home = await transitions.settle(
        Home,
        stable_for=timedelta(milliseconds=90),
    )

    assert type(home) is Home
    assert source.index == 4
    assert [call[0] for call in hook.calls] == [61, 62, 63, 64]
    assert binders.calls == [Home]


async def test_deadline_and_retry_sleeps_are_bounded_and_serial() -> None:
    source = SequenceSource(observed(sequence=71))
    binders = RecordingBinderFactory()
    clock = ManualClock()
    hook = SequenceHook()
    transitions = engine(
        source,
        binders,
        clock,
        configured_timeouts=timeouts(
            delay=timedelta(milliseconds=40),
        ),
        hook=hook,
    )

    with pytest.raises(ScreenTimeoutError):
        await transitions.wait_for(Home, timeout=timedelta(milliseconds=55))

    assert clock.sleeps == [pytest.approx(0.04), pytest.approx(0.015)]
    assert sum(clock.sleeps) == pytest.approx(0.055)
    assert source.maximum_active == 1
    deadlines = [call[1] for call in source.calls]
    deadlines.extend(call[1] for call in hook.calls)
    assert deadlines
    assert all(deadline is deadlines[0] for deadline in deadlines)


async def test_cancellation_resistant_sleep_cannot_extend_root_deadline() -> None:
    source = SequenceSource(observed(sequence=72))
    binders = RecordingBinderFactory()
    sleeper = CancellationResistantSleep()
    transitions = TransitionEngine(
        source,
        binder_factory=binders,
        app_package="com.example",
        timeouts=timeouts(),
        sleep=sleeper,
    )
    release = asyncio.get_running_loop().call_later(0.2, sleeper.release.set)
    started_at = monotonic()

    try:
        with pytest.raises(ScreenTimeoutError):
            await asyncio.wait_for(
                transitions.wait_for(Home, timeout=timedelta(milliseconds=20)),
                timeout=0.4,
            )
    finally:
        sleeper.release.set()
        release.cancel()
    await asyncio.wait_for(sleeper.completed.wait(), timeout=0.2)

    assert monotonic() - started_at < 0.1
    assert sleeper.cancelled.is_set()


async def test_external_cancellation_is_preserved_while_sleep_is_running() -> None:
    source = SequenceSource(observed(sequence=73))
    binders = RecordingBinderFactory()
    sleeper = CancellationResistantSleep()
    transitions = TransitionEngine(
        source,
        binder_factory=binders,
        app_package="com.example",
        timeouts=timeouts(),
        sleep=sleeper,
    )
    transition = asyncio.create_task(
        transitions.wait_for(Home, timeout=timedelta(milliseconds=100))
    )
    await sleeper.started.wait()
    safety_release = asyncio.get_running_loop().call_later(0.05, sleeper.release.set)

    transition.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(transition, timeout=0.2)
    finally:
        sleeper.release.set()
        safety_release.cancel()
    await asyncio.wait_for(sleeper.completed.wait(), timeout=0.2)

    assert sleeper.cancelled.is_set()


async def test_late_sleep_exception_is_consumed_after_root_timeout() -> None:
    source = SequenceSource(observed(sequence=74))
    binders = RecordingBinderFactory()
    sleeper = CancellationResistantSleep(error_after_cancellation=True)
    transitions = TransitionEngine(
        source,
        binder_factory=binders,
        app_package="com.example",
        timeouts=timeouts(),
        sleep=sleeper,
    )
    loop = asyncio.get_running_loop()
    unhandled: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda event_loop, context: unhandled.append(context))
    safety_release = loop.call_later(0.2, sleeper.release.set)

    try:
        with pytest.raises(ScreenTimeoutError):
            await asyncio.wait_for(
                transitions.wait_for(Home, timeout=timedelta(milliseconds=20)),
                timeout=0.4,
            )
        sleeper.release.set()
        await asyncio.wait_for(sleeper.completed.wait(), timeout=0.2)
        await asyncio.sleep(0)
    finally:
        sleeper.release.set()
        safety_release.cancel()
        loop.set_exception_handler(previous_handler)

    assert sleeper.cancelled.is_set()
    assert unhandled == []


async def test_hung_interruption_hook_is_cancelled_by_root_deadline() -> None:
    source = SequenceSource(observed(sequence=81))
    binders = RecordingBinderFactory()
    hook = HungHook()
    transitions = TransitionEngine(
        source,
        binder_factory=binders,
        app_package="com.example",
        timeouts=timeouts(),
        interruption_hook=hook,
    )

    with pytest.raises(ScreenTimeoutError) as captured_error:
        await asyncio.wait_for(
            transitions.wait_for(Home, timeout=timedelta(milliseconds=20)),
            timeout=0.25,
        )

    error = captured_error.value
    await asyncio.wait_for(hook.cancelled_event.wait(), timeout=0.1)
    assert hook.cancelled
    assert error.timeout == timedelta(milliseconds=20)
    assert error.observation_count == 1
    assert error.history[0].sequence == 81
    assert error.history[0].hook_timed_out
    assert "hook timed out" in str(error)
    assert "interruption handled" not in str(error)


async def test_cancellation_resistant_hook_cannot_extend_root_deadline() -> None:
    source = SequenceSource(observed(sequence=811))
    binders = RecordingBinderFactory()
    hook = CancellationResistantHook()
    transitions = TransitionEngine(
        source,
        binder_factory=binders,
        app_package="com.example",
        timeouts=timeouts(),
        interruption_hook=hook,
    )
    release = asyncio.get_running_loop().call_later(0.2, hook.release.set)
    started_at = monotonic()

    try:
        with pytest.raises(ScreenTimeoutError) as captured_error:
            await asyncio.wait_for(
                transitions.wait_for(Home, timeout=timedelta(milliseconds=20)),
                timeout=0.4,
            )
    finally:
        hook.release.set()
        release.cancel()
    await asyncio.wait_for(hook.completed.wait(), timeout=0.2)

    assert monotonic() - started_at < 0.1
    assert hook.cancelled.is_set()
    assert captured_error.value.history[0].hook_timed_out


async def test_post_deadline_hook_exception_is_classified_as_screen_timeout() -> None:
    source = SequenceSource(observed(sequence=812))
    binders = RecordingBinderFactory()
    hook = CancellationResistantHook(error_after_timeout=True)
    transitions = TransitionEngine(
        source,
        binder_factory=binders,
        app_package="com.example",
        timeouts=timeouts(),
        interruption_hook=hook,
    )
    release = asyncio.get_running_loop().call_later(0.1, hook.release.set)

    try:
        with pytest.raises(ScreenTimeoutError) as captured_error:
            await transitions.wait_for(Home, timeout=timedelta(milliseconds=20))
    finally:
        hook.release.set()
        release.cancel()
    await asyncio.wait_for(hook.completed.wait(), timeout=0.2)

    assert captured_error.value.history[0].hook_timed_out


async def test_external_cancellation_is_preserved_while_hook_is_running() -> None:
    source = SequenceSource(observed(sequence=813))
    binders = RecordingBinderFactory()
    hook = HungHook()
    transitions = TransitionEngine(
        source,
        binder_factory=binders,
        app_package="com.example",
        timeouts=timeouts(),
        interruption_hook=hook,
    )
    transition = asyncio.create_task(transitions.wait_for(Home, timeout=timedelta(seconds=1)))
    await asyncio.sleep(0)

    transition.cancel()
    with pytest.raises(asyncio.CancelledError):
        await transition

    await asyncio.wait_for(hook.cancelled_event.wait(), timeout=0.1)
    assert hook.cancelled


async def test_immediate_hook_timeout_propagates_without_root_timeout_history() -> None:
    source = SequenceSource(observed(sequence=82))
    binders = RecordingBinderFactory()
    hook = ImmediateTimeoutHook()
    transitions = TransitionEngine(
        source,
        binder_factory=binders,
        app_package="com.example",
        timeouts=timeouts(),
        interruption_hook=hook,
    )

    with pytest.raises(TimeoutError) as captured_error:
        await transitions.wait_for(Home, timeout=timedelta(seconds=1))

    assert captured_error.value is hook.error
    assert captured_error.value.__cause__ is hook.cause
    assert not isinstance(captured_error.value, ScreenTimeoutError)
    assert hook.parent_was_unexpired
    assert source.index == 1


async def test_immediate_non_timeout_hook_exception_preserves_identity_and_cause() -> None:
    source = SequenceSource(observed(sequence=83))
    binders = RecordingBinderFactory()
    hook = ImmediateRuntimeErrorHook()
    transitions = TransitionEngine(
        source,
        binder_factory=binders,
        app_package="com.example",
        timeouts=timeouts(),
        interruption_hook=hook,
    )

    with pytest.raises(RuntimeError) as captured_error:
        await transitions.wait_for(Home, timeout=timedelta(seconds=1))

    assert captured_error.value is hook.error
    assert captured_error.value.__cause__ is hook.cause


async def test_blocking_binder_is_bounded_and_does_not_block_event_loop() -> None:
    source = SequenceSource(observed(node("com.example:id/home", "com.example"), sequence=84))
    binders = BlockingBinderFactory()
    transitions = TransitionEngine(
        source,
        binder_factory=binders,
        app_package="com.example",
        timeouts=timeouts(),
    )
    release_timer = Timer(0.2, binders.release.set)
    release_timer.start()
    started_at = monotonic()

    try:
        with pytest.raises(ScreenTimeoutError):
            await asyncio.wait_for(
                transitions.wait_for(Home, timeout=timedelta(milliseconds=30)),
                timeout=0.4,
            )
    finally:
        binders.release.set()
        release_timer.cancel()

    assert await asyncio.to_thread(binders.started.wait, 0.2)
    assert monotonic() - started_at < 0.1
    assert binders.thread_ids[0] != get_ident()


async def test_live_binder_work_is_drained_before_another_binding_starts() -> None:
    source = SequenceSource(observed(node("com.example:id/home", "com.example"), sequence=841))
    binders = BlockingBinderFactory()
    transitions = TransitionEngine(
        source,
        binder_factory=binders,
        app_package="com.example",
        timeouts=timeouts(),
    )
    safety_release = asyncio.get_running_loop().call_later(0.3, binders.release.set)

    try:
        with pytest.raises(ScreenTimeoutError):
            await transitions.wait_for(Home, timeout=timedelta(milliseconds=20))
        assert await asyncio.to_thread(binders.started.wait, 0.2)
        assert len(binders.thread_ids) == 1
        assert binders.calls == []

        second = asyncio.create_task(transitions.wait_for(Home, timeout=timedelta(seconds=1)))
        try:
            await asyncio.sleep(0.02)
            assert not second.done()
            assert len(binders.thread_ids) == 1
            assert binders.calls == []
        finally:
            binders.release.set()
        bound = await asyncio.wait_for(second, timeout=0.2)
    finally:
        binders.release.set()
        safety_release.cancel()

    assert type(bound) is Home
    assert len(binders.thread_ids) == 2
    assert binders.calls == [Home, Home]


async def test_immediate_binder_exception_preserves_identity_and_cause() -> None:
    source = SequenceSource(observed(node("com.example:id/home", "com.example"), sequence=85))
    binders = FailingBinderFactory()
    transitions = TransitionEngine(
        source,
        binder_factory=binders,
        app_package="com.example",
        timeouts=timeouts(),
    )

    with pytest.raises(ValueError, match="binder dependency failed") as captured_error:
        await transitions.wait_for(Home, timeout=timedelta(seconds=1))

    assert captured_error.value is binders.error
    assert captured_error.value.__cause__ is binders.cause


async def test_late_binder_exception_is_drained_before_next_binding() -> None:
    source = SequenceSource(observed(node("com.example:id/home", "com.example"), sequence=851))
    binders = BlockingOnceFailingBinderFactory()
    transitions = TransitionEngine(
        source,
        binder_factory=binders,
        app_package="com.example",
        timeouts=timeouts(),
    )
    loop = asyncio.get_running_loop()
    unhandled: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda event_loop, context: unhandled.append(context))
    safety_release = loop.call_later(0.3, binders.release.set)

    try:
        with pytest.raises(ScreenTimeoutError):
            await transitions.wait_for(Home, timeout=timedelta(milliseconds=20))
        assert await asyncio.to_thread(binders.started.wait, 0.2)

        second = asyncio.create_task(transitions.wait_for(Home, timeout=timedelta(seconds=1)))
        try:
            await asyncio.sleep(0.02)
            assert not second.done()
            assert binders.invocation_count == 1
            binders.release.set()
            bound = await asyncio.wait_for(second, timeout=0.2)
        finally:
            binders.release.set()
            if not second.done():
                second.cancel()
                await asyncio.gather(second, return_exceptions=True)
        await asyncio.sleep(0)
    finally:
        binders.release.set()
        safety_release.cancel()
        loop.set_exception_handler(previous_handler)

    assert type(bound) is Home
    assert binders.completed.is_set()
    assert binders.invocation_count == 2
    assert binders.error.__cause__ is binders.cause
    assert unhandled == []


async def test_parent_deadline_identity_reaches_source_and_hook() -> None:
    source = SequenceSource(observed(node("com.example:id/home", "com.example"), sequence=86))
    binders = RecordingBinderFactory()
    hook = SequenceHook()
    clock = ManualClock()
    parent = OperationDeadline.start(timedelta(seconds=1), clock=clock)
    transitions = engine(source, binders, clock, hook=hook)

    home = await transitions.wait_for(Home, deadline=parent)

    assert type(home) is Home
    assert source.calls[0][1] is parent
    assert hook.calls[0][1] is parent


async def test_parent_deadline_and_timeout_are_mutually_exclusive() -> None:
    source = SequenceSource(observed(sequence=87))
    binders = RecordingBinderFactory()
    clock = ManualClock()
    parent = OperationDeadline.start(timedelta(seconds=1), clock=clock)

    with pytest.raises(ValueError, match=r"timeout.*deadline"):
        await engine(source, binders, clock).wait_for(
            Home,
            timeout=timedelta(seconds=1),
            deadline=parent,
        )

    assert source.calls == []


async def test_zero_initial_retry_delay_uses_a_positive_floor() -> None:
    source = SequenceSource(observed(sequence=91))
    binders = RecordingBinderFactory()
    clock = ManualClock()
    configured = timeouts(
        transition=timedelta(milliseconds=3),
        delay=timedelta(),
        maximum_delay=timedelta(milliseconds=2),
    )

    with pytest.raises(ScreenTimeoutError):
        await engine(
            source,
            binders,
            clock,
            configured_timeouts=configured,
        ).wait_for(Home)

    assert clock.sleeps
    assert all(0 < delay <= 0.002 for delay in clock.sleeps)
    assert sum(clock.sleeps) == pytest.approx(0.003)


async def test_initial_retry_delay_is_capped_by_maximum_delay() -> None:
    source = SequenceSource(observed(sequence=92))
    binders = RecordingBinderFactory()
    clock = ManualClock()
    configured = timeouts(
        transition=timedelta(milliseconds=45),
        delay=timedelta(milliseconds=100),
        maximum_delay=timedelta(milliseconds=20),
    )

    with pytest.raises(ScreenTimeoutError):
        await engine(
            source,
            binders,
            clock,
            configured_timeouts=configured,
        ).wait_for(Home)

    assert clock.sleeps == [
        pytest.approx(0.02),
        pytest.approx(0.02),
        pytest.approx(0.005),
    ]
