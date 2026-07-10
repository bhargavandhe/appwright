# Pytest integration

Install the optional pytest dependency to register Appwright's plugin:

```shell
python -m pip install "appwright[pytest]"
```

The plugin uses ordinary pytest tests and fixtures. Appwright does not provide a custom test
runner.

## Fixtures

### `appwright`

Creates the synchronous Appwright runtime and closes it after the test.

```python
def test_connected_devices(appwright):
    devices = appwright.android.devices()
    assert devices
```

### `appwright_config`

Returns the validated `AppwrightConfiguration` after applying CLI, environment, project, and
default precedence.

```python
def test_configuration(appwright_config):
    assert appwright_config.timeouts.action.total_seconds() > 0
```

### `android_device`

Connects to the configured local or remote Appium server and yields a `Device`. It starts tracing
according to the retention policy and owns device/session teardown.

```python
def test_device_is_reachable(android_device):
    assert android_device.hierarchy().content
```

### `mobile_app`

Launches `app_package` and yields an `App`. This fixture requires `app_package` in
`[tool.appwright]`, `APPWRIGHT_PACKAGE`, or `--appwright-package`.

```python
from appwright.sync_api import expect


def test_home_screen(mobile_app):
    expect(mobile_app.get_by_text("Home")).to_be_visible()
```

## Configure a run

Project-wide defaults are usually the most convenient:

```toml
[tool.appwright]
serial = "emulator-5554"
app_package = "com.example.app"
trace_mode = "retain-on-failure"
screenshot_mode = "only-on-failure"
artifacts_path = ".appwright-artifacts"
```

Override them from CI or a one-off command:

```shell
pytest tests/mobile -v \
  --appwright-serial "$ANDROID_SERIAL" \
  --appwright-package com.example.app \
  --appwright-clear-data
```

## Failure diagnostics

On setup or test-call failure, the plugin attempts to capture diagnostics without replacing the
original test failure:

```text
.appwright-artifacts/
└── tests-test-sign-in.py-test-sign-in/
    ├── failure.png
    ├── hierarchy.xml
    ├── appium-server.jsonl
    └── trace.zip
```

If diagnostic capture itself fails, pytest adds the error to an `Appwright diagnostics` report
section and preserves the original failure.

Retention modes:

| Setting | Values | Default |
| --- | --- | --- |
| Trace | `off`, `always`, `retain-on-failure` | `retain-on-failure` |
| Screenshot | `off`, `only-on-failure` | `only-on-failure` |

Examples:

```shell
pytest --appwright-trace always
pytest --appwright-trace off --appwright-screenshot off
pytest --appwright-artifacts build/mobile-artifacts
```

## Parallel execution

Each `android_device` fixture owns a session and managed local server. Give each parallel pytest
worker a distinct device serial; two sessions should not attempt to control the same device.

Appwright currently leaves device allocation and worker-to-device mapping to your CI setup. A
simple approach is one pytest process per serial:

```shell
APPWRIGHT_SERIAL=emulator-5554 pytest tests/mobile
```

## Custom fixtures

Build application-specific fixtures on top of `mobile_app`:

```python
import pytest


@pytest.fixture
def signed_in_app(mobile_app):
    mobile_app.get_by_label("Email").fill("user@example.com")
    mobile_app.get_by_label("Password").fill("password")
    mobile_app.get_by_text("Sign in").tap()
    return mobile_app
```

Avoid session-scoped wrapping fixtures unless their lifecycle is deliberately coordinated with
the function-scoped Appwright fixtures.

## CI checklist

- Pin Appwright, Appium, UiAutomator2, and Android image versions.
- Run `appwright doctor` during image validation, not before every test.
- Boot and unlock the emulator before pytest starts.
- Wait until `adb get-state` reports `device`.
- Upload `.appwright-artifacts/` even when pytest fails.
- Allocate one device per concurrently running Appwright session.
- Preserve Appium server logs from remote providers when managed-server logs are unavailable.
