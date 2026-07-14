"""Public synchronous typed-screen facade contracts."""

from datetime import timedelta

from appwright.models import RetryPolicy, Timeouts
from appwright.sync_api import (
    Interruption,
    MobileApp,
    Screen,
    button,
    by_id,
    sync_appwright,
    text_field,
    visible,
)
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


class Login(Screen):
    ready = visible(by_id("login_submit"))

    username = text_field(by_id("username"))
    submit = button(by_id("login_submit"))


class Home(Screen):
    ready = visible(by_id("home"))


class Passkey(Interruption):
    ready = visible(by_id("maybe_later"))
    priority = 100

    later = button(by_id("maybe_later"))

    def dismiss(self) -> None:
        self.later.tap()


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


def test_sync_app_mobile_runs_typed_journey_and_worker_thread_interruption() -> None:
    factory = FakeBackendFactory()
    with sync_appwright(factory) as appwright:
        device = appwright.android.connect(timeouts=mobile_timeouts())
        app = device.launch_app(package=APP_PACKAGE)
        backend = factory.backends[0]
        backend.default_result = backend.default_result.model_copy(
            update={"elements": (element(editable=True),)}
        )
        backend.observation_sources.extend(
            (
                hierarchy("login_submit"),
                hierarchy("maybe_later"),
                hierarchy("home"),
            )
        )

        mobile = app.mobile(interruptions=(Passkey,), max_dismissals=2)
        login = mobile.wait_for(Login)
        login.username.fill("test-user")
        home = login.submit.tap_then(Home)

        assert type(mobile) is MobileApp
        assert type(login) is Login
        assert type(home) is Home
        assert backend.calls.count(FakeCallKind.DISPATCH) == 3
