"""Bounded, observation-driven back-stack recovery tests."""

from __future__ import annotations

import asyncio
import gc
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from threading import Event, Timer
from typing import Any, assert_type, cast

import pytest

from appwright.errors import IndeterminateActionError
from appwright.models.data import ElementSnapshot, ErrorDetails, Rect
from appwright.models.enums import ActionKind, ErrorCode
from appwright.observations import Observation, parse_hierarchy
from appwright.operations import (
    ActionReceipt,
    DispatchState,
    OperationDeadline,
    OperationStage,
    ReplaySafety,
)
from appwright.screens.elements import ElementBinder, by_id
from appwright.screens.model import (
    AsyncScreen,
    DeviceScope,
    DeviceScreen,
    Readiness,
    Screen,
    visible,
)
from appwright.screens.readiness import ReadinessEvaluation, evaluate_readiness
from appwright.screens.recovery import (
    BackRecovery,
    RecoveryEngine,
    RecoveryError,
    RecoveryFailureReason,
    back_until,
)
from appwright.screens.targets import ScreenDefinition

APP_PACKAGE = "com.example"


class Home(Screen):
    ready = visible(by_id(f"{APP_PACKAGE}:id/home"))


class Permission(DeviceScreen):
    ready = visible(by_id("com.android.permissioncontroller:id/permission_allow_button"))


class AsyncHome(AsyncScreen):
    ready = visible(by_id(f"{APP_PACKAGE}:id/home"))


def hierarchy(*resource_ids: tuple[str, str]) -> str:
    nodes = "".join(
        (
            f'<node text="{resource_id}" resource-id="{resource_id}" '
            f'class="android.widget.TextView" package="{package}" '
            'enabled="true" bounds="[0,0][100,100]" displayed="true" />'
        )
        for resource_id, package in resource_ids
    )
    return f'<hierarchy rotation="0">{nodes}</hierarchy>'


def observed(sequence: int, *resource_ids: tuple[str, str]) -> Observation:
    return parse_hierarchy(
        hierarchy(*resource_ids),
        sequence=sequence,
        package=None,
    )


LOGIN = (f"{APP_PACKAGE}:id/login", APP_PACKAGE)
MENU = (f"{APP_PACKAGE}:id/menu", APP_PACKAGE)
DETAILS = (f"{APP_PACKAGE}:id/details", APP_PACKAGE)
HOME = (f"{APP_PACKAGE}:id/home", APP_PACKAGE)
ALLOW = (
    "com.android.permissioncontroller:id/permission_allow_button",
    "com.android.permissioncontroller",
)


def indeterminate_back_error() -> IndeterminateActionError:
    receipt = ActionReceipt(
        action=ActionKind.PRESS,
        locator="key='BACK'",
        replay_safety=ReplaySafety.NON_REPLAYABLE,
        stage=OperationStage.DISPATCH,
        dispatch_state=DispatchState.UNKNOWN,
        started_at=datetime.now(UTC),
        pre_action=ElementSnapshot(
            identity="device-keyboard",
            displayed=True,
            enabled=True,
            selected=False,
            checked=False,
            checkable=False,
            focusable=False,
            focused=False,
            editable=False,
            bounds=Rect(x=0, y=0, width=1, height=1),
        ),
    )
    return IndeterminateActionError(
        ErrorDetails(
            code=ErrorCode.INDETERMINATE_ACTION,
            api_name="keyboard.press",
            message="BACK dispatch outcome is unknown",
        ),
        receipt,
    )


class SequenceSource:
    def __init__(self, *observations: Observation) -> None:
        self.observations = list(observations)
        self.packages: list[str | None] = []
        self.deadlines: list[OperationDeadline] = []
        self.active = 0
        self.maximum_active = 0

    async def capture(
        self,
        package: str | None,
        deadline: OperationDeadline,
    ) -> Observation:
        self.packages.append(package)
        self.deadlines.append(deadline)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            await asyncio.sleep(0)
            if not self.observations:
                raise AssertionError("unexpected observation capture")
            return self.observations.pop(0)
        finally:
            self.active -= 1


class RecordingBinderFactory:
    def __init__(self) -> None:
        self.app_binder = cast(ElementBinder, object())
        self.device_binder = cast(ElementBinder, object())
        self.screen_types: list[type[ScreenDefinition]] = []

    def __call__(self, screen_type: type[ScreenDefinition], /) -> ElementBinder:
        self.screen_types.append(screen_type)
        if screen_type.scope is DeviceScope:
            return self.device_binder
        return self.app_binder


