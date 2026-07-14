"""Bound asynchronous controls and integrated mobile screen orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from threading import Lock
from typing import Any, TypeVar, cast, overload

from appwright.core.runtime import AsyncApp, AsyncLocator, AsyncLocatorRoot
from appwright.models.data import ActionRequest, ElementSnapshot, QueryResult, Rect, Screenshot
from appwright.models.enums import ActionKind, Direction, Key
from appwright.observations import Observation, ObservationEngine
from appwright.operations import ActionReceipt, OperationDeadline
from appwright.screens.elements import ControlKind, ElementDescriptor
from appwright.screens.errors import (
    LifecycleTimeoutError,
    TransitionFailureError,
    TransitionTimeoutError,
)
from appwright.screens.interruptions import InterruptionManager, InterruptionType
from appwright.screens.model import AsyncInterruption, AsyncScreen, DeviceScope
from appwright.screens.recovery import BackRecovery, RecoveryEngine
from appwright.screens.targets import (
    AsyncScreenDefinition,
    ScreenChoice,
    ScreenDefinition,
    ScreenTarget,
)
from appwright.screens.transitions import ScreenTimeoutError, TransitionEngine

ScreenT = TypeVar("ScreenT", bound=AsyncScreenDefinition)
AnyScreenT = TypeVar("AnyScreenT", bound=ScreenDefinition)
AppControlT = TypeVar("AppControlT")
DeviceControlT = TypeVar("DeviceControlT")
ResultT = TypeVar("ResultT")
DEVICE_SESSION_KERNEL_ATTRIBUTE = "appwright_mobile_session_kernel"
mobile_session_kernel_creation_lock = Lock()


def lifecycle_task_set() -> set[asyncio.Task[Any]]:
    return set()


def _require_async_screen_type(screen_type: object) -> None:
    if not isinstance(screen_type, type) or not issubclass(screen_type, AsyncScreen):
        raise TypeError("async mobile operations require an AsyncScreen type")


def _require_async_target(
    target: type[Any] | ScreenTarget[Any],
) -> None:
    if isinstance(target, ScreenTarget):
        for screen_type in target.screens:
            _require_async_screen_type(screen_type)
        return
    _require_async_screen_type(target)


def _require_async_interruption_type(interruption_type: object) -> None:
    if not isinstance(interruption_type, type) or not issubclass(
        interruption_type,
        AsyncInterruption,
    ):
        raise TypeError("async mobile interruptions require an AsyncInterruption type")


@dataclass(eq=False, slots=True)
class LifecycleLease:
    """Explicit authority to nest work inside one active device lifecycle."""

    coordinator: AsyncLifecycleCoordinator
    deadline: OperationDeadline
    parent: LifecycleLease | None = None
    owner_task: asyncio.Task[Any] | None = None
    active: bool = True
    body_done: bool = False
    live_tasks: set[asyncio.Task[Any]] = field(default_factory=lifecycle_task_set)
    action_receipt: ActionReceipt | None = None
    lock_owned: bool = False

    @property
    def root(self) -> LifecycleLease:
        lease = self
        while lease.parent is not None:
            lease = lease.parent
        return lease

    def is_active(self) -> bool:
        lease: LifecycleLease | None = self
        while lease is not None:
            if not lease.active:
                return False
            lease = lease.parent
        return True


class AsyncLifecycleCoordinator:
    """Serialize typed mobile operations while allowing structural nested actions."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.context: ContextVar[LifecycleLease | None] = ContextVar(
            f"appwright_mobile_lifecycle_{id(self)}",
            default=None,
        )
        self.cancelled_transition_receipt: ActionReceipt | None = None

    @property
    def current_lease(self) -> LifecycleLease | None:
        lease = self.context.get()
        return lease if lease is not None and lease.is_active() else None

    @property
    def current_deadline(self) -> OperationDeadline | None:
        lease = self.current_lease
        return None if lease is None else lease.deadline

    def authorized_parent(
        self,
        lease: LifecycleLease | None = None,
    ) -> LifecycleLease | None:
        current = self.current_lease
        current_task = asyncio.current_task()
        if current is not None and current.owner_task is current_task:
            return current
        if lease is not None and lease.coordinator is self and lease.is_active():
            return lease
        return None

    def root_deadline(
        self,
        timeout: timedelta,
        *,
        lease: LifecycleLease | None = None,
    ) -> OperationDeadline:
        parent = self.authorized_parent(lease)
        return (
            OperationDeadline.start(timeout) if parent is None else parent.deadline.child(timeout)
        )

    async def acquire_root(self, deadline: OperationDeadline, name: str) -> None:
        remaining = deadline.remaining().total_seconds()
        if remaining <= 0:
            raise LifecycleTimeoutError(name, deadline)
        timeout_context = asyncio.timeout(remaining)
        try:
            async with timeout_context:
                await self.lock.acquire()
        except TimeoutError as error:
            if timeout_context.expired():
                raise LifecycleTimeoutError(name, deadline) from error
            raise
        if deadline.expired():
            self.lock.release()
            raise LifecycleTimeoutError(name, deadline)

    def finish_task(
        self,
        lease: LifecycleLease,
        task: asyncio.Task[Any],
    ) -> None:
        root = lease.root
        root.live_tasks.discard(task)
        if lease.parent is None:
            root.body_done = True
        else:
            lease.active = False
        if root.body_done and not root.live_tasks:
            root.active = False
            if root.lock_owned:
                root.lock_owned = False
                self.lock.release()

    async def await_task(
        self,
        task: asyncio.Task[ResultT],
        lease: LifecycleLease,
        name: str,
    ) -> ResultT:
        remaining = lease.deadline.remaining().total_seconds()
        if remaining <= 0:
            task.cancel()
            raise LifecycleTimeoutError(name, lease.deadline)
        timer = asyncio.create_task(asyncio.sleep(remaining))
        try:
            done, pending = await asyncio.wait(
                {task, timer},
                return_when=asyncio.FIRST_COMPLETED,
            )
            del pending
            if task in done:
                return task.result()
            await asyncio.sleep(0)
            if task.done():
                return task.result()
            task.cancel()
            raise LifecycleTimeoutError(name, lease.deadline)
        except asyncio.CancelledError:
            receipt = lease.root.action_receipt
            if receipt is not None:
                self.cancelled_transition_receipt = receipt
            task.cancel()
            raise
        finally:
            timer.cancel()

    async def run(
        self,
        deadline: OperationDeadline,
        *,
        name: str,
        operation: Callable[[OperationDeadline], Awaitable[ResultT]],
        lease: LifecycleLease | None = None,
    ) -> ResultT:
        """Run one hard-bounded lifecycle body under session-wide exclusion."""

        parent = self.authorized_parent(lease)
        if parent is None:
            await self.acquire_root(deadline, name)
            active_deadline = deadline
        else:
            if parent.deadline.expired():
                raise LifecycleTimeoutError(name, parent.deadline)
            active_deadline = (
                deadline if deadline.expires_at <= parent.deadline.expires_at else parent.deadline
            )

        active_lease = LifecycleLease(
            coordinator=self,
            deadline=active_deadline,
            parent=parent,
            lock_owned=parent is None,
        )
        root = active_lease.root

        async def execute() -> ResultT:
            reset_token = self.context.set(active_lease)
            try:
                return await operation(active_deadline)
            finally:
                self.context.reset(reset_token)

        task = asyncio.create_task(execute())
        active_lease.owner_task = task
        root.live_tasks.add(task)
        task.add_done_callback(lambda finished: self.finish_task(active_lease, finished))
        return await self.await_task(task, active_lease, name)


