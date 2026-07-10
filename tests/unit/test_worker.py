"""Dedicated Appium worker tests."""

import asyncio
import threading
from datetime import timedelta

import pytest

from appwright.backends.appium.worker import SessionWorker
from appwright.backends.base import BackendError
from appwright.models.enums import SessionState


class FakeDriver:
    def __init__(self) -> None:
        self.quit_called = False

    def quit(self) -> None:
        self.quit_called = True


class FakeClientConfig:
    def __init__(self) -> None:
        self.timeout = 120.0


class FakeCommandExecutor:
    def __init__(self) -> None:
        self.client_config = FakeClientConfig()


class FakeTransportDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.command_executor = FakeCommandExecutor()


@pytest.mark.asyncio
async def test_worker_invokes_and_closes_driver() -> None:
    driver = FakeDriver()
    worker = await SessionWorker.create(lambda: driver, timedelta(seconds=1))
    result = await worker.invoke(
        lambda selected_driver: selected_driver is driver, timedelta(seconds=1)
    )
    assert result
    await worker.close()
    assert driver.quit_called
    assert worker.state is SessionState.CLOSED


@pytest.mark.asyncio
async def test_cancellation_taints_and_retires_session() -> None:
    driver = FakeDriver()
    worker = await SessionWorker.create(lambda: driver, timedelta(seconds=1))
    release = threading.Event()

    def blocking_operation(selected_driver: FakeDriver) -> None:
        release.wait(timeout=2)

    task = asyncio.create_task(worker.invoke(blocking_operation, timedelta(seconds=5)))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker.state is SessionState.TAINTED
    release.set()
    await worker.close()
    assert driver.quit_called


@pytest.mark.asyncio
async def test_timeout_taints_and_rejects_follow_up_commands() -> None:
    driver = FakeDriver()
    worker = await SessionWorker.create(lambda: driver, timedelta(seconds=1))
    release = threading.Event()

    def blocking_operation(selected_driver: FakeDriver) -> None:
        release.wait(timeout=2)

    with pytest.raises(BackendError):
        await worker.invoke(blocking_operation, timedelta(milliseconds=1))
    assert worker.state is SessionState.TAINTED
    with pytest.raises(BackendError):
        await worker.invoke(lambda selected_driver: None, timedelta(seconds=1))
    release.set()
    await worker.close()
    assert driver.quit_called


@pytest.mark.asyncio
async def test_creation_timeout_retires_driver_created_after_timeout() -> None:
    driver = FakeDriver()
    release = threading.Event()

    def delayed_factory() -> FakeDriver:
        release.wait(timeout=2)
        return driver

    with pytest.raises(TimeoutError):
        await SessionWorker.create(delayed_factory, timedelta(milliseconds=1))
    release.set()
    attempt = 0
    while not driver.quit_called and attempt < 100:
        await asyncio.sleep(0.001)
        attempt += 1
    assert driver.quit_called


@pytest.mark.asyncio
async def test_concurrent_callers_execute_in_submission_order() -> None:
    driver = FakeDriver()
    worker = await SessionWorker.create(lambda: driver, timedelta(seconds=1))
    order: list[int] = []

    def operation(value: int) -> int:
        order.append(value)
        return value

    tasks = tuple(
        asyncio.create_task(
            worker.invoke(
                lambda selected_driver, value=value: operation(value),
                timedelta(seconds=1),
            )
        )
        for value in range(5)
    )
    results = await asyncio.gather(*tasks)
    assert results == [0, 1, 2, 3, 4]
    assert order == [0, 1, 2, 3, 4]
    await worker.close()


@pytest.mark.asyncio
async def test_command_deadline_is_applied_to_selenium_transport() -> None:
    driver = FakeTransportDriver()
    worker = await SessionWorker.create(lambda: driver, timedelta(seconds=1))
    deadline = timedelta(milliseconds=125)
    observed = await worker.invoke(
        lambda selected_driver: selected_driver.command_executor.client_config.timeout,
        deadline,
    )
    assert observed == pytest.approx(0.125)
    await worker.close()


@pytest.mark.asyncio
async def test_close_returns_when_driver_quit_is_blocked() -> None:
    release = threading.Event()

    class BlockingQuitDriver(FakeDriver):
        def quit(self) -> None:
            release.wait(timeout=2)
            super().quit()

    driver = BlockingQuitDriver()
    worker = await SessionWorker.create(lambda: driver, timedelta(seconds=1))
    await asyncio.wait_for(
        worker.close(timeout=timedelta(milliseconds=5)),
        timeout=0.1,
    )
    assert worker.state is SessionState.CLOSED
    release.set()
