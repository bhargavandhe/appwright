"""Public sync/async facade parity and encapsulation tests."""

import inspect

import pytest

from appwright.api.generated import async_api, sync_api
from appwright.api.specification import SPECIFICATION
from appwright.core.runtime import AsyncAppwright
from appwright.errors import AppwrightError, InvalidSelectorError, TimeoutError
from tests.fakes import FakeBackendFactory


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


def test_public_error_module_exports_base_and_timeout() -> None:
    assert issubclass(TimeoutError, AppwrightError)
    assert issubclass(InvalidSelectorError, AppwrightError)
