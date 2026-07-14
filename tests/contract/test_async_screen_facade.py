"""Public async typed-screen facade contracts."""

from __future__ import annotations

import builtins
from datetime import timedelta

import pytest

from appwright.async_api import (
    Appwright,
    AsyncDeviceScreen,
    AsyncInterruption,
    AsyncScreen,
    InterruptionError,
    LifecycleTimeoutError,
    MobileApp,
    RecoveryError,
    ScreenTimeoutError,
    TransitionTimeoutError,
    button,
    by_id,
    text_field,
    visible,
)
from appwright.core.runtime import AsyncAppwright
from appwright.models import RetryPolicy, Timeouts
from appwright.models.data import QueryResult
from tests.fakes import FakeBackendFactory, FakeCallKind, element

APP_PACKAGE = "com.example"


def hierarchy(*resource_ids: str) -> str:
    nodes = "".join(
        (
            f'<node resource-id="{APP_PACKAGE}:id/{resource_id}" '
            f'package="{APP_PACKAGE}" class="android.widget.Button" '
            'clickable="true" enabled="true" bounds="[0,0][100,100]" '
            'displayed="true" />'
        )
        for resource_id in resource_ids
    )
    return f"<hierarchy>{nodes}</hierarchy>"


class Login(AsyncScreen):
    ready = visible(by_id("login_submit"))

    username = text_field(by_id("username"))
    submit = button(by_id("login_submit"))


class Home(AsyncScreen):
    ready = visible(by_id("home"))


class Permission(AsyncDeviceScreen):
    ready = visible(by_id("permission_allow"))


class Passkey(AsyncInterruption):
    ready = visible(by_id("maybe_later"))
    priority = 100

    later = button(by_id("maybe_later"))

    async def dismiss(self) -> None:
        await self.later.tap()


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
async def test_app_mobile_runs_a_public_typed_screen_journey() -> None:
    factory = FakeBackendFactory()
    appwright = Appwright(AsyncAppwright(factory))
    device = await appwright.android.connect(timeouts=mobile_timeouts())
    app = await device.launch_app(package=APP_PACKAGE)
    backend = factory.backends[0]
    backend.default_result = QueryResult(elements=(element(editable=True),))
    backend.observation_sources.extend(
        (
            hierarchy("login_submit"),
            hierarchy("maybe_later"),
            hierarchy("home"),
        )
    )

    mobile = app.mobile(interruptions=(Passkey,), max_dismissals=2)
    login = await mobile.wait_for(Login)
    await login.username.fill("test-user")
    home = await login.submit.tap_then(Home)

    assert type(mobile) is MobileApp
    assert type(login) is Login
    assert type(home) is Home
    assert backend.calls.count(FakeCallKind.DISPATCH) == 3
    await appwright.close()


def test_async_facade_exports_typed_mobile_types_and_errors() -> None:
    assert issubclass(Login, AsyncScreen)
    assert issubclass(Permission, AsyncDeviceScreen)
    assert issubclass(Passkey, AsyncInterruption)
    assert issubclass(ScreenTimeoutError, builtins.TimeoutError)
    assert issubclass(TransitionTimeoutError, builtins.TimeoutError)
    assert issubclass(LifecycleTimeoutError, builtins.TimeoutError)
    assert issubclass(InterruptionError, RuntimeError)
    assert issubclass(RecoveryError, RuntimeError)
