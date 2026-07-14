"""Deadline-aware, atomic polling for typed mobile screens."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, Literal, Protocol, TypeVar, cast, overload

from appwright.models.config import Timeouts
from appwright.observations import Observation
from appwright.operations import OperationDeadline
from appwright.screens.elements import ElementBinder
from appwright.screens.model import DeviceScope
from appwright.screens.readiness import ReadinessDiagnostic, evaluate_readiness
from appwright.screens.targets import ScreenChoice, ScreenDefinition, ScreenTarget


class ObservationSource(Protocol):
    """A serialized source of immutable whole-device observations."""

    async def capture(
        self,
        package: str | None,
        deadline: OperationDeadline,
    ) -> Observation: ...


class InterruptionHook(Protocol):
    """Structural hook that may dismiss an interruption from an observation."""

    async def handle(
        self,
        observation: Observation,
        parent_deadline: OperationDeadline,
    ) -> bool: ...


class ScreenBinderFactory(Protocol):
    """Create the scope-appropriate element binder for a concrete screen type."""

    def __call__(self, screen_type: type[ScreenDefinition]) -> ElementBinder: ...


ResultT = TypeVar("ResultT")


class _RootDeadlineExpired(Exception):
    """Internal signal that a child task exceeded the shared root deadline."""


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


async def _await_child_before_deadline(
    awaitable: Awaitable[ResultT],
    deadline: OperationDeadline,
) -> ResultT:
    """Hard-bound child work without waiting for cancellation-resistant code."""

    future = asyncio.ensure_future(awaitable)
    remaining = deadline.remaining().total_seconds()
    if remaining <= 0:
        _cancel_detached(future)
        raise _RootDeadlineExpired
    try:
        done, pending = await asyncio.wait((future,), timeout=remaining)
        del pending
    except BaseException:
        _cancel_detached(future)
        raise
    if not done:
        _cancel_detached(future)
        raise _RootDeadlineExpired
    if deadline.expired():
        try:
            future.result()
        except BaseException as error:
            raise _RootDeadlineExpired from error
        raise _RootDeadlineExpired
    return future.result()


async def _await_live_future_before_deadline(
    future: asyncio.Future[ResultT],
    deadline: OperationDeadline,
) -> ResultT:
    """Bound a child while allowing its non-cancellable work to finish safely."""

    remaining = deadline.remaining().total_seconds()
    if remaining <= 0:
        raise _RootDeadlineExpired
    done, pending = await asyncio.wait((future,), timeout=remaining)
    del pending
    if not done:
        raise _RootDeadlineExpired
    if deadline.expired():
        try:
            future.result()
        except BaseException as error:
            raise _RootDeadlineExpired from error
        raise _RootDeadlineExpired
    return future.result()


async def _acquire_lock_before_deadline(
    lock: asyncio.Lock,
    deadline: OperationDeadline,
) -> None:
    """Acquire a binding slot without leaking ownership at deadline races."""

    future = asyncio.ensure_future(lock.acquire())
    remaining = deadline.remaining().total_seconds()
    if remaining <= 0:
        _cancel_detached(future)
        raise _RootDeadlineExpired
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
        raise _RootDeadlineExpired
    if not future.result():
        raise RuntimeError("asyncio lock acquisition returned false")
    if deadline.expired():
        lock.release()
        raise _RootDeadlineExpired


@dataclass(frozen=True, slots=True)
class CandidateMatchSummary:
    """Readiness summary for one candidate in one observation."""

    screen: str
    scope: Literal["app", "device"]
    ready: bool
    matched: int
    total: int
    diagnostics: tuple[ReadinessDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class TransitionObservation:
    """One bounded transition-history entry."""

    sequence: int
    captured_at: datetime
    interruption_handled: bool
    candidates: tuple[CandidateMatchSummary, ...]
    hook_timed_out: bool = False


@dataclass(frozen=True, slots=True)
class TransitionHistory:
    """Bounded observations retained for a transition failure."""

    observations: tuple[TransitionObservation, ...]
    observation_count: int


@dataclass(frozen=True, slots=True)
class _PollMatch:
    """A ready screen plus the still-live root operation context."""

    screen_type: type[ScreenDefinition]
    observation_sequence: int
    deadline: OperationDeadline
    budget: timedelta
    operation: str
    candidates: tuple[type[ScreenDefinition], ...]
    history: tuple[TransitionObservation, ...]
    observation_count: int


class ScreenTimeoutError(TimeoutError):
    """A screen target did not become ready within its transition budget."""

    def __init__(
        self,
        *,
        operation: str,
        candidate_screens: tuple[str, ...],
        timeout: timedelta,
        transition_history: TransitionHistory,
    ) -> None:
        self.operation = operation
        self.candidate_screens = candidate_screens
        self.timeout = timeout
        self.transition_history = transition_history
        targets = ", ".join(candidate_screens)
        if transition_history.observations:
            latest = transition_history.observations[-1]
            if latest.hook_timed_out:
                latest_detail = "interruption hook timed out"
            elif latest.interruption_handled:
                latest_detail = "interruption handled"
            else:
                latest_detail = (
                    ", ".join(
                        f"{candidate.screen}={candidate.matched}/{candidate.total}"
                        for candidate in latest.candidates
                    )
                    or "no candidate summaries"
                )
            detail = f"; last sequence {latest.sequence}: {latest_detail}"
        else:
            detail = "; no observations captured"
        super().__init__(
            f"{operation} timed out after {timeout.total_seconds():g}s waiting for "
            f"{targets}{detail}"
        )

    @property
    def history(self) -> tuple[TransitionObservation, ...]:
        """Return the bounded observation entries directly."""

        return self.transition_history.observations

    @property
    def observation_count(self) -> int:
        """Return all observations seen, including entries dropped from history."""

        return self.transition_history.observation_count


ScreenT = TypeVar("ScreenT", bound=ScreenDefinition)


class TransitionEngine:
    """Poll screen targets from serialized whole-device observations."""

    def __init__(
        self,
        source: ObservationSource,
        *,
        binder_factory: ScreenBinderFactory,
        app_package: str,
        timeouts: Timeouts,
        monotonic_clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        interruption_hook: InterruptionHook | None = None,
        history_limit: int = 50,
    ) -> None:
        if not app_package:
            raise ValueError("app_package must not be empty")
        if history_limit < 1:
            raise ValueError("history_limit must be at least one")
        self.source = source
        self.binder_factory = binder_factory
        self.app_package = app_package
        self.timeouts = timeouts
        self.monotonic_clock = monotonic_clock
        self.sleep = sleep
        self.interruption_hook = interruption_hook
        self.history_limit = history_limit
        self.binding_lock = asyncio.Lock()
        self.binding_future: asyncio.Future[Any] | None = None

    async def wait_for(
        self,
        screen_type: type[ScreenT],
        *,
        timeout: timedelta | None = None,
        deadline: OperationDeadline | None = None,
    ) -> ScreenT:
        """Wait for one screen and return it bound with its concrete scope."""

        match = await self._poll(
            (cast(type[ScreenDefinition], screen_type),),
            operation="wait_for",
            timeout=timeout,
            parent_deadline=deadline,
            stable_for=None,
        )
        return await self._bind_match(cast(type[ScreenT], match.screen_type), match)

    async def wait_for_any(
        self,
        target: ScreenTarget[ScreenT],
        *,
        timeout: timedelta | None = None,
        deadline: OperationDeadline | None = None,
    ) -> ScreenChoice[ScreenT]:
        """Return the first declared candidate ready in one atomic observation."""

        if not target.screens:
            raise ValueError("screen target must contain at least one screen")
        candidates = cast(tuple[type[ScreenDefinition], ...], target.screens)
        match = await self._poll(
            candidates,
            operation="wait_for_any",
            timeout=timeout,
            parent_deadline=deadline,
            stable_for=None,
        )
        return ScreenChoice(
            screen=await self._bind_match(
                cast(type[ScreenT], match.screen_type),
                match,
            ),
            observation_sequence=match.observation_sequence,
        )

    @overload
    async def settle(
        self,
        screen: type[ScreenT],
        *,
        stable_for: timedelta | None = None,
        timeout: timedelta | None = None,
        deadline: OperationDeadline | None = None,
    ) -> ScreenT: ...

    @overload
    async def settle(
        self,
        screen: ScreenT,
        *,
        stable_for: timedelta | None = None,
        timeout: timedelta | None = None,
        deadline: OperationDeadline | None = None,
    ) -> ScreenT: ...

    async def settle(
        self,
        screen: type[ScreenT] | ScreenT,
        *,
        stable_for: timedelta | None = None,
        timeout: timedelta | None = None,
        deadline: OperationDeadline | None = None,
    ) -> ScreenT:
        """Return once a screen remains continuously ready for a stable window."""

        screen_type = screen if isinstance(screen, type) else type(screen)
        stability = self.timeouts.stability if stable_for is None else stable_for
        if stability < timedelta():
            raise ValueError("stable_for must not be negative")
        match = await self._poll(
            (cast(type[ScreenDefinition], screen_type),),
            operation="settle",
            timeout=timeout,
            parent_deadline=deadline,
            stable_for=stability,
        )
        return await self._bind_match(screen_type, match)

    def _bind(self, screen_type: type[ScreenT]) -> ScreenT:
        binder = self.binder_factory(cast(type[ScreenDefinition], screen_type))
        return cast(ScreenT, screen_type(binder))  # pyright: ignore[reportUnnecessaryCast]

    async def _bind_match(
        self,
        screen_type: type[ScreenT],
        match: _PollMatch,
    ) -> ScreenT:
        acquired = False
        lock_transferred = False
        try:
            await _acquire_lock_before_deadline(
                self.binding_lock,
                match.deadline,
            )
            acquired = True
            binding_future = asyncio.create_task(asyncio.to_thread(self._bind, screen_type))
            self.binding_future = binding_future
            try:
                return await _await_live_future_before_deadline(
                    binding_future,
                    match.deadline,
                )
            except _RootDeadlineExpired:
                lock_transferred = True
                binding_future.add_done_callback(self._finish_detached_binding)
                raise
            except asyncio.CancelledError:
                lock_transferred = True
                binding_future.add_done_callback(self._finish_detached_binding)
                raise
        except _RootDeadlineExpired as error:
            raise self._timeout_error(
                match.operation,
                match.candidates,
                match.budget,
                match.history,
                match.observation_count,
            ) from error
        finally:
            if acquired and not lock_transferred:
                self.binding_future = None
                self.binding_lock.release()

    def _finish_detached_binding(self, future: asyncio.Future[Any]) -> None:
        """Drain late binder work and release the next serialized binding slot."""

        _consume_future_outcome(future)
        if self.binding_future is future:
            self.binding_future = None
        if self.binding_lock.locked():
            self.binding_lock.release()

    def _summary(
        self,
        observation: Observation,
        screen_type: type[ScreenDefinition],
    ) -> CandidateMatchSummary:
        device_scoped = screen_type.scope is DeviceScope
        evaluation = evaluate_readiness(
            observation,
            screen_type.ready,
            package=None if device_scoped else self.app_package,
        )
        return CandidateMatchSummary(
            screen=screen_type.__name__,
            scope="device" if device_scoped else "app",
            ready=evaluation.ready,
            matched=evaluation.matched,
            total=evaluation.total,
            diagnostics=evaluation.diagnostics,
        )

    async def _poll(
        self,
        candidates: tuple[type[ScreenDefinition], ...],
        *,
        operation: str,
        timeout: timedelta | None,
        parent_deadline: OperationDeadline | None,
        stable_for: timedelta | None,
    ) -> _PollMatch:
        if timeout is not None and parent_deadline is not None:
            raise ValueError("timeout and deadline are mutually exclusive")
        if parent_deadline is None:
            budget = self.timeouts.transition if timeout is None else timeout
            if budget < timedelta():
                raise ValueError("timeout must not be negative")
            deadline = OperationDeadline.start(budget, clock=self.monotonic_clock)
        else:
            deadline = parent_deadline
            budget = deadline.remaining()
        history: deque[TransitionObservation] = deque(maxlen=self.history_limit)
        observation_count = 0
        stable_since: float | None = None
        maximum_delay = self.timeouts.retry.maximum_delay
        positive_floor = min(timedelta(milliseconds=1), maximum_delay)
        delay = min(
            max(self.timeouts.retry.initial_delay, positive_floor),
            maximum_delay,
        )

        while not deadline.expired():
            try:
                observation = await self.source.capture(package=None, deadline=deadline)
            except TimeoutError as error:
                if not deadline.expired():
                    raise
                raise self._timeout_error(
                    operation,
                    candidates,
                    budget,
                    history,
                    observation_count,
                ) from error
            observation_count += 1

            handled = False
            if self.interruption_hook is not None:
                try:
                    handled = await _await_child_before_deadline(
                        self.interruption_hook.handle(observation, deadline),
                        deadline,
                    )
                except _RootDeadlineExpired as error:
                    history.append(
                        TransitionObservation(
                            sequence=observation.sequence,
                            captured_at=observation.captured_at,
                            interruption_handled=False,
                            candidates=(),
                            hook_timed_out=True,
                        )
                    )
                    raise self._timeout_error(
                        operation,
                        candidates,
                        budget,
                        history,
                        observation_count,
                    ) from error

            if handled:
                stable_since = None
                history.append(
                    TransitionObservation(
                        sequence=observation.sequence,
                        captured_at=observation.captured_at,
                        interruption_handled=True,
                        candidates=(),
                    )
                )
            else:
                summaries = tuple(self._summary(observation, candidate) for candidate in candidates)
                history.append(
                    TransitionObservation(
                        sequence=observation.sequence,
                        captured_at=observation.captured_at,
                        interruption_handled=False,
                        candidates=summaries,
                    )
                )
                ready_index = next(
                    (index for index, summary in enumerate(summaries) if summary.ready),
                    None,
                )
                if ready_index is None:
                    stable_since = None
                elif stable_for is None or stable_for == timedelta():
                    if not deadline.expired():
                        return _PollMatch(
                            screen_type=candidates[ready_index],
                            observation_sequence=observation.sequence,
                            deadline=deadline,
                            budget=budget,
                            operation=operation,
                            candidates=candidates,
                            history=tuple(history),
                            observation_count=observation_count,
                        )
                else:
                    now = self.monotonic_clock()
                    if stable_since is None:
                        stable_since = now
                    if now - stable_since >= stable_for.total_seconds() and not deadline.expired():
                        return _PollMatch(
                            screen_type=candidates[ready_index],
                            observation_sequence=observation.sequence,
                            deadline=deadline,
                            budget=budget,
                            operation=operation,
                            candidates=candidates,
                            history=tuple(history),
                            observation_count=observation_count,
                        )

            remaining = deadline.remaining()
            if remaining <= timedelta():
                break
            sleep_for = min(delay, remaining)
            try:
                await _await_child_before_deadline(
                    self.sleep(sleep_for.total_seconds()),
                    deadline,
                )
            except _RootDeadlineExpired as error:
                raise self._timeout_error(
                    operation,
                    candidates,
                    budget,
                    history,
                    observation_count,
                ) from error
            delay = min(
                max(delay * self.timeouts.retry.multiplier, positive_floor),
                maximum_delay,
            )

        raise self._timeout_error(
            operation,
            candidates,
            budget,
            history,
            observation_count,
        )

    @staticmethod
    def _timeout_error(
        operation: str,
        candidates: tuple[type[ScreenDefinition], ...],
        budget: timedelta,
        history: deque[TransitionObservation] | tuple[TransitionObservation, ...],
        observation_count: int,
    ) -> ScreenTimeoutError:
        return ScreenTimeoutError(
            operation=operation,
            candidate_screens=tuple(candidate.__name__ for candidate in candidates),
            timeout=budget,
            transition_history=TransitionHistory(
                observations=tuple(history),
                observation_count=observation_count,
            ),
        )
