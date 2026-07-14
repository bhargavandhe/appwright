"""Static inference contracts for the public async typed-screen facade."""

from typing import assert_type

from appwright.async_api import (
    App,
    AppScope,
    AsyncButton,
    AsyncDeviceScreen,
    AsyncInterruption,
    AsyncScreen,
    AsyncTextField,
    DeviceScope,
    MobileApp,
    ScreenChoice,
    ScreenTarget,
    back_until,
    button,
    by_id,
    one_of,
    text_field,
    visible,
)


class Login(AsyncScreen):
    ready = visible(by_id("login_submit"))

    username = text_field(by_id("username"))
    submit = button(by_id("login_submit"))


class Home(AsyncScreen):
    ready = visible(by_id("home"))


class Permission(AsyncDeviceScreen):
    ready = visible(by_id("permission_allow"))

    allow = button(by_id("permission_allow"))


class Passkey(AsyncInterruption):
    ready = visible(by_id("maybe_later"))
    priority = 100

    later = button(by_id("maybe_later"))

    async def dismiss(self) -> None:
        await self.later.tap()


async def typed_public_journey(app: App) -> None:
    mobile = app.mobile(interruptions=(Passkey,), max_dismissals=4)
    assert_type(mobile, MobileApp)

    login = await mobile.wait_for(Login)
    assert_type(login, Login)
    assert_type(login.username, AsyncTextField[AppScope])
    assert_type(login.submit, AsyncButton[AppScope])

    target = one_of(Home, Permission)
    assert_type(target, ScreenTarget[Home | Permission])
    observed = await mobile.wait_for_any(target)
    assert_type(observed, ScreenChoice[Home | Permission])
    choice = await login.submit.tap_then(target)
    assert_type(choice, ScreenChoice[Home | Permission])
    if isinstance(choice.screen, Permission):
        assert_type(choice.screen.allow, AsyncButton[DeviceScope])

    settled = await mobile.settle(Home)
    assert_type(settled, Home)
    assert_type(await mobile.settle(settled), Home)

    recovered = await mobile.ensure(
        Home,
        recovery=back_until(Home, max_steps=2),
    )
    assert_type(recovered, Home)
