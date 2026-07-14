"""Typed test doubles for backend contract tests."""

from datetime import UTC, datetime, timedelta
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
from appwright.models.enums import ActionKind, Key
from appwright.operations import (
    ActionReceipt,
    DispatchState,
    OperationStage,
    replay_safety_for,
)
from appwright.selectors.compiler import LocatorPlan


class FakeCallKind(StrEnum):
    START = "start"
    CREATE_SESSION = "create_session"
    OBSERVE = "observe"
    RESOLVE = "resolve"
    DISPATCH = "dispatch"
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
        self.query_outcomes: list[QueryResult | BackendFailure] = []
        self.query_timeouts: list[timedelta] = []
        self.observation_sources: list[str] = []
        self.default_result = QueryResult(elements=(element(),))
        self.options: AndroidSessionOptions | None = None
        self.action_requests: list[ActionRequest] = []
        self.dispatch_errors: list[BackendError] = []
        self.drag_errors: list[BackendError] = []
        self.press_key_errors: list[BackendError] = []
        self.tap_point_errors: list[BackendError] = []
        self.scroll_errors: list[BackendError] = []
        self.action_receipts: list[ActionReceipt] = []
        self.closed = False
        self.server_logs: list[ServerLogRecord] = []
        self.session_capabilities: SessionCapabilities | None = None

    async def start(self, timeout: timedelta) -> None:
        self.calls.append(FakeCallKind.START)

    async def create_session(self, options: AndroidSessionOptions) -> None:
        self.options = options
        self.calls.append(FakeCallKind.CREATE_SESSION)

    def next_query_result(self) -> QueryResult:
        if self.query_outcomes:
            outcome = self.query_outcomes.pop(0)
            if isinstance(outcome, BackendFailure):
                if outcome.kind is BackendFailureKind.RECOVERABLE:
                    raise RecoverableBackendError(outcome)
                raise BackendError(outcome)
            return outcome
        if self.query_failures:
            failure = self.query_failures.pop(0)
            if failure.kind is BackendFailureKind.RECOVERABLE:
                raise RecoverableBackendError(failure)
            raise BackendError(failure)
        if self.query_results:
            return self.query_results.pop(0)
        return self.default_result

    async def observe(self, timeout: timedelta) -> HierarchySource:
        self.calls.append(FakeCallKind.OBSERVE)
        content = self.observation_sources.pop(0) if self.observation_sources else "<hierarchy />"
        return HierarchySource(content=content)

    async def resolve(self, plan: LocatorPlan, timeout: timedelta) -> QueryResult:
        self.calls.append(FakeCallKind.RESOLVE)
        self.query_timeouts.append(timeout)
        return self.next_query_result()

    async def dispatch(
        self,
        plan: LocatorPlan,
        request: ActionRequest,
        pre_action: ElementSnapshot,
        timeout: timedelta,
    ) -> ActionReceipt:
        self.calls.append(FakeCallKind.DISPATCH)
        self.action_requests.append(request)
        if self.dispatch_errors:
            raise self.dispatch_errors.pop(0)
        now = datetime.now(UTC)
        dispatched = not request.trial and not (
            request.kind in {ActionKind.CHECK, ActionKind.UNCHECK}
            and pre_action.checked is (request.kind is ActionKind.CHECK)
        )
        receipt = ActionReceipt(
            action=request.kind,
            locator=plan.description,
            replay_safety=replay_safety_for(request.kind),
            stage=OperationStage.DISPATCH if dispatched else OperationStage.RESOLVE,
            dispatch_state=(
                DispatchState.DISPATCHED if dispatched else DispatchState.NOT_DISPATCHED
            ),
            started_at=now,
            dispatched_at=now if dispatched else None,
            pre_action=pre_action,
        )
        self.action_receipts.append(receipt)
        return receipt

    async def query(self, plan: LocatorPlan, timeout: timedelta) -> QueryResult:
        self.calls.append(FakeCallKind.QUERY)
        self.query_timeouts.append(timeout)
        return self.next_query_result()

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
        if self.drag_errors:
            raise self.drag_errors.pop(0)
        return ActionResult(element=self.default_result.elements[0])

    async def scroll_into_view(
        self,
        plan: LocatorPlan,
        timeout: timedelta,
    ) -> OperationResult:
        self.calls.append(FakeCallKind.SCROLL_INTO_VIEW)
        if self.scroll_errors:
            raise self.scroll_errors.pop(0)
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
        if self.press_key_errors:
            raise self.press_key_errors.pop(0)

    async def tap_point(self, point: Point, timeout: timedelta) -> None:
        self.calls.append(FakeCallKind.TAP_POINT)
        if self.tap_point_errors:
            raise self.tap_point_errors.pop(0)

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
