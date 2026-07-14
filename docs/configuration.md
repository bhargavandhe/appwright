# Configuration

Appwright resolves test configuration in this order, from highest to lowest precedence:

1. pytest command-line options;
2. `APPWRIGHT_*` environment variables;
3. `[tool.appwright]` in `pyproject.toml`; and
4. validated defaults.

Run `appwright inspect-config` from the project root to see the resolved configuration and tested
compatibility ranges as one JSON document.

## Project configuration

```toml
[tool.appwright]
serial = "emulator-5554"
server_url = "https://appium.example.test/wd/hub"
app_package = "com.example.app"
clear_data = false
action_timeout_seconds = 30
expect_timeout_seconds = 5
stability_window_milliseconds = 200
trace_mode = "retain-on-failure"
screenshot_mode = "only-on-failure"
artifacts_path = ".appwright-artifacts"
```

Omit `server_url` to let Appwright manage a local Appium process. Project configuration is used
by the pytest fixtures and CLI configuration inspector. Direct API calls use the models passed to
them.

## Environment variables

| Variable | Example | Meaning |
| --- | --- | --- |
| `APPWRIGHT_SERIAL` | `emulator-5554` | ADB device serial |
| `APPWRIGHT_SERVER_URL` | `https://grid.example/wd/hub` | Existing remote Appium endpoint |
| `APPWRIGHT_PACKAGE` | `com.example.app` | Package launched by `mobile_app` |
| `APPWRIGHT_CLEAR_DATA` | `true` | Clear application data before launch |
| `APPWRIGHT_ACTION_TIMEOUT_SECONDS` | `30` | Default action deadline |
| `APPWRIGHT_EXPECT_TIMEOUT_SECONDS` | `5` | Default assertion deadline |
| `APPWRIGHT_STABILITY_WINDOW_MILLISECONDS` | `200` | Required stable UI interval |
| `APPWRIGHT_TRACE` | `retain-on-failure` | `off`, `always`, or `retain-on-failure` |
| `APPWRIGHT_SCREENSHOT` | `only-on-failure` | `off` or `only-on-failure` |
| `APPWRIGHT_ARTIFACTS` | `.appwright-artifacts` | Pytest artifact root |

Boolean environment values accept `1`, `true`, `yes`, `on`, `0`, `false`, `no`, and `off`.

## Pytest options

```shell
pytest \
  --appwright-serial emulator-5554 \
  --appwright-server-url https://grid.example/wd/hub \
  --appwright-package com.example.app \
  --appwright-clear-data \
  --appwright-trace retain-on-failure \
  --appwright-screenshot only-on-failure \
  --appwright-artifacts .appwright-artifacts
```

See [Pytest integration](pytest.md) for fixture behavior and retention details.

## Direct API timeouts

Pass a typed timeout model when opening a connection:

```python
from datetime import timedelta

from appwright.models import RetryPolicy, Timeouts


timeouts = Timeouts(
    probe=timedelta(seconds=2),
    wait=timedelta(seconds=30),
    action=timedelta(seconds=20),
    transition=timedelta(seconds=90),
    interruption=timedelta(seconds=30),
    stability=timedelta(milliseconds=250),
    transport=timedelta(seconds=90),
    server_start=timedelta(seconds=45),
    retry=RetryPolicy(
        initial_delay=timedelta(milliseconds=25),
        multiplier=2,
        maximum_delay=timedelta(milliseconds=500),
    ),
)

device = appwright.android.connect(
    serial="emulator-5554",
    timeouts=timeouts,
)
```

An operation's remaining Appwright deadline is translated into its transport timeout. If a
blocking Appium request is cancelled or times out with an uncertain outcome, Appwright taints the
session and closes it when the request unwinds instead of reusing potentially inconsistent state.
`probe`, `wait`, `action`, `transition`, and `interruption` are separate budgets; nested work always
uses the smaller of its own budget and the remaining parent deadline.

## Remote authentication

```python
from pydantic import SecretStr

from appwright.models import AppiumSecurityOptions, AppiumServer


server = AppiumServer.remote(
    url="https://cloud.example/wd/hub",
    security=AppiumSecurityOptions(
        username="project-user",
        access_key=SecretStr("secret-access-key"),
        verify_tls=True,
    ),
)
```

Keep credentials in environment-backed secret management rather than committing them to source.
Appwright stores access keys in Pydantic `SecretStr` values and sanitizes them from its logs,
exceptions, and traces.

## Typed vendor capabilities

Vendor options use `AdditionalCapability` rather than dictionaries:

```python
from appwright.models import AdditionalCapability, CapabilityValue


video = AdditionalCapability.boolean("video", True)
build_name = AdditionalCapability.string("build", "pull-request-42")
token = AdditionalCapability.string(
    "token",
    "secret-value",
    sensitive=True,
)

tags = AdditionalCapability.array(
    "tags",
    items=(
        CapabilityValue.string("android"),
        CapabilityValue.string("smoke"),
    ),
)

provider_options = AdditionalCapability.object(
    "vendor:options",
    entries=(video, build_name, token, tags),
)

device = appwright.android.connect(
    capabilities=(provider_options,),
)
app = device.launch_app(package="com.example.app")
```

Use `sensitive=True` when a provider's capability name does not obviously contain `token`,
`password`, `secret`, or `access-key`. Nested capabilities are redacted recursively.

Top-level additional capabilities must use a W3C vendor namespace, such as `vendor:options`.
Nested keys follow the provider's schema. Appwright rejects duplicate top-level names and
framework-owned capabilities such as the device serial, automation engine, and application.

`CapabilityValue` is itself a strict model. Its factories cover strings, integers, numbers,
booleans, nulls, arrays, and nested objects without requiring discriminator strings.

## Local server selection

Customize a managed local server without untyped process arguments:

```python
from pathlib import Path

from appwright.models import AppiumServer


server = AppiumServer.local(
    host="127.0.0.1",
    port=4725,
    executable=Path("/opt/homebrew/bin/appium"),
)
```

Leaving `port` unset lets Appwright choose an available ephemeral port, which is usually best for
parallel CI workers.
