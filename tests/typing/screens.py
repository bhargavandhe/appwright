"""Static typing fixture for typed screens and scope-safe controls."""

from typing import assert_type

from appwright.screens import (
    AppScope,
    Button,
    Choice,
    DeviceScope,
    DeviceScreen,
    Interruption,
    Screen,
    ScreenChoice,
    ScreenTarget,
    TextField,
    button,
    by_accessibility_id,
    by_id,
    choice,
    one_of,
    text_field,
    visible,
)
from appwright.screens.elements import AsyncButton, AsyncChoice, AsyncTextField
from appwright.screens.model import (
    AsyncDeviceScreen,
    AsyncInterruption,
    AsyncScreen,
)


class Login(Screen):
    ready = visible(by_id("login_submit"))

    user_id = text_field(by_accessibility_id("User I.D"))
    environment = choice(by_id("environment"))
    submit = button(by_id("login_submit"))


class Permission(DeviceScreen):
    ready = visible(by_id("permission_deny_button"))

    deny = button(by_id("permission_deny_button"))


class PermissionInterruption(DeviceScreen, Interruption[DeviceScope]):
    ready = visible(by_id("permission_allow_button"))
    priority = 200

    allow = button(by_id("permission_allow_button"))

    def dismiss(self) -> None:
        self.allow.tap()


class AsyncLogin(AsyncScreen):
    ready = visible(by_id("login_submit"))

    user_id = text_field(by_accessibility_id("User I.D"))
    environment = choice(by_id("environment"))
    submit = button(by_id("login_submit"))


class AsyncPermission(AsyncDeviceScreen):
    ready = visible(by_id("permission_deny_button"))

    deny = button(by_id("permission_deny_button"))


class AsyncPermissionInterruption(
    AsyncDeviceScreen,
    AsyncInterruption[DeviceScope],
):
    ready = visible(by_id("permission_allow_button"))
    priority = 200

    allow = button(by_id("permission_allow_button"))

    async def dismiss(self) -> None:
        await self.allow.tap()


def typed_controls(
    login: Login,
    permission: Permission,
    permission_interruption: PermissionInterruption,
) -> None:
    assert_type(login.user_id, TextField[AppScope])
    assert_type(login.environment, Choice[AppScope])
    assert_type(login.submit, Button[AppScope])
    assert_type(permission.deny, Button[DeviceScope])
    assert_type(permission_interruption.allow, Button[DeviceScope])
    assert_type(one_of(Login, Permission), ScreenTarget[Login | Permission])

    login.user_id.fill("user")
    login.environment.select()
    login.submit.tap()
    permission.deny.tap()
    login.submit.and_(login.user_id)
    assert_type(login.environment.select_then(Login), Login)
    assert_type(
        login.submit.tap_then(one_of(Login, Permission)),
        ScreenChoice[Login | Permission],
    )

    login.submit.fill("invalid")  # type: ignore[attr-defined]
    login.user_id.check()  # type: ignore[attr-defined]
    login.submit.and_(permission.deny)  # type: ignore[arg-type]


async def typed_async_controls(
    login: AsyncLogin,
    permission: AsyncPermission,
    permission_interruption: AsyncPermissionInterruption,
) -> None:
    assert_type(login.user_id, AsyncTextField[AppScope])
    assert_type(login.environment, AsyncChoice[AppScope])
    assert_type(login.submit, AsyncButton[AppScope])
    assert_type(permission.deny, AsyncButton[DeviceScope])
    assert_type(permission_interruption.allow, AsyncButton[DeviceScope])
    assert_type(
        one_of(AsyncLogin, AsyncPermission),
        ScreenTarget[AsyncLogin | AsyncPermission],
    )

    await login.user_id.fill("user")
    await login.environment.select()
    await login.submit.tap()
    await permission.deny.tap()
    login.submit.and_(login.user_id)
    assert_type(await login.environment.select_then(AsyncLogin), AsyncLogin)
    assert_type(
        await login.submit.tap_then(one_of(AsyncLogin, AsyncPermission)),
        ScreenChoice[AsyncLogin | AsyncPermission],
    )

    login.submit.fill("invalid")  # type: ignore[attr-defined]
    login.user_id.check()  # type: ignore[attr-defined]
    login.submit.and_(permission.deny)  # type: ignore[arg-type]
