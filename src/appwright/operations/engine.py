"""Deadline and replay-safety rules for operation execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from time import monotonic

from appwright.models.data import ElementSnapshot
from appwright.models.enums import ActionKind
from appwright.operations.models import ActionReceipt, DispatchState, ReplaySafety


def replay_safety_for(action: ActionKind) -> ReplaySafety:
    """Classify whether an action may safely be repeated after dispatch."""

    if action in {ActionKind.CHECK, ActionKind.UNCHECK}:
        return ReplaySafety.IDEMPOTENT
    return ReplaySafety.NON_REPLAYABLE


def may_retry(receipt: ActionReceipt) -> bool:
    """Return whether the action represented by ``receipt`` may be retried."""

    return (
        receipt.dispatch_state is DispatchState.NOT_DISPATCHED
        or receipt.replay_safety is ReplaySafety.IDEMPOTENT
    )


def actionability_problem(element: ElementSnapshot, kind: ActionKind) -> str | None:
    """Return the canonical reason an element cannot receive ``kind`` yet."""

    if not element.displayed:
        return "element is not visible"
    if element.bounds.width == 0 or element.bounds.height == 0:
        return "element has no visible area"
    enabled_actions = {
        ActionKind.TAP,
        ActionKind.DOUBLE_TAP,
        ActionKind.LONG_PRESS,
        ActionKind.FILL,
        ActionKind.CLEAR,
        ActionKind.PRESS,
        ActionKind.CHECK,
        ActionKind.UNCHECK,
    }
    if kind in enabled_actions and not element.enabled:
        return "element is not enabled"
    if kind in {ActionKind.FILL, ActionKind.CLEAR, ActionKind.PRESS} and not element.editable:
        return "element is not editable"
    if kind in {ActionKind.CHECK, ActionKind.UNCHECK} and not element.checkable:
        return "element is not checkable"
    return None


@dataclass(frozen=True, slots=True)
class OperationDeadline:
    """A monotonic deadline shared by one operation and all of its children."""

    started_at: float
    expires_at: float
    clock: Callable[[], float] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def _now(self) -> float:
        return monotonic() if self.clock is None else self.clock()

    @classmethod
    def start(
        cls,
        timeout: timedelta,
        *,
        clock: Callable[[], float] | None = None,
    ) -> OperationDeadline:
        started_at = monotonic() if clock is None else clock()
        return cls(
            started_at=started_at,
            expires_at=started_at + timeout.total_seconds(),
            clock=clock,
        )

    def remaining(self) -> timedelta:
        """Return the non-negative time remaining before expiration."""

        return timedelta(seconds=max(self.expires_at - self._now(), 0.0))

    def elapsed(self) -> timedelta:
        """Return the non-negative elapsed time since this deadline began."""

        return timedelta(seconds=max(self._now() - self.started_at, 0.0))

    def expired(self) -> bool:
        """Return whether the deadline has elapsed."""

        return self._now() >= self.expires_at

    def child(self, timeout: timedelta) -> OperationDeadline:
        """Create a deadline bounded by both ``timeout`` and this parent."""

        started_at = self._now()
        return type(self)(
            started_at=started_at,
            expires_at=min(self.expires_at, started_at + timeout.total_seconds()),
            clock=self.clock,
        )
