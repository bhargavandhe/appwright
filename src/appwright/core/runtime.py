"""Canonical asynchronous Appwright runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import TypeVar

from appwright.backends.appium import AppiumBackend
from appwright.backends.base import (
    AutomationBackend,
    BackendError,
    BackendFailureKind,
    IndeterminateActionBackendError,
    RecoverableBackendError,
)
from appwright.core.devices import discover_android_devices
from appwright.core.errors import (
    AppiumUnavailableError,
    AppwrightError,
    DeviceNotFoundError,
    IndeterminateActionError,
    InvalidSelectorError,
    ProtocolError,
    SessionTaintedError,
    StrictModeViolationError,
    TargetClosedError,
)
from appwright.core.errors import (
    TimeoutError as AppwrightTimeoutError,
)
from appwright.models.config import (
    AdditionalCapability,
    AndroidConnectionOptions,
    AndroidDeviceSelector,
    AndroidSessionOptions,
    AppiumServer,
    AppiumTimeouts,
    ApplicationOptions,
)
from appwright.models.data import (
    ActionRequest,
    CallLogEntry,
    DeviceInfo,
    ElementSnapshot,
    ErrorDetails,
    HierarchySource,
    InstallApplicationRequest,
    OperationResult,
    Point,
    QueryResult,
    Rect,
    Screenshot,
    ServerLogRecord,
    TraceArtifact,
    TraceEvent,
    TraceField,
    UninstallApplicationRequest,
)
from appwright.models.enums import (
    ActionKind,
    DeviceState,
    Direction,
    ErrorCode,
    Key,
    MatchMode,
    MobileCommand,
    Role,
    ServerMode,
    TraceArtifactKind,
    TraceEventKind,
    WaitState,
)
from appwright.operations import (
    ActionReceipt,
    DispatchState,
    OperationDeadline,
    OperationStage,
    actionability_problem,
    replay_safety_for,
)
from appwright.selectors.compiler import LocatorPlan, SelectorCompilationError, compile_selector
from appwright.selectors.models import Selector, TextMatcher
from appwright.tracing import TraceRecorder

BackendFactory = Callable[[AppiumServer], AutomationBackend]
TextValue = str
BackendResult = TypeVar("BackendResult")


def text_matcher(value: TextValue, exact: bool) -> TextMatcher:
    return TextMatcher(value=value, mode=MatchMode.EXACT if exact else MatchMode.CONTAINS)


def error_details(
    *,
    code: ErrorCode,
    api_name: str,
    message: str,
    plan: LocatorPlan | None = None,
    locator: str | None = None,
    elapsed: timedelta | None = None,
    call_log: tuple[CallLogEntry, ...] = (),
    expected: str | None = None,
    received: str | None = None,
    appium_command: MobileCommand | None = None,
) -> ErrorDetails:
    return ErrorDetails(
        code=code,
        api_name=api_name,
        message=message,
        locator=plan.description if plan is not None else locator,
        strategy=None if plan is None else plan.strategy,
        elapsed=elapsed,
        call_log=call_log,
        expected=expected,
        received=received,
        appium_command=appium_command,
    )


def translated_backend_error(
    error: BackendError,
    api_name: str,
    plan: LocatorPlan | None = None,
) -> Exception:
    failure = error.failure
    if failure.kind is BackendFailureKind.NOT_STARTED:
        return TargetClosedError(
            error_details(
                code=ErrorCode.TARGET_CLOSED,
                api_name=api_name,
                message=failure.message,
                plan=plan,
                appium_command=failure.appium_command,
            )
        )
    if failure.kind is BackendFailureKind.UNAVAILABLE:
        return AppiumUnavailableError(
            error_details(
                code=ErrorCode.APPIUM_UNAVAILABLE,
                api_name=api_name,
                message=failure.message,
                plan=plan,
                appium_command=failure.appium_command,
            )
        )
    if failure.kind is BackendFailureKind.TAINTED:
        return SessionTaintedError(
            error_details(
                code=ErrorCode.SESSION_TAINTED,
                api_name=api_name,
                message=failure.message,
                plan=plan,
                appium_command=failure.appium_command,
            )
        )
    return ProtocolError(
        error_details(
            code=ErrorCode.PROTOCOL_ERROR,
            api_name=api_name,
            message=failure.message,
            plan=plan,
            appium_command=failure.appium_command,
        )
    )


def stable_snapshots(first: ElementSnapshot, second: ElementSnapshot) -> bool:
    return (
        first.text == second.text
        and first.accessible_name == second.accessible_name
        and first.resource_id == second.resource_id
        and first.class_name == second.class_name
        and first.displayed == second.displayed
        and first.enabled == second.enabled
        and first.selected == second.selected
        and first.checked == second.checked
        and first.bounds == second.bounds
        and first.window_id == second.window_id
    )


def diagnostic_snapshot(snapshot: ElementSnapshot) -> ElementSnapshot:
    return snapshot.model_copy(
        update={
            "text": "[REDACTED]",
            "accessible_name": "[REDACTED]",
        }
    )


def diagnostic_query_result(result: QueryResult) -> QueryResult:
    return QueryResult(elements=tuple(diagnostic_snapshot(element) for element in result.elements))


def device_action_snapshot(identity: str) -> ElementSnapshot:
    """Build a non-element target record for device-level action receipts."""

    return ElementSnapshot(
        identity=identity,
        displayed=False,
        enabled=True,
        selected=False,
        checked=False,
        checkable=False,
        focusable=False,
        focused=False,
        editable=False,
        bounds=Rect(x=0, y=0, width=0, height=0),
    )


def trace_event(kind: TraceEventKind, name: str, fields: tuple[tuple[str, str], ...]) -> TraceEvent:
    return TraceEvent(
        kind=kind,
        name=name,
        fields=tuple(TraceField(name=field_name, value=value) for field_name, value in fields),
    )


class AsyncLocatorRoot:
    def __init__(
        self,
        device: AsyncDevice,
        package: str | None,
        application_generation: int | None = None,
    ) -> None:
        self.device = device
        self.package = package
        self.application_generation = application_generation

    def locator(self, selector: Selector) -> AsyncLocator:
        return AsyncLocator(
            device=self.device,
            selector=selector,
            package=self.package,
            application_generation=self.application_generation,
        )

    def get_by_text(self, value: TextValue, *, exact: bool = True) -> AsyncLocator:
        return self.locator(Selector.text(text_matcher(value, exact)))

    def get_by_label(self, value: str) -> AsyncLocator:
        return self.locator(Selector.label(text_matcher(value, True)))

    def get_by_placeholder(self, value: str) -> AsyncLocator:
        return self.locator(Selector.placeholder(value))

    def get_by_test_id(self, value: str) -> AsyncLocator:
        return self.locator(Selector.test_id(value))

    def get_by_resource_id(self, value: str) -> AsyncLocator:
        return self.locator(Selector.resource_id(value))

    def get_by_content_description(self, value: str) -> AsyncLocator:
        return self.locator(Selector.content_description(value))

    def get_by_role(
        self,
        role: Role,
        *,
        name: TextValue | None = None,
        exact: bool = True,
    ) -> AsyncLocator:
        matcher = None if name is None else text_matcher(name, exact)
        return self.locator(Selector.by_role(role, matcher))


class AsyncLocator:
    def __init__(
        self,
        device: AsyncDevice,
        selector: Selector,
        package: str | None,
        application_generation: int | None = None,
    ) -> None:
        self.device = device
        self.selector = selector
        self.package = package
        self.application_generation = application_generation

    @property
    def first(self) -> AsyncLocator:
        return self.nth(0)

    @property
    def last(self) -> AsyncLocator:
        return self.nth(-1)

    def nth(self, index: int) -> AsyncLocator:
        return AsyncLocator(
            self.device,
            self.selector.nth(index),
            self.package,
            self.application_generation,
        )

    def locator(self, selector: Selector) -> AsyncLocator:
        return AsyncLocator(
            self.device,
            self.selector.descendant(selector),
            self.package,
            self.application_generation,
        )

    def and_(self, other: AsyncLocator) -> AsyncLocator:
        self.validate_same_device(other)
        return AsyncLocator(
            self.device,
            self.selector.and_selector(other.selector),
            self.package,
            self.application_generation,
        )

    def or_(self, other: AsyncLocator) -> AsyncLocator:
        self.validate_same_device(other)
        return AsyncLocator(
            self.device,
            self.selector.or_selector(other.selector),
            self.package,
            self.application_generation,
        )

    def filter(
        self,
        *,
        has: AsyncLocator | None = None,
        has_not: AsyncLocator | None = None,
        has_text: TextValue | None = None,
        has_not_text: TextValue | None = None,
    ) -> AsyncLocator:
        selector = self.selector
        if has is not None:
            self.validate_same_device(has)
            selector = selector.has(has.selector)
        if has_not is not None:
            self.validate_same_device(has_not)
            selector = selector.has_not(has_not.selector)
        if has_text is not None:
            selector = selector.has_text(text_matcher(has_text, False))
        if has_not_text is not None:
            selector = selector.has_not_text(text_matcher(has_not_text, False))
        return AsyncLocator(
            self.device,
            selector,
            self.package,
            self.application_generation,
        )

    def validate_same_device(self, other: AsyncLocator) -> None:
        if other.device is not self.device:
            raise ValueError("locators must belong to the same device")
        if other.application_generation != self.application_generation:
            raise ValueError("locators must belong to the same application generation")
        if other.package != self.package:
            raise ValueError("locators must have the same application scope")

    def plan(self) -> LocatorPlan:
        self.device.validate_application_generation(
            self.application_generation,
            "locator",
        )
        try:
            return compile_selector(self.selector, self.package)
        except SelectorCompilationError as error:
            details = self.device.enrich_error(
                ErrorDetails(
                    code=ErrorCode.INVALID_SELECTOR,
                    api_name="locator",
                    message=str(error),
                    locator=self.selector.model_dump_json(),
                )
            )
            self.device.record_error(details)
            raise InvalidSelectorError(details) from error

    async def query_once(self, timeout: timedelta) -> QueryResult:
        plan = self.plan()
        started = monotonic()
        result = await self.device.backend.resolve(plan, timeout)
        self.device.tracing.record(
            trace_event(
                TraceEventKind.QUERY,
                "locator.query",
                (
                    ("locator", plan.description),
                    ("strategy", plan.strategy.value),
                    ("selector_ast", self.selector.model_dump_json()),
                    ("locator_plan", plan.model_dump_json()),
                    ("result", diagnostic_query_result(result).model_dump_json()),
                    ("count", str(len(result.elements))),
                    ("elapsed", str(monotonic() - started)),
                ),
            )
        )
        return result

    async def query(self, timeout: timedelta | None = None) -> QueryResult:
        plan = self.plan()
        selected_timeout = timeout if timeout is not None else self.device.timeouts.transport
        deadline = OperationDeadline.start(selected_timeout)
        delay = self.device.timeouts.retry.initial_delay
        call_log: list[CallLogEntry] = []
        while True:
            try:
                return await self.query_once(deadline.remaining())
            except RecoverableBackendError as error:
                call_log.append(
                    CallLogEntry(
                        message=error.failure.message,
                        elapsed=deadline.elapsed(),
                    )
                )
                if deadline.expired():
                    details = error_details(
                        code=ErrorCode.TIMEOUT,
                        api_name="locator.query",
                        message=f"timeout querying {plan.description}",
                        plan=plan,
                        elapsed=deadline.elapsed(),
                        call_log=tuple(call_log),
                    )
                    raise AppwrightTimeoutError(self.device.record_error(details)) from error
                await self.wait_before_retry(delay, deadline)
                delay = self.next_delay(delay)
            except BackendError as error:
                raise self.translate_backend_error(error, "locator.query", plan) from error

    def translate_backend_error(
        self,
        error: BackendError,
        api_name: str,
        plan: LocatorPlan,
    ) -> Exception:
        translated = translated_backend_error(error, api_name, plan)
        if isinstance(translated, AppwrightError):
            translated = type(translated)(self.device.enrich_error(translated.details))
            self.device.tracing.record(
                trace_event(
                    TraceEventKind.ERROR,
                    api_name,
                    (("details", translated.details.model_dump_json()),),
                )
            )
        return translated

    async def all(self) -> tuple[AsyncLocator, ...]:
        count = await self.count()
        return tuple(self.nth(index) for index in range(count))

    async def probe_all(
        self,
        timeout: timedelta | None = None,
    ) -> tuple[ElementSnapshot, ...]:
        """Return every current match after retrying transient backend failures."""

        selected_timeout = timeout if timeout is not None else self.device.timeouts.probe
        return (await self.query(selected_timeout)).elements

    async def probe(self, timeout: timedelta | None = None) -> ElementSnapshot | None:
        """Return the current strict match without waiting for application state."""

        elements = await self.probe_all(timeout)
        if not elements:
            return None
        if len(elements) > 1:
            return self.strict_element(
                QueryResult(elements=elements),
                "locator.probe",
                self.plan(),
            )
        return next(iter(elements))

    async def element_infos(self) -> tuple[ElementSnapshot, ...]:
        return await self.probe_all()

    async def count(self) -> int:
        return len(await self.probe_all())

    async def element_info(self) -> ElementSnapshot:
        element = await self.probe()
        if element is not None:
            return element
        return self.strict_element(
            QueryResult(elements=()),
            "locator.element_info",
            self.plan(),
        )

    async def is_visible(self) -> bool:
        element = await self.probe()
        return element is not None and element.displayed

    async def is_enabled(self) -> bool:
        element = await self.probe()
        return element is not None and element.enabled

    async def is_checked(self) -> bool:
        element = await self.probe()
        return element is not None and element.checked

    async def text_content(self) -> str:
        return (await self.element_info()).text

    async def accessible_name(self) -> str:
        return (await self.element_info()).accessible_name

    async def bounds(self) -> Rect:
        return (await self.element_info()).bounds

    def strict_element(
        self,
        result: QueryResult,
        api_name: str,
        plan: LocatorPlan,
        *,
        elapsed: timedelta | None = None,
        call_log: tuple[CallLogEntry, ...] = (),
    ) -> ElementSnapshot:
        count = len(result.elements)
        if count != 1:
            details = error_details(
                code=ErrorCode.STRICT_MODE,
                api_name=api_name,
                message=f"locator resolved to {count} elements",
                plan=plan,
                elapsed=elapsed,
                call_log=call_log,
                received=str(count),
                expected="1",
            )
            selected_details = self.device.record_error(details)
            raise StrictModeViolationError(selected_details)
        return result.elements[0]

    async def perform(
        self,
        request: ActionRequest,
        *,
        timeout: timedelta | None = None,
        auto_scroll: bool = False,
    ) -> ActionReceipt:
        plan = self.plan()
        selected_timeout = timeout if timeout is not None else self.device.timeouts.action
        deadline = OperationDeadline.start(selected_timeout)
        delay = self.device.timeouts.retry.initial_delay
        call_log: list[CallLogEntry] = []
        last_result: QueryResult | None = None
        auto_scroll_attempted = False
        while True:
            if deadline.expired():
                if last_result is not None and len(last_result.elements) > 1:
                    count = len(last_result.elements)
                    details = error_details(
                        code=ErrorCode.STRICT_MODE,
                        api_name=f"locator.{request.kind.value}",
                        message=(
                            f"timeout waiting for {plan.description} to resolve uniquely; "
                            f"received {count} elements"
                        ),
                        plan=plan,
                        elapsed=deadline.elapsed(),
                        call_log=tuple(call_log),
                        expected="1",
                        received=str(count),
                    )
                    raise StrictModeViolationError(self.device.record_error(details))
                message = f"timeout waiting to {request.kind.value} {plan.description}"
                details = error_details(
                    code=ErrorCode.TIMEOUT,
                    api_name=f"locator.{request.kind.value}",
                    message=message,
                    plan=plan,
                    elapsed=deadline.elapsed(),
                    call_log=tuple(call_log),
                )
                selected_details = self.device.record_error(details)
                raise AppwrightTimeoutError(selected_details)
            remaining = deadline.remaining()
            try:
                result = await self.device.backend.resolve(plan, remaining)
                last_result = result
            except RecoverableBackendError as error:
                call_log.append(
                    CallLogEntry(
                        message=error.failure.message,
                        elapsed=deadline.elapsed(),
                    )
                )
                await self.wait_before_retry(delay, deadline)
                delay = self.next_delay(delay)
                continue
            except BackendError as error:
                raise self.translate_backend_error(
                    error,
                    f"locator.{request.kind.value}",
                    plan,
                ) from error
            if len(result.elements) == 0:
                call_log.append(
                    CallLogEntry(
                        message="locator resolved to no elements",
                        elapsed=deadline.elapsed(),
                    )
                )
                if auto_scroll and not auto_scroll_attempted:
                    auto_scroll_attempted = True
                    await self.scroll_into_view(timeout=deadline.remaining())
            elif len(result.elements) > 1:
                call_log.append(
                    CallLogEntry(
                        message=f"locator resolved to {len(result.elements)} elements",
                        elapsed=deadline.elapsed(),
                    )
                )
            else:
                element = result.elements[0]
                problem = None if request.force else actionability_problem(element, request.kind)
                if problem is not None:
                    call_log.append(
                        CallLogEntry(
                            message=problem,
                            elapsed=deadline.elapsed(),
                        )
                    )
                    if (
                        auto_scroll
                        and not auto_scroll_attempted
                        and problem in {"element is not visible", "element has no visible area"}
                    ):
                        auto_scroll_attempted = True
                        await self.scroll_into_view(timeout=deadline.remaining())
                else:
                    try:
                        stable = request.force or await self.check_stability(
                            element,
                            plan,
                            deadline,
                        )
                    except BackendError as error:
                        raise self.translate_backend_error(
                            error,
                            f"locator.{request.kind.value}",
                            plan,
                        ) from error
                    if stable:
                        try:
                            if deadline.expired():
                                continue
                            receipt = await self.device.backend.dispatch(
                                plan,
                                request,
                                element,
                                deadline.remaining(),
                            )
                            self.device.tracing.record(
                                trace_event(
                                    TraceEventKind.ACTION,
                                    f"locator.{request.kind.value}",
                                    (
                                        ("locator", plan.description),
                                        ("strategy", plan.strategy.value),
                                        (
                                            "receipt",
                                            receipt.model_copy(
                                                update={
                                                    "pre_action": diagnostic_snapshot(
                                                        receipt.pre_action
                                                    )
                                                }
                                            ).model_dump_json(),
                                        ),
                                        ("elapsed", str(deadline.elapsed().total_seconds())),
                                    ),
                                )
                            )
                            return receipt
                        except IndeterminateActionBackendError as error:
                            receipt = error.receipt
                            may_retry_unknown = (
                                request.kind in {ActionKind.CHECK, ActionKind.UNCHECK}
                                and receipt.action is request.kind
                                and receipt.dispatch_state is DispatchState.UNKNOWN
                            )
                            if may_retry_unknown:
                                call_log.append(
                                    CallLogEntry(
                                        message=(
                                            f"{request.kind.value} dispatch outcome was unknown; "
                                            "re-observing idempotent state"
                                        ),
                                        elapsed=deadline.elapsed(),
                                    )
                                )
                            else:
                                details = error_details(
                                    code=ErrorCode.INDETERMINATE_ACTION,
                                    api_name=f"locator.{request.kind.value}",
                                    message=(
                                        f"{request.kind.value} dispatch outcome is unknown; "
                                        "the action was not replayed"
                                    ),
                                    plan=plan,
                                    elapsed=deadline.elapsed(),
                                    call_log=tuple(call_log),
                                    appium_command=error.failure.appium_command,
                                )
                                safe_receipt = receipt.model_copy(
                                    update={"pre_action": diagnostic_snapshot(receipt.pre_action)}
                                )
                                raise IndeterminateActionError(
                                    self.device.record_error(details),
                                    safe_receipt,
                                ) from error
                        except RecoverableBackendError as error:
                            call_log.append(
                                CallLogEntry(
                                    message=error.failure.message,
                                    elapsed=deadline.elapsed(),
                                )
                            )
                        except BackendError as error:
                            if error.failure.kind is BackendFailureKind.MATCH_COUNT:
                                call_log.append(
                                    CallLogEntry(
                                        message=error.failure.message,
                                        elapsed=deadline.elapsed(),
                                    )
                                )
                            else:
                                raise self.translate_backend_error(
                                    error,
                                    f"locator.{request.kind.value}",
                                    plan,
                                ) from error
                    else:
                        call_log.append(
                            CallLogEntry(
                                message="element is not stable",
                                elapsed=deadline.elapsed(),
                            )
                        )
            await self.wait_before_retry(delay, deadline)
            delay = self.next_delay(delay)

    async def check_stability(
        self,
        first: ElementSnapshot,
        plan: LocatorPlan,
        deadline: OperationDeadline,
    ) -> bool:
        remaining = deadline.remaining().total_seconds()
        stability_seconds = self.device.timeouts.stability.total_seconds()
        if remaining <= stability_seconds:
            return False
        await asyncio.sleep(stability_seconds)
        try:
            result = await self.device.backend.resolve(plan, deadline.remaining())
        except RecoverableBackendError:
            return False
        return len(result.elements) == 1 and stable_snapshots(first, result.elements[0])

    async def wait_before_retry(
        self,
        delay: timedelta,
        deadline: OperationDeadline,
    ) -> None:
        seconds = min(delay.total_seconds(), deadline.remaining().total_seconds())
        if seconds > 0:
            await asyncio.sleep(seconds)

    def next_delay(self, delay: timedelta) -> timedelta:
        policy = self.device.timeouts.retry
        seconds = min(
            delay.total_seconds() * policy.multiplier,
            policy.maximum_delay.total_seconds(),
        )
        return timedelta(seconds=seconds)

    async def tap(
        self,
        *,
        force: bool = False,
        trial: bool = False,
        auto_scroll: bool = False,
        timeout: timedelta | None = None,
    ) -> None:
        await self.perform(
            ActionRequest(kind=ActionKind.TAP, force=force, trial=trial),
            timeout=timeout,
            auto_scroll=auto_scroll,
        )

    async def double_tap(
        self,
        *,
        force: bool = False,
        trial: bool = False,
        auto_scroll: bool = False,
        timeout: timedelta | None = None,
    ) -> None:
        await self.perform(
            ActionRequest(kind=ActionKind.DOUBLE_TAP, force=force, trial=trial),
            timeout=timeout,
            auto_scroll=auto_scroll,
        )

    async def long_press(
        self,
        *,
        force: bool = False,
        trial: bool = False,
        auto_scroll: bool = False,
        timeout: timedelta | None = None,
    ) -> None:
        await self.perform(
            ActionRequest(kind=ActionKind.LONG_PRESS, force=force, trial=trial),
            timeout=timeout,
            auto_scroll=auto_scroll,
        )

    async def fill(
        self,
        value: str,
        *,
        force: bool = False,
        trial: bool = False,
        auto_scroll: bool = False,
        timeout: timedelta | None = None,
    ) -> None:
        await self.perform(
            ActionRequest(
                kind=ActionKind.FILL,
                text=value,
                force=force,
                trial=trial,
            ),
            timeout=timeout,
            auto_scroll=auto_scroll,
        )

    async def clear(
        self,
        *,
        force: bool = False,
        trial: bool = False,
        auto_scroll: bool = False,
        timeout: timedelta | None = None,
    ) -> None:
        await self.perform(
            ActionRequest(kind=ActionKind.CLEAR, force=force, trial=trial),
            timeout=timeout,
            auto_scroll=auto_scroll,
        )

    async def press(
        self,
        key: str,
        *,
        force: bool = False,
        trial: bool = False,
        auto_scroll: bool = False,
        timeout: timedelta | None = None,
    ) -> None:
        await self.perform(
            ActionRequest(
                kind=ActionKind.PRESS,
                key=key,
                force=force,
                trial=trial,
            ),
            timeout=timeout,
            auto_scroll=auto_scroll,
        )

    async def check(
        self,
        *,
        force: bool = False,
        trial: bool = False,
        auto_scroll: bool = False,
        timeout: timedelta | None = None,
    ) -> None:
        await self.perform(
            ActionRequest(kind=ActionKind.CHECK, force=force, trial=trial),
            timeout=timeout,
            auto_scroll=auto_scroll,
        )

    async def uncheck(
        self,
        *,
        force: bool = False,
        trial: bool = False,
        auto_scroll: bool = False,
        timeout: timedelta | None = None,
    ) -> None:
        await self.perform(
            ActionRequest(kind=ActionKind.UNCHECK, force=force, trial=trial),
            timeout=timeout,
            auto_scroll=auto_scroll,
        )

    async def swipe(
        self,
        direction: Direction,
        *,
        percent: float = 0.75,
        force: bool = False,
        trial: bool = False,
        timeout: timedelta | None = None,
    ) -> None:
        await self.perform(
            ActionRequest(
                kind=ActionKind.SWIPE,
                direction=direction,
                percent=percent,
                force=force,
                trial=trial,
            ),
            timeout=timeout,
        )

    async def scroll(
        self,
        direction: Direction,
        *,
        percent: float = 0.75,
        force: bool = False,
        trial: bool = False,
        timeout: timedelta | None = None,
    ) -> None:
        await self.perform(
            ActionRequest(
                kind=ActionKind.SCROLL,
                direction=direction,
                percent=percent,
                force=force,
                trial=trial,
            ),
            timeout=timeout,
        )

    async def scroll_into_view(
        self,
        *,
        timeout: timedelta | None = None,
    ) -> OperationResult:
        """Explicitly ask the backend to bring this locator into the viewport."""

        selected_timeout = timeout if timeout is not None else self.device.timeouts.action
        deadline = OperationDeadline.start(selected_timeout)
        plan = self.plan()
        result, receipt = await self.device.dispatch_device_action(
            lambda: self.device.backend.scroll_into_view(
                plan,
                deadline.remaining(),
            ),
            action=ActionKind.SCROLL,
            api_name="locator.scroll_into_view",
            locator=plan.description,
            pre_action=device_action_snapshot("locator-scroll"),
            deadline=deadline,
        )
        del receipt
        return result

    async def drag_to(
        self,
        target: AsyncLocator,
        *,
        force: bool = False,
        trial: bool = False,
        timeout: timedelta | None = None,
    ) -> None:
        self.validate_same_device(target)
        selected_timeout = timeout if timeout is not None else self.device.timeouts.action
        deadline = OperationDeadline.start(selected_timeout)
        source_receipt = await self.perform(
            ActionRequest(kind=ActionKind.DRAG_TO, force=force, trial=True),
            timeout=deadline.remaining(),
        )
        await target.perform(
            ActionRequest(kind=ActionKind.DRAG_TO, force=force, trial=True),
            timeout=deadline.remaining(),
        )
        if trial:
            return
        await self.device.dispatch_device_action(
            lambda: self.device.backend.drag(
                self.plan(),
                target.plan(),
                deadline.remaining(),
            ),
            action=ActionKind.DRAG_TO,
            api_name="locator.drag_to",
            locator=self.plan().description,
            pre_action=source_receipt.pre_action,
            deadline=deadline,
        )

    async def screenshot(
        self,
        path: Path | None = None,
        *,
        timeout: timedelta | None = None,
    ) -> Screenshot:
        selected_timeout = timeout if timeout is not None else self.device.timeouts.action
        deadline = OperationDeadline.start(selected_timeout)
        await self.perform(
            ActionRequest(kind=ActionKind.SCREENSHOT, trial=True),
            timeout=deadline.remaining(),
        )
        screenshot = await self.device.backend.element_screenshot(
            self.plan(), path, deadline.remaining()
        )
        self.device.attach_artifact(
            TraceArtifactKind.SCREENSHOT,
            screenshot.content,
            "png",
            "image/png",
        )
        return screenshot

    async def wait_for(
        self,
        state: WaitState = WaitState.VISIBLE,
        *,
        timeout: timedelta | None = None,
    ) -> None:
        selected_timeout = timeout if timeout is not None else self.device.timeouts.wait
        deadline = OperationDeadline.start(selected_timeout)
        delay = self.device.timeouts.retry.initial_delay
        call_log: list[CallLogEntry] = []
        last_result: QueryResult | None = None
        while True:
            if deadline.expired():
                plan = self.plan()
                if last_result is not None and len(last_result.elements) > 1:
                    self.strict_element(
                        last_result,
                        "locator.wait_for",
                        plan,
                        elapsed=deadline.elapsed(),
                        call_log=tuple(call_log),
                    )
                details = error_details(
                    code=ErrorCode.TIMEOUT,
                    api_name="locator.wait_for",
                    message=f"timeout waiting for locator to be {state.value}",
                    plan=plan,
                    elapsed=deadline.elapsed(),
                    call_log=tuple(call_log),
                    expected=state.value,
                )
                selected_details = self.device.record_error(details)
                raise AppwrightTimeoutError(selected_details)
            try:
                result = await self.query_once(deadline.remaining())
                last_result = result
                elements = result.elements
                observation_valid = True
            except RecoverableBackendError as error:
                elements = ()
                observation_valid = False
                call_log.append(
                    CallLogEntry(
                        message=error.failure.message,
                        elapsed=deadline.elapsed(),
                    )
                )
            except BackendError as error:
                raise self.translate_backend_error(
                    error, "locator.wait_for", self.plan()
                ) from error
            unique = observation_valid and len(elements) == 1
            attached = unique
            visible = unique and next(iter(elements)).displayed
            satisfied = observation_valid and (
                (state is WaitState.ATTACHED and attached)
                or (state is WaitState.DETACHED and not elements)
                or (state is WaitState.VISIBLE and visible)
                or (state is WaitState.HIDDEN and (not elements or (unique and not visible)))
            )
            if satisfied:
                return
            if len(elements) > 1:
                call_log.append(
                    CallLogEntry(
                        message=f"locator resolved to {len(elements)} elements",
                        elapsed=deadline.elapsed(),
                    )
                )
            if deadline.expired():
                plan = self.plan()
                if len(elements) > 1:
                    self.strict_element(
                        QueryResult(elements=elements),
                        "locator.wait_for",
                        plan,
                        elapsed=deadline.elapsed(),
                        call_log=tuple(call_log),
                    )
                details = error_details(
                    code=ErrorCode.TIMEOUT,
                    api_name="locator.wait_for",
                    message=f"timeout waiting for locator to be {state.value}",
                    plan=plan,
                    elapsed=deadline.elapsed(),
                    call_log=tuple(call_log),
                    expected=state.value,
                )
                selected_details = self.device.record_error(details)
                raise AppwrightTimeoutError(selected_details)
            await self.wait_before_retry(delay, deadline)
            delay = self.next_delay(delay)


class AsyncKeyboard:
    def __init__(self, device: AsyncDevice) -> None:
        self.device = device

    async def press(self, key: Key, *, timeout: timedelta | None = None) -> None:
        selected_timeout = timeout if timeout is not None else self.device.timeouts.action
        deadline = OperationDeadline.start(selected_timeout)
        await self.device.dispatch_device_action(
            lambda: self.device.backend.press_key(key, deadline.remaining()),
            action=ActionKind.PRESS,
            api_name="keyboard.press",
            locator=f"key={key.value!r}",
            pre_action=device_action_snapshot("device-keyboard"),
            deadline=deadline,
        )


class AsyncTouchscreen:
    def __init__(self, device: AsyncDevice) -> None:
        self.device = device

    async def tap(self, point: Point, *, timeout: timedelta | None = None) -> None:
        selected_timeout = timeout if timeout is not None else self.device.timeouts.action
        deadline = OperationDeadline.start(selected_timeout)
        await self.device.dispatch_device_action(
            lambda: self.device.backend.tap_point(point, deadline.remaining()),
            action=ActionKind.TAP,
            api_name="touchscreen.tap",
            locator=f"point=({point.x},{point.y})",
            pre_action=device_action_snapshot("device-touchscreen"),
            deadline=deadline,
        )


class AsyncScreen(AsyncLocatorRoot):
    pass


class AsyncApp(AsyncLocatorRoot):
    def __init__(self, device: AsyncDevice, package: str, application_generation: int) -> None:
        super().__init__(
            device=device,
            package=package,
            application_generation=application_generation,
        )
        self.package_name = package
        self.application_generation = application_generation

    def ensure_current(self, api_name: str) -> None:
        self.device.validate_application_generation(self.application_generation, api_name)

    async def activate(self) -> None:
        self.ensure_current("app.activate")
        await self.device.call_backend(
            lambda: self.device.backend.activate_app(
                self.package_name, self.device.timeouts.action
            ),
            "app.activate",
        )

    async def terminate(self) -> None:
        self.ensure_current("app.terminate")
        await self.device.call_backend(
            lambda: self.device.backend.terminate_app(
                self.package_name, self.device.timeouts.action
            ),
            "app.terminate",
        )

    async def clear_data(self) -> None:
        self.ensure_current("app.clear_data")
        await self.device.call_backend(
            lambda: self.device.backend.clear_app(self.package_name, self.device.timeouts.action),
            "app.clear_data",
        )

    async def reset(self) -> None:
        await self.terminate()
        await self.clear_data()
        await self.activate()

    async def screenshot(self, path: Path | None = None) -> Screenshot:
        self.ensure_current("app.screenshot")
        return await self.device.screenshot(path)

    async def close(self) -> None:
        await self.terminate()
        self.device.close_application_handle(self.application_generation)


class AsyncDevice:
    def __init__(
        self,
        backend: AutomationBackend,
        selector: AndroidDeviceSelector,
        timeouts: AppiumTimeouts,
        capabilities: tuple[AdditionalCapability, ...] = (),
    ) -> None:
        self.backend = backend
        self.selector = selector
        self.timeouts = timeouts
        self.tracing = TraceRecorder()
        access_key = backend.server.security.access_key
        if access_key is not None:
            self.tracing.register_secret(access_key.get_secret_value())
        for capability in capabilities:
            self.tracing.register_capability(capability)
        self.screen = AsyncScreen(device=self, package=None)
        self.keyboard = AsyncKeyboard(self)
        self.touchscreen = AsyncTouchscreen(self)
        self.active_app: AsyncApp | None = None
        self.application_generation = 0
        self.closed = False

    def validate_application_generation(
        self,
        application_generation: int | None,
        api_name: str,
    ) -> None:
        if self.closed:
            raise TargetClosedError(
                error_details(
                    code=ErrorCode.TARGET_CLOSED,
                    api_name=api_name,
                    message="device is closed",
                )
            )
        if (
            application_generation is not None
            and application_generation != self.application_generation
        ):
            raise TargetClosedError(
                error_details(
                    code=ErrorCode.TARGET_CLOSED,
                    api_name=api_name,
                    message="application handle was replaced by a later launch",
                )
            )

    def close_application_handle(self, application_generation: int | None) -> None:
        self.validate_application_generation(application_generation, "app.close")
        self.application_generation += 1
        self.active_app = None

    def enrich_error(self, details: ErrorDetails) -> ErrorDetails:
        trace_path = self.tracing.planned_output_path or self.tracing.output_path
        screenshot_path = None if trace_path is None else trace_path.parent / "failure.png"
        redactor = self.tracing.redactor
        log_excerpt = tuple(
            record.model_copy(update={"message": redactor.sanitize_text(record.message)})
            for record in self.backend.server_logs[-50:]
        )
        call_log = tuple(
            entry.model_copy(update={"message": redactor.sanitize_text(entry.message)})
            for entry in details.call_log
        )
        return details.model_copy(
            update={
                "message": redactor.sanitize_text(details.message),
                "locator": (
                    None if details.locator is None else redactor.sanitize_text(details.locator)
                ),
                "expected": (
                    None if details.expected is None else redactor.sanitize_text(details.expected)
                ),
                "received": (
                    None if details.received is None else redactor.sanitize_text(details.received)
                ),
                "call_log": call_log,
                "trace_path": trace_path,
                "screenshot_path": screenshot_path,
                "appium_server_log": log_excerpt,
            }
        )

    def record_error(self, details: ErrorDetails) -> ErrorDetails:
        selected_details = self.enrich_error(details)
        self.tracing.record(
            trace_event(
                TraceEventKind.ERROR,
                selected_details.api_name,
                (("details", selected_details.model_dump_json()),),
            )
        )
        return selected_details

    async def dispatch_device_action(
        self,
        operation: Callable[[], Awaitable[BackendResult]],
        *,
        action: ActionKind,
        api_name: str,
        locator: str,
        pre_action: ElementSnapshot,
        deadline: OperationDeadline,
    ) -> tuple[BackendResult, ActionReceipt]:
        """Conservatively receipt a legacy non-element backend command."""

        started_at = datetime.now(UTC)
        pending = ActionReceipt(
            action=action,
            locator=locator,
            replay_safety=replay_safety_for(action),
            stage=OperationStage.DISPATCH,
            dispatch_state=DispatchState.NOT_DISPATCHED,
            started_at=started_at,
            pre_action=pre_action,
        )
        try:
            result = await operation()
        except BackendError as error:
            unknown = pending.model_copy(update={"dispatch_state": DispatchState.UNKNOWN})
            safe_unknown = unknown.model_copy(
                update={"pre_action": diagnostic_snapshot(unknown.pre_action)}
            )
            details = error_details(
                code=ErrorCode.INDETERMINATE_ACTION,
                api_name=api_name,
                message=(
                    f"{api_name} dispatch outcome is unknown; the action was not replayed: "
                    f"{error.failure.message}"
                ),
                locator=locator,
                elapsed=deadline.elapsed(),
                appium_command=error.failure.appium_command,
            )
            raise IndeterminateActionError(
                self.record_error(details),
                safe_unknown,
            ) from error

        dispatched = pending.model_copy(
            update={
                "dispatch_state": DispatchState.DISPATCHED,
                "dispatched_at": datetime.now(UTC),
            }
        )
        self.tracing.record(
            trace_event(
                TraceEventKind.ACTION,
                api_name,
                (
                    ("locator", locator),
                    (
                        "receipt",
                        dispatched.model_copy(
                            update={"pre_action": diagnostic_snapshot(dispatched.pre_action)}
                        ).model_dump_json(),
                    ),
                    ("elapsed", str(deadline.elapsed().total_seconds())),
                ),
            )
        )
        return result, dispatched

    async def call_backend(
        self,
        operation: Callable[[], Awaitable[BackendResult]],
        api_name: str,
    ) -> BackendResult:
        try:
            return await operation()
        except BackendError as error:
            translated = translated_backend_error(error, api_name)
            if isinstance(translated, AppwrightError):
                translated = type(translated)(self.enrich_error(translated.details))
                self.tracing.record(
                    trace_event(
                        TraceEventKind.ERROR,
                        api_name,
                        (("details", translated.details.model_dump_json()),),
                    )
                )
            raise translated from error

    def attach_artifact(
        self,
        kind: TraceArtifactKind,
        content: bytes,
        extension: str,
        media_type: str,
    ) -> None:
        sequence = self.tracing.artifact_count + 1
        self.tracing.attach(
            TraceArtifact(
                kind=kind,
                name=f"{kind.value}-{sequence}.{extension}",
                media_type=media_type,
                content=content,
            )
        )

    async def launch_app(
        self,
        application: ApplicationOptions | None = None,
        *,
        package: str | None = None,
        app_path: Path | None = None,
        clear_data: bool = False,
    ) -> AsyncApp:
        self.validate_application_generation(None, "device.launch_app")
        if application is not None and (package is not None or app_path is not None or clear_data):
            raise ValueError(
                "application options cannot be combined with launch_app convenience arguments"
            )
        if application is not None:
            selected_application = application
        else:
            if package is None:
                raise ValueError("launch_app requires an application package")
            selected_application = ApplicationOptions(
                package=package,
                app_path=app_path,
                clear_data=clear_data,
            )
        if selected_application.app_path is not None:
            await self.install_app(selected_application.app_path)
        if selected_application.clear_data:
            await self.call_backend(
                lambda: self.backend.clear_app(
                    selected_application.package,
                    self.timeouts.action,
                ),
                "device.launch_app",
            )
        await self.call_backend(
            lambda: self.backend.activate_app(
                selected_application.package,
                self.timeouts.action,
            ),
            "device.launch_app",
        )
        self.application_generation += 1
        selected_package = selected_application.package
        self.active_app = AsyncApp(
            self,
            selected_package,
            self.application_generation,
        )
        self.tracing.record(
            trace_event(
                TraceEventKind.SESSION,
                "device.launch_app",
                (
                    ("package", selected_package),
                    ("app_path", str(selected_application.app_path or "")),
                ),
            )
        )
        return self.active_app

    async def screenshot(self, path: Path | None = None) -> Screenshot:
        screenshot = await self.call_backend(
            lambda: self.backend.screenshot(path, self.timeouts.action),
            "device.screenshot",
        )
        self.attach_artifact(
            TraceArtifactKind.SCREENSHOT,
            screenshot.content,
            "png",
            "image/png",
        )
        return screenshot

    async def hierarchy(self) -> HierarchySource:
        hierarchy = await self.call_backend(
            lambda: self.backend.observe(self.timeouts.action),
            "device.hierarchy",
        )
        self.attach_artifact(
            TraceArtifactKind.HIERARCHY,
            hierarchy.content.encode(),
            "xml",
            "application/xml",
        )
        return hierarchy

    async def server_logs(self) -> tuple[ServerLogRecord, ...]:
        records = await self.call_backend(
            self.backend.read_server_logs,
            "device.server_logs",
        )
        return tuple(
            record.model_copy(
                update={"message": self.tracing.redactor.sanitize_text(record.message)}
            )
            for record in records
        )

    async def install_app(
        self,
        path: Path,
        *,
        replace: bool = True,
        grant_permissions: bool = False,
        timeout: timedelta | None = None,
    ) -> OperationResult:
        selected_timeout = timeout if timeout is not None else self.timeouts.action
        request = InstallApplicationRequest(
            path=path,
            replace=replace,
            grant_permissions=grant_permissions,
        )
        return await self.call_backend(
            lambda: self.backend.install_app(request, selected_timeout),
            "device.install_app",
        )

    async def uninstall_app(
        self,
        package: str,
        *,
        keep_data: bool = False,
        timeout: timedelta | None = None,
    ) -> OperationResult:
        selected_timeout = timeout if timeout is not None else self.timeouts.action
        request = UninstallApplicationRequest(package=package, keep_data=keep_data)
        return await self.call_backend(
            lambda: self.backend.uninstall_app(request, selected_timeout),
            "device.uninstall_app",
        )

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            await self.backend.close()
        finally:
            if self.backend.server_logs:
                content = "\n".join(
                    record.model_dump_json() for record in self.backend.server_logs
                ).encode()
                self.attach_artifact(
                    TraceArtifactKind.SERVER_LOG,
                    content,
                    "jsonl",
                    "application/x-ndjson",
                )


class AsyncAndroid:
    def __init__(self, backend_factory: BackendFactory) -> None:
        self.backend_factory = backend_factory
        self.devices_list: list[AsyncDevice] = []

    async def devices(self) -> tuple[DeviceInfo, ...]:
        return await discover_android_devices()

    async def connect(
        self,
        options: AndroidConnectionOptions | None = None,
        *,
        serial: str | None = None,
        server: AppiumServer | None = None,
        timeouts: AppiumTimeouts | None = None,
        capabilities: tuple[AdditionalCapability, ...] = (),
    ) -> AsyncDevice:
        if options is not None and (
            serial is not None or server is not None or timeouts is not None or capabilities
        ):
            raise ValueError(
                "connection options cannot be combined with connect convenience arguments"
            )
        selected_server = (
            options.server
            if options is not None
            else (server if server is not None else AppiumServer.local())
        )
        selected_timeouts = (
            options.timeouts
            if options is not None
            else (timeouts if timeouts is not None else AppiumTimeouts())
        )
        selector = options.selector if options is not None else AndroidDeviceSelector(serial=serial)
        selected_capabilities = options.capabilities if options is not None else capabilities
        if (
            selector.serial is None
            and selected_server.mode is ServerMode.LOCAL
            and self.backend_factory is AppiumBackend
        ):
            discovered = await discover_android_devices()
            online = tuple(device for device in discovered if device.state is DeviceState.ONLINE)
            if len(online) != 1:
                message = (
                    "no online Android device was found"
                    if not online
                    else "multiple Android devices are online; select one by serial"
                )
                raise DeviceNotFoundError(
                    error_details(
                        code=ErrorCode.DEVICE_NOT_FOUND,
                        api_name="android.connect",
                        message=message,
                        received=str(len(online)),
                        expected="1",
                    )
                )
            selector = AndroidDeviceSelector(serial=online[0].serial)
        backend = self.backend_factory(selected_server)
        try:
            await backend.start(selected_timeouts.server_start)
            await backend.create_session(
                AndroidSessionOptions(
                    device=selector,
                    timeouts=selected_timeouts,
                    capabilities=selected_capabilities,
                )
            )
        except BackendError as error:
            with suppress(BaseException):
                await asyncio.shield(backend.close())
            raise AppiumUnavailableError(
                error_details(
                    code=ErrorCode.APPIUM_UNAVAILABLE,
                    api_name="android.connect",
                    message=error.failure.message,
                )
            ) from error
        except BaseException:
            with suppress(BaseException):
                await asyncio.shield(backend.close())
            raise
        device = AsyncDevice(
            backend=backend,
            selector=selector,
            timeouts=selected_timeouts,
            capabilities=selected_capabilities,
        )
        session_capabilities = backend.session_capabilities
        if session_capabilities is not None:
            redacted_capabilities = session_capabilities.redacted()
            device.tracing.seed_artifact(
                TraceArtifact(
                    kind=TraceArtifactKind.CAPABILITIES,
                    name="session-capabilities.json",
                    media_type="application/json",
                    content=redacted_capabilities.model_dump_json(indent=2).encode(),
                )
            )
        device.tracing.seed_event(
            trace_event(
                TraceEventKind.SESSION,
                "android.connect",
                (("serial", selector.serial or ""),),
            )
        )
        self.devices_list.append(device)
        return device

    async def close(self) -> None:
        errors: list[BaseException] = []
        for device in tuple(self.devices_list):
            try:
                await device.close()
            except BaseException as error:
                errors.append(error)
        self.devices_list.clear()
        if errors:
            raise BaseExceptionGroup("one or more Android devices failed to close", errors)


class AsyncAppwright:
    def __init__(self, backend_factory: BackendFactory = AppiumBackend) -> None:
        self.backend_factory = backend_factory
        self.android = AsyncAndroid(backend_factory)
        self.closed = False

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.android.close()


class AsyncAppwrightContextManager:
    def __init__(self, backend_factory: BackendFactory = AppiumBackend) -> None:
        self.backend_factory = backend_factory
        self.appwright: AsyncAppwright | None = None

    async def __aenter__(self) -> AsyncAppwright:
        self.appwright = AsyncAppwright(self.backend_factory)
        return self.appwright

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        appwright = self.appwright
        if appwright is not None:
            await appwright.close()


def async_appwright(
    backend_factory: BackendFactory = AppiumBackend,
) -> AsyncAppwrightContextManager:
    return AsyncAppwrightContextManager(backend_factory)
