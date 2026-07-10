"""Backend-neutral async runtime contract tests."""

from datetime import timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest

from appwright.assertions.async_assertions import expect
from appwright.backends.appium import AppiumBackend
from appwright.backends.base import BackendFailure, BackendFailureKind
from appwright.core.errors import (
    DeviceNotFoundError,
    InvalidSelectorError,
    ProtocolError,
    StrictModeViolationError,
    TargetClosedError,
)
from appwright.core.runtime import AsyncAndroid, actionability_problem, async_appwright
from appwright.models.config import AppiumTimeouts, RetryPolicy
from appwright.models.data import Point, QueryResult, Rect
from appwright.models.enums import ActionKind, Direction, Key, MatchMode, MobileCommand
from appwright.selectors import Selector, TextMatcher
from tests.fakes import FakeBackendFactory, FakeCallKind, element


@pytest.mark.asyncio
async def test_connect_creates_device_session_and_device_api_is_immediately_usable() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(serial="device-1")
        backend = factory.backends[0]
        assert backend.calls[:2] == [FakeCallKind.START, FakeCallKind.CREATE_SESSION]
        assert await device.screen.get_by_text("Welcome").count() == 1
        await device.keyboard.press(Key.ENTER)
        await device.touchscreen.tap(Point(x=1, y=2))
        await device.hierarchy()
        await device.install_app(Path("application.apk"))
        await device.uninstall_app("com.example")


@pytest.mark.asyncio
async def test_launch_app_reuses_session_and_invalidates_replaced_app_handles() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(serial="device-1")
        first_app = await device.launch_app(package="com.example.first")
        stale_locator = first_app.get_by_text("Welcome")
        second_app = await device.launch_app(
            package="com.example.second",
            app_path=Path("application.apk"),
            clear_data=True,
        )
        assert await second_app.get_by_text("Welcome").count() == 1
        backend = factory.backends[0]
        assert backend.calls.count(FakeCallKind.CREATE_SESSION) == 1
        assert FakeCallKind.INSTALL in backend.calls
        assert FakeCallKind.CLEAR in backend.calls
        assert backend.calls.count(FakeCallKind.ACTIVATE) == 2
        with pytest.raises(TargetClosedError):
            await stale_locator.count()


@pytest.mark.asyncio
async def test_app_close_invalidates_app_and_locator_handles_without_closing_device() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(serial="device-1")
        app = await device.launch_app(package="com.example")
        locator = app.get_by_text("Welcome")
        await app.close()
        with pytest.raises(TargetClosedError):
            await locator.count()
        assert await device.screen.get_by_text("Welcome").count() == 1


