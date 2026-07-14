"""Synchronous typed controls backed by the canonical async lifecycle kernel."""

from __future__ import annotations

from collections.abc import Coroutine
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast, overload

from appwright.core.runtime import AsyncApp, AsyncLocatorRoot
from appwright.models.data import ActionRequest, ElementSnapshot, Rect, Screenshot
from appwright.models.enums import ActionKind, Direction, Key
from appwright.operations import ActionReceipt, OperationDeadline
from appwright.screens.elements import ControlKind, ElementDescriptor
from appwright.screens.interruptions import InterruptionManager, InterruptionType
from appwright.screens.model import DeviceScope, Interruption, Screen
from appwright.screens.recovery import BackRecovery, RecoveryEngine
from appwright.screens.runtime import (
    AsyncBoundButton,
    AsyncBoundCheckbox,
    AsyncBoundChoice,
    AsyncBoundElement,
    AsyncBoundScrollable,
    AsyncBoundTextField,
    AsyncLifecycleCoordinator,
    CurrentAppObservationSource,
    LifecycleLease,
    mobile_session_kernel,
)
from appwright.screens.targets import (
    ScreenChoice,
    ScreenDefinition,
    ScreenTarget,
    SyncScreenDefinition,
)
from appwright.screens.transitions import TransitionEngine

ResultT = TypeVar("ResultT")
ScreenT = TypeVar("ScreenT", bound=SyncScreenDefinition)


class CoroutineRunner(Protocol):
    """Run a coroutine from either the sync owner or an interruption worker."""

    def run(self, coroutine: Coroutine[Any, Any, ResultT]) -> ResultT: ...


def require_sync_screen_type(screen_type: object) -> None:
    if not isinstance(screen_type, type) or not issubclass(screen_type, Screen):
        raise TypeError("sync mobile operations require a Screen type")


def require_sync_target(target: type[Any] | ScreenTarget[Any]) -> None:
    if isinstance(target, ScreenTarget):
        for screen_type in target.screens:
            require_sync_screen_type(screen_type)
        return
    require_sync_screen_type(target)


def require_sync_interruption_type(interruption_type: object) -> None:
    if not isinstance(interruption_type, type) or not issubclass(
        interruption_type,
        Interruption,
    ):
        raise TypeError("sync mobile interruptions require an Interruption type")


class SyncBoundElement:
    """Blocking adapter for one canonical asynchronous bound element."""

    def __init__(self, implementation: AsyncBoundElement, runner: CoroutineRunner) -> None:
        self.implementation = implementation
        self.runner = runner

    def and_(self, other: SyncBoundElement) -> SyncBoundElement:
        return SyncBoundElement(
            self.implementation.and_(other.implementation),
            self.runner,
        )

    def or_(self, other: SyncBoundElement) -> SyncBoundElement:
        return SyncBoundElement(
            self.implementation.or_(other.implementation),
            self.runner,
        )

    def probe(self, *, timeout: timedelta | None = None) -> ElementSnapshot | None:
        return self.runner.run(self.implementation.probe(timeout=timeout))

    def is_visible(self) -> bool:
        return self.runner.run(self.implementation.is_visible())

    def is_enabled(self) -> bool:
        return self.runner.run(self.implementation.is_enabled())

    def is_selected(self) -> bool:
        return self.runner.run(self.implementation.is_selected())

    def is_checked(self) -> bool:
        return self.runner.run(self.implementation.is_checked())

    def text_content(self) -> str:
        return self.runner.run(self.implementation.text_content())

    def accessible_name(self) -> str:
        return self.runner.run(self.implementation.accessible_name())

    def bounds(self) -> Rect:
        return self.runner.run(self.implementation.bounds())

    def screenshot(
        self,
        path: Path | None = None,
        *,
        timeout: timedelta | None = None,
    ) -> Screenshot:
        return self.runner.run(self.implementation.screenshot(path, timeout=timeout))

    def wait_for(self, *, timeout: timedelta | None = None) -> None:
        self.runner.run(self.implementation.wait_for(timeout=timeout))

    def raw_action(
        self,
        request: ActionRequest,
        *,
        timeout: timedelta | None = None,
        auto_scroll: bool = False,
    ) -> ActionReceipt:
        return self.runner.run(
            self.implementation.raw_action(
                request,
                timeout=timeout,
                auto_scroll=auto_scroll,
            )
        )

    def perform_then(
        self,
        request: ActionRequest,
        target: type[ScreenT] | ScreenTarget[ScreenT],
        *,
        timeout: timedelta | None,
    ) -> ScreenT | ScreenChoice[ScreenT]:
        return self.runner.run(
            self.implementation.perform_then(
                request,
                target,
                timeout=timeout,
                validate_target=require_sync_target,
            )
        )


