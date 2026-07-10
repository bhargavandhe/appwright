# Installation and first session

Use the [quickstart](quickstart.md) if you want the shortest path to a pytest test. This guide
explains installation choices and direct session management.

## Supported versions

Appwright's checked-in compatibility manifest currently targets:

| Component | Supported range |
| --- | --- |
| Python | 3.11+ |
| Android | API 26+ |
| Appium server | 3.x |
| Appium Python client | 5.x |
| Selenium | 4.26+ and below 5 |
| UiAutomator2 driver | 7.x |

Appium client and Selenium releases can affect one another. Use the project's locked versions for
development and test both your minimum and current combinations before releasing infrastructure
changes.

## Install Appwright

For ordinary tests:

```shell
python -m pip install appwright
```

For pytest fixtures and failure artifacts:

```shell
python -m pip install "appwright[pytest]"
```

For contributors working from this repository:

```shell
uv sync --all-extras
uv run appwright doctor
```

Appwright is intentionally not responsible for installing Appium's external toolchain. Install
Node.js, Appium 3, Java, the Android SDK, and UiAutomator2 through your normal workstation or CI
provisioning.

## Managed local Appium

`AppiumServer.local()` is the default. Appwright:

- locates the existing `appium` executable;
- verifies the supported major version and driver;
- starts Appium on an available port;
- waits for readiness;
- captures its output as structured log records; and
- stops it on context teardown.

```python
from appwright.models import AppiumServer
from appwright.sync_api import sync_appwright


with sync_appwright() as appwright:
    device = appwright.android.connect(
        serial="emulator-5554",
        server=AppiumServer.local(),
    )
    app = device.launch_app(package="com.example.app")
    app.get_by_text("Continue").tap()
```

Use context managers. `Android.connect()` creates the Appium session, so `device.screen`,
`device.keyboard`, screenshots, hierarchy inspection, installation, and other device operations
work before an application is launched. `Device.close()` owns and closes that session.

`launch_app()` installs an APK when `app_path` is provided, optionally clears application data,
and activates the package within the existing device session. It never replaces the Appium
driver. Launching another package invalidates earlier `App` objects and their locators; use
`device.screen` when you intentionally need a locator whose lifetime spans application launches.

## Remote Appium

Point Appwright at an Appium server, grid, or cloud endpoint with a typed server model:

```python
from pydantic import SecretStr

from appwright.models import AppiumSecurityOptions, AppiumServer
from appwright.sync_api import sync_appwright


server = AppiumServer.remote(
    url="https://appium.example.test/wd/hub",
    security=AppiumSecurityOptions(
        username="automation-user",
        access_key=SecretStr("secret-value"),
        verify_tls=True,
    ),
)

with sync_appwright() as appwright:
    device = appwright.android.connect(server=server)
    app = device.launch_app(package="com.example.app")
```

Appwright never starts or stops a remote server. Credentials use Pydantic secret values and are
redacted from Appwright diagnostics. Direct-connect redirects advertised by remote servers are
disabled: Appwright keeps commands on the authenticated endpoint instead of forwarding traffic or
credentials to a server-selected host.

## Launch by package or APK

Use convenience arguments for simple sessions:

```python
app = device.launch_app(
    package="com.example.app",
    clear_data=True,
)
```

Use `ApplicationOptions` when configuration is assembled or shared:

```python
from pathlib import Path

from appwright.models import ApplicationOptions


application = ApplicationOptions(
    app_path=Path("build/app-debug.apk"),
    package="com.example.app",
    clear_data=True,
)
app = device.launch_app(application)
```

Do not combine an `ApplicationOptions` object with the convenience keyword arguments; choose one
form so there is a single source of truth.

## Typed connection options

The same rule applies to connections:

```python
from appwright.models import (
    AndroidConnectionOptions,
    AndroidDeviceSelector,
    AppiumServer,
)


connection = AndroidConnectionOptions(
    selector=AndroidDeviceSelector(serial="emulator-5554"),
    server=AppiumServer.local(),
)
device = appwright.android.connect(connection)
```

Capabilities belong to the device session and must therefore be supplied to `connect()`, not
`launch_app()`. For a one-off session, `connect(serial="emulator-5554")` is equivalent and more
concise.

## Session lifecycle

Application lifecycle methods are explicit:

```python
app.activate()
app.terminate()
app.activate()
app.clear_data()
app.reset()
app.close()
```

At the device level you can install and remove applications:

```python
from pathlib import Path


device.install_app(Path("build/app-debug.apk"), grant_permissions=True)
device.uninstall_app("com.example.app", keep_data=False)
```

Normally you should rely on the enclosing Appwright context rather than calling every `close()`
manually.

## Supported imports

Application code should import documented APIs from:

- `appwright.sync_api`;
- `appwright.async_api`;
- `appwright.models`;
- `appwright.selectors`; and
- `appwright.errors`.

Core and backend modules are implementation details even though the project's no-private-state
rule avoids underscore-prefixed names.
