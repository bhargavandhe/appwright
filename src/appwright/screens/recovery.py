"""Bounded, typed recovery of a requested screen through the device back stack."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from hashlib import blake2b
from typing import Any, Generic, Protocol, TypeAlias, TypeVar, cast, overload

from appwright.core.errors import IndeterminateActionError
from appwright.observations import Observation
from appwright.operations import OperationDeadline
from appwright.screens.elements import ElementBinder
from appwright.screens.model import AppScope, AsyncScreen, DeviceScope, Screen
from appwright.screens.readiness import evaluate_readiness
from appwright.screens.targets import ScreenDefinition

ScreenT = TypeVar("ScreenT", bound=ScreenDefinition)
AppScreenT = TypeVar("AppScreenT", bound=Screen[AppScope])
DeviceScreenT = TypeVar("DeviceScreenT", bound=Screen[DeviceScope])
AsyncAppScreenT = TypeVar("AsyncAppScreenT", bound=AsyncScreen[AppScope])
AsyncDeviceScreenT = TypeVar(
    "AsyncDeviceScreenT",
    bound=AsyncScreen[DeviceScope],
)
ResultT = TypeVar("ResultT")
FailureScreenT = TypeVar("FailureScreenT", bound=ScreenDefinition)
RecoveryModeT = TypeVar("RecoveryModeT", bound=ScreenDefinition)
BinderModeT = TypeVar(
    "BinderModeT",
    bound=ScreenDefinition,
    contravariant=True,
)
SyncResultT = TypeVar("SyncResultT", bound=Screen[Any])
AsyncResultT = TypeVar("AsyncResultT", bound=AsyncScreen[Any])


def _validate_max_steps(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("max_steps must be a nonnegative integer")
    if value < 0:
        raise ValueError("max_steps must be nonnegative")
    return value


def _validate_history_limit(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("history_limit must be a positive integer")
    if value < 1:
        raise ValueError("history_limit must be positive")
    return value


class ObservationSource(Protocol):
    """Serialized source of immutable, whole-device observations."""

    async def capture(
        self,
        package: str | None,
        deadline: OperationDeadline,
    ) -> Observation: ...


class InterruptionHook(Protocol):
    """Optional interruption handler shared with transition polling."""

    async def handle(
        self,
        observation: Observation,
        parent_deadline: OperationDeadline,
    ) -> bool: ...


class ScreenBinderFactory(Protocol[BinderModeT]):
    """Select a binder for a screen definition in one runtime mode."""

    def __call__(self, screen_type: type[BinderModeT], /) -> ElementBinder: ...


SyncScreenBinderFactory: TypeAlias = ScreenBinderFactory[Screen[Any]]
AsyncScreenBinderFactory: TypeAlias = ScreenBinderFactory[AsyncScreen[Any]]


@dataclass(frozen=True, slots=True)
class BackRecovery(Generic[ScreenT]):
    """A request to reveal ``screen`` using at most ``max_steps`` BACK presses."""

    screen: type[ScreenT]
    max_steps: int = 6

    def __post_init__(self) -> None:
        _validate_max_steps(self.max_steps)


@overload
def back_until(
    screen: type[AppScreenT],
    *,
    max_steps: int = 6,
) -> BackRecovery[AppScreenT]: ...


@overload
def back_until(
    screen: type[DeviceScreenT],
    *,
    max_steps: int = 6,
) -> BackRecovery[DeviceScreenT]: ...


@overload
def back_until(
    screen: type[AsyncAppScreenT],
    *,
    max_steps: int = 6,
) -> BackRecovery[AsyncAppScreenT]: ...


@overload
def back_until(
    screen: type[AsyncDeviceScreenT],
    *,
    max_steps: int = 6,
) -> BackRecovery[AsyncDeviceScreenT]: ...


def back_until(
    screen: type[ScreenDefinition],
    *,
    max_steps: int = 6,
) -> BackRecovery[Any]:
    """Build a typed, immutable bounded-back recovery request."""

    return BackRecovery(screen=screen, max_steps=max_steps)


class RecoveryFailureReason(StrEnum):
    """Terminal reason for a bounded recovery failure."""

    EXHAUSTED = "exhausted"
    DEADLINE = "deadline"
    LOOP = "loop"


@dataclass(frozen=True, slots=True)
class RecoveryHistoryEntry:
    """One observation considered by a recovery attempt."""

    observation_sequence: int
    state_signature: str
    back_attempt: int
    interruption_handled: bool
    readiness_checked: bool
    ready: bool | None
    matched: int | None
    total: int | None
    hook_timed_out: bool = False


class RecoveryError(RuntimeError):
    """Structured failure from bounded back-stack recovery."""

    def __init__(
        self,
        *,
        screen_type: type[ScreenDefinition],
        attempts: int,
        history: tuple[RecoveryHistoryEntry, ...],
        observation_count: int,
        reason: RecoveryFailureReason,
    ) -> None:
        self.screen_type = screen_type
        self.attempts = attempts
        self.history = history
        self.observation_count = observation_count
        self.reason = reason
        super().__init__(
            f"back recovery for {screen_type.__name__} {reason.value} "
            f"after {attempts} BACK attempt(s) and {observation_count} observation(s) "
            f"({len(history)} retained)"
        )


class _ParentDeadlineExpired(Exception):
    """Internal control flow translated into a structured recovery failure."""


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


def _state_signature(observation: Observation) -> str:
    """Fingerprint UI state while excluding capture metadata and element identities."""

    elements: list[tuple[object, ...]] = []
    for element in observation.elements:
        snapshot = element.snapshot
        elements.append(
            (
                element.parent,
                element.children,
                snapshot.text,
                snapshot.accessible_name,
                snapshot.resource_id,
                snapshot.class_name,
                snapshot.package_name,
                snapshot.displayed,
                snapshot.enabled,
                snapshot.selected,
                snapshot.checked,
                snapshot.checkable,
                snapshot.focusable,
                snapshot.focused,
                snapshot.editable,
                (
                    snapshot.bounds.x,
                    snapshot.bounds.y,
                    snapshot.bounds.width,
                    snapshot.bounds.height,
                ),
                snapshot.window_id,
                element.hint,
                element.clickable,
                element.heading,
                element.text_has_clickable_span,
            )
        )
    payload = repr(tuple(elements)).encode()
    return blake2b(payload, digest_size=16).hexdigest()


def _bind_screen(screen_type: type[ScreenT], binder: ElementBinder) -> ScreenT:
    constructor: Callable[[ElementBinder], ScreenT] = screen_type
    return constructor(binder)


def _build_bound_screen(
    screen_type: type[ScreenT],
    binder_factory: ScreenBinderFactory[ScreenT],
) -> ScreenT:
    return _bind_screen(screen_type, binder_factory(screen_type))


class RecoveryEngine(Generic[RecoveryModeT]):
    """Ensure a typed screen with bounded, serialized BACK recovery."""

    def __init__(
        self,
        observations: ObservationSource,
        *,
        binder_factory: ScreenBinderFactory[RecoveryModeT],
        app_package: str,
        press_back: Callable[[], Awaitable[None]],
        retry_delay: timedelta = timedelta(milliseconds=100),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        interruption_hook: InterruptionHook | None = None,
        history_limit: int = 100,
    ) -> None:
        if retry_delay < timedelta():
            raise ValueError("retry_delay must be nonnegative")
        _validate_history_limit(history_limit)
        self.observations = observations
        self.binder_factory = binder_factory
        self.app_package = app_package
        self.press_back = press_back
        self.retry_delay = retry_delay
        self.sleep_callback = sleep
        self.interruption_hook = interruption_hook
        self.history_limit = history_limit
        self.operation_lock = asyncio.Lock()
        self.pending_back: asyncio.Future[None] | None = None
        self.pending_binder: asyncio.Future[Any] | None = None
        self.pending_hook: asyncio.Future[bool] | None = None

    @overload
    async def ensure(
        self: RecoveryEngine[Screen[Any]],
        screen_type: type[SyncResultT],
        recovery: BackRecovery[SyncResultT],
        parent_deadline: OperationDeadline,
    ) -> SyncResultT: ...

    @overload
    async def ensure(
        self: RecoveryEngine[AsyncScreen[Any]],
        screen_type: type[AsyncResultT],
        recovery: BackRecovery[AsyncResultT],
        parent_deadline: OperationDeadline,
    ) -> AsyncResultT: ...

    @overload
    async def ensure(
        self: RecoveryEngine[ScreenDefinition],
        screen_type: type[ScreenT],
        recovery: BackRecovery[ScreenT],
        parent_deadline: OperationDeadline,
    ) -> ScreenT: ...

    async def ensure(
        self,
        screen_type: type[ScreenT],
        recovery: BackRecovery[ScreenT],
        parent_deadline: OperationDeadline,
    ) -> ScreenT:
        """Return ``screen_type``, pressing BACK only when initial readiness fails."""

        if recovery.screen is not screen_type:
            raise ValueError("recovery target must be the same screen passed to ensure")
        acquired = False

        async def acquire_operation_lock() -> None:
            nonlocal acquired
            await self.operation_lock.acquire()
            acquired = True

        try:
            await self._within_deadline(acquire_operation_lock, parent_deadline)
            return await self._ensure_serial(screen_type, recovery, parent_deadline)
        except _ParentDeadlineExpired as error:
            failure = self._failure(
                screen_type,
                0,
                [],
                0,
                RecoveryFailureReason.DEADLINE,
            )
            cause = error.__cause__
            if cause is None:
                raise failure from None
            raise failure from cause
        finally:
            if acquired:
                self.operation_lock.release()

    async def _ensure_serial(
        self,
        screen_type: type[ScreenT],
        recovery: BackRecovery[ScreenT],
        parent_deadline: OperationDeadline,
    ) -> ScreenT:
        history: deque[RecoveryHistoryEntry] = deque(maxlen=self.history_limit)
        observation_count = 0
        unready_state_counts: dict[str, int] = {}
        attempts = 0
        readiness_package = None if screen_type.scope is DeviceScope else self.app_package

        pending_hook = self.pending_hook
        if pending_hook is not None:
            await self._wait_tracked_future(pending_hook, parent_deadline)
            self.pending_hook = None
            with suppress(BaseException):
                pending_hook.result()

        await self._await_pending_back(parent_deadline)

        while True:
            try:
                observation = await self._within_deadline(
                    lambda: self.observations.capture(None, parent_deadline),
                    parent_deadline,
                )
                observation_count += 1
                signature = _state_signature(observation)

                interruption_hook = self.interruption_hook
                if interruption_hook is not None:
                    try:
                        pending_hook = asyncio.ensure_future(
                            interruption_hook.handle(
                                observation,
                                parent_deadline,
                            )
                        )
                        pending_hook.add_done_callback(_consume_future_outcome)
                        self.pending_hook = pending_hook
                        try:
                            await self._wait_tracked_future(
                                pending_hook,
                                parent_deadline,
                            )
                        except BaseException:
                            pending_hook.cancel()
                            raise
                        self.pending_hook = None
                        handled = pending_hook.result()
                    except _ParentDeadlineExpired:
                        history.append(
                            RecoveryHistoryEntry(
                                observation_sequence=observation.sequence,
                                state_signature=signature,
                                back_attempt=attempts,
                                interruption_handled=False,
                                readiness_checked=False,
                                ready=None,
                                matched=None,
                                total=None,
                                hook_timed_out=True,
                            )
                        )
                        raise
                    if handled:
                        # Interruption disappearance is its own bounded episode.
                        # States on either side are not evidence of a BACK loop.
                        unready_state_counts.clear()
                        history.append(
                            RecoveryHistoryEntry(
                                observation_sequence=observation.sequence,
                                state_signature=signature,
                                back_attempt=attempts,
                                interruption_handled=True,
                                readiness_checked=False,
                                ready=None,
                                matched=None,
                                total=None,
                            )
                        )
                        await self._delay(parent_deadline)
                        continue

                evaluation = evaluate_readiness(
                    observation,
                    screen_type.ready,
                    package=readiness_package,
                )
                history.append(
                    RecoveryHistoryEntry(
                        observation_sequence=observation.sequence,
                        state_signature=signature,
                        back_attempt=attempts,
                        interruption_handled=False,
                        readiness_checked=True,
                        ready=evaluation.ready,
                        matched=evaluation.matched,
                        total=evaluation.total,
                    )
                )
                if parent_deadline.expired():
                    raise _ParentDeadlineExpired
                if evaluation.ready:
                    return await self._bind_before_deadline(
                        screen_type,
                        parent_deadline,
                    )
                state_count = unready_state_counts.get(signature, 0) + 1
                unready_state_counts[signature] = state_count
                if state_count >= 3:
                    raise self._failure(
                        screen_type,
                        attempts,
                        history,
                        observation_count,
                        RecoveryFailureReason.LOOP,
                    )
                if attempts >= recovery.max_steps:
                    raise self._failure(
                        screen_type,
                        attempts,
                        history,
                        observation_count,
                        RecoveryFailureReason.EXHAUSTED,
                    )

                attempts += 1
                await self._press_back_once(parent_deadline)
                await self._delay(parent_deadline)
            except _ParentDeadlineExpired as error:
                failure = self._failure(
                    screen_type,
                    attempts,
                    history,
                    observation_count,
                    RecoveryFailureReason.DEADLINE,
                )
                cause = error.__cause__
                if cause is None:
                    raise failure from None
                raise failure from cause

    async def _within_deadline(
        self,
        operation: Callable[[], Awaitable[ResultT]],
        parent_deadline: OperationDeadline,
    ) -> ResultT:
        remaining = parent_deadline.remaining().total_seconds()
        if remaining <= 0:
            raise _ParentDeadlineExpired
        future = asyncio.ensure_future(operation())
        try:
            done, pending_tasks = await asyncio.wait((future,), timeout=remaining)
            del pending_tasks
        except BaseException:
            _cancel_detached(future)
            raise
        if not done:
            _cancel_detached(future)
            raise _ParentDeadlineExpired
        if parent_deadline.expired():
            try:
                future.result()
            except BaseException as error:
                raise _ParentDeadlineExpired from error
            raise _ParentDeadlineExpired
        return future.result()

    @staticmethod
    async def _wait_tracked_future(
        future: asyncio.Future[Any],
        parent_deadline: OperationDeadline,
    ) -> None:
        """Wait within the root budget without canceling tracked background work."""

        remaining = parent_deadline.remaining().total_seconds()
        if remaining <= 0:
            raise _ParentDeadlineExpired
        done, pending_tasks = await asyncio.wait((future,), timeout=remaining)
        del pending_tasks
        if not done:
            raise _ParentDeadlineExpired
        if parent_deadline.expired():
            try:
                future.result()
            except BaseException as error:
                raise _ParentDeadlineExpired from error
            raise _ParentDeadlineExpired

    async def _drain_pending_binder(
        self,
        parent_deadline: OperationDeadline,
    ) -> None:
        pending = self.pending_binder
        if pending is None:
            return
        await self._wait_tracked_future(pending, parent_deadline)
        self.pending_binder = None
        try:
            pending.result()
        except BaseException:
            # This work belonged to an operation that already timed out or was
            # cancelled. Its outcome is consumed before a new factory call starts.
            return

    async def _bind_before_deadline(
        self,
        screen_type: type[ScreenT],
        parent_deadline: OperationDeadline,
    ) -> ScreenT:
        await self._drain_pending_binder(parent_deadline)
        if parent_deadline.expired():
            raise _ParentDeadlineExpired
        pending = asyncio.ensure_future(
            asyncio.to_thread(
                _build_bound_screen,
                screen_type,
                cast(ScreenBinderFactory[ScreenT], self.binder_factory),
            )
        )
        pending.add_done_callback(_consume_future_outcome)
        self.pending_binder = pending
        await self._wait_tracked_future(pending, parent_deadline)
        self.pending_binder = None
        return pending.result()

    async def _await_pending_back(
        self,
        parent_deadline: OperationDeadline,
    ) -> None:
        pending = self.pending_back
        if pending is None:
            return
        remaining = parent_deadline.remaining().total_seconds()
        if remaining <= 0:
            raise _ParentDeadlineExpired
        done, pending_tasks = await asyncio.wait((pending,), timeout=remaining)
        del pending_tasks
        if not done:
            raise _ParentDeadlineExpired
        if parent_deadline.expired():
            try:
                pending.result()
            except BaseException as error:
                raise _ParentDeadlineExpired from error
            raise _ParentDeadlineExpired
        try:
            pending.result()
        except IndeterminateActionError:
            # UNKNOWN non-replayable dispatch is terminal for this engine. Keep
            # the exact completed future so every later recovery re-raises it.
            raise
        except BaseException:
            self.pending_back = None
            raise
        self.pending_back = None

    async def _press_back_once(
        self,
        parent_deadline: OperationDeadline,
    ) -> None:
        await self._await_pending_back(parent_deadline)
        pending: asyncio.Future[None] = asyncio.ensure_future(self.press_back())
        pending.add_done_callback(_consume_future_outcome)
        self.pending_back = pending
        try:
            await self._await_pending_back(parent_deadline)
        except asyncio.CancelledError:
            # BACK is non-replayable. Let a possibly dispatched command finish and
            # make every later recovery wait for that outcome before observing or
            # issuing another BACK.
            raise

    async def _delay(self, parent_deadline: OperationDeadline) -> None:
        if parent_deadline.expired():
            raise _ParentDeadlineExpired
        if self.retry_delay <= timedelta():
            return
        delay = min(
            self.retry_delay.total_seconds(),
            parent_deadline.remaining().total_seconds(),
        )
        if delay <= 0:
            raise _ParentDeadlineExpired
        await self._within_deadline(
            lambda: self.sleep_callback(delay),
            parent_deadline,
        )

    @staticmethod
    def _failure(
        screen_type: type[FailureScreenT],
        attempts: int,
        history: Iterable[RecoveryHistoryEntry],
        observation_count: int,
        reason: RecoveryFailureReason,
    ) -> RecoveryError:
        return RecoveryError(
            screen_type=cast(type[ScreenDefinition], screen_type),
            attempts=attempts,
            history=tuple(history),
            observation_count=observation_count,
            reason=reason,
        )
