"""Appium boundary contract tests with blocking client doubles."""

from __future__ import annotations

import asyncio
import threading
from datetime import timedelta
from time import sleep
from typing import cast

import pytest
from appium.options.android import UiAutomator2Options
from appium.webdriver.client_config import AppiumClientConfig
from pydantic import SecretStr
from selenium.common.exceptions import (
    StaleElementReferenceException,
    UnknownMethodException,
    WebDriverException,
)

from appwright.backends.appium.adapter import (
    AppiumBackend,
    execute_action,
    execute_dispatch,
    execute_query,
    execute_scroll_into_view,
)
from appwright.backends.appium.worker import SessionWorker
from appwright.backends.base import (
    BackendError,
    BackendFailure,
    BackendFailureKind,
    IndeterminateActionBackendError,
    RecoverableBackendError,
)
from appwright.models.base import StrictModel
from appwright.models.config import (
    AndroidDeviceSelector,
    AndroidSessionOptions,
    AppiumSecurityOptions,
    AppiumServer,
)
from appwright.models.data import ActionRequest, ServerLogRecord
from appwright.models.enums import (
    ActionKind,
    Direction,
    LocatorStrategy,
    LogStream,
    MobileCommand,
)
from appwright.operations import DispatchState, ReplaySafety
from appwright.selectors.compiler import LocatorPlan


class ScriptCall(StrictModel):
    command: str
    arguments: str


class FakeElement:
    def __init__(self, identity: str, package: str) -> None:
        self.id = identity
        self.package = package
        self.text = "Welcome"
        self.class_name = "android.widget.TextView"
        self.displayed = True
        self.enabled = True
        self.selected = False
        self.checked = False
        self.checkable = False
        self.rect = {"x": 1, "y": 2, "width": 100, "height": 40}
        self.clicked = False
        self.cleared = False
        self.keys: list[str] = []
        self.screenshot_as_png = b"png"
        self.ancestors: list[FakeElement] = []

    def get_attribute(self, name: str) -> str:
        if name == "className":
            return self.class_name
        if name == "contentDescription":
            return "Welcome"
        if name == "resourceId":
            return f"{self.package}:id/welcome"
        if name == "package":
            return self.package
        if name == "checked":
            return str(self.checked).lower()
        if name == "checkable":
            return str(self.checkable).lower()
        if name == "focused":
            return "false"
        if name == "focusable":
            return "true"
        return ""

    def is_displayed(self) -> bool:
        return self.displayed

    def is_enabled(self) -> bool:
        return self.enabled

    def is_selected(self) -> bool:
        return self.selected

    def click(self) -> None:
        self.clicked = True

    def clear(self) -> None:
        self.cleared = True

    def send_keys(self, value: str) -> None:
        self.keys.append(value)

    def find_elements(self, *, by: str, value: str) -> list[FakeElement]:
        return self.ancestors


class CamelCaseWindowIdElement(FakeElement):
    def get_attribute(self, name: str) -> str:
        if name == "window-id":
            raise UnknownMethodException("'window-id' attribute is unknown")
        if name == "windowId":
            return "42"
        return super().get_attribute(name)


class UnsupportedWindowIdElement(FakeElement):
    def get_attribute(self, name: str) -> str:
        if name in {"window-id", "windowId"}:
            raise UnknownMethodException(f"{name!r} attribute is unknown")
        return super().get_attribute(name)


class WindowIdTransportFailureElement(FakeElement):
    def get_attribute(self, name: str) -> str:
        if name in {"window-id", "windowId"}:
            raise WebDriverException("transport lost while reading window ID")
        return super().get_attribute(name)


class StaleAfterClickElement(FakeElement):
    def __init__(self, identity: str, package: str) -> None:
        super().__init__(identity, package)
        self.checkable = True
        self.stale = False
        self.click_count = 0

    def get_attribute(self, name: str) -> str:
        if self.stale:
            raise StaleElementReferenceException("screen changed after click")
        return super().get_attribute(name)

    def click(self) -> None:
        self.click_count += 1
        self.stale = True


class TransportFailureAfterClickElement(FakeElement):
    def __init__(self, identity: str, package: str) -> None:
        super().__init__(identity, package)
        self.click_count = 0

    def click(self) -> None:
        self.click_count += 1
        raise WebDriverException("connection lost after command submission")


