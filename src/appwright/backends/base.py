"""Backend-neutral automation contract."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from appwright.models.base import StrictModel
from appwright.models.config import AndroidSessionOptions, AppiumServer, SessionCapabilities
from appwright.models.data import (
    ActionRequest,
    ActionResult,
    HierarchySource,
    InstallApplicationRequest,
    OperationResult,
    Point,
    QueryResult,
    Screenshot,
    ServerLogRecord,
    UninstallApplicationRequest,
)
from appwright.models.enums import Key, MobileCommand
from appwright.selectors.compiler import LocatorPlan


class BackendFailureKind(StrEnum):
    MATCH_COUNT = "match_count"
    NOT_STARTED = "not_started"
    RECOVERABLE = "recoverable"
    TAINTED = "tainted"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class BackendFailure(StrictModel):
    kind: BackendFailureKind
    message: str
    match_count: int | None = None
    appium_command: MobileCommand | None = None


class BackendError(Exception):
    def __init__(self, failure: BackendFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


class RecoverableBackendError(BackendError):
    pass


class AutomationBackend(Protocol):
    server: AppiumServer
    server_logs: list[ServerLogRecord]
    session_capabilities: SessionCapabilities | None

    async def start(self, timeout: timedelta) -> None: ...

    async def create_session(self, options: AndroidSessionOptions) -> None: ...

    async def query(self, plan: LocatorPlan, timeout: timedelta) -> QueryResult: ...

    async def perform(
        self,
        plan: LocatorPlan,
        request: ActionRequest,
        timeout: timedelta,
    ) -> ActionResult: ...

    async def screenshot(self, path: Path | None, timeout: timedelta) -> Screenshot: ...

    async def element_screenshot(
        self,
        plan: LocatorPlan,
        path: Path | None,
        timeout: timedelta,
    ) -> Screenshot: ...

    async def drag(
        self,
        source: LocatorPlan,
        destination: LocatorPlan,
        timeout: timedelta,
    ) -> ActionResult: ...

    async def scroll_into_view(
        self,
        plan: LocatorPlan,
        timeout: timedelta,
    ) -> OperationResult: ...

    async def hierarchy(self, timeout: timedelta) -> HierarchySource: ...

    async def read_server_logs(self) -> tuple[ServerLogRecord, ...]: ...

    async def install_app(
        self,
        request: InstallApplicationRequest,
        timeout: timedelta,
    ) -> OperationResult: ...

    async def uninstall_app(
        self,
        request: UninstallApplicationRequest,
        timeout: timedelta,
    ) -> OperationResult: ...

    async def activate_app(self, package: str, timeout: timedelta) -> None: ...

    async def terminate_app(self, package: str, timeout: timedelta) -> None: ...

    async def clear_app(self, package: str, timeout: timedelta) -> None: ...

    async def press_key(self, key: Key, timeout: timedelta) -> None: ...

    async def tap_point(self, point: Point, timeout: timedelta) -> None: ...

    async def close_session(self) -> None: ...

    async def close(self) -> None: ...