class BackRecorder:
    def __init__(self) -> None:
        self.calls = 0
        self.active = 0
        self.maximum_active = 0

    async def __call__(self) -> None:
        self.calls += 1
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            await asyncio.sleep(0)
        finally:
            self.active -= 1


class Hook:
    def __init__(self, *handled: bool) -> None:
        self.handled = list(handled)
        self.sequences: list[int] = []
        self.deadlines: list[OperationDeadline] = []

    async def handle(
        self,
        observation: Observation,
        parent_deadline: OperationDeadline,
    ) -> bool:
        self.sequences.append(observation.sequence)
        self.deadlines.append(parent_deadline)
        if not self.handled:
            return False
        return self.handled.pop(0)


def engine(
    source: SequenceSource,
    back: BackRecorder,
    binders: RecordingBinderFactory,
    *,
    interruption_hook: Hook | None = None,
    retry_delay: timedelta = timedelta(),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> RecoveryEngine[ScreenDefinition]:
    return RecoveryEngine(
        source,
        binder_factory=binders,
        app_package=APP_PACKAGE,
        press_back=back,
        retry_delay=retry_delay,
        sleep=sleep,
        interruption_hook=interruption_hook,
    )


async def test_already_ready_returns_bound_screen_without_pressing_back() -> None:
    source = SequenceSource(observed(1, HOME))
    back = BackRecorder()
    binders = RecordingBinderFactory()
    deadline = OperationDeadline.start(timedelta(seconds=1))

    result = await engine(source, back, binders).ensure(
        Home,
        back_until(Home),
        deadline,
    )

    assert_type(result, Home)
    assert isinstance(result, Home)
    assert result.binder is binders.app_binder
    assert back.calls == 0
    assert binders.screen_types == [Home]
    assert source.packages == [None]
    assert source.deadlines == [deadline]


async def test_recovery_succeeds_after_n_serial_back_steps() -> None:
    source = SequenceSource(
        observed(1, DETAILS),
        observed(2, MENU),
        observed(3, HOME),
    )
    back = BackRecorder()
    binders = RecordingBinderFactory()

    result = await engine(source, back, binders).ensure(
        Home,
        back_until(Home, max_steps=3),
        OperationDeadline.start(timedelta(seconds=1)),
    )

    assert isinstance(result, Home)
    assert back.calls == 2
    assert back.maximum_active == 1
    assert source.maximum_active == 1
    assert source.packages == [None, None, None]


async def test_recovery_can_succeed_on_the_exact_maximum_step() -> None:
    source = SequenceSource(
        observed(1, DETAILS),
        observed(2, MENU),
        observed(3, HOME),
    )
    back = BackRecorder()
    binders = RecordingBinderFactory()

    result = await engine(source, back, binders).ensure(
        Home,
        back_until(Home, max_steps=2),
        OperationDeadline.start(timedelta(seconds=1)),
    )

    assert isinstance(result, Home)
    assert back.calls == 2


async def test_zero_steps_observes_once_and_never_presses_back() -> None:
    source = SequenceSource(observed(1, LOGIN))
    back = BackRecorder()
    binders = RecordingBinderFactory()

    with pytest.raises(RecoveryError) as captured:
        await engine(source, back, binders).ensure(
            Home,
            back_until(Home, max_steps=0),
            OperationDeadline.start(timedelta(seconds=1)),
        )

    error = captured.value
    assert error.reason is RecoveryFailureReason.EXHAUSTED
    assert error.attempts == 0
    assert len(error.history) == 1
    assert back.calls == 0
    assert source.packages == [None]


async def test_device_screen_uses_device_scope_and_concrete_binder_selection() -> None:
    source = SequenceSource(observed(1, ALLOW))
    back = BackRecorder()
    binders = RecordingBinderFactory()

    result = await engine(source, back, binders).ensure(
        Permission,
        back_until(Permission),
        OperationDeadline.start(timedelta(seconds=1)),
    )

    assert_type(result, Permission)
    assert isinstance(result, Permission)
    assert result.binder is binders.device_binder
    assert binders.screen_types == [Permission]
    assert source.packages == [None]


async def test_app_screen_does_not_match_device_package_content() -> None:
    source = SequenceSource(observed(1, (f"{APP_PACKAGE}:id/home", "android")))
    back = BackRecorder()
    binders = RecordingBinderFactory()

    with pytest.raises(RecoveryError) as captured:
        await engine(source, back, binders).ensure(
            Home,
            back_until(Home, max_steps=0),
            OperationDeadline.start(timedelta(seconds=1)),
        )

    assert captured.value.reason is RecoveryFailureReason.EXHAUSTED
    assert back.calls == 0


async def test_retry_delay_is_capped_by_parent_deadline() -> None:
    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()
    source = SequenceSource(observed(1, LOGIN))
    back = BackRecorder()
    binders = RecordingBinderFactory()
    sleeps: list[float] = []

    async def advance_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.value += seconds

    deadline = OperationDeadline.start(timedelta(milliseconds=50), clock=clock)

    with pytest.raises(RecoveryError) as captured:
        await engine(
            source,
            back,
            binders,
            retry_delay=timedelta(seconds=5),
            sleep=advance_sleep,
        ).ensure(Home, back_until(Home, max_steps=3), deadline)

    error = captured.value
    assert error.reason is RecoveryFailureReason.DEADLINE
    assert error.attempts == 1
    assert len(error.history) == 1
    assert sleeps == [pytest.approx(0.05)]
    assert back.calls == 1
    assert source.packages == [None]
    assert source.deadlines == [deadline]


async def test_readiness_evaluation_crossing_deadline_is_structured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()

    def evaluate_after_deadline(
        observation: Observation,
        readiness: Readiness,
        *,
        package: str | None,
    ) -> ReadinessEvaluation:
        result = evaluate_readiness(observation, readiness, package=package)
        clock.value = 2.0
        return result

    monkeypatch.setattr(
        "appwright.screens.recovery.evaluate_readiness",
        evaluate_after_deadline,
    )
    deadline = OperationDeadline.start(timedelta(seconds=1), clock=clock)

    with pytest.raises(RecoveryError) as captured:
        await engine(
            SequenceSource(observed(1, HOME)),
            BackRecorder(),
            RecordingBinderFactory(),
        ).ensure(Home, back_until(Home), deadline)

    error = captured.value
    assert error.reason is RecoveryFailureReason.DEADLINE
    assert error.attempts == 0
    assert error.observation_count == 1
    assert len(error.history) == 1
    assert error.history[0].observation_sequence == 1
    assert error.history[0].ready is True


async def test_binder_construction_crossing_deadline_is_structured() -> None:
    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    class DeadlineBinderFactory(RecordingBinderFactory):
        def __init__(self, clock: Clock) -> None:
            super().__init__()
            self.clock = clock

        def __call__(self, screen_type: type[ScreenDefinition], /) -> ElementBinder:
            binder = super().__call__(screen_type)
            self.clock.value = 2.0
            return binder

    clock = Clock()
    deadline = OperationDeadline.start(timedelta(seconds=1), clock=clock)

    with pytest.raises(RecoveryError) as captured:
        await engine(
            SequenceSource(observed(1, HOME)),
            BackRecorder(),
            DeadlineBinderFactory(clock),
        ).ensure(Home, back_until(Home), deadline)

    error = captured.value
    assert error.reason is RecoveryFailureReason.DEADLINE
    assert error.attempts == 0
    assert error.observation_count == 1
    assert len(error.history) == 1
    assert error.history[0].observation_sequence == 1
    assert error.history[0].ready is True


async def test_capture_dependency_timeout_propagates_with_original_cause() -> None:
    cause = RuntimeError("capture root cause")
    dependency_timeout = TimeoutError("capture dependency timed out early")

    class FailingSource:
        async def capture(
            self,
            package: str | None,
            deadline: OperationDeadline,
        ) -> Observation:
            del package, deadline
            raise dependency_timeout from cause

    recovery_engine = RecoveryEngine(
        FailingSource(),
        binder_factory=RecordingBinderFactory(),
        app_package=APP_PACKAGE,
        press_back=BackRecorder(),
        retry_delay=timedelta(),
    )

    with pytest.raises(TimeoutError) as captured:
        await recovery_engine.ensure(
            Home,
            back_until(Home),
            OperationDeadline.start(timedelta(seconds=1)),
        )

    assert captured.value is dependency_timeout
    assert captured.value.__cause__ is cause


async def test_interruption_dependency_timeout_propagates_with_original_cause() -> None:
    cause = RuntimeError("interruption root cause")
    dependency_timeout = TimeoutError("interruption dependency timed out early")

    class FailingHook:
        async def handle(
            self,
            observation: Observation,
            parent_deadline: OperationDeadline,
        ) -> bool:
            del observation, parent_deadline
            raise dependency_timeout from cause

    recovery_engine = RecoveryEngine(
        SequenceSource(observed(1, HOME)),
        binder_factory=RecordingBinderFactory(),
        app_package=APP_PACKAGE,
        press_back=BackRecorder(),
        retry_delay=timedelta(),
        interruption_hook=FailingHook(),
    )

    with pytest.raises(TimeoutError) as captured:
        await recovery_engine.ensure(
            Home,
            back_until(Home),
            OperationDeadline.start(timedelta(seconds=1)),
        )

    assert captured.value is dependency_timeout
    assert captured.value.__cause__ is cause


async def test_back_dependency_timeout_propagates_with_original_cause() -> None:
    cause = RuntimeError("back root cause")
    dependency_timeout = TimeoutError("back dependency timed out early")

    async def failing_back() -> None:
        raise dependency_timeout from cause

    recovery_engine = RecoveryEngine(
        SequenceSource(observed(1, LOGIN)),
        binder_factory=RecordingBinderFactory(),
        app_package=APP_PACKAGE,
        press_back=failing_back,
        retry_delay=timedelta(),
    )

    with pytest.raises(TimeoutError) as captured:
        await recovery_engine.ensure(
            Home,
            back_until(Home),
            OperationDeadline.start(timedelta(seconds=1)),
        )

    assert captured.value is dependency_timeout
    assert captured.value.__cause__ is cause


async def test_sleep_dependency_timeout_propagates_with_original_cause() -> None:
    cause = RuntimeError("sleep root cause")
    dependency_timeout = TimeoutError("sleep dependency timed out early")

    async def failing_sleep(seconds: float) -> None:
        del seconds
        raise dependency_timeout from cause

    recovery_engine = RecoveryEngine(
        SequenceSource(observed(1, LOGIN)),
        binder_factory=RecordingBinderFactory(),
        app_package=APP_PACKAGE,
        press_back=BackRecorder(),
        retry_delay=timedelta(milliseconds=1),
        sleep=failing_sleep,
    )

    with pytest.raises(TimeoutError) as captured:
        await recovery_engine.ensure(
            Home,
            back_until(Home),
            OperationDeadline.start(timedelta(seconds=1)),
        )

    assert captured.value is dependency_timeout
    assert captured.value.__cause__ is cause


async def test_handled_interruption_suppresses_pre_dismissal_observation() -> None:
    # The same target-ready hierarchy is returned twice. The first observation must
    # be discarded because the interruption hook owns it; the second may be read.
    source = SequenceSource(observed(1, HOME), observed(2, HOME))
    back = BackRecorder()
    binders = RecordingBinderFactory()
    hook = Hook(True, False)
    deadline = OperationDeadline.start(timedelta(seconds=1))

    result = await engine(
        source,
        back,
        binders,
        interruption_hook=hook,
    ).ensure(Home, back_until(Home, max_steps=0), deadline)

    assert isinstance(result, Home)
    assert source.packages == [None, None]
    assert hook.sequences == [1, 2]
    assert hook.deadlines == [deadline, deadline]
    assert back.calls == 0


async def test_repeated_handled_state_is_owned_by_interruption_wait() -> None:
    source = SequenceSource(
        observed(1, HOME),
        observed(2, HOME),
        observed(3, HOME),
    )
    back = BackRecorder()
    binders = RecordingBinderFactory()
    hook = Hook(True, True, False)

    result = await engine(
        source,
        back,
        binders,
        interruption_hook=hook,
    ).ensure(
        Home,
        back_until(Home, max_steps=0),
        OperationDeadline.start(timedelta(seconds=1)),
    )

    assert isinstance(result, Home)
    assert hook.sequences == [1, 2, 3]
    assert back.calls == 0


async def test_waiting_for_serial_operation_lock_honors_parent_deadline() -> None:
    class BlockingSource:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def capture(
            self,
            package: str | None,
            deadline: OperationDeadline,
        ) -> Observation:
            del package, deadline
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            return observed(self.calls, HOME)

    source = BlockingSource()
    back = BackRecorder()
    binders = RecordingBinderFactory()
    recovery_engine = RecoveryEngine(
        source,
        binder_factory=binders,
        app_package=APP_PACKAGE,
        press_back=back,
        retry_delay=timedelta(),
    )
    first = asyncio.create_task(
        recovery_engine.ensure(
            Home,
            back_until(Home),
            OperationDeadline.start(timedelta(seconds=1)),
        )
    )
    await source.entered.wait()

    async def release_later() -> None:
        await asyncio.sleep(0.1)
        source.release.set()

    releaser = asyncio.create_task(release_later())
    started_at = asyncio.get_running_loop().time()
    try:
        with pytest.raises(RecoveryError) as captured:
            await recovery_engine.ensure(
                Home,
                back_until(Home),
                OperationDeadline.start(timedelta(milliseconds=15)),
            )
        elapsed = asyncio.get_running_loop().time() - started_at
    finally:
        source.release.set()
        await first
        await releaser

    assert captured.value.reason is RecoveryFailureReason.DEADLINE
    assert elapsed < 0.07
    assert source.calls == 1


async def test_one_unchanged_transient_state_can_recover_on_next_back() -> None:
    source = SequenceSource(
        observed(10, LOGIN),
        observed(11, LOGIN),
        observed(12, HOME),
    )
    back = BackRecorder()
    binders = RecordingBinderFactory()

    result = await engine(source, back, binders).ensure(
        Home,
        back_until(Home, max_steps=2),
        OperationDeadline.start(timedelta(seconds=1)),
    )

    assert isinstance(result, Home)
    assert back.calls == 2


async def test_interruption_segments_back_stack_loop_tracking() -> None:
    source = SequenceSource(
        observed(20, LOGIN),
        observed(21, ALLOW),
        observed(22, LOGIN),
        observed(23, HOME),
    )
    back = BackRecorder()
    binders = RecordingBinderFactory()
    hook = Hook(False, True, False, False)

    result = await engine(
        source,
        back,
        binders,
        interruption_hook=hook,
    ).ensure(
        Home,
        back_until(Home, max_steps=2),
        OperationDeadline.start(timedelta(seconds=1)),
    )

    assert isinstance(result, Home)
    assert hook.sequences == [20, 21, 22, 23]
    assert back.calls == 2


async def test_persistent_repeated_state_with_new_sequences_is_reported_as_loop() -> None:
    source = SequenceSource(
        observed(30, LOGIN),
        observed(31, LOGIN),
        observed(32, LOGIN),
    )
    back = BackRecorder()
    binders = RecordingBinderFactory()

    with pytest.raises(RecoveryError) as captured:
        await engine(source, back, binders).ensure(
            Home,
            back_until(Home, max_steps=6),
            OperationDeadline.start(timedelta(seconds=1)),
        )

    error = captured.value
    assert error.reason is RecoveryFailureReason.LOOP
    assert error.attempts == 2
    assert tuple(entry.observation_sequence for entry in error.history) == (30, 31, 32)
    assert len({entry.state_signature for entry in error.history}) == 1
    assert back.calls == 2


async def test_exhaustion_retains_immutable_structured_history() -> None:
    source = SequenceSource(
        observed(1, LOGIN),
        observed(2, DETAILS),
        observed(3, MENU),
    )
    back = BackRecorder()
    binders = RecordingBinderFactory()

    with pytest.raises(RecoveryError) as captured:
        await engine(source, back, binders).ensure(
            Home,
            back_until(Home, max_steps=2),
            OperationDeadline.start(timedelta(seconds=1)),
        )

    error = captured.value
    assert error.reason is RecoveryFailureReason.EXHAUSTED
    assert error.screen_type is Home
    assert error.attempts == 2
    assert tuple(entry.back_attempt for entry in error.history) == (0, 1, 2)
    assert all(entry.readiness_checked for entry in error.history)
    assert all(entry.ready is False for entry in error.history)
    assert all(entry.interruption_handled is False for entry in error.history)
    assert "Home" in str(error)
    assert "exhausted" in str(error)


async def test_handled_observation_history_is_bounded_with_total_count() -> None:
    class Clock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            return self.value

    class EndlessSource:
        def __init__(self) -> None:
            self.calls = 0
            self.template = observed(0, HOME)

        async def capture(
            self,
            package: str | None,
            deadline: OperationDeadline,
        ) -> Observation:
            del package, deadline
            self.calls += 1
            return self.template.model_copy(update={"sequence": self.calls})

    class AdvancingHandledHook:
        def __init__(self, clock: Clock) -> None:
            self.clock = clock
            self.calls = 0

        async def handle(
            self,
            observation: Observation,
            parent_deadline: OperationDeadline,
        ) -> bool:
            del observation, parent_deadline
            self.calls += 1
            self.clock.value += 1.0
            return True

    clock = Clock()
    source = EndlessSource()
    hook = AdvancingHandledHook(clock)
    recovery_engine = RecoveryEngine(
        source,
        binder_factory=RecordingBinderFactory(),
        app_package=APP_PACKAGE,
        press_back=BackRecorder(),
        retry_delay=timedelta(),
        interruption_hook=hook,
        history_limit=7,
    )

    with pytest.raises(RecoveryError) as captured:
        await recovery_engine.ensure(
            Home,
            back_until(Home, max_steps=0),
            OperationDeadline.start(timedelta(seconds=850), clock=clock),
        )

    error = captured.value
    assert error.reason is RecoveryFailureReason.DEADLINE
    assert error.attempts == 0
    assert error.observation_count == 850
    assert len(error.history) == 7
    assert tuple(entry.observation_sequence for entry in error.history) == tuple(range(844, 851))
    assert error.history[-1].hook_timed_out
    assert source.calls == 850
    assert hook.calls == 850


async def test_cancellation_resistant_hook_cannot_outlive_recovery_deadline() -> None:
    release = asyncio.Event()
    release_handle = asyncio.get_running_loop().call_later(0.2, release.set)

    class CancellationResistantHook:
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
                await release.wait()
                raise RuntimeError("late hook failure") from None

    recovery_engine = RecoveryEngine(
        SequenceSource(observed(1, HOME)),
        binder_factory=RecordingBinderFactory(),
        app_package=APP_PACKAGE,
        press_back=BackRecorder(),
        retry_delay=timedelta(),
        interruption_hook=CancellationResistantHook(),
    )

    started_at = asyncio.get_running_loop().time()
    try:
        with pytest.raises(RecoveryError) as captured:
            await recovery_engine.ensure(
                Home,
                back_until(Home),
                OperationDeadline.start(timedelta(milliseconds=10)),
            )
        elapsed = asyncio.get_running_loop().time() - started_at
    finally:
        release.set()
        release_handle.cancel()
        await asyncio.sleep(0)

    assert captured.value.reason is RecoveryFailureReason.DEADLINE
    assert captured.value.observation_count == 1
    assert len(captured.value.history) == 1
    assert captured.value.history[0].observation_sequence == 1
    assert captured.value.history[0].hook_timed_out
    assert not captured.value.history[0].readiness_checked
    assert elapsed < 0.08


async def test_late_hook_is_drained_before_a_later_recovery_observes() -> None:
    class CancellationResistantHook:
        def __init__(self) -> None:
            self.calls = 0
            self.active = 0
            self.maximum_active = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def handle(
            self,
            observation: Observation,
            parent_deadline: OperationDeadline,
        ) -> bool:
            del observation, parent_deadline
            self.calls += 1
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            try:
                if self.calls == 1:
                    self.started.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        await self.release.wait()
                return False
            finally:
                self.active -= 1

    source = SequenceSource(observed(1, HOME), observed(2, HOME))
    hook = CancellationResistantHook()
    recovery_engine = RecoveryEngine(
        source,
        binder_factory=RecordingBinderFactory(),
        app_package=APP_PACKAGE,
        press_back=BackRecorder(),
        retry_delay=timedelta(),
        interruption_hook=hook,
    )

    with pytest.raises(RecoveryError) as first:
        await recovery_engine.ensure(
            Home,
            back_until(Home),
            OperationDeadline.start(timedelta(milliseconds=10)),
        )
    assert first.value.reason is RecoveryFailureReason.DEADLINE
    await hook.started.wait()

    second = asyncio.create_task(
        recovery_engine.ensure(
            Home,
            back_until(Home),
            OperationDeadline.start(timedelta(seconds=1)),
        )
    )
    await asyncio.sleep(0.01)
    assert hook.calls == 1
    assert source.packages == [None]
    assert not second.done()

    hook.release.set()
    result = await second
    assert isinstance(result, Home)
    assert hook.calls == 2
    assert hook.maximum_active == 1


async def test_deadline_boundary_retains_completed_dependency_failure_as_cause() -> None:
    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    dependency_error = RuntimeError("capture failed at deadline")
    clock = Clock()

    class BoundaryFailingSource:
        async def capture(
            self,
            package: str | None,
            deadline: OperationDeadline,
        ) -> Observation:
            del package, deadline
            clock.value = 1.0
            raise dependency_error

    recovery_engine = RecoveryEngine(
        BoundaryFailingSource(),
        binder_factory=RecordingBinderFactory(),
        app_package=APP_PACKAGE,
        press_back=BackRecorder(),
        retry_delay=timedelta(),
    )

    with pytest.raises(RecoveryError) as captured:
        await recovery_engine.ensure(
            Home,
            back_until(Home),
            OperationDeadline.start(timedelta(seconds=1), clock=clock),
        )

    assert captured.value.reason is RecoveryFailureReason.DEADLINE
    assert captured.value.__cause__ is dependency_error


async def test_blocking_binder_construction_is_hard_bounded_by_deadline() -> None:
    release = Event()
    timer = Timer(0.2, release.set)

    class BlockingBinderFactory(RecordingBinderFactory):
        def __call__(self, screen_type: type[ScreenDefinition], /) -> ElementBinder:
            release.wait()
            return super().__call__(screen_type)

    timer.start()
    started_at = asyncio.get_running_loop().time()
    try:
        with pytest.raises(RecoveryError) as captured:
            await RecoveryEngine(
                SequenceSource(observed(1, HOME)),
                binder_factory=BlockingBinderFactory(),
                app_package=APP_PACKAGE,
                press_back=BackRecorder(),
                retry_delay=timedelta(),
            ).ensure(
                Home,
                back_until(Home),
                OperationDeadline.start(timedelta(milliseconds=10)),
            )
        elapsed = asyncio.get_running_loop().time() - started_at
    finally:
        release.set()
        timer.cancel()

    assert captured.value.reason is RecoveryFailureReason.DEADLINE
    assert elapsed < 0.08


async def test_cancelled_recovery_never_overlaps_its_in_flight_back_action() -> None:
    class ControlledBack:
        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def __call__(self) -> None:
            self.calls += 1
            self.started.set()
            await self.release.wait()

    source = SequenceSource(observed(1, LOGIN), observed(2, HOME))
    back = ControlledBack()
    recovery_engine = RecoveryEngine(
        source,
        binder_factory=RecordingBinderFactory(),
        app_package=APP_PACKAGE,
        press_back=back,
        retry_delay=timedelta(),
    )
    first = asyncio.create_task(
        recovery_engine.ensure(
            Home,
            back_until(Home, max_steps=1),
            OperationDeadline.start(timedelta(seconds=1)),
        )
    )
    await back.started.wait()

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    with pytest.raises(RecoveryError) as captured:
        await recovery_engine.ensure(
            Home,
            back_until(Home, max_steps=1),
            OperationDeadline.start(timedelta(milliseconds=10)),
        )

    assert captured.value.reason is RecoveryFailureReason.DEADLINE
    assert back.calls == 1
    assert source.packages == [None]

    back.release.set()
    await asyncio.sleep(0)
    result = await recovery_engine.ensure(
        Home,
        back_until(Home, max_steps=1),
        OperationDeadline.start(timedelta(seconds=1)),
    )
    assert isinstance(result, Home)
    assert back.calls == 1


async def test_indeterminate_back_failure_is_retained_and_never_replayed() -> None:
    error = indeterminate_back_error()

    class IndeterminateBack:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self) -> None:
            self.calls += 1
            raise error

    source = SequenceSource(observed(1, LOGIN), observed(2, LOGIN))
    back = IndeterminateBack()
    recovery_engine = RecoveryEngine(
        source,
        binder_factory=RecordingBinderFactory(),
        app_package=APP_PACKAGE,
        press_back=back,
        retry_delay=timedelta(),
    )

    with pytest.raises(IndeterminateActionError) as first:
        await recovery_engine.ensure(
            Home,
            back_until(Home, max_steps=1),
            OperationDeadline.start(timedelta(seconds=1)),
        )
    with pytest.raises(IndeterminateActionError) as second:
        await recovery_engine.ensure(
            Home,
            back_until(Home, max_steps=1),
            OperationDeadline.start(timedelta(seconds=1)),
        )

    assert first.value is error
    assert second.value is error
    assert first.value.receipt is error.receipt
    assert back.calls == 1
    assert source.packages == [None]


