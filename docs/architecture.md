# Architecture

Appwright is a Playwright-style semantic layer over Appium 3. It owns the test-facing behavior;
Appium owns device transport and platform automation.

```text
Generated sync API ─┐
                    ├── Canonical async runtime
Generated async API ┘            │
                    Mobile lifecycle / screen kernel
                                  │
                         Backend-neutral protocol
                                  │
                            Appium adapter
                                  │
                     Appium Python client worker
                                  │
                    Appium 3 + UiAutomator2 + Android
```

## Responsibility boundary

Appwright owns:

- typed public APIs and configuration;
- immutable selector descriptions and compilation;
- lazy strict locator resolution;
- actionability, retries, and assertion polling;
- deadline and cancellation semantics;
- atomic hierarchy observations, typed screens, transitions, and interruptions;
- error rewriting, tracing, and pytest artifacts; and
- managed local server lifecycle.

Appium and UiAutomator2 own:

- Android session creation and WebDriver transport;
- device and application commands;
- element lookup and native gestures;
- APK installation and application activation; and
- interoperability with existing Appium grids and clouds.

## Canonical async runtime

There is one domain implementation: the asynchronous runtime. The async facade delegates to it
directly. The synchronous facade drives the same coroutines through a scoped Greenlet dispatcher.
The facades are generated from checked-in templates, and CI verifies exact method-signature parity.

The official Appium Python client is blocking. Every Appium session therefore owns a dedicated
`ThreadPoolExecutor` with one worker. This provides two important guarantees:

1. driver commands for one session execute serially; and
2. blocking calls never consume asyncio's global worker pool.

Different sessions can progress independently.

## Mobile lifecycle kernel

The typed-screen layer follows three rules:

1. one root deadline owns an operation and every nested action, observation, interruption, and
   recovery step;
2. one immutable hierarchy observation is evaluated locally against every competing screen in a
   poll; and
3. action dispatch and destination waiting are separate phases joined by an `ActionReceipt`.

Screen readiness does not issue one Appium request per locator. The observation engine captures
the whole device once, assigns a monotonic sequence, and evaluates app-scoped and device-scoped
conditions from that same state. A final live resolution is still required immediately before an
element action.

Typed lifecycle operations are serialized per device session. Explicit structural work, such as
an interruption dismissal during a transition, may re-enter through a scoped lifecycle lease;
unrelated child tasks and separate app wrappers cannot bypass serialization.

## Cancellation and uncertain commands

Cancelling an asyncio task stops waiting for a blocking Appium request but cannot interrupt the
HTTP operation already executing in its worker. The device may have received the command even
when the caller no longer sees its result.

Appwright responds conservatively:

1. mark the session `TAINTED`;
2. reject subsequent commands;
3. wait for the in-flight worker call to unwind;
4. schedule `driver.quit()`; and
5. record the uncertain command and teardown result.

Deterministic retirement is safer than silently reusing a session with unknown state.
The same principle applies above transport: a non-replayable action with an unknown dispatch
state is surfaced with its receipt and is never automatically repeated.

## Typed boundaries

All framework-owned records are strict, frozen Pydantic models. Enums represent framework states,
actions, command names, strategies, and policies. Typed mappings may exist as registries, but not
as substitutes for structured records.

Appium and Selenium require JSON-compatible mappings at their boundary. Only the Appium adapter
serializes validated models into those mappings, passes them immediately, and validates responses
back into models. Selenium `WebElement` instances never leave the adapter.

## Selector pipeline

Public factories and `Selector` models build an immutable discriminated AST. A locator operation:

1. compiles that AST into a `LocatorPlan`;
2. chooses resource ID, accessibility ID, or class name when semantics permit;
3. otherwise generates internal XPath through one audited literal encoder;
4. resolves fresh matches through the backend;
5. enforces strictness and actionability; and
6. performs the requested action or query.

Raw XPath is not accepted. App-scoped plans additionally filter native strategy results by package
so system or other-application elements do not leak into an application locator.

## Public API boundary

Supported imports are documented exports from `appwright.sync_api`, `appwright.async_api`,
`appwright.models`, `appwright.selectors`, and `appwright.errors`. Backend and core packages are
implementation details.

The project prohibits underscore-prefixed authored state variables. API stability is therefore
defined by documented exports and generated surface checks rather than Python naming conventions.

## Design decisions

- [ADR 0001: Appium-first execution backend](adr/0001-appium-first-backend.md)
- [ADR 0002: Canonical asynchronous runtime](adr/0002-canonical-async-runtime.md)
- [ADR 0003: Typed records and descriptive state names](adr/0003-typed-boundaries-and-state-names.md)
