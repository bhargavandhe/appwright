"""Managed Appium service validation tests."""

import asyncio
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from appwright.backends.appium.models import CommandOutput
from appwright.backends.appium.service import (
    ManagedAppiumService,
    inspect_local_appium,
    uiautomator2_version,
)


def test_uiautomator2_version_ignores_terminal_coloring() -> None:
    listing = "\x1b[33muiautomator2\x1b[39m@\x1b[33m7.0.0\x1b[39m [installed]"
    assert uiautomator2_version(listing) == "7.0.0"


def test_uiautomator2_version_is_absent_for_other_drivers() -> None:
    assert uiautomator2_version("xcuitest@10.0.0 [installed]") is None


def test_local_installation_rejects_incompatible_appium(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "appium"
    executable.touch()

    def incompatible_version(
        selected: Path,
        arguments: tuple[str, ...],
        timeout: timedelta,
    ) -> CommandOutput:
        return CommandOutput(
            exit_code=0,
            standard_output="2.19.0",
            standard_error="",
        )

    monkeypatch.setattr(
        "appwright.backends.appium.service.execute_command",
        incompatible_version,
    )
    with pytest.raises(RuntimeError, match="requires Appium 3"):
        inspect_local_appium(executable)


def test_server_log_snapshot_reads_running_output() -> None:
    service = ManagedAppiumService(
        host="127.0.0.1",
        port=4723,
        executable=None,
        timeout=timedelta(seconds=1),
    )
    service.prepare_log_files()
    output = service.standard_output_handle
    assert output is not None
    output.write(b"server ready\n")
    output.flush()
    assert any(record.message == "server ready" for record in service.snapshot_logs())
    service.collect_logs()
    service.executor.shutdown(wait=True, cancel_futures=True)


def test_inspection_commands_receive_bounded_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "appium"
    executable.touch()
    observed_timeouts: list[timedelta] = []

    def successful_command(
        selected: Path,
        arguments: tuple[str, ...],
        timeout: timedelta,
    ) -> CommandOutput:
        observed_timeouts.append(timeout)
        output = "3.2.0" if arguments == ("--version",) else "uiautomator2@7.0.0"
        return CommandOutput(exit_code=0, standard_output=output, standard_error="")

    monkeypatch.setattr(
        "appwright.backends.appium.service.execute_command",
        successful_command,
    )
    expected = timedelta(milliseconds=250)
    inspect_local_appium(executable, expected)
    assert observed_timeouts == [expected, expected]


@pytest.mark.asyncio
async def test_service_started_after_timeout_is_retired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeService:
        def __init__(self) -> None:
            self.running = False
            self.stop_called = False

        @property
        def is_running(self) -> bool:
            return self.running

        def stop(self) -> None:
            self.stop_called = True
            self.running = False

    release = threading.Event()
    service = ManagedAppiumService(
        host="127.0.0.1",
        port=4723,
        executable=None,
        timeout=timedelta(milliseconds=1),
    )
    fake_service = FakeService()
    service.service = fake_service  # type: ignore[assignment]

    def delayed_start() -> str:
        release.wait(timeout=2)
        fake_service.running = True
        return "http://127.0.0.1:4723"

    monkeypatch.setattr(service, "start_blocking", delayed_start)
    with pytest.raises(TimeoutError):
        await service.start()
    release.set()
    attempt = 0
    while not fake_service.stop_called and attempt < 100:
        await asyncio.sleep(0.001)
        attempt += 1
    assert fake_service.stop_called
