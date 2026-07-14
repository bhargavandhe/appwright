"""Bound typed controls and integrated mobile screen runtime contracts."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest

from appwright.core.runtime import async_appwright
from appwright.errors import TargetClosedError
from appwright.models.config import RetryPolicy, Timeouts
from appwright.models.data import HierarchySource, QueryResult
from appwright.operations import OperationDeadline
from appwright.screens import errors as screen_errors
from appwright.screens.elements import button, by_id, text_field
from appwright.screens.errors import LifecycleTimeoutError, TransitionTimeoutError
from appwright.screens.model import (
    AsyncDeviceScreen,
    AsyncInterruption,
    AsyncScreen,
    Interruption,
    Screen,
    visible,
)
from appwright.screens.recovery import back_until
from appwright.screens.runtime import (
    AsyncLifecycleCoordinator,
    AsyncMobileApp,
    AsyncScreenBinder,
)
from appwright.screens.targets import one_of
from appwright.screens.transitions import ScreenTimeoutError
from tests.fakes import FakeBackendFactory, FakeCallKind, element

APP_PACKAGE = "com.example"
DEVICE_PACKAGE = "com.android.permissioncontroller"


def hierarchy(*nodes: tuple[str, str]) -> str:
    source = "".join(
        (
            f'<node resource-id="{resource_id}" package="{package}" '
            'class="android.widget.Button" clickable="true" enabled="true" '
            'bounds="[0,0][100,100]" displayed="true" />'
        )
        for resource_id, package in nodes
    )
    return f"<hierarchy>{source}</hierarchy>"


LOGIN = (f"{APP_PACKAGE}:id/login_submit", APP_PACKAGE)
HOME = (f"{APP_PACKAGE}:id/home", APP_PACKAGE)
PASSKEY = (f"{APP_PACKAGE}:id/maybe_later", APP_PACKAGE)
PERMISSION = (
    f"{DEVICE_PACKAGE}:id/permission_allow_button",
    DEVICE_PACKAGE,
)


class Login(AsyncScreen):
    ready = visible(by_id("login_submit"))

    username = text_field(by_id("username"))
    submit = button(by_id("login_submit"))


class Home(AsyncScreen):
    ready = visible(by_id("home"))


class Permission(AsyncDeviceScreen):
    ready = visible(by_id(f"{DEVICE_PACKAGE}:id/permission_allow_button"))

    allow = button(by_id(f"{DEVICE_PACKAGE}:id/permission_allow_button"))


class Passkey(AsyncInterruption):
    ready = visible(by_id("maybe_later"))
    priority = 100

    later = button(by_id("maybe_later"))

    async def dismiss(self) -> None:
        await self.later.tap()


class SyncLogin(Screen):
    ready = visible(by_id("login_submit"))


class SyncPasskey(Interruption):
    ready = visible(by_id("maybe_later"))
    priority = 100

    def dismiss(self) -> None:
        return None


def mobile_timeouts() -> Timeouts:
    return Timeouts(
        action=timedelta(milliseconds=100),
        transition=timedelta(milliseconds=100),
        interruption=timedelta(milliseconds=50),
        stability=timedelta(milliseconds=1),
        retry=RetryPolicy(
            initial_delay=timedelta(milliseconds=1),
            multiplier=1,
            maximum_delay=timedelta(milliseconds=1),
        ),
    )


@pytest.mark.asyncio
async def test_bound_controls_dispatch_capability_specific_locator_actions() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]
        backend.default_result = QueryResult(elements=(element(editable=True),))
        backend.observation_sources.append(hierarchy(LOGIN))
        mobile = AsyncMobileApp(app)

        login = await mobile.wait_for(Login)
        await login.username.fill("test-user")
        await login.submit.tap()

        assert isinstance(login.binder, AsyncScreenBinder)
        assert login.binder.scope_package == APP_PACKAGE
        assert backend.calls.count(FakeCallKind.DISPATCH) == 2
        assert not hasattr(login.submit, "fill")


@pytest.mark.asyncio
async def test_tap_then_dispatches_once_and_returns_exact_destination_type() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]
        backend.observation_sources.extend((hierarchy(LOGIN), hierarchy(HOME)))
        mobile = AsyncMobileApp(app)
        login = await mobile.wait_for(Login)

        home = await login.submit.tap_then(Home)

        assert type(home) is Home
        assert backend.calls.count(FakeCallKind.DISPATCH) == 1
        assert backend.calls.count(FakeCallKind.OBSERVE) == 2


@pytest.mark.asyncio
async def test_device_screen_receives_unscoped_binder() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        factory.backends[0].observation_sources.append(hierarchy(PERMISSION))

        permission = await AsyncMobileApp(app).wait_for(Permission)

        assert isinstance(permission.binder, AsyncScreenBinder)
        assert permission.binder.scope_package is None


@pytest.mark.asyncio
async def test_device_screen_control_rejects_replaced_originating_app() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]
        backend.observation_sources.append(hierarchy(PERMISSION))
        backend.default_result = QueryResult(elements=(element(),))
        permission = await AsyncMobileApp(app).wait_for(Permission)
        await app.close()
        dispatches_before = backend.calls.count(FakeCallKind.DISPATCH)

        with pytest.raises(TargetClosedError):
            await permission.allow.tap()

        assert backend.calls.count(FakeCallKind.DISPATCH) == dispatches_before


@pytest.mark.asyncio
async def test_settle_dismisses_delayed_interruption_once_before_stable_home() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]
        backend.observation_sources.extend(
            (
                hierarchy(HOME),
                hierarchy(HOME, PASSKEY),
                hierarchy(HOME, PASSKEY),
                hierarchy(HOME),
                hierarchy(HOME),
            )
        )
        mobile = AsyncMobileApp(app, interruptions=(Passkey,))

        home = await mobile.settle(Home, stable_for=timedelta(milliseconds=1))

        assert type(home) is Home
        assert backend.calls.count(FakeCallKind.DISPATCH) == 1
        assert mobile.interruptions.history


@pytest.mark.asyncio
async def test_ensure_uses_bounded_back_recovery() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]
        backend.observation_sources.extend((hierarchy(LOGIN), hierarchy(HOME)))
        mobile = AsyncMobileApp(app)

        home = await mobile.ensure(Home, recovery=back_until(Home, max_steps=2))

        assert type(home) is Home
        assert backend.calls.count(FakeCallKind.PRESS_KEY) == 1


@pytest.mark.asyncio
async def test_tap_then_timeout_retains_exact_action_receipt_and_cause() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]
        backend.observation_sources.append(hierarchy(LOGIN))
        mobile = AsyncMobileApp(app)
        login = await mobile.wait_for(Login)

        with pytest.raises(TransitionTimeoutError) as captured:
            await login.submit.tap_then(Home, timeout=timedelta(milliseconds=10))

        error = captured.value
        assert error.receipt is backend.action_receipts[0]
        assert error.screen_timeout is error.__cause__
        assert isinstance(error.__cause__, ScreenTimeoutError)
        assert error.transition_history is error.screen_timeout.transition_history
        assert backend.calls.count(FakeCallKind.DISPATCH) == 1


@pytest.mark.asyncio
async def test_tap_then_failure_retains_exact_action_receipt_and_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]
        backend.observation_sources.extend((hierarchy(LOGIN), hierarchy(HOME)))
        mobile = AsyncMobileApp(app)
        login = await mobile.wait_for(Login)
        original_observe = backend.observe

        async def observe_then_replace(timeout: timedelta) -> HierarchySource:
            captured = await original_observe(timeout)
            device.close_application_handle(app.application_generation)
            return captured

        monkeypatch.setattr(backend, "observe", observe_then_replace)

        with pytest.raises(screen_errors.TransitionFailureError) as captured:
            await login.submit.tap_then(Home)

        error = captured.value
        assert error.receipt is backend.action_receipts[0]
        assert error.transition_error is error.__cause__
        assert isinstance(error.transition_error, TargetClosedError)


@pytest.mark.asyncio
async def test_cancelled_tap_then_records_receipt_without_wrapping_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]
        backend.observation_sources.append(hierarchy(LOGIN))
        mobile = AsyncMobileApp(app)
        login = await mobile.wait_for(Login)
        observation_started = asyncio.Event()
        observation_release = asyncio.Event()

        async def blocking_observe(timeout: timedelta) -> HierarchySource:
            del timeout
            observation_started.set()
            await observation_release.wait()
            return HierarchySource(content=hierarchy(HOME))

        monkeypatch.setattr(backend, "observe", blocking_observe)
        transition = asyncio.create_task(login.submit.tap_then(Home))
        await observation_started.wait()
        transition.cancel()

        try:
            with pytest.raises(asyncio.CancelledError):
                await transition
        finally:
            observation_release.set()

        receipt = backend.action_receipts[0]
        assert mobile.cancelled_transition_receipt is receipt
        assert mobile.coordinator.cancelled_transition_receipt is receipt


@pytest.mark.asyncio
async def test_mobile_runtime_rejects_closed_application_before_observing() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        mobile = AsyncMobileApp(app)
        backend = factory.backends[0]
        await app.close()
        backend.observation_sources.append(hierarchy(HOME))
        observations_before = backend.calls.count(FakeCallKind.OBSERVE)

        with pytest.raises(TargetClosedError):
            await mobile.wait_for(Home)

        assert backend.calls.count(FakeCallKind.OBSERVE) == observations_before


@pytest.mark.asyncio
async def test_mobile_runtime_rejects_generation_change_during_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        mobile = AsyncMobileApp(app)
        backend = factory.backends[0]
        backend.observation_sources.append(hierarchy(HOME))
        original_observe = backend.observe

        async def observe_then_replace(timeout: timedelta) -> HierarchySource:
            captured = await original_observe(timeout)
            device.close_application_handle(app.application_generation)
            return captured

        monkeypatch.setattr(backend, "observe", observe_then_replace)

        with pytest.raises(TargetClosedError):
            await mobile.wait_for(Home)


@pytest.mark.asyncio
async def test_action_and_destination_polling_exclude_an_unrelated_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]
        backend.observation_sources.append(hierarchy(LOGIN))
        mobile = AsyncMobileApp(app)
        login = await mobile.wait_for(Login)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocking_observe(timeout: timedelta) -> HierarchySource:
            del timeout
            backend.calls.append(FakeCallKind.OBSERVE)
            entered.set()
            await release.wait()
            return HierarchySource(content=hierarchy(HOME))

        monkeypatch.setattr(backend, "observe", blocking_observe)
        transition = asyncio.create_task(login.submit.tap_then(Home))
        await entered.wait()
        unrelated = asyncio.create_task(login.submit.tap())
        await asyncio.sleep(0.01)

        assert backend.calls.count(FakeCallKind.DISPATCH) == 1
        assert not unrelated.done()

        release.set()
        assert type(await transition) is Home
        await unrelated
        assert backend.calls.count(FakeCallKind.DISPATCH) == 2


@pytest.mark.asyncio
async def test_lifecycle_lock_wait_honors_action_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]
        backend.observation_sources.append(hierarchy(LOGIN))
        mobile = AsyncMobileApp(app)
        login = await mobile.wait_for(Login)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocking_observe(timeout: timedelta) -> HierarchySource:
            del timeout
            backend.calls.append(FakeCallKind.OBSERVE)
            entered.set()
            await release.wait()
            return HierarchySource(content=hierarchy(HOME))

        monkeypatch.setattr(backend, "observe", blocking_observe)
        transition = asyncio.create_task(login.submit.tap_then(Home))
        await entered.wait()
        try:
            with pytest.raises(LifecycleTimeoutError):
                await login.submit.tap(timeout=timedelta(milliseconds=10))
        finally:
            release.set()
        assert type(await transition) is Home


@pytest.mark.asyncio
async def test_snapshot_reads_consume_the_active_lifecycle_budget() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]
        backend.observation_sources.append(hierarchy(LOGIN))
        backend.default_result = QueryResult(elements=(element(editable=True),))
        mobile = AsyncMobileApp(app)
        login = await mobile.wait_for(Login)
        deadline = OperationDeadline.start(timedelta(milliseconds=10))

        async def read_snapshots(active_deadline: OperationDeadline) -> None:
            del active_deadline
            assert await login.username.text_content() == "Welcome"
            assert await login.username.accessible_name() == "Welcome"
            assert (await login.username.bounds()).width == 100

        await mobile.coordinator.run(
            deadline,
            name="test.snapshot",
            operation=read_snapshots,
        )

        assert backend.calls.count(FakeCallKind.RESOLVE) == 3
        assert len(backend.query_timeouts) == 3
        assert all(
            timedelta() < timeout <= timedelta(milliseconds=10)
            for timeout in backend.query_timeouts
        )


@pytest.mark.asyncio
async def test_mobile_wrappers_on_one_device_share_lifecycle_exclusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]
        backend.observation_sources.extend((hierarchy(LOGIN), hierarchy(LOGIN)))
        first_mobile = AsyncMobileApp(app)
        second_mobile = AsyncMobileApp(app)
        first_login = await first_mobile.wait_for(Login)
        second_login = await second_mobile.wait_for(Login)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocking_observe(timeout: timedelta) -> HierarchySource:
            del timeout
            backend.calls.append(FakeCallKind.OBSERVE)
            entered.set()
            await release.wait()
            return HierarchySource(content=hierarchy(HOME))

        monkeypatch.setattr(backend, "observe", blocking_observe)
        transition = asyncio.create_task(first_login.submit.tap_then(Home))
        await entered.wait()
        unrelated = asyncio.create_task(second_login.submit.tap())
        await asyncio.sleep(0.01)

        assert first_mobile.coordinator is second_mobile.coordinator
        assert backend.calls.count(FakeCallKind.DISPATCH) == 1
        assert not unrelated.done()

        release.set()
        assert type(await transition) is Home
        await unrelated
        assert backend.calls.count(FakeCallKind.DISPATCH) == 2


@pytest.mark.asyncio
async def test_mobile_wrappers_share_one_observation_sequence() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]
        backend.observation_sources.extend((hierarchy(LOGIN), hierarchy(LOGIN)))
        first_mobile = AsyncMobileApp(app)
        second_mobile = AsyncMobileApp(app)

        first_choice = await first_mobile.wait_for_any(one_of(Login, Home))
        second_choice = await second_mobile.wait_for_any(one_of(Login, Home))

        assert first_mobile.observation_engine is second_mobile.observation_engine
        assert (
            first_choice.observation_sequence,
            second_choice.observation_sequence,
        ) == (1, 2)


@pytest.mark.asyncio
async def test_async_mobile_runtime_rejects_sync_and_mixed_targets_before_io() -> None:
    factory = FakeBackendFactory()
    async with async_appwright(factory) as appwright:
        device = await appwright.android.connect(timeouts=mobile_timeouts())
        app = await device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]

        with pytest.raises(TypeError, match="AsyncInterruption"):
            AsyncMobileApp(
                app,
                interruptions=(SyncPasskey,),  # type: ignore[arg-type]
            )

        mobile = AsyncMobileApp(app)
        observations_before = backend.calls.count(FakeCallKind.OBSERVE)
        with pytest.raises(TypeError, match="AsyncScreen"):
            await mobile.wait_for(SyncLogin)  # type: ignore[type-var]
        mixed_target: Any = one_of(Home, SyncLogin)
        with pytest.raises(TypeError, match="AsyncScreen"):
            await mobile.wait_for_any(mixed_target)
        assert backend.calls.count(FakeCallKind.OBSERVE) == observations_before

        backend.observation_sources.append(hierarchy(LOGIN))
        login = await mobile.wait_for(Login)
        dispatches_before = backend.calls.count(FakeCallKind.DISPATCH)
        with pytest.raises(TypeError, match="AsyncScreen"):
            await login.submit.tap_then(SyncLogin)  # type: ignore[type-var]
        assert backend.calls.count(FakeCallKind.DISPATCH) == dispatches_before


@pytest.mark.asyncio
async def test_inherited_lifecycle_context_does_not_authorize_child_task_reentry() -> None:
    coordinator = AsyncLifecycleCoordinator()
    child_entered = asyncio.Event()
    child_task: asyncio.Task[None] | None = None

    async def child_body(deadline: OperationDeadline) -> None:
        del deadline
        child_entered.set()

    async def outer_body(deadline: OperationDeadline) -> None:
        nonlocal child_task
        del deadline

        async def run_child() -> None:
            child_deadline = coordinator.root_deadline(timedelta(milliseconds=100))
            await coordinator.run(
                child_deadline,
                name="test.child",
                operation=child_body,
            )

        child_task = asyncio.create_task(run_child())
        await asyncio.sleep(0.01)
        assert not child_entered.is_set()

    await coordinator.run(
        OperationDeadline.start(timedelta(milliseconds=100)),
        name="test.outer",
        operation=outer_body,
    )
    assert child_task is not None
    await child_task
    assert child_entered.is_set()


@pytest.mark.asyncio
async def test_lifecycle_timeout_retains_exclusion_until_resistant_work_stops() -> None:
    coordinator = AsyncLifecycleCoordinator()
    resistant_started = asyncio.Event()
    resistant_release = asyncio.Event()
    resistant_stopped = asyncio.Event()

    async def resistant_body(deadline: OperationDeadline) -> None:
        del deadline
        resistant_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await resistant_release.wait()
        resistant_stopped.set()

    async def immediate_body(deadline: OperationDeadline) -> None:
        del deadline

    try:
        with pytest.raises(LifecycleTimeoutError):
            await coordinator.run(
                OperationDeadline.start(timedelta(milliseconds=5)),
                name="test.resistant",
                operation=resistant_body,
            )
        assert resistant_started.is_set()
        assert not resistant_stopped.is_set()

        with pytest.raises(LifecycleTimeoutError):
            await coordinator.run(
                OperationDeadline.start(timedelta(milliseconds=5)),
                name="test.excluded",
                operation=immediate_body,
            )
    finally:
        resistant_release.set()

    await asyncio.wait_for(resistant_stopped.wait(), timeout=0.1)
    await coordinator.run(
        OperationDeadline.start(timedelta(milliseconds=100)),
        name="test.after_release",
        operation=immediate_body,
    )
