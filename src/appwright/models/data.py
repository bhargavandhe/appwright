"""Runtime data models shared across layers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic

from pydantic import Field, model_validator

from appwright.models.base import StrictModel
from appwright.models.enums import (
    ActionKind,
    DeviceState,
    Direction,
    ErrorCode,
    LocatorStrategy,
    LogStream,
    MobileCommand,
    TraceArtifactKind,
    TraceEventKind,
)


class Point(StrictModel):
    x: float
    y: float


class Deadline(StrictModel):
    started_at: float
    expires_at: float

    @classmethod
    def start(cls, timeout: timedelta) -> Deadline:
        started_at = monotonic()
        return cls(
            started_at=started_at,
            expires_at=started_at + timeout.total_seconds(),
        )

    def elapsed(self) -> timedelta:
        return timedelta(seconds=max(monotonic() - self.started_at, 0))

    def remaining(self, minimum: timedelta | None = None) -> timedelta:
        remaining_seconds = max(self.expires_at - monotonic(), 0)
        if minimum is not None and remaining_seconds > 0:
            remaining_seconds = max(remaining_seconds, minimum.total_seconds())
        return timedelta(seconds=remaining_seconds)

    def expired(self) -> bool:
        return monotonic() >= self.expires_at


class Rect(StrictModel):
    x: float
    y: float
    width: float = Field(ge=0)
    height: float = Field(ge=0)


class DeviceInfo(StrictModel):
    serial: str
    state: DeviceState
    model: str | None = None
    product: str | None = None
    transport_id: str | None = None


class ElementSnapshot(StrictModel):
    identity: str
    text: str = ""
    accessible_name: str = ""
    resource_id: str = ""
    class_name: str = ""
    package_name: str = ""
    displayed: bool
    enabled: bool
    selected: bool
    checked: bool
    checkable: bool
    focusable: bool
    focused: bool
    editable: bool
    bounds: Rect
    window_id: str = ""


class QueryResult(StrictModel):
    elements: tuple[ElementSnapshot, ...]


class ActionRequest(StrictModel):
    kind: ActionKind
    text: str | None = None
    key: str | None = None
    direction: Direction | None = None
    percent: float | None = Field(default=None, gt=0, le=1)
    force: bool = False
    trial: bool = False

    @model_validator(mode="after")
    def validate_action(self) -> ActionRequest:
        gesture_kinds = {ActionKind.SWIPE, ActionKind.SCROLL}
        if self.kind in gesture_kinds and self.direction is None:
            raise ValueError(f"{self.kind.value} requires a direction")
        if self.kind not in gesture_kinds and self.direction is not None:
            raise ValueError("direction is only valid for swipe and scroll actions")
        if self.kind not in gesture_kinds and self.percent is not None:
            raise ValueError("percent is only valid for swipe and scroll actions")
        if self.kind is ActionKind.FILL and self.text is None:
            raise ValueError("fill requires text")
        if self.kind is not ActionKind.FILL and self.text is not None:
            raise ValueError("text is only valid for fill actions")
        if self.kind is ActionKind.PRESS and self.key is None:
            raise ValueError("press requires a key")
        if self.kind is not ActionKind.PRESS and self.key is not None:
            raise ValueError("key is only valid for press actions")
        return self


class ActionResult(StrictModel):
    element: ElementSnapshot


class OperationResult(StrictModel):
    succeeded: bool


class InstallApplicationRequest(StrictModel):
    path: Path
    replace: bool = True
    grant_permissions: bool = False


class UninstallApplicationRequest(StrictModel):
    package: str = Field(min_length=1)
    keep_data: bool = False


class HierarchySource(StrictModel):
    content: str


class Screenshot(StrictModel):
    content: bytes
    path: Path | None = None


class CallLogEntry(StrictModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    message: str
    elapsed: timedelta


class TraceField(StrictModel):
    name: str
    value: str


class TraceEvent(StrictModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: TraceEventKind
    name: str
    fields: tuple[TraceField, ...] = ()


class TraceManifest(StrictModel):
    format_version: int = 2
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_count: int = Field(ge=0)
    artifact_count: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    dropped_event_count: int = Field(default=0, ge=0)
    dropped_artifact_count: int = Field(default=0, ge=0)
    truncated: bool = False


class TraceLimits(StrictModel):
    maximum_events: int = Field(default=100_000, ge=1)
    maximum_artifacts: int = Field(default=1_000, ge=1)
    maximum_total_bytes: int = Field(default=100 * 1024 * 1024, ge=1)
    maximum_artifact_bytes: int = Field(default=25 * 1024 * 1024, ge=1)


class TraceArtifact(StrictModel):
    kind: TraceArtifactKind
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    media_type: str = Field(min_length=1)
    content: bytes

    def index(self) -> TraceArtifactIndex:
        return TraceArtifactIndex(
            kind=self.kind,
            name=self.name,
            media_type=self.media_type,
            size=len(self.content),
        )


class TraceArtifactIndex(StrictModel):
    kind: TraceArtifactKind
    name: str
    media_type: str
    size: int = Field(ge=0)


class ServerLogRecord(StrictModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stream: LogStream
    message: str


class ErrorDetails(StrictModel):
    code: ErrorCode
    api_name: str
    message: str
    locator: str | None = None
    strategy: LocatorStrategy | None = None
    appium_command: MobileCommand | None = None
    appium_server_log: tuple[ServerLogRecord, ...] = ()
    expected: str | None = None
    received: str | None = None
    elapsed: timedelta | None = None
    screenshot_path: Path | None = None
    trace_path: Path | None = None
    call_log: tuple[CallLogEntry, ...] = ()
