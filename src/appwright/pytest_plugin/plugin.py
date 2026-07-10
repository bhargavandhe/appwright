"""Pytest fixtures and failure diagnostics for Appwright."""

import os
import re
from collections.abc import Generator
from pathlib import Path
from typing import cast

import pytest

from appwright.api.generated.sync_api import App, Appwright, Device, sync_appwright
from appwright.models.enums import ScreenshotMode, TraceMode
from appwright.models.project import (
    AppwrightConfigSource,
    AppwrightConfiguration,
    load_configuration,
    optional_url,
)

device_stash_key = pytest.StashKey[Device]()
failure_stash_key = pytest.StashKey[bool]()
artifact_path_stash_key = pytest.StashKey[Path]()
screenshot_mode_stash_key = pytest.StashKey[ScreenshotMode]()


def write_private_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o600)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("appwright")
    group.addoption("--appwright-serial", action="store", default=None)
    group.addoption("--appwright-server-url", action="store", default=None)
    group.addoption("--appwright-package", action="store", default=None)
    group.addoption("--appwright-clear-data", action="store_true", default=False)
    group.addoption(
        "--appwright-trace",
        action="store",
        choices=tuple(mode.value for mode in TraceMode),
        default=None,
    )
    group.addoption(
        "--appwright-screenshot",
        action="store",
        choices=tuple(mode.value for mode in ScreenshotMode),
        default=None,
    )
    group.addoption(
        "--appwright-artifacts",
        action="store",
        default=None,
    )


@pytest.fixture
def appwright() -> Generator[Appwright, None, None]:
    context = sync_appwright()
    instance = context.__enter__()
    try:
        yield instance
    finally:
        context.__exit__(None, None, None)


@pytest.fixture
def appwright_config(pytestconfig: pytest.Config) -> AppwrightConfiguration:
    serial_option = pytestconfig.getoption("appwright_serial")
    server_option = pytestconfig.getoption("appwright_server_url")
    package_option = pytestconfig.getoption("appwright_package")
    clear_data_option = bool(pytestconfig.getoption("appwright_clear_data"))
    trace_option = pytestconfig.getoption("appwright_trace")
    screenshot_option = pytestconfig.getoption("appwright_screenshot")
    artifacts_option = pytestconfig.getoption("appwright_artifacts")
    cli = AppwrightConfigSource(
        serial=None if serial_option is None else str(serial_option),
        server_url=optional_url(None if server_option is None else str(server_option)),
        app_package=None if package_option is None else str(package_option),
        clear_data=True if clear_data_option else None,
        trace_mode=None if trace_option is None else TraceMode(str(trace_option)),
        screenshot_mode=(
            None if screenshot_option is None else ScreenshotMode(str(screenshot_option))
        ),
        artifacts_path=None if artifacts_option is None else Path(str(artifacts_option)),
    )
    return load_configuration(cli)


@pytest.fixture
def android_device(
    appwright: Appwright,
    appwright_config: AppwrightConfiguration,
    request: pytest.FixtureRequest,
) -> Generator[Device, None, None]:
    device = appwright.android.connect(
        serial=appwright_config.serial,
        server=appwright_config.server,
        timeouts=appwright_config.timeouts,
    )
    item = cast(
        pytest.Item,
        request.node,  # pyright: ignore[reportUnknownMemberType]
    )
    item.stash[device_stash_key] = device
    item.stash[failure_stash_key] = False
    item.stash[artifact_path_stash_key] = appwright_config.artifacts_path / re.sub(
        r"[^A-Za-z0-9._-]+", "-", item.nodeid
    ).strip("-")
    item.stash[screenshot_mode_stash_key] = appwright_config.screenshot_mode
    trace_mode = appwright_config.trace_mode
    if trace_mode is not TraceMode.OFF:
        device.tracing.start(item.stash[artifact_path_stash_key] / "trace.zip")
    try:
        yield device
    finally:
        teardown_errors: list[BaseException] = []
        try:
            device.close()
        except BaseException as error:
            teardown_errors.append(error)
        failed = item.stash[failure_stash_key]
        retain_trace = trace_mode is TraceMode.ALWAYS or (
            trace_mode is TraceMode.RETAIN_ON_FAILURE and failed
        )
        try:
            if device.tracing.active and retain_trace:
                device.tracing.stop()
            elif device.tracing.active:
                device.tracing.discard()
        except BaseException as error:
            teardown_errors.append(error)
        if teardown_errors:
            raise BaseExceptionGroup("Appwright fixture teardown failed", teardown_errors)


@pytest.fixture
def mobile_app(android_device: Device, appwright_config: AppwrightConfiguration) -> App:
    package = appwright_config.app_package
    if package is None:
        pytest.fail("mobile_app requires --appwright-package")
    return android_device.launch_app(package=package, clear_data=appwright_config.clear_data)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item,
    call: pytest.CallInfo[None],
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if not report.failed or report.when not in {"setup", "call"}:
        return report
    item.stash[failure_stash_key] = True
    device = item.stash.get(device_stash_key, None)
    if device is None:
        return report
    artifacts = item.stash[artifact_path_stash_key]
    artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(artifacts, 0o700)
    screenshot_mode = item.stash[screenshot_mode_stash_key]
    diagnostic_errors: list[str] = []
    if screenshot_mode is ScreenshotMode.ONLY_ON_FAILURE:
        try:
            screenshot_path = artifacts / "failure.png"
            screenshot = device.screenshot(screenshot_path)
            if not screenshot_path.exists():
                screenshot_path.write_bytes(screenshot.content)
            os.chmod(screenshot_path, 0o600)
        except Exception as error:
            diagnostic_errors.append(
                device.tracing.redactor.sanitize_text(f"screenshot capture failed: {error}")
            )
    try:
        hierarchy = device.hierarchy()
        write_private_text(
            artifacts / "hierarchy.xml",
            device.tracing.redactor.sanitize_text(hierarchy.content),
        )
    except Exception as error:
        diagnostic_errors.append(
            device.tracing.redactor.sanitize_text(f"hierarchy capture failed: {error}")
        )
    try:
        logs = device.server_logs()
        log_content = "\n".join(record.model_dump_json() for record in logs)
        write_private_text(
            artifacts / "appium-server.jsonl",
            device.tracing.redactor.sanitize_text(log_content),
        )
    except Exception as error:
        diagnostic_errors.append(
            device.tracing.redactor.sanitize_text(f"server log capture failed: {error}")
        )
    details = [f"artifacts: {artifacts}", *diagnostic_errors]
    report.sections.append(("Appwright diagnostics", "\n".join(details)))
    return report
