"""Single-threaded worker for a blocking Appium session."""

from __future__ import annotations

import asyncio
import builtins
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from datetime import timedelta
from typing import Any, TypeVar

from appwright.backends.base import BackendError, BackendFailure, BackendFailureKind
from appwright.models.enums import SessionState

Result = TypeVar("Result")
DEFAULT_CLEANUP_TIMEOUT = timedelta(seconds=10)


def configure_transport_timeout(driver: Any, timeout: timedelta) -> None:
    """Apply the current command deadline through Selenium's public client config."""
    command_executor = getattr(driver, "command_executor", None)
    client_config = getattr(command_executor, "client_config", None)
    if client_config is not None:
        client_config.timeout = max(timeout.total_seconds(), 0.001)


def quit_driver(driver: Any, timeout: timedelta = DEFAULT_CLEANUP_TIMEOUT) -> None:
    configure_transport_timeout(driver, timeout)
    driver.quit()


def retire_created_driver(creation_future: Future[Any]) -> None:
    with suppress(Exception):
        driver = creation_future.result()
        quit_driver(driver)


class SessionWorker:
    """Own one driver and serialize every operation onto one worker thread."""

    def __init__(self, driver: Any, executor: ThreadPoolExecutor) -> None:
        self.driver = driver
        self.executor = executor
        self.state = SessionState.ACTIVE
        self.command_lock = asyncio.Lock()
        self.cleanup_future: Future[None] | None = None

    @classmethod
    async def create(cls, factory: Callable[[], Any], timeout: timedelta) -> SessionWorker:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="appwright-appium")
        creation_future = executor.submit(factory)
        try:
            driver = await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(creation_future)),
                timeout=timeout.total_seconds(),
            )
        except BaseException:
            creation_future.add_done_callback(retire_created_driver)
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        return cls(driver=driver, executor=executor)

    def mark_tainted(self) -> None:
        if self.state is not SessionState.ACTIVE:
            return
        self.state = SessionState.TAINTED
        self.cleanup_future = self.executor.submit(quit_driver, self.driver)

    async def invoke(
        self,
        operation: Callable[[Any], Result],
        timeout: timedelta,
    ) -> Result:
        if self.state is not SessionState.ACTIVE:
            raise self.session_state_error()
        async with self.command_lock:
            if self.state is not SessionState.ACTIVE:
                raise self.session_state_error()

            def deadline_operation(driver: Any) -> Result:
                configure_transport_timeout(driver, timeout)
                return operation(driver)

            command_future = self.executor.submit(deadline_operation, self.driver)
            wrapped_future = asyncio.wrap_future(command_future)
            try:
                return await asyncio.wait_for(
                    asyncio.shield(wrapped_future),
                    timeout=timeout.total_seconds(),
                )
            except asyncio.CancelledError:
                self.mark_tainted()
                raise
            except builtins.TimeoutError:
                self.mark_tainted()
                raise BackendError(
                    BackendFailure(
                        kind=BackendFailureKind.TAINTED,
                        message="Appium command timed out and the session was retired",
                    )
                ) from None

    def session_state_error(self) -> BackendError:
        kind = (
            BackendFailureKind.TAINTED
            if self.state is SessionState.TAINTED
            else BackendFailureKind.NOT_STARTED
        )
        return BackendError(
            BackendFailure(kind=kind, message=f"Appium session is {self.state.value}")
        )

    async def close(self, timeout: timedelta = DEFAULT_CLEANUP_TIMEOUT) -> None:
        if self.state is SessionState.CLOSED:
            return
        if self.state is SessionState.ACTIVE:
            self.state = SessionState.CLOSING
            self.cleanup_future = self.executor.submit(quit_driver, self.driver, timeout)
        cleanup_future = self.cleanup_future
        if cleanup_future is not None:
            with suppress(Exception):
                await asyncio.wait_for(
                    asyncio.shield(asyncio.wrap_future(cleanup_future)),
                    timeout=timeout.total_seconds(),
                )
        self.state = SessionState.CLOSED
        self.executor.shutdown(wait=False, cancel_futures=True)