@pytest.mark.asyncio
async def test_async_locator_actions_and_assertions() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        action=timedelta(seconds=1),
        expectation=timedelta(seconds=1),
        stability=timedelta(milliseconds=1),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(serial="device-1", timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        locator = app.get_by_text("Welcome")
        await locator.tap()
        await expect(locator).to_be_visible()
        await expect(locator).to_have_text("Welcome")
    backend = factory.backends[0]
    assert FakeCallKind.PERFORM in backend.calls
    assert backend.closed


@pytest.mark.asyncio
async def test_assertion_retries_until_match() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        expectation=timedelta(seconds=1),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.query_results.extend(
            (
                QueryResult(elements=()),
                QueryResult(elements=(element(),)),
            )
        )
        await expect(app.get_by_text("Welcome")).to_be_visible()


@pytest.mark.asyncio
async def test_strict_mode_rejects_multiple_elements() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(stability=timedelta(milliseconds=1))
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.default_result = QueryResult(
            elements=(element(identity="one"), element(identity="two"))
        )
        with pytest.raises(StrictModeViolationError):
            await app.get_by_text("Welcome").tap()


@pytest.mark.asyncio
async def test_gestures_artifacts_and_application_management() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(stability=timedelta(milliseconds=1))
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        locator = app.get_by_text("Welcome")
        await locator.swipe(Direction.UP)
        await locator.scroll(Direction.DOWN, percent=0.5)
        screenshot = await locator.screenshot()
        await locator.drag_to(app.get_by_resource_id("target"))
        hierarchy = await device.hierarchy()
        logs = await device.server_logs()
        installed = await device.install_app(Path("example.apk"))
        uninstalled = await device.uninstall_app("com.example")
    backend = factory.backends[0]
    assert screenshot.content == b"element-png"
    assert hierarchy.content == "<hierarchy />"
    assert logs == ()
    assert installed.succeeded
    assert uninstalled.succeeded
    assert FakeCallKind.DRAG in backend.calls
    assert FakeCallKind.INSTALL in backend.calls
    assert FakeCallKind.UNINSTALL in backend.calls


@pytest.mark.asyncio
async def test_assertion_retries_recoverable_backend_failures_with_remaining_deadline() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        expectation=timedelta(milliseconds=100),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.query_failures.append(
            BackendFailure(
                kind=BackendFailureKind.RECOVERABLE,
                message="stale element",
            )
        )
        await expect(app.get_by_text("Welcome")).to_be_visible()
        assert backend.query_timeouts
        assert all(timeout <= timeouts.expectation for timeout in backend.query_timeouts)


@pytest.mark.asyncio
async def test_negative_visibility_does_not_accept_duplicate_matches() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect()
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.default_result = QueryResult(
            elements=(element(identity="one"), element(identity="two"))
        )
        with pytest.raises(StrictModeViolationError):
            await expect(app.get_by_text("Welcome")).not_.to_be_visible()


@pytest.mark.asyncio
async def test_locator_all_returns_lazy_locators_and_snapshots_are_explicit() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect()
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.default_result = QueryResult(
            elements=(element(identity="one"), element(identity="two"))
        )
        locator = app.get_by_text("Welcome")
        locators = await locator.all()
        snapshots = await locator.element_infos()
        assert len(locators) == 2
        assert len(snapshots) == 2


@pytest.mark.asyncio
async def test_local_connection_requires_one_unambiguous_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_devices() -> tuple[()]:
        return ()

    monkeypatch.setattr("appwright.core.runtime.discover_android_devices", no_devices)
    android = AsyncAndroid(AppiumBackend)
    with pytest.raises(DeviceNotFoundError):
        await android.connect()


@pytest.mark.asyncio
async def test_action_attempts_typed_scroll_before_retrying_offscreen_target() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        action=timedelta(seconds=1),
        stability=timedelta(milliseconds=1),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.query_results.append(QueryResult(elements=()))
        await app.get_by_text("Welcome").tap()
    assert FakeCallKind.SCROLL_INTO_VIEW in backend.calls


@pytest.mark.asyncio
async def test_assertion_surface_uses_typed_snapshots() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect()
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.default_result = QueryResult(
            elements=(
                element(
                    checked=True,
                    checkable=True,
                    editable=True,
                    focused=True,
                    selected=True,
                ),
            )
        )
        locator = app.get_by_text("Welcome")
        assertions = expect(locator)
        await assertions.to_be_enabled()
        await assertions.to_be_editable()
        await assertions.to_be_checked()
        await assertions.to_be_focused()
        await assertions.to_be_selected()
        await assertions.to_have_text("Welcome")
        await assertions.to_contain_text("come")
        await assertions.to_have_accessible_name("Welcome")
        await assertions.to_have_resource_id("com.example:id/target")
        await assertions.to_have_count(1)
        await assertions.not_.to_be_disabled()
        await assertions.not_.to_be_unchecked()


@pytest.mark.asyncio
async def test_invalid_selector_is_a_structured_public_error() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect()
        app = await device.launch_app(package="com.example")
        locator = app.locator(Selector.text(TextMatcher(value="unsupported", mode=MatchMode.REGEX)))
        with pytest.raises(InvalidSelectorError) as captured:
            await locator.count()
        assert captured.value.details.code.value == "invalid_selector"
        assert "regular-expression selectors are not supported" in str(captured.value)


@pytest.mark.asyncio
async def test_locators_cannot_cross_application_and_screen_scopes() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect()
        app = await device.launch_app(package="com.example")
        with pytest.raises(ValueError, match="same application"):
            device.screen.get_by_text("Allow").and_(app.get_by_text("Continue"))


@pytest.mark.asyncio
async def test_error_redaction_and_planned_artifact_paths(tmp_path: Path) -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect()
        trace_path = tmp_path / "trace.zip"
        device.tracing.register_pattern("canary-[a-z]+")
        device.tracing.start(trace_path)
        app = await device.launch_app(package="com.example")
        factory.backends[0].query_failures.append(
            BackendFailure(
                kind=BackendFailureKind.UNKNOWN,
                message="transport exposed canary-secret",
                appium_command=MobileCommand.SCROLL_GESTURE,
            )
        )
        with pytest.raises(ProtocolError) as captured:
            await app.get_by_text("Welcome").count()
        assert "canary-secret" not in str(captured.value)
        assert captured.value.details.trace_path == trace_path
        assert captured.value.details.screenshot_path == tmp_path / "failure.png"
        assert captured.value.details.appium_command is MobileCommand.SCROLL_GESTURE
        device.tracing.stop()


@pytest.mark.asyncio
async def test_trace_start_replays_connection_seed_event(tmp_path: Path) -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(serial="device-1")
        path = tmp_path / "trace.zip"
        device.tracing.start(path)
        device.tracing.stop()
    with ZipFile(path) as archive:
        assert "android.connect" in archive.read("events.jsonl").decode()


def test_actionability_rejects_zero_area_elements() -> None:
    snapshot = element().model_copy(update={"bounds": Rect(x=0, y=0, width=0, height=40)})
    assert actionability_problem(snapshot, ActionKind.TAP) == "element has no visible area"
