# ADR 0002: Canonical asynchronous runtime

Status: accepted

The domain runtime is asynchronous. Each blocking Appium session owns one single-worker executor,
which serializes every driver command. Cancellation or a transport timeout retires the session
because an in-flight HTTP operation cannot be interrupted safely.

The synchronous API is a generated-style facade over the same runtime and uses a scoped Greenlet
dispatcher. There is no independent synchronous implementation whose behavior can drift.
