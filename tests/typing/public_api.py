"""Static typing fixture for the public API."""

from typing import assert_type

from appwright.async_api import async_appwright
from appwright.errors import IndeterminateActionError
from appwright.models import (
    ActionReceipt,
    AdditionalCapability,
    AndroidConnectionOptions,
    AppiumServer,
    ApplicationOptions,
    ElementSnapshot,
    Role,
    Timeouts,
)
from appwright.sync_api import expect, sync_appwright


def typed_indeterminate_action(error: IndeterminateActionError) -> None:
    assert_type(error.receipt, ActionReceipt)


def typed_example() -> None:
    with sync_appwright() as appwright:
        device = appwright.android.connect(
            serial="emulator-5554",
            server=AppiumServer.local(),
        )
        app = device.launch_app(package="com.example")
        locator = app.get_by_role(Role.BUTTON, name="Continue")
        assert_type(locator.probe(), ElementSnapshot | None)
        assert_type(locator.probe_all(), tuple[ElementSnapshot, ...])
        locator.tap(auto_scroll=True)
        locator.scroll_into_view()
        expect(locator).to_be_visible()
        expect(locator).not_.to_be_hidden()


async def typed_async_example() -> None:
    assert Timeouts().probe.total_seconds() == 2
    capability = AdditionalCapability.boolean("vendor:video", True)
    options = AndroidConnectionOptions(
        server=AppiumServer.local(),
        capabilities=(capability,),
    )
    application = ApplicationOptions(package="com.example")
    async with async_appwright() as appwright:
        device = await appwright.android.connect(options)
        app = await device.launch_app(application)
        locators = await app.get_by_text("Welcome").all()
        if locators:
            assert_type(await locators[0].probe(), ElementSnapshot | None)
            assert_type(await locators[0].probe_all(), tuple[ElementSnapshot, ...])
            await locators[0].tap(trial=True, auto_scroll=True)
            await locators[0].scroll_into_view()
