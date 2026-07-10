"""Generated sync facade contract tests."""

from datetime import timedelta

from appwright.models.config import AppiumTimeouts
from appwright.models.data import QueryResult
from appwright.sync_api import expect, sync_appwright
from tests.fakes import FakeBackendFactory, FakeCallKind, element


def test_sync_api_drives_async_core() -> None:
    factory = FakeBackendFactory()
    timeouts = AppiumTimeouts(stability=timedelta(milliseconds=1))
    with sync_appwright(factory) as appwright:
        device = appwright.android.connect(serial="device-1", timeouts=timeouts)
        app = device.launch_app(package="com.example")
        backend = factory.backends[0]
        backend.default_result = QueryResult(elements=(element(editable=True, text="Email"),))
        locator = app.get_by_label("Email")
        locator.fill("user@example.com")
        expect(locator).to_be_visible()
    assert FakeCallKind.PERFORM in factory.backends[0].calls