async def test_abandoned_back_failure_is_consumed_without_loop_warning() -> None:
    error = indeterminate_back_error()

    class LateFailingBack:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.completed = asyncio.Event()

        async def __call__(self) -> None:
            self.started.set()
            try:
                await self.release.wait()
                raise error
            finally:
                self.completed.set()

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    contexts: list[dict[str, Any]] = []
    loop.set_exception_handler(lambda event_loop, context: contexts.append(context))
    back = LateFailingBack()
    recovery_engine = RecoveryEngine(
        SequenceSource(observed(1, LOGIN)),
        binder_factory=RecordingBinderFactory(),
        app_package=APP_PACKAGE,
        press_back=back,
        retry_delay=timedelta(),
    )
    recovery_task = asyncio.create_task(
        recovery_engine.ensure(
            Home,
            back_until(Home, max_steps=1),
            OperationDeadline.start(timedelta(seconds=1)),
        )
    )
    await back.started.wait()

    try:
        recovery_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await recovery_task
        back.release.set()
        await back.completed.wait()
        await asyncio.sleep(0)
        del recovery_task
        del recovery_engine
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert not any(
        context.get("message") == "Task exception was never retrieved" for context in contexts
    )


async def test_timed_out_binder_is_drained_before_another_factory_call() -> None:
    class SerialBinderFactory:
        def __init__(self) -> None:
            self.calls = 0
            self.active = 0
            self.maximum_active = 0
            self.started = Event()
            self.release = Event()
            self.completed = Event()
            self.binder = cast(ElementBinder, object())

        def __call__(self, screen_type: type[ScreenDefinition], /) -> ElementBinder:
            del screen_type
            self.calls += 1
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            try:
                if self.calls == 1:
                    self.started.set()
                    self.release.wait()
                return self.binder
            finally:
                self.active -= 1
                self.completed.set()

    binders = SerialBinderFactory()
    recovery_engine = RecoveryEngine(
        SequenceSource(observed(1, HOME), observed(2, HOME), observed(3, HOME)),
        binder_factory=binders,
        app_package=APP_PACKAGE,
        press_back=BackRecorder(),
        retry_delay=timedelta(),
    )

    try:
        with pytest.raises(RecoveryError) as first:
            await recovery_engine.ensure(
                Home,
                back_until(Home),
                OperationDeadline.start(timedelta(milliseconds=10)),
            )
        with pytest.raises(RecoveryError) as second:
            await recovery_engine.ensure(
                Home,
                back_until(Home),
                OperationDeadline.start(timedelta(milliseconds=10)),
            )
        assert first.value.reason is RecoveryFailureReason.DEADLINE
        assert second.value.reason is RecoveryFailureReason.DEADLINE
        assert binders.calls == 1
        assert binders.maximum_active == 1
    finally:
        binders.release.set()
    assert binders.completed.wait(timeout=0.2)

    result = await recovery_engine.ensure(
        Home,
        back_until(Home),
        OperationDeadline.start(timedelta(seconds=1)),
    )

    assert isinstance(result, Home)
    assert binders.calls == 2
    assert binders.maximum_active == 1


