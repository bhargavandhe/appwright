"""Backend-neutral async runtime contract tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest

from appwright.assertions.async_assertions import expect
from appwright.backends.appium import AppiumBackend
from appwright.backends.base import (
    BackendError,
    BackendFailure,
    BackendFailureKind,
    IndeterminateActionBackendError,
    RecoverableBackendError,
)
from appwright.core.errors import (
    DeviceNotFoundError,
    ExpectationError,
    IndeterminateActionError,
    InvalidSelectorError,
    ProtocolError,
    StrictModeViolationError,
    TargetClosedError,
)
from appwright.core.errors import TimeoutError as AppwrightTimeoutError
from appwright.core.runtime import AsyncAndroid, async_appwright
from appwright.models.config import AppiumTimeouts, RetryPolicy
from appwright.models.data import Point, QueryResult, Rect
from appwright.models.enums import (
    ActionKind,
    Direction,
    Key,
    MatchMode,
    MobileCommand,
    WaitState,
)
from appwright.operations import (
    ActionReceipt,
    DispatchState,
    OperationStage,
    ReplaySafety,
    actionability_problem,
    replay_safety_for,
)
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
        wait=timedelta(seconds=1),
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
    assert FakeCallKind.DISPATCH in backend.calls
    assert backend.closed


@pytest.mark.asyncio
async def test_assertion_retries_until_match() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        wait=timedelta(seconds=1),
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
    timeouts = AppiumTimeouts(
        action=timedelta(milliseconds=10),
        stability=timedelta(milliseconds=1),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
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
        wait=timedelta(milliseconds=100),
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
async def test_immediate_locator_probe_retries_recoverable_backend_failure() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        probe=timedelta(milliseconds=100),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.query_failures.append(
            BackendFailure(
                kind=BackendFailureKind.RECOVERABLE,
                message="element became stale during snapshot",
            )
        )

        snapshot = await app.get_by_text("Welcome").probe()

        assert snapshot is not None
        assert snapshot.identity == "element-1"
        assert backend.calls.count(FakeCallKind.RESOLVE) == 2
        assert all(timeout <= timeouts.probe for timeout in backend.query_timeouts)


@pytest.mark.asyncio
async def test_probe_returns_none_for_zero_matches_and_snapshot_for_one() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect()
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.query_results.extend(
            (
                QueryResult(elements=()),
                QueryResult(elements=(element(identity="only"),)),
            )
        )
        locator = app.get_by_text("Welcome")

        assert await locator.probe() is None
        assert (await locator.probe()).identity == "only"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_probe_rejects_duplicates_and_probe_all_preserves_them() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect()
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        duplicates = QueryResult(elements=(element(identity="one"), element(identity="two")))
        backend.query_results.extend((duplicates, duplicates))
        locator = app.get_by_text("Welcome")

        with pytest.raises(StrictModeViolationError):
            await locator.probe()
        assert tuple(item.identity for item in await locator.probe_all()) == (
            "one",
            "two",
        )


@pytest.mark.asyncio
async def test_negative_visibility_does_not_accept_duplicate_matches() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        wait=timedelta(milliseconds=10),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.default_result = QueryResult(
            elements=(element(identity="one"), element(identity="two"))
        )
        with pytest.raises(StrictModeViolationError):
            await expect(app.get_by_text("Welcome")).not_.to_be_visible()
        assert backend.calls.count(FakeCallKind.RESOLVE) > 1


@pytest.mark.asyncio
async def test_assertion_waits_for_transient_duplicate_to_become_unique() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        wait=timedelta(milliseconds=100),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.query_results.extend(
            (
                QueryResult(elements=(element(identity="old"), element(identity="new"))),
                QueryResult(elements=(element(identity="new"),)),
            )
        )

        await expect(app.get_by_text("Welcome")).to_be_visible()

        assert backend.calls.count(FakeCallKind.RESOLVE) == 2


@pytest.mark.asyncio
async def test_locator_wait_retries_duplicates_and_reports_persistent_strictness() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        wait=timedelta(milliseconds=10),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        duplicates = QueryResult(elements=(element(identity="one"), element(identity="two")))
        backend.default_result = duplicates

        with pytest.raises(StrictModeViolationError):
            await app.get_by_text("Welcome").wait_for(WaitState.VISIBLE)

        assert backend.calls.count(FakeCallKind.RESOLVE) > 1


@pytest.mark.asyncio
async def test_locator_wait_accepts_unique_match_after_transient_duplicate() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        wait=timedelta(milliseconds=100),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.query_results.extend(
            (
                QueryResult(elements=(element(identity="old"), element(identity="new"))),
                QueryResult(elements=(element(identity="new"),)),
            )
        )

        await app.get_by_text("Welcome").wait_for(WaitState.VISIBLE)

        assert backend.calls.count(FakeCallKind.RESOLVE) == 2


@pytest.mark.asyncio
async def test_recoverable_probe_never_falsely_satisfies_hidden_wait() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        wait=timedelta(milliseconds=10),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.query_failures.append(
            BackendFailure(
                kind=BackendFailureKind.RECOVERABLE,
                message="stale hierarchy",
            )
        )

        with pytest.raises(AppwrightTimeoutError):
            await app.get_by_text("Welcome").wait_for(WaitState.HIDDEN)

        assert backend.calls.count(FakeCallKind.RESOLVE) > 1


@pytest.mark.asyncio
async def test_recoverable_probe_never_falsely_satisfies_negated_assertion() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        wait=timedelta(milliseconds=10),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.query_failures.extend(
            [
                BackendFailure(
                    kind=BackendFailureKind.RECOVERABLE,
                    message="stale hierarchy",
                )
            ]
            * 100
        )

        with pytest.raises(ExpectationError):
            await expect(app.get_by_text("Welcome")).not_.to_be_visible()


@pytest.mark.asyncio
async def test_assertion_failure_retains_retry_call_log() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        wait=timedelta(milliseconds=10),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.query_failures.append(
            BackendFailure(
                kind=BackendFailureKind.RECOVERABLE,
                message="stale hierarchy",
            )
        )

        with pytest.raises(ExpectationError) as captured:
            await expect(app.get_by_text("Welcome")).to_have_text("Dashboard")

        assert captured.value.details.call_log
        assert captured.value.details.call_log[0].message == "stale hierarchy"


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
async def test_action_does_not_implicitly_scroll_when_target_is_temporarily_missing() -> None:
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
    assert FakeCallKind.SCROLL_INTO_VIEW not in backend.calls


@pytest.mark.asyncio
async def test_action_scrolls_only_when_auto_scroll_is_explicitly_enabled() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        action=timedelta(milliseconds=100),
        stability=timedelta(milliseconds=1),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        unique = QueryResult(elements=(element(identity="target"),))
        backend.query_results.extend((QueryResult(elements=()), unique, unique))

        await app.get_by_text("Welcome").tap(auto_scroll=True)

        assert backend.calls.count(FakeCallKind.SCROLL_INTO_VIEW) == 1
        assert backend.action_requests[-1].kind is ActionKind.TAP


@pytest.mark.asyncio
async def test_scroll_into_view_is_an_explicit_locator_operation() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect()
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]

        result = await app.get_by_text("Welcome").scroll_into_view()

        assert result.succeeded
        assert backend.calls.count(FakeCallKind.SCROLL_INTO_VIEW) == 1


@pytest.mark.asyncio
async def test_scroll_transport_failure_has_unknown_receipt_and_no_replay() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect()
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.scroll_errors.append(
            BackendError(
                BackendFailure(
                    kind=BackendFailureKind.UNKNOWN,
                    message="connection dropped after scroll submission",
                )
            )
        )

        with pytest.raises(IndeterminateActionError) as captured:
            await app.get_by_text("Welcome").scroll_into_view()

        assert captured.value.receipt.action is ActionKind.SCROLL
        assert captured.value.receipt.dispatch_state is DispatchState.UNKNOWN
        assert backend.calls.count(FakeCallKind.SCROLL_INTO_VIEW) == 1


@pytest.mark.asyncio
async def test_action_waits_for_transient_duplicate_to_become_unique() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        action=timedelta(milliseconds=100),
        stability=timedelta(milliseconds=1),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        unique = QueryResult(elements=(element(identity="unique"),))
        backend.query_results.extend(
            (
                QueryResult(elements=(element(identity="old"), element(identity="new"))),
                unique,
                unique,
            )
        )

        await app.get_by_text("Welcome").tap()

        assert backend.action_requests[-1].kind is ActionKind.TAP


@pytest.mark.asyncio
async def test_action_reports_persistent_duplicate_only_after_deadline() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        action=timedelta(milliseconds=10),
        stability=timedelta(milliseconds=1),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.default_result = QueryResult(
            elements=(element(identity="old"), element(identity="new"))
        )

        with pytest.raises(StrictModeViolationError):
            await app.get_by_text("Welcome").tap()

        assert backend.calls.count(FakeCallKind.RESOLVE) > 1


def unknown_dispatch_receipt(action: ActionKind) -> ActionReceipt:
    return ActionReceipt(
        action=action,
        locator="text(exact)='Welcome'",
        replay_safety=replay_safety_for(action),
        stage=OperationStage.DISPATCH,
        dispatch_state=DispatchState.UNKNOWN,
        started_at=datetime.now(UTC),
        pre_action=element(checkable=action in {ActionKind.CHECK, ActionKind.UNCHECK}),
    )


@pytest.mark.asyncio
async def test_unknown_tap_dispatch_raises_typed_error_without_replay() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(stability=timedelta(milliseconds=1))
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        receipt = unknown_dispatch_receipt(ActionKind.TAP)
        backend.dispatch_errors.append(
            IndeterminateActionBackendError(
                BackendFailure(
                    kind=BackendFailureKind.UNKNOWN,
                    message="connection dropped after click submission",
                ),
                receipt,
            )
        )

        with pytest.raises(IndeterminateActionError) as captured:
            await app.get_by_text("Welcome").tap()

        assert captured.value.receipt.action is ActionKind.TAP
        assert captured.value.receipt.dispatch_state is DispatchState.UNKNOWN
        assert backend.calls.count(FakeCallKind.DISPATCH) == 1
        assert FakeCallKind.PERFORM not in backend.calls


@pytest.mark.asyncio
async def test_unknown_tap_never_replays_when_backend_receipt_claims_idempotence() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(stability=timedelta(milliseconds=1))
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        malformed_receipt = unknown_dispatch_receipt(ActionKind.CHECK).model_copy(
            update={
                "action": ActionKind.TAP,
                "replay_safety": ReplaySafety.IDEMPOTENT,
            }
        )
        backend.dispatch_errors.append(
            IndeterminateActionBackendError(
                BackendFailure(
                    kind=BackendFailureKind.UNKNOWN,
                    message="malformed receipt from custom backend",
                ),
                malformed_receipt,
            )
        )

        with pytest.raises(IndeterminateActionError):
            await app.get_by_text("Welcome").tap()

        assert backend.calls.count(FakeCallKind.DISPATCH) == 1


@pytest.mark.asyncio
async def test_final_stability_backend_error_is_translated() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(stability=timedelta(milliseconds=1))
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.query_outcomes.extend(
            (
                QueryResult(elements=(element(),)),
                BackendFailure(
                    kind=BackendFailureKind.UNKNOWN,
                    message="transport failed during stability probe",
                ),
            )
        )

        with pytest.raises(ProtocolError) as captured:
            await app.get_by_text("Welcome").tap()

        assert "stability probe" in str(captured.value)
        assert backend.calls.count(FakeCallKind.DISPATCH) == 0


@pytest.mark.asyncio
async def test_drag_transport_failure_has_unknown_receipt_and_no_replay() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(stability=timedelta(milliseconds=1))
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.drag_errors.append(
            BackendError(
                BackendFailure(
                    kind=BackendFailureKind.UNKNOWN,
                    message="connection dropped after drag submission",
                )
            )
        )

        with pytest.raises(IndeterminateActionError) as captured:
            await app.get_by_text("Welcome").drag_to(app.get_by_resource_id("target"))

        assert captured.value.receipt.action is ActionKind.DRAG_TO
        assert captured.value.receipt.dispatch_state is DispatchState.UNKNOWN
        assert captured.value.receipt.pre_action.text == "[REDACTED]"
        assert captured.value.receipt.pre_action.accessible_name == "[REDACTED]"
        assert backend.calls.count(FakeCallKind.DRAG) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["keyboard", "touchscreen"])
async def test_device_action_transport_failure_has_unknown_receipt(
    surface: str,
) -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect()
        backend = factory.backends[0]
        failure = BackendError(
            BackendFailure(
                kind=BackendFailureKind.UNKNOWN,
                message=f"connection dropped after {surface} submission",
            )
        )
        if surface == "keyboard":
            backend.press_key_errors.append(failure)
            operation = device.keyboard.press(Key.ENTER)
        else:
            backend.tap_point_errors.append(failure)
            operation = device.touchscreen.tap(Point(x=10, y=20))

        with pytest.raises(IndeterminateActionError) as captured:
            await operation

        expected_action = ActionKind.PRESS if surface == "keyboard" else ActionKind.TAP
        assert captured.value.receipt.action is expected_action
        assert captured.value.receipt.dispatch_state is DispatchState.UNKNOWN


@pytest.mark.asyncio
async def test_pre_dispatch_recoverable_failure_may_retry_tap() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        action=timedelta(milliseconds=100),
        stability=timedelta(milliseconds=1),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.dispatch_errors.append(
            RecoverableBackendError(
                BackendFailure(
                    kind=BackendFailureKind.RECOVERABLE,
                    message="element changed before submission",
                )
            )
        )

        await app.get_by_text("Welcome").tap()

        assert backend.calls.count(FakeCallKind.DISPATCH) == 2


@pytest.mark.asyncio
async def test_idempotent_check_may_retry_unknown_dispatch_after_reobservation() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(
        action=timedelta(milliseconds=100),
        stability=timedelta(milliseconds=1),
        retry=RetryPolicy(initial_delay=timedelta(milliseconds=1)),
    )
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=timeouts)
        app = await device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.default_result = QueryResult(elements=(element(checked=False, checkable=True),))
        backend.dispatch_errors.append(
            IndeterminateActionBackendError(
                BackendFailure(
                    kind=BackendFailureKind.UNKNOWN,
                    message="connection dropped after check submission",
                ),
                unknown_dispatch_receipt(ActionKind.CHECK),
            )
        )

        await app.get_by_text("Welcome").check()

        assert backend.calls.count(FakeCallKind.DISPATCH) == 2


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