@dataclass(frozen=True, slots=True)
class MobileSessionKernel:
    """Device-owned resources shared by every typed mobile wrapper."""

    coordinator: AsyncLifecycleCoordinator
    observation_engine: ObservationEngine


def mobile_session_kernel(app: AsyncApp) -> MobileSessionKernel:
    """Return the atomically initialized kernel for one device session."""

    with mobile_session_kernel_creation_lock:
        existing = getattr(app.device, DEVICE_SESSION_KERNEL_ATTRIBUTE, None)
        if existing is None:
            kernel = MobileSessionKernel(
                coordinator=AsyncLifecycleCoordinator(),
                observation_engine=ObservationEngine(app.device.backend),
            )
            setattr(app.device, DEVICE_SESSION_KERNEL_ATTRIBUTE, kernel)
            return kernel
        if not isinstance(existing, MobileSessionKernel):
            raise RuntimeError("device mobile session kernel has an invalid value")
        return existing


class CurrentAppObservationSource:
    """Validate the application handle on both sides of every device observation."""

    def __init__(self, app: AsyncApp, engine: ObservationEngine) -> None:
        self.app = app
        self.engine = engine

    async def capture(
        self,
        package: str | None,
        deadline: OperationDeadline,
    ) -> Observation:
        self.app.ensure_current("mobile.observe")
        observation = await self.engine.capture(package, deadline)
        self.app.ensure_current("mobile.observe")
        return observation