class SlowClickElement(FakeElement):
    def __init__(self, identity: str, package: str) -> None:
        super().__init__(identity, package)
        self.click_count = 0

    def click(self) -> None:
        self.click_count += 1
        sleep(0.05)


class StatefulElement(FakeElement):
    def __init__(
        self,
        identity: str,
        package: str,
        *,
        checked: bool = False,
        checkable: bool = True,
        displayed: bool = True,
        enabled: bool = True,
        editable: bool = False,
    ) -> None:
        super().__init__(identity, package)
        self.checked = checked
        self.checkable = checkable
        self.displayed = displayed
        self.enabled = enabled
        if editable:
            self.class_name = "android.widget.EditText"


class TransportFailureStatefulElement(StatefulElement):
    def click(self) -> None:
        raise WebDriverException("connection lost after live state changed")


class SnapshotFailureElement(FakeElement):
    def is_displayed(self) -> bool:
        raise StaleElementReferenceException("stale during final snapshot")


class PreDispatchFailureDriver:
    def find_elements(self, *, by: str, value: str) -> list[FakeElement]:
        raise StaleElementReferenceException("stale before command submission")


class HierarchyDriver:
    def __init__(self) -> None:
        self.capabilities = {"platformName": "Android"}
        self.quit_called = False
        self.page_source_reads = 0
        self.find_elements_calls = 0

    @property
    def page_source(self) -> str:
        self.page_source_reads += 1
        return "<hierarchy><node text='Welcome'/></hierarchy>"

    def find_elements(self, *, by: str, value: str) -> list[FakeElement]:
        self.find_elements_calls += 1
        return []

    def quit(self) -> None:
        self.quit_called = True


class TransportFailureGestureDriver:
    def __init__(self, element: FakeElement) -> None:
        self.capabilities = {"platformName": "Android"}
        self.quit_called = False
        self.element = element
        self.script_count = 0

    def find_elements(self, *, by: str, value: str) -> list[FakeElement]:
        return [self.element]

    def execute_script(self, command: str, arguments: object) -> None:
        self.script_count += 1
        raise WebDriverException("transport lost with very-secret")

    def quit(self) -> None:
        self.quit_called = True


class TimeoutRecordingClientConfig:
    def __init__(self) -> None:
        self.timeout = 120.0


class TimeoutRecordingCommandExecutor:
    def __init__(self) -> None:
        self.client_config = TimeoutRecordingClientConfig()


class TimeoutRecordingDriver(TransportFailureGestureDriver):
    def __init__(self, element: FakeElement) -> None:
        super().__init__(element)
        self.command_executor = TimeoutRecordingCommandExecutor()
        self.transport_timeouts: list[float] = []

    def find_elements(self, *, by: str, value: str) -> list[FakeElement]:
        self.transport_timeouts.append(self.command_executor.client_config.timeout)
        if len(self.transport_timeouts) == 1:
            sleep(0.03)
        return [self.element]


class BlockingFindDriver(TransportFailureGestureDriver):
    def __init__(self, element: FakeElement) -> None:
        super().__init__(element)
        self.find_started = threading.Event()
        self.release_find = threading.Event()

    def find_elements(self, *, by: str, value: str) -> list[FakeElement]:
        self.find_started.set()
        self.release_find.wait(timeout=2)
        return [self.element]


class FakeDriver:
    def __init__(self, elements: tuple[FakeElement, ...]) -> None:
        self.elements = elements
        self.scripts: list[ScriptCall] = []

    def find_elements(self, *, by: str, value: str) -> list[FakeElement]:
        return list(self.elements)

    def execute_script(self, command: str, arguments: object) -> None:
        self.scripts.append(ScriptCall(command=command, arguments=str(arguments)))


class FakeRemoteDriver:
    def __init__(self) -> None:
        self.capabilities = {"platformName": "Android"}
        self.quit_called = False

    def quit(self) -> None:
        self.quit_called = True


class ClientConfigSnapshot(StrictModel):
    username: str | None
    password: str | None
    ignore_certificates: bool
    direct_connection: bool


