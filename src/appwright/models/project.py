"""Validated project configuration with explicit precedence."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import TypeVar, cast

from pydantic import AnyHttpUrl, Field, field_validator

from appwright.models.base import StrictModel
from appwright.models.config import AppiumServer, Timeouts
from appwright.models.enums import ScreenshotMode, TraceMode

Value = TypeVar("Value")


class AppwrightConfigSource(StrictModel):
    serial: str | None = None
    server_url: AnyHttpUrl | None = None
    app_package: str | None = None
    clear_data: bool | None = None
    probe_timeout_seconds: float | None = Field(default=None, gt=0)
    wait_timeout_seconds: float | None = Field(default=None, gt=0)
    action_timeout_seconds: float | None = Field(default=None, gt=0)
    transition_timeout_seconds: float | None = Field(default=None, gt=0)
    interruption_timeout_seconds: float | None = Field(default=None, gt=0)
    transport_timeout_seconds: float | None = Field(default=None, gt=0)
    # Compatibility fallback for configurations written before wait became the
    # canonical name. `wait_timeout_seconds` wins when both are supplied.
    expect_timeout_seconds: float | None = Field(default=None, gt=0)
    stability_window_milliseconds: float | None = Field(default=None, gt=0)
    trace_mode: TraceMode | None = None
    screenshot_mode: ScreenshotMode | None = None
    artifacts_path: Path | None = None

    @field_validator("trace_mode", mode="before")
    @classmethod
    def parse_trace_mode(cls, value: object) -> object:
        return TraceMode(value) if isinstance(value, str) else value

    @field_validator("screenshot_mode", mode="before")
    @classmethod
    def parse_screenshot_mode(cls, value: object) -> object:
        return ScreenshotMode(value) if isinstance(value, str) else value

    @field_validator("artifacts_path", mode="before")
    @classmethod
    def parse_artifacts_path(cls, value: object) -> object:
        return Path(value) if isinstance(value, str) else value


class AppwrightConfiguration(StrictModel):
    serial: str | None = None
    server: AppiumServer = AppiumServer.local()
    app_package: str | None = None
    clear_data: bool = False
    timeouts: Timeouts = Timeouts()
    trace_mode: TraceMode = TraceMode.RETAIN_ON_FAILURE
    screenshot_mode: ScreenshotMode = ScreenshotMode.ONLY_ON_FAILURE
    artifacts_path: Path = Path(".appwright-artifacts")


def mapping_value(mapping: Mapping[object, object], key: str) -> Mapping[object, object]:
    value = mapping.get(key)
    if not isinstance(value, Mapping):
        return cast(Mapping[object, object], {})
    return cast(Mapping[object, object], value)


def project_source(path: Path) -> AppwrightConfigSource:
    if not path.exists():
        return AppwrightConfigSource()
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    root = cast(Mapping[object, object], parsed)
    tool = mapping_value(root, "tool")
    selected = mapping_value(tool, "appwright")
    return AppwrightConfigSource.model_validate(selected)


def optional_url(value: str | None) -> AnyHttpUrl | None:
    return None if value is None else AnyHttpUrl(value)


def optional_boolean(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean environment value: {value}")


def optional_float(value: str | None) -> float | None:
    return None if value is None else float(value)


def environment_source() -> AppwrightConfigSource:
    return AppwrightConfigSource(
        serial=os.environ.get("APPWRIGHT_SERIAL"),
        server_url=optional_url(os.environ.get("APPWRIGHT_SERVER_URL")),
        app_package=os.environ.get("APPWRIGHT_PACKAGE"),
        clear_data=optional_boolean(os.environ.get("APPWRIGHT_CLEAR_DATA")),
        probe_timeout_seconds=optional_float(os.environ.get("APPWRIGHT_PROBE_TIMEOUT_SECONDS")),
        wait_timeout_seconds=optional_float(os.environ.get("APPWRIGHT_WAIT_TIMEOUT_SECONDS")),
        action_timeout_seconds=optional_float(os.environ.get("APPWRIGHT_ACTION_TIMEOUT_SECONDS")),
        transition_timeout_seconds=optional_float(
            os.environ.get("APPWRIGHT_TRANSITION_TIMEOUT_SECONDS")
        ),
        interruption_timeout_seconds=optional_float(
            os.environ.get("APPWRIGHT_INTERRUPTION_TIMEOUT_SECONDS")
        ),
        transport_timeout_seconds=optional_float(
            os.environ.get("APPWRIGHT_TRANSPORT_TIMEOUT_SECONDS")
        ),
        expect_timeout_seconds=optional_float(os.environ.get("APPWRIGHT_EXPECT_TIMEOUT_SECONDS")),
        stability_window_milliseconds=optional_float(
            os.environ.get("APPWRIGHT_STABILITY_WINDOW_MILLISECONDS")
        ),
        trace_mode=(
            None
            if os.environ.get("APPWRIGHT_TRACE") is None
            else TraceMode(os.environ["APPWRIGHT_TRACE"])
        ),
        screenshot_mode=(
            None
            if os.environ.get("APPWRIGHT_SCREENSHOT") is None
            else ScreenshotMode(os.environ["APPWRIGHT_SCREENSHOT"])
        ),
        artifacts_path=(
            None
            if os.environ.get("APPWRIGHT_ARTIFACTS") is None
            else Path(os.environ["APPWRIGHT_ARTIFACTS"])
        ),
    )


def selected_value(
    cli_value: Value | None,
    environment_value: Value | None,
    file_value: Value | None,
    default: Value,
) -> Value:
    if cli_value is not None:
        return cli_value
    if environment_value is not None:
        return environment_value
    if file_value is not None:
        return file_value
    return default


def _effective_wait_timeout_seconds(source: AppwrightConfigSource) -> float | None:
    """Return the canonical wait budget, falling back to the legacy expect name."""

    return (
        source.wait_timeout_seconds
        if source.wait_timeout_seconds is not None
        else source.expect_timeout_seconds
    )


def load_configuration(
    cli: AppwrightConfigSource | None = None,
    *,
    pyproject_path: Path = Path("pyproject.toml"),
) -> AppwrightConfiguration:
    cli_source = cli if cli is not None else AppwrightConfigSource()
    environment = environment_source()
    file = project_source(pyproject_path)
    defaults = Timeouts()
    server_url = selected_value(
        cli_source.server_url,
        environment.server_url,
        file.server_url,
        None,
    )
    probe_seconds = selected_value(
        cli_source.probe_timeout_seconds,
        environment.probe_timeout_seconds,
        file.probe_timeout_seconds,
        defaults.probe.total_seconds(),
    )
    wait_seconds = selected_value(
        _effective_wait_timeout_seconds(cli_source),
        _effective_wait_timeout_seconds(environment),
        _effective_wait_timeout_seconds(file),
        defaults.wait.total_seconds(),
    )
    action_seconds = selected_value(
        cli_source.action_timeout_seconds,
        environment.action_timeout_seconds,
        file.action_timeout_seconds,
        defaults.action.total_seconds(),
    )
    transition_seconds = selected_value(
        cli_source.transition_timeout_seconds,
        environment.transition_timeout_seconds,
        file.transition_timeout_seconds,
        defaults.transition.total_seconds(),
    )
    interruption_seconds = selected_value(
        cli_source.interruption_timeout_seconds,
        environment.interruption_timeout_seconds,
        file.interruption_timeout_seconds,
        defaults.interruption.total_seconds(),
    )
    transport_seconds = selected_value(
        cli_source.transport_timeout_seconds,
        environment.transport_timeout_seconds,
        file.transport_timeout_seconds,
        defaults.transport.total_seconds(),
    )
    stability_milliseconds = selected_value(
        cli_source.stability_window_milliseconds,
        environment.stability_window_milliseconds,
        file.stability_window_milliseconds,
        defaults.stability.total_seconds() * 1000,
    )
    return AppwrightConfiguration(
        serial=selected_value(cli_source.serial, environment.serial, file.serial, None),
        server=(
            AppiumServer.local() if server_url is None else AppiumServer.remote(url=str(server_url))
        ),
        app_package=selected_value(
            cli_source.app_package,
            environment.app_package,
            file.app_package,
            None,
        ),
        clear_data=selected_value(
            cli_source.clear_data,
            environment.clear_data,
            file.clear_data,
            False,
        ),
        timeouts=Timeouts(
            probe=timedelta(seconds=probe_seconds),
            wait=timedelta(seconds=wait_seconds),
            action=timedelta(seconds=action_seconds),
            transition=timedelta(seconds=transition_seconds),
            interruption=timedelta(seconds=interruption_seconds),
            transport=timedelta(seconds=transport_seconds),
            stability=timedelta(milliseconds=stability_milliseconds),
        ),
        trace_mode=selected_value(
            cli_source.trace_mode,
            environment.trace_mode,
            file.trace_mode,
            TraceMode.RETAIN_ON_FAILURE,
        ),
        screenshot_mode=selected_value(
            cli_source.screenshot_mode,
            environment.screenshot_mode,
            file.screenshot_mode,
            ScreenshotMode.ONLY_ON_FAILURE,
        ),
        artifacts_path=selected_value(
            cli_source.artifacts_path,
            environment.artifacts_path,
            file.artifacts_path,
            Path(".appwright-artifacts"),
        ),
    )
