"""Typed models used exclusively at the Appium boundary."""

from pathlib import Path

from pydantic import Field

from appwright.models.base import StrictModel
from appwright.models.enums import Direction


class AppCommandArguments(StrictModel):
    app_id: str = Field(serialization_alias="appId")


class ElementGestureArguments(StrictModel):
    element_id: str = Field(serialization_alias="elementId")
    duration: int | None = None


class PointGestureArguments(StrictModel):
    x: int
    y: int


class SwipeGestureArguments(StrictModel):
    element_id: str = Field(serialization_alias="elementId")
    direction: Direction
    percent: float = Field(default=0.75, gt=0, le=1)
    speed: int | None = Field(default=None, gt=0)


class ScrollGestureArguments(StrictModel):
    element_id: str = Field(serialization_alias="elementId")
    direction: Direction
    percent: float = Field(default=0.75, gt=0, le=1)
    speed: int | None = Field(default=None, gt=0)


class DragGestureArguments(StrictModel):
    element_id: str = Field(serialization_alias="elementId")
    end_x: int = Field(serialization_alias="endX")
    end_y: int = Field(serialization_alias="endY")
    speed: int | None = Field(default=None, gt=0)


class InstallAppArguments(StrictModel):
    app_path: str = Field(serialization_alias="appPath")
    replace: bool
    grant_permissions: bool = Field(serialization_alias="grantPermissions")


class RemoveAppArguments(StrictModel):
    app_id: str = Field(serialization_alias="appId")
    keep_data: bool = Field(serialization_alias="keepData")


class CommandOutput(StrictModel):
    exit_code: int
    standard_output: str
    standard_error: str


class LocalAppiumInstallation(StrictModel):
    executable: Path
    version: str
    driver_version: str
    driver_listing: str