class AsyncBoundElement:
    """Observation-aware asynchronous operations common to every control."""

    def __init__(
        self,
        locator: AsyncLocator,
        transitions: TransitionEngine,
        coordinator: AsyncLifecycleCoordinator,
        lease: LifecycleLease | None,
    ) -> None:
        self.locator = locator
        self.transitions = transitions
        self.coordinator = coordinator
        self.lease = lease

    def and_(self, other: AsyncBoundElement) -> AsyncBoundElement:
        if other.coordinator is not self.coordinator:
            raise ValueError("bound elements must belong to the same mobile runtime")
        return AsyncBoundElement(
            self.locator.and_(other.locator),
            self.transitions,
            self.coordinator,
            self.lease,
        )

    def or_(self, other: AsyncBoundElement) -> AsyncBoundElement:
        if other.coordinator is not self.coordinator:
            raise ValueError("bound elements must belong to the same mobile runtime")
        return AsyncBoundElement(
            self.locator.or_(other.locator),
            self.transitions,
            self.coordinator,
            self.lease,
        )

    async def _run(
        self,
        name: str,
        timeout: timedelta | None,
        default: timedelta,
        operation: Callable[[timedelta], Awaitable[ResultT]],
    ) -> ResultT:
        budget = default if timeout is None else timeout
        if budget < timedelta():
            raise ValueError("timeout must not be negative")
        deadline = self.coordinator.root_deadline(budget, lease=self.lease)

        async def run_operation(active_deadline: OperationDeadline) -> ResultT:
            remaining = min(budget, active_deadline.remaining())
            return await operation(remaining)

        return await self.coordinator.run(
            deadline,
            name=name,
            operation=run_operation,
            lease=self.lease,
        )

    async def probe(self, *, timeout: timedelta | None = None) -> ElementSnapshot | None:
        return await self._run(
            "element.probe",
            timeout,
            self.locator.device.timeouts.probe,
            self.locator.probe,
        )

    async def _strict_snapshot(self, remaining: timedelta) -> ElementSnapshot:
        element = await self.locator.probe(remaining)
        if element is not None:
            return element
        return self.locator.strict_element(
            QueryResult(elements=()),
            "typed.element.snapshot",
            self.locator.plan(),
        )

    async def _read_snapshot(
        self,
        remaining: timedelta,
        reader: Callable[[ElementSnapshot], ResultT],
    ) -> ResultT:
        return reader(await self._strict_snapshot(remaining))

    async def is_visible(self) -> bool:
        element = await self.probe()
        return element is not None and element.displayed

    async def is_enabled(self) -> bool:
        element = await self.probe()
        return element is not None and element.enabled

    async def is_selected(self) -> bool:
        element = await self.probe()
        return element is not None and element.selected

    async def is_checked(self) -> bool:
        element = await self.probe()
        return element is not None and element.checked

    async def text_content(self) -> str:
        return await self._run(
            "element.text_content",
            None,
            self.locator.device.timeouts.probe,
            lambda remaining: self._read_snapshot(
                remaining,
                lambda element: element.text,
            ),
        )

    async def accessible_name(self) -> str:
        return await self._run(
            "element.accessible_name",
            None,
            self.locator.device.timeouts.probe,
            lambda remaining: self._read_snapshot(
                remaining,
                lambda element: element.accessible_name,
            ),
        )

    async def bounds(self) -> Rect:
        return await self._run(
            "element.bounds",
            None,
            self.locator.device.timeouts.probe,
            lambda remaining: self._read_snapshot(
                remaining,
                lambda element: element.bounds,
            ),
        )

    async def screenshot(
        self,
        path: Path | None = None,
        *,
        timeout: timedelta | None = None,
    ) -> Screenshot:
        return await self._run(
            "element.screenshot",
            timeout,
            self.locator.device.timeouts.action,
            lambda remaining: self.locator.screenshot(path, timeout=remaining),
        )

    async def wait_for(self, *, timeout: timedelta | None = None) -> None:
        await self._run(
            "element.wait_for",
            timeout,
            self.locator.device.timeouts.wait,
            lambda remaining: self.locator.wait_for(timeout=remaining),
        )

    async def raw_action(
        self,
        request: ActionRequest,
        *,
        timeout: timedelta | None = None,
        auto_scroll: bool = False,
    ) -> ActionReceipt:
        """Explicit escape hatch for a typed low-level action request."""

        return await self._run(
            f"element.{request.kind.value}",
            timeout,
            self.locator.device.timeouts.action,
            lambda remaining: self.locator.perform(
                request,
                timeout=remaining,
                auto_scroll=auto_scroll,
            ),
        )

    async def perform_then(
        self,
        request: ActionRequest,
        target: type[AnyScreenT] | ScreenTarget[AnyScreenT],
        *,
        timeout: timedelta | None,
        validate_target: Callable[[type[Any] | ScreenTarget[Any]], None],
    ) -> AnyScreenT | ScreenChoice[AnyScreenT]:
        validate_target(target)
        budget = self.locator.device.timeouts.transition if timeout is None else timeout
        if budget < timedelta():
            raise ValueError("timeout must not be negative")
        deadline = self.coordinator.root_deadline(budget, lease=self.lease)

        receipt: ActionReceipt | None = None

        async def run_operation(
            active_deadline: OperationDeadline,
        ) -> AnyScreenT | ScreenChoice[AnyScreenT]:
            nonlocal receipt
            action_budget = min(
                self.locator.device.timeouts.action,
                active_deadline.remaining(),
            )
            receipt = await self.locator.perform(request, timeout=action_budget)
            active_lease = self.coordinator.current_lease
            if active_lease is not None:
                active_lease.root.action_receipt = receipt
            try:
                if isinstance(target, ScreenTarget):
                    return await self.transitions.wait_for_any(
                        target,
                        deadline=active_deadline,
                    )
                return await self.transitions.wait_for(
                    target,
                    deadline=active_deadline,
                )
            except ScreenTimeoutError as error:
                raise TransitionTimeoutError(receipt, error) from error

        try:
            return await self.coordinator.run(
                deadline,
                name=f"element.{request.kind.value}_then",
                operation=run_operation,
                lease=self.lease,
            )
        except asyncio.CancelledError:
            raise
        except TransitionTimeoutError:
            raise
        except TransitionFailureError:
            raise
        except Exception as error:
            if receipt is None:
                raise
            raise TransitionFailureError(receipt, error) from error

    async def _act_then(
        self,
        request: ActionRequest,
        target: type[ScreenT] | ScreenTarget[ScreenT],
        *,
        timeout: timedelta | None,
    ) -> ScreenT | ScreenChoice[ScreenT]:
        return await self.perform_then(
            request,
            target,
            timeout=timeout,
            validate_target=_require_async_target,
        )


