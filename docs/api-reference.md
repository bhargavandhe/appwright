# API guide

This is a compact guide to the supported public object model. It is not generated class-level
documentation; signatures in the checked-in sync and async facades remain the source of truth.

## Imports

```python
from appwright.sync_api import expect, sync_appwright
from appwright.async_api import async_appwright
from appwright.models import AppiumServer, Direction, Key, Role, WaitState
from appwright.selectors import Selector, TextMatcher
from appwright.errors import AppwrightError
```

Avoid importing from `appwright.core` or `appwright.backends`.

## Object graph

```text
Appwright
└── android: Android
    ├── devices() -> tuple[DeviceInfo, ...]
    └── connect(...) -> Device
        ├── screen: Screen
        ├── keyboard: Keyboard
        ├── touchscreen: Touchscreen
        ├── tracing: TraceRecorder
        └── launch_app(...) -> App
            ├── locator factories -> Locator
            └── mobile(...) -> MobileApp
                ├── wait_for(Screen)
                ├── wait_for_any(one_of(...))
                ├── settle(Screen)
                └── ensure(Screen, recovery=back_until(...))
```

The async graph is identical. Methods that perform I/O return awaitables.

## `Appwright` and `Android`

```python
with sync_appwright() as appwright:
    available = appwright.android.devices()
    device = appwright.android.connect(serial="emulator-5554")
```

- `Android.devices()` discovers ADB devices.
- `Android.connect()` accepts either `AndroidConnectionOptions` or convenience keywords.
- `Android.connect()` creates the device-level Appium session; typed capabilities are connection
  options.
- `Android.close()` closes devices created by that Android facade.
- `Appwright.close()` closes the whole runtime; context management is preferred.

## `Device`

Lifecycle and diagnostics:

```python
app = device.launch_app(package="com.example.app")
device.install_app(path)
device.uninstall_app("com.example.app")
device.screenshot(path)
device.hierarchy()
device.server_logs()
device.close()
```

Input and cross-application UI:

```python
device.keyboard.press(Key.BACK)
device.touchscreen.tap(point)
device.screen.get_by_text("Allow").tap()
```

Use `device.screen` for system dialogs and UI outside the application package. Locators created
from an `App` are package-scoped and become closed when a later `launch_app()` replaces the active
application handle. The Appium session itself remains alive until `Device.close()`.

## `App` and `Screen`

Both are locator roots and provide:

- `locator(Selector)`;
- `get_by_text()`;
- `get_by_label()`;
- `get_by_placeholder()`;
- `get_by_test_id()`;
- `get_by_resource_id()`;
- `get_by_content_description()`; and
- `get_by_role()`.

Text and role-name factories accept strings with exact or contains matching; Python regular
expressions are not supported. On Android, `get_by_label()` is an exact content-description match,
and `get_by_placeholder()` matches the accessibility hint when one is exposed. See
[Selectors, actions, and assertions](selectors.md#android-accessibility-semantics) for role support
and composition semantics.

`App` additionally exposes:

- `package_name`;
- `activate()`;
- `terminate()`;
- `clear_data()`;
- `reset()`;
- `screenshot()`; and
- `close()`.

## Typed screens and mobile lifecycle

The synchronous facade exports `Screen`, `DeviceScreen`, `Interruption`, typed control builders
(`element`, `button`, `text_field`, `checkbox`, `choice`, and `scrollable`), selector helpers, and
the readiness combinators `visible`, `all_of`, and `any_of`. The asynchronous equivalents are
`AsyncScreen`, `AsyncDeviceScreen`, and `AsyncInterruption`.

Call `App.mobile(interruptions=(...))` to create the device-session lifecycle facade. It provides:

- `wait_for(ScreenType)` for one bounded transition;
- `wait_for_any(one_of(A, B, ...))` for an atomic screen race;
- `settle(screen, stable_for=...)` for a stable destination;
- `ensure(ScreenType, recovery=back_until(...))` for bounded Back recovery; and
- `cancelled_transition_receipt` for diagnosing cancellation after action dispatch.

Controls expose only valid capabilities: buttons tap, text fields fill, choices select, and
scrollables move the viewport. `tap_then()` and `select_then()` combine dispatch with destination
waiting and preserve an `ActionReceipt` when dispatch becomes indeterminate. Registered
interruptions are prioritized, dismissal-count bounded, and share the parent transition deadline.

Use `DeviceScreen` for Android permission dialogs or other device-owned surfaces. Ordinary
`Screen` selectors remain scoped to the active app package.

## `Locator`

Composition:

- `locator()`;
- `filter()`;
- `and_()` and `or_()`;
- `first` and `last` properties; and
- `nth()`.

Actions:

- `tap()`, `double_tap()`, and `long_press()`;
- `fill()`, `clear()`, and `press()`;
- `check()` and `uncheck()`;
- `swipe()`, `scroll()`, and `drag_to()`;
- `screenshot()`; and
- `wait_for()`.

Multi-element operations:

- `count()`;
- `all()`; and
- `element_infos()`.

Single-element queries:

- `is_visible()`, `is_enabled()`, and `is_checked()`;
- `text_content()` and `accessible_name()`;
- `bounds()`; and
- `element_info()`.

Every action accepts an optional `timeout`. Input actions also accept `force` and `trial`.
`swipe()` and `scroll()` accept a `Direction` and a percentage between zero and one.

## Assertions

Create assertions with `expect(locator)`. Available expectations are:

- visible or hidden;
- enabled or disabled;
- editable;
- checked or unchecked;
- focused;
- selected;
- exact or contained text;
- accessible name;
- resource ID; and
- count.

Use `.not_` to negate an expectation.

## Sync and async mapping

Synchronous:

```python
with sync_appwright() as appwright:
    device = appwright.android.connect()
    app = device.launch_app(package="com.example.app")
    app.get_by_text("Continue").tap()
    expect(app.get_by_text("Home")).to_be_visible()
```

Asynchronous:

```python
from appwright.async_api import async_appwright, expect as async_expect


async def run_test() -> None:
    async with async_appwright() as appwright:
        device = await appwright.android.connect()
        app = await device.launch_app(package="com.example.app")
        await app.get_by_text("Continue").tap()
        await async_expect(app.get_by_text("Home")).to_be_visible()
```

Do not mix sync locators with async assertions or vice versa.

## Return models

Queries and diagnostics return immutable Pydantic models such as `DeviceInfo`, `ElementSnapshot`,
`Rect`, `Point`, and `Screenshot`. Use normal attribute access or Pydantic serialization:

```python
snapshot = locator.element_info()
print(snapshot.bounds)
print(snapshot.model_dump_json(indent=2))
```

No documented public method accepts or returns an untyped dictionary.
