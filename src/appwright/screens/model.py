"""Immutable screen definitions and readiness conditions."""

from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Generic

from typing_extensions import TypeVar

from appwright.selectors.models import Selector

if TYPE_CHECKING:
    from appwright.screens.elements import ElementBinder


class AppScope:
    """Marker for selectors resolved within the application package."""


class DeviceScope:
    """Marker for selectors resolved across device-owned surfaces."""


ScopeT = TypeVar("ScopeT", default=AppScope)

FRAMEWORK_BASE_ATTRIBUTE = "appwright_framework_screen_base"
REQUIRES_PRIORITY_ATTRIBUTE = "appwright_requires_interruption_priority"
MISSING_VALUE = object()


class Readiness:
    """Base type for immutable screen-readiness expressions."""


@dataclass(frozen=True, slots=True)
class Visible(Readiness):
    """Require one selector to have a visible match."""

    selector: Selector


@dataclass(frozen=True, slots=True)
class AllOf(Readiness):
    """Require every child readiness expression to match."""

    conditions: tuple[Readiness, ...]


@dataclass(frozen=True, slots=True)
class AnyOf(Readiness):
    """Require at least one child readiness expression to match."""

    conditions: tuple[Readiness, ...]


def visible(selector: Selector) -> Visible:
    """Build a visible-element readiness condition."""

    return Visible(selector=selector)


def all_of(*conditions: Readiness) -> AllOf:
    """Build an ordered conjunction of readiness conditions."""

    return AllOf(conditions=conditions)


def any_of(*conditions: Readiness) -> AnyOf:
    """Build an ordered disjunction of readiness conditions."""

    return AnyOf(conditions=conditions)


class _ScreenDefinitionMeta(ABCMeta):
    """Validate concrete screen definitions after abstract methods are resolved."""

    def __new__(
        mcls,
        name: str,
        bases: tuple[type[Any], ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> _ScreenDefinitionMeta:
        cls = super().__new__(mcls, name, bases, namespace, **kwargs)
        if namespace.get(FRAMEWORK_BASE_ATTRIBUTE) is True or cls.__abstractmethods__:
            return cls

        requires_priority = any(
            base.__dict__.get(REQUIRES_PRIORITY_ATTRIBUTE) is True for base in cls.__mro__
        )
        readiness = getattr(cls, "ready", MISSING_VALUE)
        if readiness is MISSING_VALUE:
            definition = "interruption" if requires_priority else "screen"
            raise TypeError(f"{name} is a concrete {definition} and must define or inherit 'ready'")
        if not isinstance(readiness, Readiness):
            raise TypeError(
                f"{name} 'ready' must be a Readiness instance, got {type(readiness).__name__}"
            )

        if requires_priority:
            priority = getattr(cls, "priority", MISSING_VALUE)
            if priority is MISSING_VALUE:
                raise TypeError(
                    f"{name} is a concrete interruption and must define or inherit 'priority'"
                )
            if not isinstance(priority, int) or isinstance(priority, bool):
                raise TypeError(f"{name} priority must be an integer; bool is not allowed")
        return cls


class _BoundScreen(Generic[ScopeT], metaclass=_ScreenDefinitionMeta):
    """Shared implementation for synchronous and asynchronous screen definitions."""

    appwright_framework_screen_base = True
    scope: ClassVar[type[AppScope] | type[DeviceScope]] = AppScope
    ready: ClassVar[Readiness]

    def __init__(self, binder: ElementBinder) -> None:
        self.element_binder = binder

    @property
    def binder(self) -> ElementBinder:
        """Return the runtime binder used to materialize control descriptors."""

        return self.element_binder


class Screen(_BoundScreen[ScopeT], Generic[ScopeT]):
    """Synchronous application screen definition."""

    appwright_framework_screen_base = True


class AsyncScreen(_BoundScreen[ScopeT], Generic[ScopeT]):
    """Asynchronous application screen definition."""

    appwright_framework_screen_base = True


class DeviceScreen(Screen[DeviceScope]):
    """Screen definition whose selectors may target device-owned UI."""

    appwright_framework_screen_base = True
    scope = DeviceScope


class AsyncDeviceScreen(AsyncScreen[DeviceScope]):
    """Asynchronous screen whose controls may target device-owned UI."""

    appwright_framework_screen_base = True
    scope = DeviceScope


class Interruption(Screen[ScopeT], Generic[ScopeT], ABC):
    """A prioritized temporary screen that knows how to dismiss itself."""

    appwright_requires_interruption_priority = True
    priority: ClassVar[int]

    @abstractmethod
    def dismiss(self) -> None | Awaitable[None]:
        """Dismiss this interruption once."""


class AsyncInterruption(AsyncScreen[ScopeT], Generic[ScopeT], ABC):
    """Prioritized asynchronous interruption with an awaitable dismissal."""

    appwright_requires_interruption_priority = True
    priority: ClassVar[int]

    @abstractmethod
    async def dismiss(self) -> None:
        """Dismiss this interruption once."""
