"""Structured public error rendering tests."""

from datetime import UTC, datetime, timedelta

from appwright.errors import IndeterminateActionError, TimeoutError
from appwright.models.data import CallLogEntry, ElementSnapshot, ErrorDetails, Rect
from appwright.models.enums import ActionKind, ErrorCode, LocatorStrategy
from appwright.operations import (
    ActionReceipt,
    DispatchState,
    OperationStage,
    ReplaySafety,
)


def test_error_string_contains_actionable_context() -> None:
    error = TimeoutError(
        ErrorDetails(
            code=ErrorCode.TIMEOUT,
            api_name="locator.tap",
            message="tap timed out",
            locator="text='Continue'",
            strategy=LocatorStrategy.XPATH,
            expected="visible",
            received="hidden",
            elapsed=timedelta(seconds=5),
            call_log=(
                CallLogEntry(
                    message="element is not stable",
                    elapsed=timedelta(seconds=1),
                ),
            ),
        )
    )
    rendered = str(error)
    assert "API: locator.tap" in rendered
    assert "Locator: text='Continue'" in rendered
    assert "Expected: visible" in rendered
    assert "Call log:" in rendered


def test_indeterminate_action_error_retains_typed_receipt() -> None:
    receipt = ActionReceipt(
        action=ActionKind.TAP,
        locator="resource_id='submit'",
        replay_safety=ReplaySafety.NON_REPLAYABLE,
        stage=OperationStage.DISPATCH,
        dispatch_state=DispatchState.UNKNOWN,
        started_at=datetime.now(UTC),
        pre_action=ElementSnapshot(
            identity="submit",
            displayed=True,
            enabled=True,
            selected=False,
            checked=False,
            checkable=False,
            focusable=True,
            focused=False,
            editable=False,
            bounds=Rect(x=0, y=0, width=10, height=10),
        ),
    )
    error = IndeterminateActionError(
        ErrorDetails(
            code=ErrorCode.INDETERMINATE_ACTION,
            api_name="locator.tap",
            message="tap dispatch outcome is unknown",
        ),
        receipt,
    )

    assert error.receipt is receipt
    assert "dispatch outcome is unknown" in str(error)
