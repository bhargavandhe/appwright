"""Prioritized, bounded handling for temporary app and device screens."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from inspect import isawaitable
from typing import Any, TypeAlias, TypeVar, cast

from appwright.observations import Observation
from appwright.operations import OperationDeadline
from appwright.screens.elements import ElementBinder
from appwright.screens.model import AsyncInterruption, DeviceScope, Interruption
from appwright.screens.readiness import evaluate_readiness

InterruptionDefinition: TypeAlias = Interruption[Any] | AsyncInterruption[Any]
InterruptionType: TypeAlias = type[InterruptionDefinition]
BinderFactory: TypeAlias = Callable[..., ElementBinder]
ResultT = TypeVar("ResultT")


def _validate_history_limit(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("history_limit must be a positive integer")
    if value < 1:
        raise ValueError("history_limit must be positive")
    return value


class _LifecycleDeadlineExpired(Exception):
    """Internal signal that one lifecycle await consumed its complete budget."""


def _consume_future_outcome(future: asyncio.Future[Any]) -> None:
    """Retrieve a detached future's terminal exception without blocking."""

    if future.cancelled():
        return
    try:
        future.exception()
    except BaseException:
        return


def _cancel_detached(future: asyncio.Future[Any]) -> None:
    """Cancel promptly without waiting for cancellation-resistant user code."""

    future.cancel()
    future.add_done_callback(_consume_future_outcome)


def _discard_awaitable(value: object) -> None:
    if not isawaitable(value):
        return
    future = asyncio.ensure_future(value)
    _cancel_detached(future)


async def _await_before_deadline(
    awaitable: Awaitable[ResultT],
    deadline: OperationDeadline,
) -> ResultT:
    """Await cooperatively without trusting user code to honor cancellation."""

    future = asyncio.ensure_future(awaitable)
    remaining = deadline.remaining().total_seconds()
    if remaining <= 0:
        _cancel_detached(future)
        raise _LifecycleDeadlineExpired
    try:
        done, pending = await asyncio.wait((future,), timeout=remaining)
        del pending
    except BaseException:
        _cancel_detached(future)
        raise
    if not done:
        _cancel_detached(future)
        raise _LifecycleDeadlineExpired
    result = future.result()
    if deadline.expired():
        _discard_awaitable(result)
        raise _LifecycleDeadlineExpired
    return result


async def _await_live_future_before_deadline(
    future: asyncio.Future[ResultT],
    deadline: OperationDeadline,
) -> ResultT:
    """Await possibly-dispatched work without cancelling or losing its lifetime."""

    remaining = deadline.remaining().total_seconds()
    if remaining <= 0:
        raise _LifecycleDeadlineExpired
    done, pending = await asyncio.wait((future,), timeout=remaining)
    del pending
    if not done:
        raise _LifecycleDeadlineExpired
    result = future.result()
    if deadline.expired():
        raise _LifecycleDeadlineExpired
    return result


async def _run_dismissal(dismiss: Callable[[], object]) -> None:
    """Run sync invocation off-loop and await any async dismissal it returns."""

    result = await asyncio.to_thread(dismiss)
    if isawaitable(result):
        await cast(Awaitable[object], result)


async def _acquire_lock_before_deadline(
    lock: asyncio.Lock,
    deadline: OperationDeadline,
) -> None:
    """Acquire ``lock`` without leaking ownership at timeout/cancellation races."""

    future = asyncio.ensure_future(lock.acquire())
    remaining = deadline.remaining().total_seconds()
    if remaining <= 0:
        _cancel_detached(future)
        raise _LifecycleDeadlineExpired
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
        raise _LifecycleDeadlineExpired
    if not future.result():
        raise RuntimeError("asyncio lock acquisition returned false")
    if deadline.expired():
        lock.release()
        raise _LifecycleDeadlineExpired


class InterruptionEvent(StrEnum):
    """One immutable event in an interruption-handling history."""

    DETECTED = "detected"
    DISMISSED = "dismissed"
    STILL_VISIBLE = "still_visible"
    DISAPPEARED = "disappeared"
    CYCLE_DETECTED = "cycle_detected"
    MAX_DISMISSALS_EXCEEDED = "max_dismissals_exceeded"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    DISMISSAL_FAILED = "dismissal_failed"