def locator_plan(package: str | None = None) -> LocatorPlan:
    return LocatorPlan(
        strategy=LocatorStrategy.ACCESSIBILITY_ID,
        value="Welcome",
        description="content_description='Welcome'",
        uses_xpath=False,
        package=package,
    )


def test_native_query_filters_elements_to_app_scope() -> None:
    expected = FakeElement("app", "com.example")
    system = FakeElement("system", "com.android.systemui")
    result = execute_query(FakeDriver((expected, system)), locator_plan("com.example"))
    assert tuple(element.identity for element in result.elements) == ("app",)


def test_snapshot_uses_uiautomator2_camel_case_window_id_attribute() -> None:
    element = CamelCaseWindowIdElement("app", "com.example")

    result = execute_query(FakeDriver((element,)), locator_plan("com.example"))

    assert result.elements[0].window_id == "42"


def test_snapshot_tolerates_uiautomator2_without_window_id_attribute() -> None:
    element = UnsupportedWindowIdElement("app", "com.example")

    result = execute_query(FakeDriver((element,)), locator_plan("com.example"))

    assert result.elements[0].window_id == ""


def test_snapshot_propagates_transport_failure_while_reading_window_id() -> None:
    element = WindowIdTransportFailureElement("app", "com.example")

    with pytest.raises(BackendError) as caught:
        execute_query(FakeDriver((element,)), locator_plan("com.example"))

    assert caught.value.failure.kind is BackendFailureKind.UNKNOWN


def test_swipe_serializes_typed_mobile_command() -> None:
    driver = FakeDriver((FakeElement("app", "com.example"),))
    result = execute_action(
        driver,
        locator_plan("com.example"),
        ActionRequest(
            kind=ActionKind.SWIPE,
            direction=Direction.UP,
            percent=0.5,
        ),
    )
    assert result.element.identity == "app"
    assert driver.scripts[0].command == MobileCommand.SWIPE_GESTURE.value
    assert "elementId" in driver.scripts[0].arguments


def test_scroll_into_view_uses_internal_scrollable_container() -> None:
    container = FakeElement("container", "com.example")
    container.is_displayed = lambda: False  # type: ignore[method-assign]
    driver = FakeDriver((container,))
    result = execute_scroll_into_view(driver, locator_plan("com.example"))
    assert result.succeeded
    assert driver.scripts[0].command == MobileCommand.SCROLL_GESTURE.value


def test_scroll_into_view_prefers_nearest_scrollable_ancestor() -> None:
    target = FakeElement("target", "com.example")
    target.is_displayed = lambda: False  # type: ignore[method-assign]
    ancestor = FakeElement("nearest", "com.example")
    target.ancestors.append(ancestor)
    driver = FakeDriver((target,))
    result = execute_scroll_into_view(driver, locator_plan("com.example"))
    assert result.succeeded
    assert "nearest" in driver.scripts[0].arguments


def test_fill_clears_and_types_without_exposing_element() -> None:
    element = StatefulElement("app", "com.example", editable=True)
    result = execute_action(
        FakeDriver((element,)),
        locator_plan("com.example"),
        ActionRequest(kind=ActionKind.FILL, text="new value"),
    )
    assert result.element.text == "Welcome"
    assert element.cleared
    assert element.keys == ["new value"]


def test_tap_and_check_use_element_click() -> None:
    tapped = FakeElement("tap", "com.example")
    checked = FakeElement("check", "com.example")
    checked.checkable = True
    execute_action(
        FakeDriver((tapped,)),
        locator_plan("com.example"),
        ActionRequest(kind=ActionKind.TAP),
    )
    execute_action(
        FakeDriver((checked,)),
        locator_plan("com.example"),
        ActionRequest(kind=ActionKind.CHECK),
    )
    assert tapped.clicked
    assert checked.clicked


def test_tap_succeeds_when_screen_transition_stales_clicked_element() -> None:
    element = StaleAfterClickElement("tap", "com.example")

    result = execute_action(
        FakeDriver((element,)),
        locator_plan("com.example"),
        ActionRequest(kind=ActionKind.TAP),
    )

    assert result.element.identity == "tap"
    assert element.click_count == 1


