# Appwright documentation

Appwright is an Android-first automation framework with Playwright-style locators and assertions,
powered by Appium 3 and UiAutomator2.

## Start here

- [Quickstart](quickstart.md) — create and run a pytest test in five minutes
- [Installation and first session](getting-started.md) — managed local and remote sessions
- [Troubleshooting](troubleshooting.md) — resolve common Android and Appium setup failures

## Write tests

- [Selectors, actions, and assertions](selectors.md)
- [Pytest integration](pytest.md)
- [Configuration](configuration.md)
- [API guide](api-reference.md)

## Diagnose and understand

- [Diagnostics and traces](diagnostics.md)
- [Architecture](architecture.md)
- [Architecture decision records](adr/0001-appium-first-backend.md)

## Current scope

The current release is alpha and Android-native first. Appium's UiAutomator2 driver is the only
implemented backend. Explicit WebView context APIs, iOS/XCUITest, visual baseline assertions,
recording/code generation, and a graphical trace viewer are deferred.