class InterruptionFailureReason(StrEnum):
    """Bounded reasons an interruption episode can stop unsuccessfully."""

    CYCLE = "cycle"
    MAX_DISMISSALS = "max_dismissals"
    DEADLINE = "deadline"
    DISMISSAL = "dismissal"


@dataclass(frozen=True, slots=True)
class InterruptionHistoryEntry:
    """Structured evidence for one interruption decision."""

    observation_sequence: int
    interruption_type: InterruptionType
    priority: int
    event: InterruptionEvent


class InterruptionError(RuntimeError):
    """A bounded interruption episode failed with immutable history."""

    def __init__(
        self,
        reason: InterruptionFailureReason,
        history: tuple[InterruptionHistoryEntry, ...],
        event_count: int,
    ) -> None:
        self.reason = reason
        self.history = history
        self.event_count = event_count
        latest = history[-1] if history else None
        suffix = ""
        if latest is not None:
            suffix = (
                f" at observation {latest.observation_sequence} "
                f"for {latest.interruption_type.__name__}"
            )
        super().__init__(f"interruption handling failed: {reason.value}{suffix}")


class InterruptionManager:
    """Choose, dismiss, and wait out registered interruptions without recapture."""

    def __init__(
        self,
        definitions: Iterable[InterruptionType],
        *,
        binder_factory: BinderFactory,
        package: str,
        timeout: timedelta,
        max_dismissals: int,
        history_limit: int = 100,
    ) -> None:
        if timeout <= timedelta():
            raise ValueError("timeout must be greater than zero")
        if isinstance(max_dismissals, bool) or max_dismissals < 1:
            raise ValueError("max_dismissals must be at least one")
        _validate_history_limit(history_limit)

        registered = tuple(definitions)
        ordered = sorted(
            enumerate(registered),
            key=lambda registration: (
                -registration[1].priority,
                registration[0],
            ),
        )
        self.definitions = tuple(registration[1] for registration in ordered)
        self.binder_factory = binder_factory
        self.package = package
        self.timeout = timeout
        self.max_dismissals = max_dismissals
        self.history_limit = history_limit

        self.history_buffer: deque[InterruptionHistoryEntry] = deque(maxlen=history_limit)
        self.total_event_count = 0
        self.active_interruption: InterruptionType | None = None
        self.active_sequence: int | None = None
        self.cycle_deadline: OperationDeadline | None = None
        self.cycle_seen: frozenset[InterruptionType] = frozenset()
        self.cycle_dismissals = 0
        self.dismissal_in_flight = False
        self.dismissal_future: asyncio.Future[None] | None = None
        self.last_observation_sequence: int | None = None
        self.handle_lock = asyncio.Lock()

    @property
    def history(self) -> tuple[InterruptionHistoryEntry, ...]:
        """Return the bounded immutable history retained for diagnostics."""

        return tuple(self.history_buffer)

    @property
    def event_count(self) -> int:
        """Return the total number of lifecycle events, including dropped history."""

        return self.total_event_count

    def _record(
        self,
        observation: Observation,
        interruption_type: InterruptionType,
        event: InterruptionEvent,
    ) -> None:
        self._record_sequence(
            observation.sequence,
            interruption_type,
            event,
        )

    def _record_sequence(
        self,
        observation_sequence: int,
        interruption_type: InterruptionType,
        event: InterruptionEvent,
    ) -> None:
        self.total_event_count += 1
        self.history_buffer.append(
            InterruptionHistoryEntry(
                observation_sequence=observation_sequence,
                interruption_type=interruption_type,
                priority=interruption_type.priority,
                event=event,
            )
        )

    def _fail(
        self,
        observation: Observation,
        interruption_type: InterruptionType,
        *,
        event: InterruptionEvent,
        reason: InterruptionFailureReason,
    ) -> InterruptionError:
        self._record(observation, interruption_type, event)
        return InterruptionError(reason, self.history, self.total_event_count)

    def _scope_package(self, interruption_type: InterruptionType) -> str | None:
        if interruption_type.scope is DeviceScope:
            return None
        return self.package

    def _evaluations(
        self,
        observation: Observation,
    ) -> tuple[tuple[InterruptionType, bool], ...]:
        return tuple(
            (
                interruption_type,
                evaluate_readiness(
                    observation,
                    interruption_type.ready,
                    package=self._scope_package(interruption_type),
                ).ready,
            )
            for interruption_type in self.definitions
        )

    def _start_or_cap_deadline(
        self,
        parent_deadline: OperationDeadline,
    ) -> OperationDeadline:
        deadline = self.cycle_deadline
        candidate = parent_deadline.child(self.timeout)
        if deadline is None or candidate.expires_at < deadline.expires_at:
            deadline = candidate
        self.cycle_deadline = deadline
        return deadline

    def _reset_cycle(self) -> None:
        self.cycle_deadline = None
        self.cycle_seen = frozenset()
        self.cycle_dismissals = 0
        if self.dismissal_future is None:
            self.dismissal_in_flight = False

    def _mark_dismissal_complete(self, interruption_type: InterruptionType) -> None:
        observation_sequence = self.active_sequence
        if observation_sequence is None:
            raise RuntimeError("completed dismissal has no active observation sequence")
        self.dismissal_future = None
        self.dismissal_in_flight = False
        self._record_sequence(
            observation_sequence,
            interruption_type,
            InterruptionEvent.DISMISSED,
        )

    async def _finish_live_dismissal(
        self,
        observation: Observation,
        interruption_type: InterruptionType,
        deadline: OperationDeadline,
    ) -> None:
        future = self.dismissal_future
        if future is None:
            return
        try:
            await _await_live_future_before_deadline(future, deadline)
        except _LifecycleDeadlineExpired:
            raise self._fail(
                observation,
                interruption_type,
                event=InterruptionEvent.DEADLINE_EXCEEDED,
                reason=InterruptionFailureReason.DEADLINE,
            ) from None
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.dismissal_future = None
            self.dismissal_in_flight = False
            failure = self._fail(
                observation,
                interruption_type,
                event=InterruptionEvent.DISMISSAL_FAILED,
                reason=InterruptionFailureReason.DISMISSAL,
            )
            raise failure from error
        self._mark_dismissal_complete(interruption_type)

    def _deadline_failure_without_lock(
        self,
        observation: Observation,
    ) -> InterruptionError:
        active = self.active_interruption
        if active is None:
            active = next(
                (
                    interruption_type
                    for interruption_type, ready in self._evaluations(observation)
                    if ready
                ),
                None,
            )
        history = deque(self.history_buffer, maxlen=self.history_limit)
        event_count = self.total_event_count
        if active is not None:
            history.append(
                InterruptionHistoryEntry(
                    observation_sequence=observation.sequence,
                    interruption_type=active,
                    priority=active.priority,
                    event=InterruptionEvent.DEADLINE_EXCEEDED,
                )
            )
            event_count += 1
        return InterruptionError(
            InterruptionFailureReason.DEADLINE,
            tuple(history),
            event_count,
        )

    def _construct_handler(
        self,
        interruption_type: InterruptionType,
    ) -> InterruptionDefinition:
        binder = self.binder_factory(interruption_type)
        return interruption_type(binder)

    async def handle(
        self,
        observation: Observation,
        parent_deadline: OperationDeadline,
    ) -> bool:
        """Handle at most one interruption from ``observation``.

        ``True`` means interruption handling owns this observation and destination
        evaluation must be suppressed. ``False`` means no interruption remains.
        """

        lock_deadline = parent_deadline.child(self.timeout)
        try:
            await _acquire_lock_before_deadline(self.handle_lock, lock_deadline)
        except _LifecycleDeadlineExpired:
            raise self._deadline_failure_without_lock(observation) from None
        try:
            return await self._handle_serial(observation, parent_deadline)
        finally:
            self.handle_lock.release()

    async def _handle_serial(
        self,
        observation: Observation,
        parent_deadline: OperationDeadline,
    ) -> bool:
        """Handle one observation while the lifecycle-state lock is held."""

        last_sequence = self.last_observation_sequence
        if last_sequence is not None and observation.sequence <= last_sequence:
            # A stale destination decision is unsafe, so claim the observation
            # without mutating lifecycle state or replaying a dismissal.
            return True
        self.last_observation_sequence = observation.sequence
        evaluations = self._evaluations(observation)

        active = self.active_interruption
        if active is not None:
            deadline = self._start_or_cap_deadline(parent_deadline)
            if deadline.expired():
                raise self._fail(
                    observation,
                    active,
                    event=InterruptionEvent.DEADLINE_EXCEEDED,
                    reason=InterruptionFailureReason.DEADLINE,
                )

            active_ready = next(
                ready for interruption_type, ready in evaluations if interruption_type is active
            )
            pending_dismissal = self.dismissal_future
            if pending_dismissal is not None and pending_dismissal.done():
                await self._finish_live_dismissal(
                    observation,
                    active,
                    deadline,
                )
            if active_ready:
                self._record(observation, active, InterruptionEvent.STILL_VISIBLE)
                return True

            if self.active_sequence is not None and observation.sequence <= self.active_sequence:
                return True
            if self.dismissal_future is not None:
                await self._finish_live_dismissal(
                    observation,
                    active,
                    deadline,
                )
            self._record(observation, active, InterruptionEvent.DISAPPEARED)
            self.active_interruption = None
            self.active_sequence = None
            self.dismissal_in_flight = False

        candidates = tuple(interruption_type for interruption_type, ready in evaluations if ready)
        if not candidates:
            self._reset_cycle()
            return False

        selected = candidates[0]
        deadline = self._start_or_cap_deadline(parent_deadline)
        self._record(observation, selected, InterruptionEvent.DETECTED)
        if deadline.expired():
            raise self._fail(
                observation,
                selected,
                event=InterruptionEvent.DEADLINE_EXCEEDED,
                reason=InterruptionFailureReason.DEADLINE,
            )
        if selected in self.cycle_seen:
            raise self._fail(
                observation,
                selected,
                event=InterruptionEvent.CYCLE_DETECTED,
                reason=InterruptionFailureReason.CYCLE,
            )
        if self.cycle_dismissals >= self.max_dismissals:
            raise self._fail(
                observation,
                selected,
                event=InterruptionEvent.MAX_DISMISSALS_EXCEEDED,
                reason=InterruptionFailureReason.MAX_DISMISSALS,
            )

        try:
            handler = await _await_before_deadline(
                asyncio.to_thread(self._construct_handler, selected),
                deadline,
            )
        except _LifecycleDeadlineExpired:
            raise self._fail(
                observation,
                selected,
                event=InterruptionEvent.DEADLINE_EXCEEDED,
                reason=InterruptionFailureReason.DEADLINE,
            ) from None
        except Exception as error:
            failure = self._fail(
                observation,
                selected,
                event=InterruptionEvent.DISMISSAL_FAILED,
                reason=InterruptionFailureReason.DISMISSAL,
            )
            raise failure from error

        self.cycle_seen = self.cycle_seen | {selected}
        self.cycle_dismissals += 1
        self.active_interruption = selected
        self.active_sequence = observation.sequence
        self.dismissal_in_flight = True
        dismiss = cast(Callable[[], object], handler.dismiss)
        dismissal_future = asyncio.create_task(_run_dismissal(dismiss))
        dismissal_future.add_done_callback(_consume_future_outcome)
        self.dismissal_future = dismissal_future
        try:
            await _await_live_future_before_deadline(
                dismissal_future,
                deadline,
            )
        except _LifecycleDeadlineExpired:
            raise self._fail(
                observation,
                selected,
                event=InterruptionEvent.DEADLINE_EXCEEDED,
                reason=InterruptionFailureReason.DEADLINE,
            ) from None
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.dismissal_future = None
            self.dismissal_in_flight = False
            failure = self._fail(
                observation,
                selected,
                event=InterruptionEvent.DISMISSAL_FAILED,
                reason=InterruptionFailureReason.DISMISSAL,
            )
            raise failure from error

        self._mark_dismissal_complete(selected)
        return True