def test_check_result_reports_requested_state_without_post_click_snapshot() -> None:
    element = StaleAfterClickElement("check", "com.example")

    result = execute_action(
        FakeDriver((element,)),
        locator_plan("com.example"),
        ActionRequest(kind=ActionKind.CHECK),
    )

    assert result.element.checked
    assert element.click_count == 1


def test_dispatch_returns_receipt_from_supplied_pre_action_snapshot() -> None:
    element = StaleAfterClickElement("tap", "com.example")
    plan = locator_plan("com.example")
    pre_action = execute_query(FakeDriver((element,)), plan).elements[0]

    receipt = execute_dispatch(
        FakeDriver((element,)),
        plan,
        ActionRequest(kind=ActionKind.TAP),
        pre_action,
    )

    assert receipt.pre_action == pre_action
    assert receipt.dispatch_state is DispatchState.DISPATCHED
    assert receipt.dispatched_at is not None
    assert element.click_count == 1


def test_trial_receipt_uses_fresh_same_id_live_snapshot() -> None:
    plan = locator_plan("com.example")
    initial = FakeElement("tap", "com.example")
    initial.text = "Initial"
    pre_action = execute_query(FakeDriver((initial,)), plan).elements[0]
    live = FakeElement("tap", "com.example")
    live.text = "Live"

    receipt = execute_dispatch(
        FakeDriver((live,)),
        plan,
        ActionRequest(kind=ActionKind.TAP, trial=True),
        pre_action,
    )

    assert receipt.dispatch_state is DispatchState.NOT_DISPATCHED
    assert receipt.pre_action.text == "Live"
    assert not live.clicked


def test_transport_failure_after_submission_carries_unknown_dispatch_receipt() -> None:
    element = TransportFailureAfterClickElement("tap", "com.example")
    plan = locator_plan("com.example")
    pre_action = execute_query(FakeDriver((element,)), plan).elements[0]

    with pytest.raises(IndeterminateActionBackendError) as caught:
        execute_dispatch(
            FakeDriver((element,)),
            plan,
            ActionRequest(kind=ActionKind.TAP),
            pre_action,
        )

    assert caught.value.receipt.dispatch_state is DispatchState.UNKNOWN
    assert caught.value.receipt.pre_action == pre_action
    assert caught.value.receipt.replay_safety is ReplaySafety.NON_REPLAYABLE
    assert element.click_count == 1


def test_failure_before_submission_remains_recoverable_not_indeterminate() -> None:
    plan = locator_plan("com.example")
    pre_action = execute_query(
        FakeDriver((FakeElement("tap", "com.example"),)),
        plan,
    ).elements[0]

    with pytest.raises(RecoverableBackendError) as caught:
        execute_dispatch(
            PreDispatchFailureDriver(),
            plan,
            ActionRequest(kind=ActionKind.TAP),
            pre_action,
        )

    assert caught.value.failure.kind is BackendFailureKind.RECOVERABLE


def test_check_dispatch_is_classified_as_idempotent_state_setting() -> None:
    element = StaleAfterClickElement("check", "com.example")
    plan = locator_plan("com.example")
    pre_action = execute_query(FakeDriver((element,)), plan).elements[0]

    receipt = execute_dispatch(
        FakeDriver((element,)),
        plan,
        ActionRequest(kind=ActionKind.CHECK),
        pre_action,
    )

    assert receipt.replay_safety is ReplaySafety.IDEMPOTENT
    assert receipt.pre_action == pre_action
    assert receipt.dispatch_state is DispatchState.DISPATCHED
    assert element.click_count == 1


def test_check_noops_when_same_live_element_is_already_checked() -> None:
    plan = locator_plan("com.example")
    pre_action = execute_query(
        FakeDriver((StatefulElement("check", "com.example", checked=False),)),
        plan,
    ).elements[0]
    live = StatefulElement("check", "com.example", checked=True)

    receipt = execute_dispatch(
        FakeDriver((live,)),
        plan,
        ActionRequest(kind=ActionKind.CHECK),
        pre_action,
    )

    assert receipt.dispatch_state is DispatchState.NOT_DISPATCHED
    assert receipt.pre_action.checked
    assert not live.clicked


