"""Project configuration precedence tests."""

from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import AnyHttpUrl

from appwright.models.enums import ScreenshotMode, ServerMode, TraceMode
from appwright.models.project import (
    AppwrightConfigSource,
    AppwrightConfiguration,
    load_configuration,
)

TIMEOUT_ENVIRONMENT_VARIABLES = (
    "APPWRIGHT_PROBE_TIMEOUT_SECONDS",
    "APPWRIGHT_WAIT_TIMEOUT_SECONDS",
    "APPWRIGHT_ACTION_TIMEOUT_SECONDS",
    "APPWRIGHT_TRANSITION_TIMEOUT_SECONDS",
    "APPWRIGHT_INTERRUPTION_TIMEOUT_SECONDS",
    "APPWRIGHT_TRANSPORT_TIMEOUT_SECONDS",
    "APPWRIGHT_EXPECT_TIMEOUT_SECONDS",
    "APPWRIGHT_STABILITY_WINDOW_MILLISECONDS",
)


def clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "APPWRIGHT_SERIAL",
        "APPWRIGHT_SERVER_URL",
        "APPWRIGHT_PACKAGE",
        "APPWRIGHT_CLEAR_DATA",
        *TIMEOUT_ENVIRONMENT_VARIABLES,
        "APPWRIGHT_TRACE",
        "APPWRIGHT_SCREENSHOT",
        "APPWRIGHT_ARTIFACTS",
    ):
        monkeypatch.delenv(name, raising=False)


def assert_timeout_values(
    configuration: AppwrightConfiguration,
    *,
    probe: float,
    wait: float,
    action: float,
    transition: float,
    interruption: float,
    transport: float,
    stability_milliseconds: float,
) -> None:
    timeouts = configuration.timeouts
    assert timeouts.probe == timedelta(seconds=probe)
    assert timeouts.wait == timedelta(seconds=wait)
    assert timeouts.action == timedelta(seconds=action)
    assert timeouts.transition == timedelta(seconds=transition)
    assert timeouts.interruption == timedelta(seconds=interruption)
    assert timeouts.transport == timedelta(seconds=transport)
    assert timeouts.stability == timedelta(milliseconds=stability_milliseconds)


def test_configuration_uses_exact_mobile_timeout_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_environment(monkeypatch)

    configuration = load_configuration(pyproject_path=tmp_path / "missing.toml")

    assert_timeout_values(
        configuration,
        probe=2,
        wait=30,
        action=30,
        transition=90,
        interruption=30,
        transport=120,
        stability_milliseconds=300,
    )


def test_file_can_configure_every_public_mobile_timeout_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_environment(monkeypatch)
    project = tmp_path / "pyproject.toml"
    project.write_text(
        """
[tool.appwright]
probe_timeout_seconds = 11
wait_timeout_seconds = 12
action_timeout_seconds = 13
transition_timeout_seconds = 14
interruption_timeout_seconds = 15
transport_timeout_seconds = 16
stability_window_milliseconds = 170
""".strip(),
        encoding="utf-8",
    )

    configuration = load_configuration(pyproject_path=project)

    assert_timeout_values(
        configuration,
        probe=11,
        wait=12,
        action=13,
        transition=14,
        interruption=15,
        transport=16,
        stability_milliseconds=170,
    )


def test_environment_overrides_file_for_every_public_mobile_timeout_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_environment(monkeypatch)
    project = tmp_path / "pyproject.toml"
    project.write_text(
        """
[tool.appwright]
probe_timeout_seconds = 11
wait_timeout_seconds = 12
action_timeout_seconds = 13
transition_timeout_seconds = 14
interruption_timeout_seconds = 15
transport_timeout_seconds = 16
stability_window_milliseconds = 170
""".strip(),
        encoding="utf-8",
    )
    for name, value in zip(
        TIMEOUT_ENVIRONMENT_VARIABLES[:6],
        ("21", "22", "23", "24", "25", "26"),
        strict=True,
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("APPWRIGHT_STABILITY_WINDOW_MILLISECONDS", "270")

    configuration = load_configuration(pyproject_path=project)

    assert_timeout_values(
        configuration,
        probe=21,
        wait=22,
        action=23,
        transition=24,
        interruption=25,
        transport=26,
        stability_milliseconds=270,
    )


def test_cli_overrides_environment_for_every_public_mobile_timeout_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_environment(monkeypatch)
    for name, value in zip(
        TIMEOUT_ENVIRONMENT_VARIABLES[:6],
        ("21", "22", "23", "24", "25", "26"),
        strict=True,
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("APPWRIGHT_STABILITY_WINDOW_MILLISECONDS", "270")
    cli = AppwrightConfigSource(
        probe_timeout_seconds=31,
        wait_timeout_seconds=32,
        action_timeout_seconds=33,
        transition_timeout_seconds=34,
        interruption_timeout_seconds=35,
        transport_timeout_seconds=36,
        stability_window_milliseconds=370,
    )

    configuration = load_configuration(cli, pyproject_path=tmp_path / "missing.toml")

    assert_timeout_values(
        configuration,
        probe=31,
        wait=32,
        action=33,
        transition=34,
        interruption=35,
        transport=36,
        stability_milliseconds=370,
    )


def test_canonical_wait_timeout_wins_over_legacy_expect_within_each_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_environment(monkeypatch)
    project = tmp_path / "pyproject.toml"
    project.write_text(
        """
[tool.appwright]
wait_timeout_seconds = 11
expect_timeout_seconds = 99
""".strip(),
        encoding="utf-8",
    )
    cli = AppwrightConfigSource(
        wait_timeout_seconds=33,
        expect_timeout_seconds=77,
    )

    assert load_configuration(pyproject_path=project).timeouts.wait == timedelta(seconds=11)

    monkeypatch.setenv("APPWRIGHT_WAIT_TIMEOUT_SECONDS", "22")
    monkeypatch.setenv("APPWRIGHT_EXPECT_TIMEOUT_SECONDS", "88")
    assert load_configuration(pyproject_path=project).timeouts.wait == timedelta(seconds=22)
    assert load_configuration(cli, pyproject_path=project).timeouts.wait == timedelta(seconds=33)


def test_legacy_expect_timeout_falls_back_to_wait_with_source_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_environment(monkeypatch)
    project = tmp_path / "legacy.toml"
    project.write_text(
        """
[tool.appwright]
expect_timeout_seconds = 44
""".strip(),
        encoding="utf-8",
    )

    assert load_configuration(pyproject_path=project).timeouts.wait == timedelta(seconds=44)

    monkeypatch.setenv("APPWRIGHT_EXPECT_TIMEOUT_SECONDS", "55")
    assert load_configuration(pyproject_path=project).timeouts.wait == timedelta(seconds=55)

    cli = AppwrightConfigSource(expect_timeout_seconds=66)
    assert load_configuration(cli, pyproject_path=project).timeouts.wait == timedelta(seconds=66)


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
