# Diagnostics and traces

Appwright treats diagnostics as part of the API rather than an afterthought. Errors carry typed
details, managed Appium logs are retained as records, and pytest can capture a portable bundle for
each failure.

## Read an Appwright error

An action or assertion failure renders the most useful structured fields:

```text
Timed out waiting for locator to become visible
API: locator.tap
Locator: get_by_text("Continue")
Strategy: xpath
Expected: visible, enabled, stable
Received: visible=false
Elapsed: 30.001s
Call log:
  - 0.012s: resolved 0 matches
  - 0.053s: resolved 1 match; visible=false
```

Catch the documented exception types and inspect their Pydantic `details` model when programmatic
handling is useful:

```python
from appwright.errors import TimeoutError


try:
    app.get_by_text("Continue").tap()
except TimeoutError as error:
    print(error.details.api_name)
    print(error.details.call_log)
    raise
```

The public hierarchy includes:

- `AppwrightError`;
- `TimeoutError`;
- `ExpectationError`;
- `StrictModeViolationError`;
- `InvalidSelectorError`;
- `DeviceNotFoundError` and `DeviceDisconnectedError`;
- `AppiumUnavailableError` and `AppiumCompatibilityError`;
- `SessionTaintedError` and `TargetClosedError`;
- `ProtocolError`; and
- `UnsupportedOperationError`.

Typed-screen operations add structured lifecycle evidence:

- `ScreenTimeoutError` retains candidate readiness summaries from bounded whole-device
  observations;
- `TransitionTimeoutError` retains the exact action receipt and destination history;
- `InterruptionError` retains bounded dismissal history plus the total event count;
- `RecoveryError` retains bounded BACK attempts and observation history; and
- `IndeterminateActionError` retains an `ActionReceipt` when Appwright cannot prove whether a
  non-replayable command reached the device.

```python
from appwright.errors import IndeterminateActionError, TransitionTimeoutError


try:
    home = login.submit.tap_then(Home)
except TransitionTimeoutError as error:
    print(error.receipt.dispatch_state)
    print(error.transition_history.observations)
    raise
except IndeterminateActionError as error:
    print(error.receipt.replay_safety)
    raise
```

Appwright retries resolution and observation before dispatch. Once a tap, key press, or gesture
may have been submitted, it never guesses by replaying it.

## Pytest artifacts

With the default policy, a failing test using `android_device` or `mobile_app` receives:

- `failure.png`;
- `hierarchy.xml`;
- `appium-server.jsonl`; and
- `trace.zip`.

Artifacts live under `.appwright-artifacts/<sanitized-test-node-id>/`. Change the root with
`--appwright-artifacts`, `APPWRIGHT_ARTIFACTS`, or `artifacts_path` in `[tool.appwright]`.

Control retention with:

```shell
pytest --appwright-trace off
pytest --appwright-trace always
pytest --appwright-trace retain-on-failure
pytest --appwright-screenshot off
pytest --appwright-screenshot only-on-failure
```

See [Pytest integration](pytest.md) for fixture lifecycle details.

## Trace contents

A trace is a versioned ZIP archive containing JSON Lines records and binary/text artifacts. It can
include:

- API calls and elapsed time;
- selector ASTs and compiled plans;
- retry and actionability attempts;
- element snapshots;
- screenshots and hierarchy sources;
- sanitized Appium commands and responses;
- redacted session capabilities;
- device and application metadata;
- managed Appium server logs; and
- errors and teardown outcomes.

Secrets are redacted by capability-name heuristics and explicit `sensitive=True` markers. Do not
treat redaction as permission to place arbitrary credentials in element text, package data, or
screenshots; artifact storage should still be access-controlled.

## Inspect a trace

Print a summary:

```shell
appwright trace inspect .appwright-artifacts/test-name/trace.zip
```

Produce machine-readable output:

```shell
appwright trace inspect trace.zip --json
```

The current CLI reports manifest metadata, event count, and artifact names. A graphical trace
viewer is deferred.

## Capture diagnostics directly

Outside pytest, device APIs expose typed records:

```python
from pathlib import Path


screenshot = device.screenshot(Path("artifacts/current.png"))
hierarchy = device.hierarchy()
server_logs = device.server_logs()

Path("artifacts/hierarchy.xml").write_text(hierarchy.content, encoding="utf-8")
```

For explicit tracing:

```python
from pathlib import Path


device.tracing.register_secret("runtime-secret")
device.tracing.register_pattern(r"session-token=[^\s]+")
device.tracing.start(Path("artifacts/trace.zip"))
try:
    app.get_by_text("Continue").tap()
finally:
    device.tracing.stop()
```

The Appwright context still owns device and server teardown.

Trace events and artifacts stream to restrictive temporary storage instead of accumulating in
memory. `TraceLimits` can bound event count, artifact count, individual artifact size, and total
trace bytes; the versioned manifest records dropped records and whether truncation occurred.
Structured element text and accessible names are removed from query/action events. Registered
secrets and patterns are also sanitized from textual trace artifacts and rendered errors.

## Managed versus remote logs

`device.server_logs()` returns logs captured by a managed local Appium service. A remote provider
may not make its server process logs available through the standard endpoint. Preserve provider
job links and logs separately when diagnosing cloud sessions.
