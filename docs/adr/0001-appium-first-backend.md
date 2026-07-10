# ADR 0001: Appium-first execution backend

Status: accepted

Appwright delegates device transport, UiAutomator2 lifecycle, WebDriver interaction, and remote
grid compatibility to Appium 3. Appwright owns the user-facing locator, waiting, assertion, error,
and tracing semantics.

Only `appwright.backends.appium` may import Appium or Selenium. The public API and the
backend-neutral protocol exchange Pydantic models, never driver or element objects. This keeps
the semantic layer replaceable while using the most mature mobile automation ecosystem for v1.
