"""Typed test doubles for backend contract tests."""

from datetime import timedelta
from enum import StrEnum
from pathlib import Path

from appwright.backends.base import (
    BackendError,
    BackendFailure,
    BackendFailureKind,
    RecoverableBackendError,
)
from appwright.models.config import AndroidSessionOptions, AppiumServer, SessionCapabilities
from appwright.models.data import (
    ActionRequest,
    ActionResult,
    ElementSnapshot,
    HierarchySource,
    InstallApplicationRequest,
    OperationResult,
    Point,
    QueryResult,
    Rect,
    Screenshot,
    ServerLogRecord,
    UninstallApplicationRequest,
)
from appwright.models.enums import Key
from appwright.selectors.compiler import LocatorPlan


class FakeCallKind(StrEnum):
    START = "start"
    CREATE_SESSION = "create_session"
    QUERY = "query"
    PERFORM = "perform"
    SCREENSHOT = "screenshot"
    ELEMENT_SCREENSHOT = "element_screenshot"
    DRAG = "drag"
    SCROLL_INTO_VIEW = "scroll_into_view"
    HIERARCHY = "hierarchy"
    INSTALL = "install"
    UNINSTALL = "uninstall"
    ACTIVATE = "activate"
    TERMINATE = "terminate"
    CLEAR = "clear"
    PRESS_KEY = "press_key"
    TAP_POINT = "tap_point"
    CLOSE_SESSION = "close_session"
    CLOSE = "close"


def element(
    *,
    identity: str = "element-1",
    text: str = "Welcome",
    displayed: bool = True,
    enabled: bool = True,
    checked: bool = False,
    checkable: bool = False,
    editable: bool = False,
    focused: bool = False,
    selected: bool = False,
) -> ElementSnapshot:
    return ElementSnapshot(
        identity=identity,
        text=text,
        accessible_name=text,
        resource_id="com.example:id/target",
        class_name="android.widget.EditText" if editable else "android.widget.TextView",
        package_name="com.example",
        displayed=displayed,
        enabled=enabled,
        selected=selected,
        checked=checked,
        checkable=checkable,
        focusable=True,
        focused=focused,
        editable=editable,
        bounds=Rect(x=0, y=0, width=100, height=40),
        window_id="window-1",
    )


class FakeBackend:
    def __init__(self, server: AppiumServer) -> None:
        self.server = server
        self.calls: list[FakeCallKind] = []
        self.query_results: list[QueryResult] = []
        self.query_failures: list[BackendFailure] = []
        self.query_timeouts: list[timedelta] = []
        self.default_result = QueryResult(elements=(element(),))
        self.options: AndroidSessionOptions | None = None
        self.action_requests: list[ActionRequest] = []
        self.closed = False
        self.server_logs: list[ServerLogRecord] = []
        self.session_capabilities: SessionCapabilities | None = None

    async def start(self, timeout: timedelta) -> None:
        self.calls.append(FakeCallKind.START)

    async def create_session(self, options: AndroidSessionOptions) -> None:
        self.options = options
        self.calls.append(FakeCallKind.CREATE_SESSION)

    async def query(self, plan: LocatorPlan, timeout: timedelta) -> QueryResult:
        self.calls.append(FakeCallKind.QUERY)
        self.query_timeouts.append(timeout)
        if self.query_failures:
            failure = self.query_failures.pop(0)
            if failure.kind is BackendFailureKind.RECOVERABLE:
                raise RecoverableBackendError(failure)
            raise BackendError(failure)
        if self.query_results:
            return self.query_results.pop(0)
        return self.default_result

    async def perform(
        self,
        plan: LocatorPlan,
        request: ActionRequest,
        timeout: timedelta,
    ) -> ActionResult:
        self.calls.append(FakeCallKind.PERFORM)
        self.action_requests.append(request)
        result = self.default_result
        if len(result.elements) != 1:
            raise BackendError(
                BackendFailure(
                    kind=BackendFailureKind.MATCH_COUNT,
                    message=f"locator resolved to {len(result.elements)} elements",
                    match_count=len(result.elements),
                )
            )
        return ActionResult(element=result.elements[0])

    async def screenshot(self, path: Path | None, timeout: timedelta) -> Screenshot:
        self.calls.append(FakeCallKind.SCREENSHOT)
        return Screenshot(content=b"png", path=path)

    async def element_screenshot(
        self,
        plan: LocatorPlan,
        path: Path | None,
        timeout: timedelta,
    ) -> Screenshot:
        self.calls.append(FakeCallKind.ELEMENT_SCREENSHOT)
        return Screenshot(content=b"element-png", path=path)

    async def drag(
        self,
        source: LocatorPlan,
        destination: LocatorPlan,
        timeout: timedelta,
    ) -> ActionResult:
        self.calls.append(FakeCallKind.DRAG)
        return ActionResult(element=self.default_result.elements[0])

    async def scroll_into_view(
        self,
        plan: LocatorPlan,
        timeout: timedelta,
    ) -> OperationResult:
        self.calls.append(FakeCallKind.SCROLL_INTO_VIEW)
        return OperationResult(succeeded=True)

    async def hierarchy(self, timeout: timedelta) -> HierarchySource:
        self.calls.append(FakeCallKind.HIERARCHY)
        return HierarchySource(content="<hierarchy />")

    async def read_server_logs(self) -> tuple[ServerLogRecord, ...]:
        return tuple(self.server_logs)

    async def install_app(
        self,
        request: InstallApplicationRequest,
        timeout: timedelta,
    ) -> OperationResult:
        self.calls.append(FakeCallKind.INSTALL)
        return OperationResult(succeeded=True)

    async def uninstall_app(
        self,
        request: UninstallApplicationRequest,
        timeout: timedelta,
    ) -> OperationResult:
        self.calls.append(FakeCallKind.UNINSTALL)
        return OperationResult(succeeded=True)

    async def activate_app(self, package: str, timeout: timedelta) -> None:
        self.calls.append(FakeCallKind.ACTIVATE)

    async def terminate_app(self, package: str, timeout: timedelta) -> None:
        self.calls.append(FakeCallKind.TERMINATE)

    async def clear_app(self, package: str, timeout: timedelta) -> None:
        self.calls.append(FakeCallKind.CLEAR)

    async def press_key(self, key: Key, timeout: timedelta) -> None:
        self.calls.append(FakeCallKind.PRESS_KEY)

    async def tap_point(self, point: Point, timeout: timedelta) -> None:
        self.calls.append(FakeCallKind.TAP_POINT)

    async def close_session(self) -> None:
        self.calls.append(FakeCallKind.CLOSE_SESSION)

    async def close(self) -> None:
        self.closed = True
        self.calls.append(FakeCallKind.CLOSE)


class FakeBackendFactory:
    def __init__(self) -> None:
        self.backends: list[FakeBackend] = []

    def __call__(self, server: AppiumServer) -> FakeBackend:
        backend = FakeBackend(server)
        self.backends.append(backend)
        return backend