def test_back_until_is_immutable_generic_and_validates_nonnegative_steps() -> None:
    recovery = back_until(Home, max_steps=4)
    async_recovery = back_until(AsyncHome, max_steps=2)

    assert_type(recovery, BackRecovery[Home])
    assert_type(async_recovery, BackRecovery[AsyncHome])
    assert recovery.screen is Home
    assert recovery.max_steps == 4
    assert async_recovery.screen is AsyncHome
    with pytest.raises(ValueError, match="max_steps"):
        back_until(Home, max_steps=-1)
    with pytest.raises(TypeError, match="max_steps"):
        back_until(Home, max_steps=True)
    with pytest.raises(TypeError, match="max_steps"):
        back_until(Home, max_steps=cast(Any, 1.5))


def test_recovery_history_limit_must_be_a_positive_integer() -> None:
    constructor = {
        "observations": SequenceSource(),
        "binder_factory": RecordingBinderFactory(),
        "app_package": APP_PACKAGE,
        "press_back": BackRecorder(),
    }

    with pytest.raises(ValueError, match="history_limit"):
        RecoveryEngine(**constructor, history_limit=0)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="history_limit"):
        RecoveryEngine(**constructor, history_limit=True)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="history_limit"):
        RecoveryEngine(
            **constructor,  # type: ignore[arg-type]
            history_limit=cast(Any, 1.5),
        )


