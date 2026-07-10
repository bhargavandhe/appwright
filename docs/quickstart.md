# Quickstart

This guide takes you from an empty Python project to a pytest test running against an installed
Android application. The local workflow lets Appwright start and stop Appium for you.

## 1. Check the prerequisites

You need:

- Python 3.11 or newer;
- Node.js and Appium 3 on `PATH`;
- Java and the Android SDK configured for UiAutomator2;
- UiAutomator2 7.x;
- an Android API 26+ emulator or device; and
- the application under test installed on that device.

Install Appium and the Android driver if needed:

```shell
npm install --global appium@3
appium driver install uiautomator2
```

Appwright does not download or update Node.js, Appium, Android SDK components, or Appium drivers.
That keeps local development and CI upgrades explicit.

## 2. Create the Python project

```shell
mkdir mobile-tests
cd mobile-tests
python -m venv .venv
source .venv/bin/activate
python -m pip install "appwright[pytest]"
```

On Windows PowerShell, activate the environment with `.venv\\Scripts\\Activate.ps1`.

If you are working from an Appwright source checkout instead of an installed release, use:

```shell
uv sync --all-extras
```

## 3. Validate the Android toolchain

Start or attach a device, unlock it, and accept its USB debugging prompt. Then run:

```shell
adb devices -l
appwright doctor
appwright devices
```

`adb devices -l` should report the target as `device`, not `offline` or `unauthorized`.
`appwright doctor` validates Appium 3, UiAutomator2 7.x, its required prerequisites, and ADB.

If exactly one online device is attached, Appwright can select it automatically. Configuring a
serial is safer on developer machines and required when several devices are online.

## 4. Configure the target

Add this to `pyproject.toml`, replacing the example values:

```toml
[tool.appwright]
serial = "emulator-5554"
app_package = "com.example.app"
clear_data = false
action_timeout_seconds = 30
expect_timeout_seconds = 5
trace_mode = "retain-on-failure"
screenshot_mode = "only-on-failure"
artifacts_path = ".appwright-artifacts"
```

Find a package name with a command such as:

```shell
adb shell pm list packages
```

You can use environment variables instead of project configuration:

```shell
export APPWRIGHT_SERIAL=emulator-5554
export APPWRIGHT_PACKAGE=com.example.app
```

Confirm the resolved configuration:

```shell
appwright inspect-config
```

## 5. Create the first test

Generate a starter file:

```shell
appwright init tests
```

Then adapt `tests/test_mobile.py` to your application:

```python
from appwright.models import Role
from appwright.sync_api import expect


def test_user_can_sign_in(mobile_app):
    email = mobile_app.get_by_label("Email")
    password = mobile_app.get_by_label("Password")

    email.fill("user@example.com")
    password.fill("correct horse battery staple")
    mobile_app.get_by_role(Role.BUTTON, name="Sign in").tap()

    expect(mobile_app.get_by_text("Welcome")).to_be_visible()
    expect(mobile_app.get_by_text("Signing in")).not_.to_be_visible()
```

The `mobile_app` fixture:

1. starts a managed local Appium server;
2. connects to the configured device;
3. launches the configured package;
4. yields an `App`; and
5. closes the session and server during teardown.

Locators are lazy. Creating `email` does not query the device; `fill()` resolves it, waits for one
visible, enabled, editable, stable match, and then performs the action.

## 6. Run the test

```shell
pytest -v
```

Configuration can also be supplied for a single run:

```shell
pytest -v \
  --appwright-serial emulator-5554 \
  --appwright-package com.example.app \
  --appwright-clear-data
```

If the test fails, look under `.appwright-artifacts/<test-node-id>/` for:

- `failure.png` — the device screenshot;
- `hierarchy.xml` — the current accessibility/UI hierarchy;
- `appium-server.jsonl` — structured managed-server output; and
- `trace.zip` — calls, retries, selector plans, snapshots, and sanitized capabilities.

Inspect the trace summary with:

```shell
appwright trace inspect .appwright-artifacts/path-to-test/trace.zip
```

## 7. Choose robust locators

Prefer selectors that describe user-facing behavior or stable application identifiers:

```python
mobile_app.get_by_test_id("sign-in")
mobile_app.get_by_resource_id("com.example.app:id/sign_in")
mobile_app.get_by_content_description("Sign in")
mobile_app.get_by_role(Role.BUTTON, name="Sign in")
mobile_app.get_by_text("Welcome")
```

On Android, `get_by_label()` means an exact accessibility content-description match. Text and role
names support exact strings and substring matching; Python regular expressions are deliberately
unsupported because Android XPath cannot preserve their semantics reliably.

Actions are strict: if a locator matches two elements, Appwright raises
`StrictModeViolationError` instead of choosing one silently. Narrow the locator with `filter()`,
`locator()`, or `nth()` only when position is truly part of the intended behavior.

## Next steps

- Learn locator composition and action semantics in [Selectors, actions, and assertions](selectors.md).
- Configure remote Appium or cloud capabilities in [Configuration](configuration.md).
- Customize pytest capture and retention in [Pytest integration](pytest.md).
- Diagnose setup failures in [Troubleshooting](troubleshooting.md).
