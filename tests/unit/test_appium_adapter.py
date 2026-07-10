"""Appium boundary contract tests with blocking client doubles."""

from __future__ import annotations

from datetime import timedelta
from typing import cast

import pytest
from appium.options.android import UiAutomator2Options
from appium.webdriver.client_config import AppiumClientConfig
from pydantic import SecretStr

from appwright.backends.appium.adapter import (
    AppiumBackend,
    execute_action,
    execute_query,
    execute_scroll_into_view,
)
from appwright.backends.appium.worker import SessionWorker
from appwright.backends.base import (
    BackendFailure,
    BackendFailureKind,
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
from appwright.selectors.compiler import LocatorPlan


class ScriptCall(StrictModel):
    command: str
    arguments: str


class FakeElement:
    def __init__(self, identity: str, package: str) -> None:
        self.id = identity
        self.package = package
        self.text = "Welcome"
        self.rect = {"x": 1, "y": 2, "width": 100, "height": 40}
        self.clicked = False
        self.cleared = False
        self.keys: list[str] = []
        self.screenshot_as_png = b"png"
        self.ancestors: list[FakeElement] = []

    def get_attribute(self, name: str) -> str:
        if name == "className":
            return "android.widget.TextView"
        if name == "contentDescription":
            return "Welcome"
        if name == "resourceId":
            return f"{self.package}:id/welcome"
        if name == "package":
            return self.package
        if name in {"checked", "checkable", "focused"}:
            return "false"
        if name == "focusable":
            return "true"
        return ""

    def is_displayed(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True

    def is_selected(self) -> bool:
        return False

    def click(self) -> None:
        self.clicked = True

    def clear(self) -> None:
        self.cleared = True

    def send_keys(self, value: str) -> None:
        self.keys.append(value)

    def find_elements(self, *, by: str, value: str) -> list[FakeElement]:
        return self.ancestors


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
    element = FakeElement("app", "com.example")
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