class SyncOnlyBinderFactory:
    def __call__(self, screen_type: type[Screen[Any]], /) -> ElementBinder:
        del screen_type
        return cast(ElementBinder, object())


class AsyncOnlyBinderFactory:
    def __call__(self, screen_type: type[AsyncScreen[Any]], /) -> ElementBinder:
        del screen_type
        return cast(ElementBinder, object())


async def typed_recovery_engine_modes(
    source: SequenceSource,
    back: BackRecorder,
    deadline: OperationDeadline,
) -> None:
    sync_engine = RecoveryEngine(
        source,
        binder_factory=SyncOnlyBinderFactory(),
        app_package=APP_PACKAGE,
        press_back=back,
    )
    async_engine = RecoveryEngine(
        source,
        binder_factory=AsyncOnlyBinderFactory(),
        app_package=APP_PACKAGE,
        press_back=back,
    )
    broad_engine = RecoveryEngine(
        source,
        binder_factory=RecordingBinderFactory(),
        app_package=APP_PACKAGE,
        press_back=back,
    )

    assert_type(sync_engine, RecoveryEngine[Screen[Any]])
    assert_type(async_engine, RecoveryEngine[AsyncScreen[Any]])
    assert_type(broad_engine, RecoveryEngine[ScreenDefinition])
    assert_type(
        await sync_engine.ensure(Home, back_until(Home), deadline),
        Home,
    )
    assert_type(
        await async_engine.ensure(AsyncHome, back_until(AsyncHome), deadline),
        AsyncHome,
    )
    assert_type(
        await broad_engine.ensure(Home, back_until(Home), deadline),
        Home,
    )
    assert_type(
        await broad_engine.ensure(AsyncHome, back_until(AsyncHome), deadline),
        AsyncHome,
    )

    await sync_engine.ensure(AsyncHome, back_until(AsyncHome), deadline)  # type: ignore[type-var]
    await async_engine.ensure(Home, back_until(Home), deadline)  # type: ignore[type-var]
