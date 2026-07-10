"""Static typing fixture for the public API."""

from appwright.async_api import async_appwright
from appwright.models import (
    AdditionalCapability,
    AndroidConnectionOptions,
    AppiumServer,
    ApplicationOptions,
    Role,
)
from appwright.sync_api import expect, sync_appwright


def typed_example() -> None:
    with sync_appwright() as appwright:
        device = appwright.android.connect(
            serial="emulator-5554",
            server=AppiumServer.local(),
        )
        app = device.launch_app(package="com.example")
        locator = app.get_by_role(Role.BUTTON, name="Continue")
        locator.tap()
        expect(locator).to_be_visible()
        expect(locator).not_.to_be_hidden()


async def typed_async_example() -> None:
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
            await locators[0].tap(trial=True)