class SyncBoundButton(SyncBoundElement):
    def tap(self, *, timeout: timedelta | None = None) -> None:
        self.runner.run(cast(AsyncBoundButton, self.implementation).tap(timeout=timeout))

    def long_press(self, *, timeout: timedelta | None = None) -> None:
        self.runner.run(cast(AsyncBoundButton, self.implementation).long_press(timeout=timeout))

    @overload
    def tap_then(
        self,
        target: type[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenT: ...

    @overload
    def tap_then(
        self,
        target: ScreenTarget[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenChoice[ScreenT]: ...

    def tap_then(
        self,
        target: type[ScreenT] | ScreenTarget[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenT | ScreenChoice[ScreenT]:
        return self.perform_then(
            ActionRequest(kind=ActionKind.TAP),
            target,
            timeout=timeout,
        )


class SyncBoundTextField(SyncBoundElement):
    def fill(self, value: str, *, timeout: timedelta | None = None) -> None:
        self.runner.run(cast(AsyncBoundTextField, self.implementation).fill(value, timeout=timeout))

    def clear(self, *, timeout: timedelta | None = None) -> None:
        self.runner.run(cast(AsyncBoundTextField, self.implementation).clear(timeout=timeout))

    def press(self, key: str, *, timeout: timedelta | None = None) -> None:
        self.runner.run(cast(AsyncBoundTextField, self.implementation).press(key, timeout=timeout))


class SyncBoundCheckbox(SyncBoundElement):
    def check(self, *, timeout: timedelta | None = None) -> None:
        self.runner.run(cast(AsyncBoundCheckbox, self.implementation).check(timeout=timeout))

    def uncheck(self, *, timeout: timedelta | None = None) -> None:
        self.runner.run(cast(AsyncBoundCheckbox, self.implementation).uncheck(timeout=timeout))


class SyncBoundChoice(SyncBoundElement):
    def select(self, *, timeout: timedelta | None = None) -> None:
        self.runner.run(cast(AsyncBoundChoice, self.implementation).select(timeout=timeout))

    @overload
    def select_then(
        self,
        target: type[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenT: ...

    @overload
    def select_then(
        self,
        target: ScreenTarget[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenChoice[ScreenT]: ...

    def select_then(
        self,
        target: type[ScreenT] | ScreenTarget[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenT | ScreenChoice[ScreenT]:
        return self.perform_then(
            ActionRequest(kind=ActionKind.TAP),
            target,
            timeout=timeout,
        )


class SyncBoundScrollable(SyncBoundElement):
    def swipe(
        self,
        direction: Direction,
        *,
        percent: float = 0.75,
        timeout: timedelta | None = None,
    ) -> None:
        self.runner.run(
            cast(AsyncBoundScrollable, self.implementation).swipe(
                direction,
                percent=percent,
                timeout=timeout,
            )
        )

    def scroll(
        self,
        direction: Direction,
        *,
        percent: float = 0.75,
        timeout: timedelta | None = None,
    ) -> None:
        self.runner.run(
            cast(AsyncBoundScrollable, self.implementation).scroll(
                direction,
                percent=percent,
                timeout=timeout,
            )
        )


class SyncScreenBinder:
    """Bind synchronous screen descriptors to blocking control adapters."""

    def __init__(
        self,
        root: AsyncLocatorRoot,
        transitions: TransitionEngine,
        coordinator: AsyncLifecycleCoordinator,
        lease: LifecycleLease | None,
        runner: CoroutineRunner,
    ) -> None:
        self.root = root
        self.transitions = transitions
        self.coordinator = coordinator
        self.lease = lease
        self.runner = runner

    def bind(self, descriptor: ElementDescriptor[Any, Any, Any, Any]) -> Any:
        locator = self.root.locator(descriptor.selector)
        implementation_type: type[AsyncBoundElement]
        wrapper_type: type[SyncBoundElement]
        if descriptor.control_kind is ControlKind.BUTTON:
            implementation_type, wrapper_type = AsyncBoundButton, SyncBoundButton
        elif descriptor.control_kind is ControlKind.TEXT_FIELD:
            implementation_type, wrapper_type = AsyncBoundTextField, SyncBoundTextField
        elif descriptor.control_kind is ControlKind.CHECKBOX:
            implementation_type, wrapper_type = AsyncBoundCheckbox, SyncBoundCheckbox
        elif descriptor.control_kind is ControlKind.CHOICE:
            implementation_type, wrapper_type = AsyncBoundChoice, SyncBoundChoice
        elif descriptor.control_kind is ControlKind.SCROLLABLE:
            implementation_type, wrapper_type = AsyncBoundScrollable, SyncBoundScrollable
        else:
            implementation_type, wrapper_type = AsyncBoundElement, SyncBoundElement
        implementation = implementation_type(
            locator,
            self.transitions,
            self.coordinator,
            self.lease,
        )
        return wrapper_type(implementation, self.runner)


class SyncScreenBinderFactory:
    def __init__(
        self,
        app: AsyncApp,
        coordinator: AsyncLifecycleCoordinator,
        runner: CoroutineRunner,
    ) -> None:
        self.app = app
        self.coordinator = coordinator
        self.runner = runner
        self.transitions: TransitionEngine | None = None

    def __call__(self, screen_type: type[ScreenDefinition]) -> SyncScreenBinder:
        require_sync_screen_type(screen_type)
        self.app.ensure_current("mobile.bind")
        transitions = self.transitions
        if transitions is None:
            raise RuntimeError("screen binder factory is not initialized")
        root = (
            AsyncLocatorRoot(
                self.app.device,
                package=None,
                application_generation=self.app.application_generation,
            )
            if screen_type.scope is DeviceScope
            else self.app
        )
        binder = SyncScreenBinder(
            root,
            transitions,
            self.coordinator,
            self.coordinator.current_lease,
            self.runner,
        )
        self.app.ensure_current("mobile.bind")
        return binder


class CanonicalSyncMobileApp:
    """Async orchestration that binds synchronous screen/control definitions."""

    def __init__(
        self,
        app: AsyncApp,
        runner: CoroutineRunner,
        *,
        interruptions: tuple[type[Interruption[Any]], ...] = (),
        max_dismissals: int = 8,
    ) -> None:
        for interruption_type in interruptions:
            require_sync_interruption_type(interruption_type)
        self.app = app
        self.runner = runner
        self.kernel = mobile_session_kernel(app)
        self.coordinator = self.kernel.coordinator
        self.observations = CurrentAppObservationSource(app, self.kernel.observation_engine)
        self.binder_factory = SyncScreenBinderFactory(app, self.coordinator, runner)
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
        self.recovery: RecoveryEngine[Screen[Any]] = RecoveryEngine(
            self.observations,
            binder_factory=cast(Any, self.binder_factory),
            app_package=app.package_name,
            press_back=self._press_back,
            retry_delay=app.device.timeouts.retry.initial_delay,
            interruption_hook=self.interruptions,
        )

    @property
    def cancelled_transition_receipt(self) -> ActionReceipt | None:
        return self.coordinator.cancelled_transition_receipt

    def transition_deadline(self, timeout: timedelta | None) -> OperationDeadline:
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
        require_sync_screen_type(screen_type)
        deadline = self.transition_deadline(timeout)

        async def operation(active_deadline: OperationDeadline) -> ScreenT:
            self.app.ensure_current("mobile.wait_for")
            return await self.transitions.wait_for(screen_type, deadline=active_deadline)

        return await self.coordinator.run(
            deadline,
            name="mobile.wait_for",
            operation=operation,
        )

    async def wait_for_any(
        self,
        target: ScreenTarget[ScreenT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenChoice[ScreenT]:
        require_sync_target(target)
        deadline = self.transition_deadline(timeout)

        async def operation(active_deadline: OperationDeadline) -> ScreenChoice[ScreenT]:
            self.app.ensure_current("mobile.wait_for_any")
            return await self.transitions.wait_for_any(target, deadline=active_deadline)

        return await self.coordinator.run(
            deadline,
            name="mobile.wait_for_any",
            operation=operation,
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
        require_sync_screen_type(screen if isinstance(screen, type) else type(screen))
        deadline = self.transition_deadline(timeout)

        async def operation(active_deadline: OperationDeadline) -> ScreenT:
            self.app.ensure_current("mobile.settle")
            return await self.transitions.settle(
                screen,
                stable_for=stable_for,
                deadline=active_deadline,
            )

        return await self.coordinator.run(
            deadline,
            name="mobile.settle",
            operation=operation,
        )

    async def ensure(
        self,
        screen_type: type[ScreenT],
        *,
        recovery: BackRecovery[ScreenT] | None = None,
        timeout: timedelta | None = None,
    ) -> ScreenT:
        require_sync_screen_type(screen_type)
        if recovery is not None:
            require_sync_screen_type(recovery.screen)
        if recovery is None:
            return await self.wait_for(screen_type, timeout=timeout)
        deadline = self.transition_deadline(timeout)

        async def operation(active_deadline: OperationDeadline) -> ScreenT:
            self.app.ensure_current("mobile.ensure")
            return await self.recovery.ensure(
                screen_type,
                recovery,
                active_deadline,
            )

        return await self.coordinator.run(
            deadline,
            name="mobile.ensure",
            operation=operation,
        )
