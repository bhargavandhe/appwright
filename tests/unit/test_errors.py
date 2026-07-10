"""Structured public error rendering tests."""

from datetime import timedelta

from appwright.errors import TimeoutError
from appwright.models.data import CallLogEntry, ErrorDetails
from appwright.models.enums import ErrorCode, LocatorStrategy


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