def test_check_clicks_when_same_live_element_became_unchecked() -> None:
    plan = locator_plan("com.example")
    pre_action = execute_query(
        FakeDriver((StatefulElement("check", "com.example", checked=True),)),
        plan,
    ).elements[0]
    live = StatefulElement("check", "com.example", checked=False)

    receipt = execute_dispatch(
        FakeDriver((live,)),
        plan,
        ActionRequest(kind=ActionKind.CHECK),
        pre_action,
    )

    assert receipt.dispatch_state is DispatchState.DISPATCHED
    assert not receipt.pre_action.checked
    assert live.clicked


def test_unknown_receipt_uses_fresh_same_id_live_snapshot() -> None:
    plan = locator_plan("com.example")
    pre_action = execute_query(
        FakeDriver((StatefulElement("check", "com.example", checked=True),)),
        plan,
    ).elements[0]
    live = TransportFailureStatefulElement("check", "com.example", checked=False)

    with pytest.raises(IndeterminateActionBackendError) as caught:
        execute_dispatch(
            FakeDriver((live,)),
            plan,
            ActionRequest(kind=ActionKind.CHECK),
            pre_action,
        )

    assert caught.value.receipt.dispatch_state is DispatchState.UNKNOWN
    assert not caught.value.receipt.pre_action.checked


def test_zero_area_live_element_is_not_actionable() -> None:
    plan = locator_plan("com.example")
    element = FakeElement("tap", "com.example")
    pre_action = execute_query(FakeDriver((element,)), plan).elements[0]
    live = FakeElement("tap", "com.example")
    live.rect = {"x": 1, "y": 2, "width": 0, "height": 40}

    with pytest.raises(RecoverableBackendError, match="element has no visible area"):
        execute_dispatch(
            FakeDriver((live,)),
            plan,
            ActionRequest(kind=ActionKind.TAP),
            pre_action,
        )

    assert not live.clicked


def test_non_checkable_live_element_cannot_receive_check() -> None:
    plan = locator_plan("com.example")
    element = StatefulElement("check", "com.example", checkable=True)
    pre_action = execute_query(FakeDriver((element,)), plan).elements[0]
    live = StatefulElement("check", "com.example", checkable=False)

    with pytest.raises(RecoverableBackendError, match="element is not checkable"):
        execute_dispatch(
            FakeDriver((live,)),
            plan,
            ActionRequest(kind=ActionKind.CHECK),
            pre_action,
        )

    assert not live.clicked


@pytest.mark.parametrize(
    "action_request",
    [
        ActionRequest(kind=ActionKind.FILL, text="value"),
        ActionRequest(kind=ActionKind.CLEAR),
        ActionRequest(kind=ActionKind.PRESS, key="ENTER"),
    ],
)
def test_non_editable_live_element_rejects_text_actions(
    action_request: ActionRequest,
) -> None:
    plan = locator_plan("com.example")
    initial = StatefulElement("field", "com.example", editable=True)
    pre_action = execute_query(FakeDriver((initial,)), plan).elements[0]
    live = StatefulElement("field", "com.example", editable=False)

    with pytest.raises(RecoverableBackendError, match="element is not editable"):
        execute_dispatch(FakeDriver((live,)), plan, action_request, pre_action)

    assert not live.cleared
    assert live.keys == []


@pytest.mark.parametrize("kind", [ActionKind.SWIPE, ActionKind.SCROLL])
def test_disabled_live_element_allows_supported_gestures(kind: ActionKind) -> None:
    plan = locator_plan("com.example")
    initial = StatefulElement("gesture", "com.example", enabled=True)
    pre_action = execute_query(FakeDriver((initial,)), plan).elements[0]
    live = StatefulElement("gesture", "com.example", enabled=False)
    driver = FakeDriver((live,))

    receipt = execute_dispatch(
        driver,
        plan,
        ActionRequest(kind=kind, direction=Direction.DOWN),
        pre_action,
    )

    assert receipt.dispatch_state is DispatchState.DISPATCHED
    assert len(driver.scripts) == 1


