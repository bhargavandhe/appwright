"""Immutable records describing an operation's dispatch lifecycle."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import model_validator

from appwright.models.base import StrictModel
from appwright.models.data import ElementSnapshot
from appwright.models.enums import ActionKind


class DispatchState(StrEnum):
    """Whether an action command is known to have reached the device."""

    NOT_DISPATCHED = "not_dispatched"
    DISPATCHED = "dispatched"
    UNKNOWN = "unknown"


class ReplaySafety(StrEnum):
    """Whether repeating an action after dispatch is safe."""

    NON_REPLAYABLE = "non_replayable"
    IDEMPOTENT = "idempotent"


class OperationStage(StrEnum):
    """The current stage of an action operation."""

    RESOLVE = "resolve"
    DISPATCH = "dispatch"
    OBSERVE = "observe"


class ActionReceipt(StrictModel):
    """Immutable evidence of an action's progress across the dispatch boundary."""

    action: ActionKind
    locator: str
    replay_safety: ReplaySafety
    stage: OperationStage
    dispatch_state: DispatchState
    started_at: datetime
    pre_action: ElementSnapshot
    dispatched_at: datetime | None = None

    @model_validator(mode="after")
    def validate_dispatched_at(self) -> ActionReceipt:
        if self.dispatch_state is DispatchState.DISPATCHED and self.dispatched_at is None:
            raise ValueError("dispatched_at is required when dispatch_state is dispatched")
        if self.dispatch_state is not DispatchState.DISPATCHED and self.dispatched_at is not None:
            raise ValueError("dispatched_at must be omitted unless dispatch_state is dispatched")
        return self
