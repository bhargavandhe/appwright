"""Appwright exception hierarchy."""

from appwright.models.data import ErrorDetails


def render_error(details: ErrorDetails) -> str:
    lines = [details.message, f"API: {details.api_name}"]
    if details.locator is not None:
        lines.append(f"Locator: {details.locator}")
    if details.strategy is not None:
        lines.append(f"Strategy: {details.strategy.value}")
    if details.expected is not None:
        lines.append(f"Expected: {details.expected}")
    if details.received is not None:
        lines.append(f"Received: {details.received}")
    if details.elapsed is not None:
        lines.append(f"Elapsed: {details.elapsed.total_seconds():.3f}s")
    if details.appium_command is not None:
        lines.append(f"Appium command: {details.appium_command.value}")
    if details.call_log:
        lines.append("Call log:")
        lines.extend(
            f"  - {entry.elapsed.total_seconds():.3f}s: {entry.message}"
            for entry in details.call_log
        )
    if details.screenshot_path is not None:
        lines.append(f"Screenshot: {details.screenshot_path}")
    if details.trace_path is not None:
        lines.append(f"Trace: {details.trace_path}")
    if details.appium_server_log:
        lines.append("Appium server log:")
        lines.extend(f"  - {record.message}" for record in details.appium_server_log)
    return "\n".join(lines)


class AppwrightError(Exception):
    """Base exception carrying structured details."""

    def __init__(self, details: ErrorDetails) -> None:
        super().__init__(render_error(details))
        self.details = details


class TimeoutError(AppwrightError):
    pass


class ExpectationError(AssertionError, AppwrightError):
    def __init__(self, details: ErrorDetails) -> None:
        AssertionError.__init__(self, render_error(details))
        self.details = details


class StrictModeViolationError(AppwrightError):
    pass


class InvalidSelectorError(AppwrightError):
    pass


class DeviceNotFoundError(AppwrightError):
    pass


class DeviceDisconnectedError(AppwrightError):
    pass


class AppiumUnavailableError(AppwrightError):
    pass


class AppiumCompatibilityError(AppwrightError):
    pass


class SessionTaintedError(AppwrightError):
    pass


class ProtocolError(AppwrightError):
    pass


class TargetClosedError(AppwrightError):
    pass


class UnsupportedOperationError(AppwrightError):
    pass