def test_non_actionable_live_element_fails_recoverably_before_dispatch() -> None:
    plan = locator_plan("com.example")
    pre_action = execute_query(
        FakeDriver((FakeElement("tap", "com.example"),)),
        plan,
    ).elements[0]
    live = StatefulElement("tap", "com.example", displayed=False)

    with pytest.raises(RecoverableBackendError):
        execute_dispatch(
            FakeDriver((live,)),
            plan,
            ActionRequest(kind=ActionKind.TAP),
            pre_action,
        )

    assert not live.clicked


def test_final_snapshot_failure_is_recoverable_before_dispatch() -> None:
    plan = locator_plan("com.example")
    pre_action = execute_query(
        FakeDriver((FakeElement("tap", "com.example"),)),
        plan,
    ).elements[0]
    live = SnapshotFailureElement("tap", "com.example")

    with pytest.raises(RecoverableBackendError):
        execute_dispatch(
            FakeDriver((live,)),
            plan,
            ActionRequest(kind=ActionKind.TAP),
            pre_action,
        )

    assert not live.clicked


@pytest.mark.parametrize(
    ("action", "command"),
    [
        (ActionKind.DOUBLE_TAP, MobileCommand.DOUBLE_CLICK_GESTURE),
        (ActionKind.LONG_PRESS, MobileCommand.LONG_CLICK_GESTURE),
        (ActionKind.SCROLL, MobileCommand.SCROLL_GESTURE),
    ],
)
def test_gesture_actions_use_central_mobile_command_enum(
    action: ActionKind,
    command: MobileCommand,
) -> None:
    driver = FakeDriver((FakeElement("app", "com.example"),))
    request = (
        ActionRequest(kind=action, direction=Direction.DOWN)
        if action is ActionKind.SCROLL
        else ActionRequest(kind=action)
    )
    execute_action(driver, locator_plan("com.example"), request)
    assert driver.scripts[0].command == command.value


def test_backend_error_messages_redact_access_keys() -> None:
    server = AppiumServer.remote(
        url="https://grid.example.test",
        security=AppiumSecurityOptions(access_key=SecretStr("very-secret")),
    )
    backend = AppiumBackend(server)
    assert backend.sanitize_message("failed with very-secret") == "failed with [REDACTED]"


def test_backend_message_sanitization_ignores_empty_access_key() -> None:
    server = AppiumServer.remote(
        url="https://grid.example.test",
        security=AppiumSecurityOptions(access_key=SecretStr("")),
    )
    backend = AppiumBackend(server)

    assert backend.sanitize_message("ordinary failure") == "ordinary failure"


@pytest.mark.asyncio
async def test_backend_server_logs_redact_access_keys() -> None:
    backend = AppiumBackend(
        AppiumServer.remote(
            url="https://grid.example.test",
            security=AppiumSecurityOptions(access_key=SecretStr("very-secret")),
        )
    )
    backend.server_logs.append(
        ServerLogRecord(
            stream=LogStream.STANDARD_ERROR,
            message="authentication failed for very-secret",
        )
    )
    records = await backend.read_server_logs()
    assert records[0].message == "authentication failed for [REDACTED]"


@pytest.mark.asyncio
async def test_backend_preserves_recoverable_error_kind() -> None:
    backend = AppiumBackend(AppiumServer.remote(url="https://grid.example.test"))
    backend.worker = await SessionWorker.create(
        FakeRemoteDriver,
        timedelta(seconds=1),
    )

    def stale_operation(driver: FakeRemoteDriver) -> None:
        raise RecoverableBackendError(
            BackendFailure(
                kind=BackendFailureKind.RECOVERABLE,
                message="stale element",
            )
        )

    with pytest.raises(RecoverableBackendError, match="stale element"):
        await backend.invoke(stale_operation, timedelta(seconds=1))
    await backend.close_session()


@pytest.mark.asyncio
async def test_observe_captures_page_source_once_without_element_queries() -> None:
    driver = HierarchyDriver()
    backend = AppiumBackend(AppiumServer.remote(url="https://grid.example.test"))
    backend.worker = await SessionWorker.create(lambda: driver, timedelta(seconds=1))

    source = await backend.observe(timedelta(seconds=1))

    assert source.content == "<hierarchy><node text='Welcome'/></hierarchy>"
    assert driver.page_source_reads == 1
    assert driver.find_elements_calls == 0
    await backend.close_session()


