"""Static inference fixture for the synchronous typed-screen facade."""

from typing import assert_type

from appwright.sync_api import (
    App,
    AppScope,
    Button,
    MobileApp,
    Screen,
    ScreenChoice,
    ScreenTarget,
    TextField,
    button,
    by_id,
    one_of,
    text_field,
    visible,
)


class Login(Screen):
    ready = visible(by_id("login_submit"))
    username = text_field(by_id("username"))
    submit = button(by_id("login_submit"))


class Home(Screen):
    ready = visible(by_id("home"))


class LoginError(Screen):
    ready = visible(by_id("login_error"))


def typed_sync_mobile(app: App) -> None:
    mobile = app.mobile()
    assert_type(mobile, MobileApp)
    login = mobile.wait_for(Login)
    assert_type(login, Login)
    assert_type(login.username, TextField[AppScope])
    assert_type(login.submit, Button[AppScope])
    target = one_of(Home, LoginError)
    assert_type(target, ScreenTarget[Home | LoginError])
    choice = login.submit.tap_then(target)
    assert_type(choice, ScreenChoice[Home | LoginError])
