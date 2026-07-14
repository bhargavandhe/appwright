"""Public sync/async facade parity and encapsulation tests."""

import builtins
import inspect

import pytest

import appwright.models as public_models
from appwright.api.generated import async_api, sync_api
from appwright.api.specification import SPECIFICATION
from appwright.core.runtime import AsyncAppwright
from appwright.errors import (
    AppwrightError,
    InterruptionError,
    InvalidSelectorError,
    RecoveryError,
    ScreenTimeoutError,
    TimeoutError,
    TransitionTimeoutError,
)
from appwright.models import Timeouts
from tests.fakes import FakeBackendFactory, FakeCallKind


def class_for_name(module: object, name: str) -> type[object]:
    value = getattr(module, name)
    if not isinstance(value, type):
        raise TypeError(f"{name} is not a class")
    return value


def test_generated_sync_and_async_signatures_match() -> None:
    for api_class in SPECIFICATION.classes:
        async_class = class_for_name(async_api, api_class.name)
        sync_class = class_for_name(sync_api, api_class.name)
        for method in api_class.methods:
            assert inspect.signature(getattr(async_class, method)) == inspect.signature(
                getattr(sync_class, method)
            )


def test_async_facade_does_not_expose_runtime_operations() -> None:
    forbidden = {
        "attach_artifact",
        "check_stability",
        "perform",
        "plan",
        "query",
        "query_once",
        "strict_element",
        "translate_backend_error",
    }
    assert forbidden.isdisjoint(dir(async_api.Locator))
    assert "attach_artifact" not in dir(async_api.Device)


@pytest.mark.asyncio
async def test_async_public_facade_wraps_backend_neutral_runtime() -> None:
    factory = FakeBackendFactory()
    appwright = async_api.Appwright(AsyncAppwright(factory))
    device = await appwright.android.connect()
    app = await device.launch_app(package="com.example")
    locator = app.get_by_text("Welcome")
    await locator.tap()
    await async_api.expect(locator).to_be_visible()
    await appwright.close()


@pytest.mark.asyncio
async def test_async_locator_facade_exposes_hardened_mobile_operations() -> None:
    factory = FakeBackendFactory()
    appwright = async_api.Appwright(AsyncAppwright(factory))
    device = await appwright.android.connect()
    app = await device.launch_app(package="com.example")
    locator = app.get_by_text("Welcome")

    assert await locator.probe() is not None
    assert len(await locator.probe_all()) == 1
    await locator.tap(auto_scroll=True)
    scroll_result = await locator.scroll_into_view()

    assert scroll_result.succeeded
    assert FakeCallKind.SCROLL_INTO_VIEW in factory.backends[0].calls
    await appwright.close()


def test_sync_locator_facade_exposes_hardened_mobile_operations() -> None:
    factory = FakeBackendFactory()
    with sync_api.sync_appwright(factory) as appwright:
        device = appwright.android.connect()
        app = device.launch_app(package="com.example")
        locator = app.get_by_text("Welcome")

        assert locator.probe() is not None
        assert len(locator.probe_all()) == 1
        locator.tap(auto_scroll=True)
        assert locator.scroll_into_view().succeeded

    assert FakeCallKind.SCROLL_INTO_VIEW in factory.backends[0].calls


def test_public_error_module_exports_base_and_timeout() -> None:
    assert issubclass(TimeoutError, AppwrightError)
    assert issubclass(InvalidSelectorError, AppwrightError)
    assert issubclass(ScreenTimeoutError, builtins.TimeoutError)
    assert issubclass(TransitionTimeoutError, builtins.TimeoutError)
    assert issubclass(InterruptionError, RuntimeError)
    assert issubclass(RecoveryError, RuntimeError)


def test_public_timeout_model_uses_mobile_first_name() -> None:
    assert Timeouts().transition.total_seconds() == 90
    assert not hasattr(public_models, "AppiumTimeouts")
