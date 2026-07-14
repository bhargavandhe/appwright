# Appwright

Appwright brings Playwright-style locators, auto-waiting, strictness, assertions, and diagnostics
to Android automation. Appium 3 and UiAutomator2 drive the device; Appwright provides the typed,
Python-native testing experience on top.

> **Project status:** alpha. Appwright currently targets Python 3.11+, Android API 26+, Appium 3,
> and UiAutomator2 7.x. Pin versions and validate them against your application before production
> adoption.

## Why Appwright?

- Lazy locators that re-resolve before every action
- Automatic waiting for visibility, enabled state, editability, and stable bounds
- Strict single-element actions that fail clearly on ambiguous matches
- Matching synchronous and asynchronous APIs
- Typed screen models, capability-safe controls, and bounded transition waits
- Prioritized interstitial handling and bounded Back recovery for real mobile journeys
- Pydantic models and enums instead of capability dictionaries and magic strings
- Failure screenshots, hierarchy dumps, Appium logs, and portable trace archives
- Managed local Appium servers or existing remote/grid/cloud endpoints

Appwright deliberately does not expose WebDriver objects, raw XPath, or untyped capability
dictionaries through its public API.

## Five-minute quickstart

Install Appwright and its pytest plugin:

```shell
python -m pip install "appwright[pytest]"
```

Appium is installed separately. If it is not already available:

```shell
npm install --global appium@3
appium driver install uiautomator2
appwright doctor
```

Connect an authorized emulator or Android device and confirm that ADB can see it:

```shell
adb devices -l
appwright devices
```

Add the target application and device to `pyproject.toml`:

```toml
[tool.appwright]
serial = "emulator-5554"
app_package = "com.example.app"
trace_mode = "retain-on-failure"
```

Create `tests/test_sign_in.py`:

```python
from appwright.models import Role
from appwright.sync_api import expect


def test_sign_in(mobile_app):
    mobile_app.get_by_label("Email").fill("user@example.com")
    mobile_app.get_by_role(Role.BUTTON, name="Sign in").tap()

    expect(mobile_app.get_by_text("Welcome")).to_be_visible()
```

Run it:

```shell
pytest -v
```

The pytest plugin manages the Appium session. When a test fails, diagnostics are stored beneath
`.appwright-artifacts/` by default.

See the [complete quickstart](docs/quickstart.md) for Android SDK setup, emulator/device checks,
environment-variable configuration, and common first-run errors.

## Typed mobile journeys

Use screen definitions for multi-screen flows. A transition observes the UI atomically, handles
registered interstitials inside the same deadline, and returns the destination with typed controls.

```python
from appwright.sync_api import (
    Interruption, Screen, button, by_id, text_field, visible,
)


class PasskeyPrompt(Interruption):
    priority = 100
    ready = visible(by_id("com.example:id/maybe_later"))
    later = button(by_id("com.example:id/maybe_later"))

    def dismiss(self) -> None:
        self.later.tap()


class Login(Screen):
    ready = visible(by_id("com.example:id/sign_in"))
    email = text_field(by_id("com.example:id/email"))
    submit = button(by_id("com.example:id/sign_in"))


class Home(Screen):
    ready = visible(by_id("com.example:id/home"))


mobile = app.mobile(interruptions=(PasskeyPrompt,))
login = mobile.wait_for(Login)
login.email.fill("user@example.com")
home = login.submit.tap_then(Home)
mobile.settle(home)
```

The async facade uses `AsyncScreen` and `AsyncInterruption`, with the same model and awaited
control operations. Low-level `Locator` APIs remain available as an escape hatch for exploratory
work and isolated actions.

## Direct synchronous API

Use a context manager whenever you create sessions directly. It closes the Appium session,
worker thread, and managed local server even when a test fails.

```python
from appwright.models import AppiumServer, Role
from appwright.sync_api import expect, sync_appwright


with sync_appwright() as appwright:
    device = appwright.android.connect(
        serial="emulator-5554",
        server=AppiumServer.local(),
    )
    app = device.launch_app(package="com.example.app", clear_data=True)

    app.get_by_label("Email").fill("user@example.com")
    app.get_by_role(Role.BUTTON, name="Sign in").tap()
    expect(app.get_by_text("Welcome")).to_be_visible()
    expect(app.get_by_text("Loading")).not_.to_be_visible()
```

## Direct asynchronous API

The async API has the same names and signatures; operations that communicate with the device are
awaited.

```python
from appwright.async_api import async_appwright, expect
from appwright.models import Role


async def sign_in() -> None:
    async with async_appwright() as appwright:
        device = await appwright.android.connect(serial="emulator-5554")
        app = await device.launch_app(package="com.example.app")

        await app.get_by_label("Email").fill("user@example.com")
        await app.get_by_role(Role.BUTTON, name="Sign in").tap()
        await expect(app.get_by_text("Welcome")).to_be_visible()
```

## Documentation

- [Documentation index](docs/index.md) — choose a guide by task
- [Quickstart](docs/quickstart.md) — go from an empty project to a passing Android test
- [Installation and first session](docs/getting-started.md) — local, remote, and direct API setup
- [Selectors, actions, and assertions](docs/selectors.md) — locator semantics and auto-waiting
- [Pytest integration](docs/pytest.md) — fixtures, options, configuration, and artifacts
- [Configuration](docs/configuration.md) — precedence, environment variables, timeouts, and clouds
- [Diagnostics and traces](docs/diagnostics.md) — failure output and trace inspection
- [API guide](docs/api-reference.md) — supported object model and method overview
- [Troubleshooting](docs/troubleshooting.md) — common setup and runtime failures
- [Architecture](docs/architecture.md) — runtime layering and design constraints

## Development

```shell
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run mypy src
uv run pyright
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for generated API and policy checks.

Questions and early proposals belong in
[GitHub Discussions](https://github.com/bhargavandhe/appwright/discussions). Use
[GitHub Issues](https://github.com/bhargavandhe/appwright/issues) for reproducible bugs and
[SECURITY.md](SECURITY.md) for private vulnerability reporting.

Appwright is licensed under Apache-2.0.
