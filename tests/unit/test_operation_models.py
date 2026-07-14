from datetime import UTC, datetime, timedelta

import pytest

from appwright.models.data import ElementSnapshot, Rect
from appwright.models.enums import ActionKind
from appwright.operations import (
    ActionReceipt,
    DispatchState,
    OperationDeadline,
    OperationStage,
    ReplaySafety,
    may_retry,
    replay_safety_for,
)


def snapshot() -> ElementSnapshot:
    return ElementSnapshot(
        identity="element-1",
        displayed=True,
        enabled=True,
        selected=False,
        checked=False,
        checkable=False,
        focusable=True,
        focused=False,
        editable=False,
        bounds=Rect(x=0, y=0, width=10, height=10),
    )


def test_non_replayable_action_cannot_retry_after_unknown_dispatch() -> None:
    receipt = ActionReceipt(
        action=ActionKind.TAP,
        locator="resource_id='submit'",
        replay_safety=ReplaySafety.NON_REPLAYABLE,
        stage=OperationStage.DISPATCH,
        dispatch_state=DispatchState.UNKNOWN,
        started_at=datetime.now(UTC),
        pre_action=snapshot(),
    )
    assert not may_retry(receipt)


def test_idempotent_action_may_retry_after_unknown_dispatch() -> None:
    receipt = ActionReceipt(
        action=ActionKind.CHECK,
        locator="resource_id='terms'",
        replay_safety=ReplaySafety.IDEMPOTENT,
        stage=OperationStage.DISPATCH,
        dispatch_state=DispatchState.UNKNOWN,
        started_at=datetime.now(UTC),
        pre_action=snapshot(),
    )
    assert may_retry(receipt)


def test_action_replay_classification_is_explicit() -> None:
    assert replay_safety_for(ActionKind.TAP) is ReplaySafety.NON_REPLAYABLE
    assert replay_safety_for(ActionKind.CHECK) is ReplaySafety.IDEMPOTENT
    assert replay_safety_for(ActionKind.UNCHECK) is ReplaySafety.IDEMPOTENT


def test_child_deadline_cannot_outlive_parent() -> None:
    parent = OperationDeadline.start(timedelta(milliseconds=50))
    child = parent.child(timedelta(seconds=5))
    assert child.expires_at <= parent.expires_at


def test_deadline_and_child_share_one_injected_monotonic_clock() -> None:
    current = 10.0

    def clock() -> float:
        return current

    parent = OperationDeadline.start(timedelta(seconds=5), clock=clock)
    current = 12.0
    child = parent.child(timedelta(seconds=10))

    assert parent.remaining() == timedelta(seconds=3)
    assert child.remaining() == timedelta(seconds=3)
    assert child.expires_at == parent.expires_at


def test_receipt_rejects_dispatched_state_without_timestamp() -> None:
    with pytest.raises(ValueError, match="dispatched_at"):
        ActionReceipt(
            action=ActionKind.TAP,
            locator="submit",
            replay_safety=ReplaySafety.NON_REPLAYABLE,
            stage=OperationStage.OBSERVE,
            dispatch_state=DispatchState.DISPATCHED,
            started_at=datetime.now(UTC),
            pre_action=snapshot(),
        )