@pytest.mark.asyncio
async def test_resolve_returns_final_element_snapshot() -> None:
    element = FakeElement("app", "com.example")
    driver = TransportFailureGestureDriver(element)
    backend = AppiumBackend(AppiumServer.remote(url="https://grid.example.test"))
    backend.worker = await SessionWorker.create(lambda: driver, timedelta(seconds=1))

    result = await backend.resolve(locator_plan("com.example"), timedelta(seconds=1))

    assert tuple(snapshot.identity for snapshot in result.elements) == ("app",)
    await backend.close_session()


@pytest.mark.asyncio
async def test_dispatch_translation_preserves_sanitization_and_mobile_command() -> None:
    element = FakeElement("app", "com.example")
    driver = TransportFailureGestureDriver(element)
    backend = AppiumBackend(
        AppiumServer.remote(
            url="https://grid.example.test",
            security=AppiumSecurityOptions(access_key=SecretStr("very-secret")),
        )
    )
    backend.worker = await SessionWorker.create(lambda: driver, timedelta(seconds=1))
    plan = locator_plan("com.example")
    pre_action = execute_query(FakeDriver((element,)), plan).elements[0]

    with pytest.raises(IndeterminateActionBackendError) as caught:
        await backend.dispatch(
            plan,
            ActionRequest(kind=ActionKind.SWIPE, direction=Direction.UP),
            pre_action,
            timedelta(seconds=1),
        )

    assert "[REDACTED]" in caught.value.failure.message
    assert "very-secret" not in caught.value.failure.message
    assert caught.value.failure.appium_command is MobileCommand.SWIPE_GESTURE
    assert caught.value.receipt.dispatch_state is DispatchState.UNKNOWN
    assert driver.script_count == 1
    await backend.close_session()


@pytest.mark.asyncio
async def test_transport_timeout_after_submission_is_indeterminate() -> None:
    element = SlowClickElement("app", "com.example")
    driver = TransportFailureGestureDriver(element)
    backend = AppiumBackend(AppiumServer.remote(url="https://grid.example.test"))
    backend.worker = await SessionWorker.create(lambda: driver, timedelta(seconds=1))
    plan = locator_plan("com.example")
    pre_action = execute_query(FakeDriver((element,)), plan).elements[0]

    with pytest.raises(IndeterminateActionBackendError) as caught:
        await backend.dispatch(
            plan,
            ActionRequest(kind=ActionKind.TAP),
            pre_action,
            timedelta(milliseconds=5),
        )

    assert caught.value.receipt.dispatch_state is DispatchState.UNKNOWN
    assert caught.value.failure.kind is BackendFailureKind.TAINTED
    assert element.click_count == 1
    await backend.close_session()


@pytest.mark.asyncio
async def test_dispatch_timeout_during_final_resolution_is_indeterminate() -> None:
    element = FakeElement("app", "com.example")
    driver = BlockingFindDriver(element)
    backend = AppiumBackend(AppiumServer.remote(url="https://grid.example.test"))
    backend.worker = await SessionWorker.create(lambda: driver, timedelta(seconds=1))
    plan = locator_plan("com.example")
    pre_action = execute_query(FakeDriver((element,)), plan).elements[0]

    try:
        with pytest.raises(IndeterminateActionBackendError) as caught:
            await backend.dispatch(
                plan,
                ActionRequest(kind=ActionKind.TAP),
                pre_action,
                timedelta(milliseconds=10),
            )
    finally:
        driver.release_find.set()

    assert driver.find_started.is_set()
    assert caught.value.receipt.dispatch_state is DispatchState.UNKNOWN
    attempt = 0
    while not element.clicked and attempt < 100:
        await asyncio.sleep(0.001)
        attempt += 1
    assert element.clicked
    await backend.close_session()


@pytest.mark.asyncio
async def test_dispatch_without_active_worker_remains_not_started() -> None:
    element = FakeElement("app", "com.example")
    plan = locator_plan("com.example")
    pre_action = execute_query(FakeDriver((element,)), plan).elements[0]
    backend = AppiumBackend(AppiumServer.remote(url="https://grid.example.test"))

    with pytest.raises(BackendError) as caught:
        await backend.dispatch(
            plan,
            ActionRequest(kind=ActionKind.TAP),
            pre_action,
            timedelta(seconds=1),
        )

    assert type(caught.value) is BackendError
    assert caught.value.failure.kind is BackendFailureKind.NOT_STARTED


