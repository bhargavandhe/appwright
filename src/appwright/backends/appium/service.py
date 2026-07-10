"""Managed local Appium server lifecycle."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from datetime import timedelta
from io import BufferedWriter
from pathlib import Path

from appium.webdriver.appium_service import AppiumService

from appwright.backends.appium.models import CommandOutput, LocalAppiumInstallation
from appwright.models.data import ServerLogRecord
from appwright.models.enums import LogStream

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
DEFAULT_INSPECTION_TIMEOUT = timedelta(seconds=10)
DEFAULT_STOP_TIMEOUT = timedelta(seconds=10)


def uiautomator2_version(driver_listing: str) -> str | None:
    clean_listing = ANSI_ESCAPE.sub("", driver_listing)
    match = re.search(r"uiautomator2@(\d+(?:\.\d+){0,2})", clean_listing, re.IGNORECASE)
    return None if match is None else match.group(1)


def execute_command(
    executable: Path,
    arguments: tuple[str, ...],
    timeout: timedelta = DEFAULT_INSPECTION_TIMEOUT,
) -> CommandOutput:
    try:
        completed = subprocess.run(
            (str(executable), *arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout.total_seconds(),
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"Appium inspection command timed out after {timeout.total_seconds():g}s"
        ) from error
    return CommandOutput(
        exit_code=completed.returncode,
        standard_output=completed.stdout.strip(),
        standard_error=completed.stderr.strip(),
    )


def inspect_local_appium(
    executable: Path | None,
    timeout: timedelta = DEFAULT_INSPECTION_TIMEOUT,
) -> LocalAppiumInstallation:
    discovered = str(executable) if executable is not None else shutil.which("appium")
    if discovered is None:
        raise RuntimeError("Appium was not found on PATH")
    selected_executable = Path(discovered)
    version_output = execute_command(selected_executable, ("--version",), timeout)
    version = version_output.standard_output or version_output.standard_error
    if version_output.exit_code != 0 or not version.startswith("3."):
        raise RuntimeError(f"Appwright requires Appium 3; detected {version or 'unknown'}")
    drivers_output = execute_command(
        selected_executable,
        ("driver", "list", "--installed"),
        timeout,
    )
    driver_listing = drivers_output.standard_output or drivers_output.standard_error
    driver_version = uiautomator2_version(driver_listing)
    if drivers_output.exit_code != 0 or driver_version is None:
        raise RuntimeError("UiAutomator2 is not installed; run: appium driver install uiautomator2")
    if not driver_version.startswith("7."):
        raise RuntimeError(f"Appwright requires UiAutomator2 7.x; detected {driver_version}")
    return LocalAppiumInstallation(
        executable=selected_executable,
        version=version,
        driver_version=driver_version,
        driver_listing=driver_listing,
    )


def available_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind((host, 0))
        return int(candidate.getsockname()[1])


class ManagedAppiumService:
    def __init__(
        self,
        *,
        host: str,
        port: int | None,
        executable: Path | None,
        timeout: timedelta,
    ) -> None:
        self.host = host
        self.port = port if port is not None else available_port(host)
        self.executable = executable
        self.timeout = timeout
        self.service = AppiumService()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="appwright-service")
        self.url: str | None = None
        self.installation: LocalAppiumInstallation | None = None
        self.standard_output_path: Path | None = None
        self.standard_error_path: Path | None = None
        self.standard_output_handle: BufferedWriter | None = None
        self.standard_error_handle: BufferedWriter | None = None
        self.server_logs: list[ServerLogRecord] = []
        self.stopped = False

    def prepare_log_files(self) -> None:
        output_descriptor, output_name = tempfile.mkstemp(prefix="appwright-appium-out-")
        error_descriptor, error_name = tempfile.mkstemp(prefix="appwright-appium-err-")
        os.close(output_descriptor)
        os.close(error_descriptor)
        self.standard_output_path = Path(output_name)
        self.standard_error_path = Path(error_name)
        self.standard_output_handle = self.standard_output_path.open("wb")
        self.standard_error_handle = self.standard_error_path.open("wb")

    def collect_logs(self) -> None:
        output_handle = self.standard_output_handle
        error_handle = self.standard_error_handle
        self.standard_output_handle = None
        self.standard_error_handle = None
        if output_handle is not None:
            output_handle.close()
        if error_handle is not None:
            error_handle.close()
        paths = (
            (self.standard_output_path, LogStream.STANDARD_OUTPUT),
            (self.standard_error_path, LogStream.STANDARD_ERROR),
        )
        self.standard_output_path = None
        self.standard_error_path = None
        for path, stream in paths:
            if path is None or not path.exists():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                self.server_logs.append(ServerLogRecord(stream=stream, message=line))
            path.unlink(missing_ok=True)

    def snapshot_logs(self) -> tuple[ServerLogRecord, ...]:
        records = list(self.server_logs)
        paths = (
            (self.standard_output_path, LogStream.STANDARD_OUTPUT),
            (self.standard_error_path, LogStream.STANDARD_ERROR),
        )
        for path, stream in paths:
            if path is None or not path.exists():
                continue
            records.extend(
                ServerLogRecord(stream=stream, message=line)
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            )
        return tuple(records)

    def start_blocking(self) -> str:
        self.installation = inspect_local_appium(self.executable, self.timeout)
        self.prepare_log_files()
        arguments = ["--address", self.host, "--port", str(self.port)]
        keyword_arguments: dict[str, object] = {
            "args": arguments,
            "timeout_ms": int(self.timeout.total_seconds() * 1000),
            "stdout": self.standard_output_handle,
            "stderr": self.standard_error_handle,
        }
        keyword_arguments["main_script"] = str(self.installation.executable)
        service_start: Callable[..., object] = self.service.start  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        try:
            service_start(**keyword_arguments)
        except BaseException:
            self.collect_logs()
            raise
        return f"http://{self.host}:{self.port}"

    def retire_late_start(self, start_future: Future[str]) -> None:
        try:
            with suppress(BaseException):
                start_future.result()
            with suppress(Exception):
                if self.service.is_running:
                    self.service.stop()
        finally:
            with suppress(Exception):
                self.collect_logs()

    async def start(self) -> str:
        start_future = self.executor.submit(self.start_blocking)
        try:
            selected_url = await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(start_future)),
                timeout=self.timeout.total_seconds(),
            )
        except BaseException:
            start_future.add_done_callback(self.retire_late_start)
            await self.stop()
            raise
        self.url = selected_url
        return selected_url

    async def stop(self, timeout: timedelta = DEFAULT_STOP_TIMEOUT) -> None:
        if self.stopped:
            return
        self.stopped = True
        loop = asyncio.get_running_loop()
        if self.service.is_running:
            with suppress(Exception):
                await asyncio.wait_for(
                    loop.run_in_executor(self.executor, self.service.stop),
                    timeout=timeout.total_seconds(),
                )
        with suppress(Exception):
            self.collect_logs()
        self.executor.shutdown(wait=False, cancel_futures=True)
