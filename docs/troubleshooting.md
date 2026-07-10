# Troubleshooting

Start with the automated checks:

```shell
appwright doctor
appwright devices
appwright inspect-config
```

Use `appwright doctor --json` when CI or provisioning scripts need structured results.

## Appium was not found on `PATH`

Verify the same shell can run:

```shell
appium --version
```

Appwright requires Appium 3. Install it through your normal Node.js toolchain, for example:

```shell
npm install --global appium@3
```

If Appium is installed in a nonstandard location, direct sessions can specify
`AppiumServer.local(executable=Path(...))`.

## UiAutomator2 is missing or incompatible

List and diagnose installed drivers:

```shell
appium driver list --installed
appium driver install uiautomator2
appium driver doctor uiautomator2
```

The current compatibility manifest expects UiAutomator2 7.x.

## Device is `unauthorized`

Unlock the physical device, accept the USB debugging prompt, and rerun:

```shell
adb devices -l
```

If the prompt does not appear, revoke USB debugging authorizations in Android developer settings,
reconnect, and restart ADB according to your workstation policy.

## Device is `offline`

An offline device cannot start a reliable session. Restart the emulator or reconnect the device,
then wait for:

```shell
adb get-state
```

to report `device`.

## More than one device is connected

Select one explicitly:

```toml
[tool.appwright]
serial = "emulator-5554"
```

or:

```shell
pytest --appwright-serial emulator-5554
```

Appwright refuses ambiguous automatic selection.

## `mobile_app requires --appwright-package`

Configure a package through one of the supported sources:

```toml
[tool.appwright]
app_package = "com.example.app"
```

```shell
export APPWRIGHT_PACKAGE=com.example.app
```

```shell
pytest --appwright-package com.example.app
```

Check the final value with `appwright inspect-config`.

## Strict mode reports multiple matches

The locator is ambiguous. Prefer a stronger semantic or ID selector, then scope or filter it:

```python
from appwright.models import Role
from appwright.selectors import Selector, TextMatcher


row = app.get_by_role(Role.LIST_ITEM).filter(has_text="Ada Lovelace")
delete = row.locator(Selector.text(TextMatcher(value="Delete")))
delete.tap()
```

Use `nth()` only if element order is the intended contract. Avoid hiding ambiguity with a
positional selector when stable identity is available.

## An action times out although the element exists

Presence alone is insufficient. Depending on the action, Appwright may also require the target to
be visible, enabled, editable/checkable, and stable.

1. Inspect the exception's call log and received state.
2. Open `failure.png` and `hierarchy.xml`.
3. Inspect `trace.zip` for snapshot changes or repeated stale elements.
4. Increase the timeout only when the application legitimately needs more time.
5. Use `trial=True` to test actionability without input.

`force=True` is a last resort for intentionally nonstandard UI. It does not bypass existence or
strictness.

## The session is tainted or closed after cancellation

Appium's Python client performs blocking HTTP requests. Python task cancellation cannot guarantee
that an in-flight device command did not execute. Appwright marks that session tainted, rejects
new work, and schedules teardown after the request returns.

Create a new device/application session. Do not catch `SessionTaintedError` and continue using the
old session.

## No Appium logs appear for a remote server

Appwright captures process logs for servers it manages locally. Standard remote endpoints do not
necessarily expose server logs. Retrieve the session/job log from the grid or cloud provider.

## Diagnostic capture failed

The original pytest failure remains authoritative. Look for the `Appwright diagnostics` report
section, which lists screenshot, hierarchy, or log capture failures separately.

## Report a reproducible bug

Include:

- Appwright version from `python -c "import appwright; print(appwright.version())"`;
- `appwright doctor --json` output;
- resolved configuration with secrets removed;
- Android API level and application technology (Views, Compose, React Native, and so on);
- the smallest failing test;
- the rendered Appwright exception; and
- a sanitized trace and Appium log when safe to share.