@pytest.mark.asyncio
async def test_dispatch_rejected_by_tainted_worker_is_not_indeterminate() -> None:
    element = FakeElement("app", "com.example")
    driver = TransportFailureGestureDriver(element)
    backend = AppiumBackend(AppiumServer.remote(url="https://grid.example.test"))
    backend.worker = await SessionWorker.create(lambda: driver, timedelta(seconds=1))
    backend.worker.mark_tainted()
    plan = locator_plan("com.example")
    pre_action = execute_query(FakeDriver((element,)), plan).elements[0]

    with pytest.raises(BackendError) as caught:
        await backend.dispatch(
            plan,
            ActionRequest(kind=ActionKind.TAP),
            pre_action,
            timedelta(seconds=1),
        )

    assert type(caught.value) is BackendError
    assert caught.value.failure.kind is BackendFailureKind.TAINTED
    assert not element.clicked
    await backend.close_session()


@pytest.mark.asyncio
async def test_legacy_perform_uses_safe_dispatch_boundary() -> None:
    element = SlowClickElement("app", "com.example")
    driver = TransportFailureGestureDriver(element)
    backend = AppiumBackend(AppiumServer.remote(url="https://grid.example.test"))
    backend.worker = await SessionWorker.create(lambda: driver, timedelta(seconds=1))

    with pytest.raises(IndeterminateActionBackendError) as caught:
        await backend.perform(
            locator_plan("com.example"),
            ActionRequest(kind=ActionKind.TAP),
            timedelta(milliseconds=5),
        )

    assert caught.value.receipt.dispatch_state is DispatchState.UNKNOWN
    assert element.click_count == 1
    await backend.close_session()


@pytest.mark.asyncio
async def test_legacy_perform_shares_one_timeout_budget() -> None:
    element = FakeElement("app", "com.example")
    driver = TimeoutRecordingDriver(element)
    backend = AppiumBackend(AppiumServer.remote(url="https://grid.example.test"))
    backend.worker = await SessionWorker.create(lambda: driver, timedelta(seconds=1))

    await backend.perform(
        locator_plan("com.example"),
        ActionRequest(kind=ActionKind.TAP),
        timedelta(milliseconds=200),
    )

    assert len(driver.transport_timeouts) == 2
    assert driver.transport_timeouts[0] == pytest.approx(0.2, abs=0.01)
    assert driver.transport_timeouts[1] < driver.transport_timeouts[0]
    await backend.close_session()


def test_remote_driver_receives_tls_auth_without_untrusted_direct_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots: list[ClientConfigSnapshot] = []

    def remote(
        command_executor: str,
        *,
        options: UiAutomator2Options,
        client_config: AppiumClientConfig,
    ) -> FakeRemoteDriver:
        snapshots.append(
            ClientConfigSnapshot(
                username=cast(
                    str | None,
                    client_config.username,  # pyright: ignore[reportUnknownMemberType]
                ),
                password=cast(
                    str | None,
                    client_config.password,  # pyright: ignore[reportUnknownMemberType]
                ),
                ignore_certificates=bool(
                    cast(
                        bool | None,
                        client_config.ignore_certificates,  # pyright: ignore[reportUnknownMemberType]
                    )
                ),
                direct_connection=client_config.direct_connection,
            )
        )
        return FakeRemoteDriver()

    monkeypatch.setattr("appwright.backends.appium.adapter.webdriver.Remote", remote)
    backend = AppiumBackend(
        AppiumServer.remote(
            url="https://grid.example.test",
            security=AppiumSecurityOptions(
                username="user",
                access_key=SecretStr("very-secret"),
                verify_tls=False,
            ),
        )
    )
    backend.server_url = "https://grid.example.test"
    backend.create_driver(
        AndroidSessionOptions(
            device=AndroidDeviceSelector(serial="device"),
        )
    )
    assert snapshots == [
        ClientConfigSnapshot(
            username="user",
            password="very-secret",
            ignore_certificates=True,
            direct_connection=False,
        )
    ]
