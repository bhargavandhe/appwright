"""Project configuration precedence tests."""

from pathlib import Path

import pytest
from pydantic import AnyHttpUrl

from appwright.models.enums import ScreenshotMode, ServerMode, TraceMode
from appwright.models.project import AppwrightConfigSource, load_configuration


def clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "APPWRIGHT_SERIAL",
        "APPWRIGHT_SERVER_URL",
        "APPWRIGHT_PACKAGE",
        "APPWRIGHT_CLEAR_DATA",
        "APPWRIGHT_ACTION_TIMEOUT_SECONDS",
        "APPWRIGHT_EXPECT_TIMEOUT_SECONDS",
        "APPWRIGHT_STABILITY_WINDOW_MILLISECONDS",
        "APPWRIGHT_TRACE",
        "APPWRIGHT_SCREENSHOT",
        "APPWRIGHT_ARTIFACTS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_configuration_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_environment(monkeypatch)
    project = tmp_path / "pyproject.toml"
    project.write_text(
        """
[tool.appwright]
serial = "file-device"
action_timeout_seconds = 1
clear_data = false
trace_mode = "always"
screenshot_mode = "off"
artifacts_path = "artifacts"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("APPWRIGHT_SERIAL", "environment-device")
    monkeypatch.setenv("APPWRIGHT_ACTION_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("APPWRIGHT_CLEAR_DATA", "true")
    cli = AppwrightConfigSource(
        serial="cli-device",
        server_url=AnyHttpUrl("https://grid.example.test/wd/hub"),
        action_timeout_seconds=3,
    )
    configuration = load_configuration(cli, pyproject_path=project)
    assert configuration.serial == "cli-device"
    assert configuration.server.mode is ServerMode.REMOTE
    assert configuration.timeouts.action.total_seconds() == 3
    assert configuration.clear_data
    assert configuration.trace_mode is TraceMode.ALWAYS
    assert configuration.screenshot_mode is ScreenshotMode.OFF
    assert configuration.artifacts_path == Path("artifacts")