class AsyncBoundButton(AsyncBoundElement):
    async def tap(self, *, timeout: timedelta | None = None) -> None:
        await self._run(
            "button.tap",
            timeout,
            self.locator.device.timeouts.action,
            lambda remaining: self.locator.tap(timeout=remaining),
        )

    async def long_press(self, *, timeout: timedelta | None = None) -> None:
        await self._run(
            "button.long_press",
            timeout,
            self.locator.device.timeouts.action,
            lambda remaining: self.locator.long_press(timeout=remaining),
        )

    @overload
    async def tap_then(
        self,
        target: type[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenT: ...

    @overload
    async def tap_then(
        self,
        target: ScreenTarget[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenChoice[ScreenT]: ...

    async def tap_then(
        self,
        target: type[ScreenT] | ScreenTarget[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenT | ScreenChoice[ScreenT]:
        return await self._act_then(
            ActionRequest(kind=ActionKind.TAP),
            target,
            timeout=timeout,
        )


class AsyncBoundTextField(AsyncBoundElement):
    async def fill(self, value: str, *, timeout: timedelta | None = None) -> None:
        await self._run(
            "text_field.fill",
            timeout,
            self.locator.device.timeouts.action,
            lambda remaining: self.locator.fill(value, timeout=remaining),
        )

    async def clear(self, *, timeout: timedelta | None = None) -> None:
        await self._run(
            "text_field.clear",
            timeout,
            self.locator.device.timeouts.action,
            lambda remaining: self.locator.clear(timeout=remaining),
        )

    async def press(self, key: str, *, timeout: timedelta | None = None) -> None:
        await self._run(
            "text_field.press",
            timeout,
            self.locator.device.timeouts.action,
            lambda remaining: self.locator.press(key, timeout=remaining),
        )


class AsyncBoundCheckbox(AsyncBoundElement):
    async def check(self, *, timeout: timedelta | None = None) -> None:
        await self._run(
            "checkbox.check",
            timeout,
            self.locator.device.timeouts.action,
            lambda remaining: self.locator.check(timeout=remaining),
        )

    async def uncheck(self, *, timeout: timedelta | None = None) -> None:
        await self._run(
            "checkbox.uncheck",
            timeout,
            self.locator.device.timeouts.action,
            lambda remaining: self.locator.uncheck(timeout=remaining),
        )


class AsyncBoundChoice(AsyncBoundElement):
    async def select(self, *, timeout: timedelta | None = None) -> None:
        await self._run(
            "choice.select",
            timeout,
            self.locator.device.timeouts.action,
            lambda remaining: self.locator.tap(timeout=remaining),
        )

    @overload
    async def select_then(
        self,
        target: type[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenT: ...

    @overload
    async def select_then(
        self,
        target: ScreenTarget[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenChoice[ScreenT]: ...

    async def select_then(
        self,
        target: type[ScreenT] | ScreenTarget[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenT | ScreenChoice[ScreenT]:
        return await self._act_then(
            ActionRequest(kind=ActionKind.TAP),
            target,
            timeout=timeout,
        )


class AsyncBoundScrollable(AsyncBoundElement):
    async def swipe(
        self,
        direction: Direction,
        *,
        percent: float = 0.75,
        timeout: timedelta | None = None,
    ) -> None:
        await self._run(
            "scrollable.swipe",
            timeout,
            self.locator.device.timeouts.action,
            lambda remaining: self.locator.swipe(
                direction,
                percent=percent,
                timeout=remaining,
            ),
        )

    async def scroll(
        self,
        direction: Direction,
        *,
        percent: float = 0.75,
        timeout: timedelta | None = None,
    ) -> None:
        await self._run(
            "scrollable.scroll",
            timeout,
            self.locator.device.timeouts.action,
            lambda remaining: self.locator.scroll(
                direction,
                percent=percent,
                timeout=remaining,
            ),
        )


class AsyncScreenBinder:
    """Bind one concrete screen scope to capability-specific async controls."""

    def __init__(
        self,
        root: AsyncLocatorRoot,
        transitions: TransitionEngine,
        coordinator: AsyncLifecycleCoordinator,
        lease: LifecycleLease | None,
    ) -> None:
        self.root = root
        self.transitions = transitions
        self.coordinator = coordinator
        self.lease = lease

    @property
    def scope_package(self) -> str | None:
        return self.root.package

    def bind(
        self,
        descriptor: ElementDescriptor[Any, Any, Any, Any],
    ) -> Any:
        locator = self.root.locator(descriptor.selector)
        control_type: type[AsyncBoundElement]
        if descriptor.control_kind is ControlKind.BUTTON:
            control_type = AsyncBoundButton
        elif descriptor.control_kind is ControlKind.TEXT_FIELD:
            control_type = AsyncBoundTextField
        elif descriptor.control_kind is ControlKind.CHECKBOX:
            control_type = AsyncBoundCheckbox
        elif descriptor.control_kind is ControlKind.CHOICE:
            control_type = AsyncBoundChoice
        elif descriptor.control_kind is ControlKind.SCROLLABLE:
            control_type = AsyncBoundScrollable
        else:
            control_type = AsyncBoundElement
        return control_type(
            locator,
            self.transitions,
            self.coordinator,
            self.lease,
        )


class AsyncScreenBinderFactory:
    """Create app- or device-scoped binders for exact screen definitions."""

    def __init__(
        self,
        app: AsyncApp,
        coordinator: AsyncLifecycleCoordinator,
    ) -> None:
        self.app = app
        self.coordinator = coordinator
        self.transitions: TransitionEngine | None = None

    def __call__(self, screen_type: type[ScreenDefinition]) -> AsyncScreenBinder:
        _require_async_screen_type(screen_type)
        self.app.ensure_current("mobile.bind")
        transitions = self.transitions
        if transitions is None:
            raise RuntimeError("screen binder factory is not initialized")
        root: AsyncLocatorRoot = (
            AsyncLocatorRoot(
                self.app.device,
                package=None,
                application_generation=self.app.application_generation,
            )
            if screen_type.scope is DeviceScope
            else self.app
        )
        binder = AsyncScreenBinder(
            root,
            transitions,
            self.coordinator,
            self.coordinator.current_lease,
        )
        self.app.ensure_current("mobile.bind")
        return binder


class AsyncMobileApp:
    """Integrated typed-screen runtime for one active asynchronous application."""

    def __init__(
        self,
        app: AsyncApp,
        *,
        interruptions: tuple[type[AsyncInterruption[Any]], ...] = (),
        max_dismissals: int = 8,
    ) -> None:
        for interruption_type in interruptions:
            _require_async_interruption_type(interruption_type)
        self.app = app
        self.kernel = mobile_session_kernel(app)
        self.coordinator = self.kernel.coordinator
        self.observation_engine = self.kernel.observation_engine
        self.observations = CurrentAppObservationSource(
            app,
            self.observation_engine,
        )
        self.binder_factory = AsyncScreenBinderFactory(app, self.coordinator)
        self.interruptions = InterruptionManager(
            cast(tuple[InterruptionType, ...], interruptions),
            binder_factory=cast(Any, self.binder_factory),
            package=app.package_name,
            timeout=app.device.timeouts.interruption,
            max_dismissals=max_dismissals,
        )
        self.transitions = TransitionEngine(
            self.observations,
            binder_factory=self.binder_factory,
            app_package=app.package_name,
            timeouts=app.device.timeouts,
            interruption_hook=self.interruptions,
        )
        self.binder_factory.transitions = self.transitions
        self.recovery = RecoveryEngine(
            self.observations,
            binder_factory=cast(Any, self.binder_factory),
            app_package=app.package_name,
            press_back=self._press_back,
            retry_delay=app.device.timeouts.retry.initial_delay,
            interruption_hook=self.interruptions,
        )

    @property
    def cancelled_transition_receipt(self) -> ActionReceipt | None:
        """Return the last receipt retained when transition waiting was cancelled."""

        return self.coordinator.cancelled_transition_receipt

    def _transition_deadline(self, timeout: timedelta | None) -> OperationDeadline:
        budget = self.app.device.timeouts.transition if timeout is None else timeout
        if budget < timedelta():
            raise ValueError("timeout must not be negative")
        return self.coordinator.root_deadline(budget)

    async def _press_back(self) -> None:
        deadline = self.coordinator.current_deadline
        timeout = (
            self.app.device.timeouts.action
            if deadline is None
            else min(self.app.device.timeouts.action, deadline.remaining())
        )
        await self.app.device.keyboard.press(Key.BACK, timeout=timeout)

    async def wait_for(
        self,
        screen_type: type[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenT:
        _require_async_screen_type(screen_type)
        deadline = self._transition_deadline(timeout)

        async def run_operation(active_deadline: OperationDeadline) -> ScreenT:
            self.app.ensure_current("mobile.wait_for")
            return await self.transitions.wait_for(
                screen_type,
                deadline=active_deadline,
            )

        return await self.coordinator.run(
            deadline,
            name="mobile.wait_for",
            operation=run_operation,
        )

    async def wait_for_any(
        self,
        target: ScreenTarget[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenChoice[ScreenT]:
        _require_async_target(target)
        deadline = self._transition_deadline(timeout)

        async def run_operation(
            active_deadline: OperationDeadline,
        ) -> ScreenChoice[ScreenT]:
            self.app.ensure_current("mobile.wait_for_any")
            return await self.transitions.wait_for_any(
                target,
                deadline=active_deadline,
            )

        return await self.coordinator.run(
            deadline,
            name="mobile.wait_for_any",
            operation=run_operation,
        )

    @overload
    async def settle(
        self,
        screen: type[ScreenT],
        *,
        stable_for: timedelta | None = None,
        timeout: timedelta | None = None,
    ) -> ScreenT: ...

    @overload
    async def settle(
        self,
        screen: ScreenT,
        *,
        stable_for: timedelta | None = None,
        timeout: timedelta | None = None,
    ) -> ScreenT: ...

    async def settle(
        self,
        screen: type[ScreenT] | ScreenT,
        *,
        stable_for: timedelta | None = None,
        timeout: timedelta | None = None,
    ) -> ScreenT:
        _require_async_screen_type(screen if isinstance(screen, type) else type(screen))
        deadline = self._transition_deadline(timeout)

        async def run_operation(active_deadline: OperationDeadline) -> ScreenT:
            self.app.ensure_current("mobile.settle")
            return await self.transitions.settle(
                screen,
                stable_for=stable_for,
                deadline=active_deadline,
            )

        return await self.coordinator.run(
            deadline,
            name="mobile.settle",
            operation=run_operation,
        )

    async def ensure(
        self,
        screen_type: type[ScreenT],
        *,
        recovery: BackRecovery[ScreenT] | None = None,
        timeout: timedelta | None = None,
    ) -> ScreenT:
        _require_async_screen_type(screen_type)
        if recovery is not None:
            _require_async_screen_type(recovery.screen)
        if recovery is None:
            return await self.wait_for(screen_type, timeout=timeout)
        deadline = self._transition_deadline(timeout)

        async def run_operation(active_deadline: OperationDeadline) -> ScreenT:
            self.app.ensure_current("mobile.ensure")
            return await self.recovery.ensure(
                screen_type,
                recovery,
                active_deadline,
            )

        return await self.coordinator.run(
            deadline,
            name="mobile.ensure",
            operation=run_operation,
        )
