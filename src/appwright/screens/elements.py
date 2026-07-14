"""Scope-safe control protocols, descriptors, and selector helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Protocol, Self, cast, overload

from typing_extensions import TypeVar

from appwright.models.data import ActionRequest, ElementSnapshot, Rect, Screenshot
from appwright.models.enums import Direction, MatchMode, Role
from appwright.operations import ActionReceipt
from appwright.screens.model import AppScope, AsyncScreen, DeviceScope, ScopeT, Screen
from appwright.selectors.models import Selector, TextMatcher

if TYPE_CHECKING:
    from appwright.screens.targets import ScreenChoice, ScreenTarget


DestinationT = TypeVar(
    "DestinationT",
    bound=Screen[AppScope] | Screen[DeviceScope],
)
AsyncDestinationT = TypeVar(
    "AsyncDestinationT",
    bound=AsyncScreen[AppScope] | AsyncScreen[DeviceScope],
)


class Element(Protocol[ScopeT]):
    """Read-only operations common to every bound control."""

    def and_(self, other: Element[ScopeT]) -> Element[ScopeT]: ...

    def or_(self, other: Element[ScopeT]) -> Element[ScopeT]: ...

    def probe(
        self,
        *,
        timeout: timedelta | None = None,
    ) -> ElementSnapshot | None: ...

    def is_visible(self) -> bool: ...

    def is_enabled(self) -> bool: ...

    def is_selected(self) -> bool: ...

    def is_checked(self) -> bool: ...

    def text_content(self) -> str: ...

    def accessible_name(self) -> str: ...

    def bounds(self) -> Rect: ...

    def screenshot(
        self,
        path: Path | None = None,
        *,
        timeout: timedelta | None = None,
    ) -> Screenshot: ...

    def wait_for(self, *, timeout: timedelta | None = None) -> None: ...

    def raw_action(
        self,
        request: ActionRequest,
        *,
        timeout: timedelta | None = None,
        auto_scroll: bool = False,
    ) -> ActionReceipt: ...


class Button(Element[ScopeT], Protocol[ScopeT]):
    """A control that supports press-like pointer actions."""

    def tap(self, *, timeout: timedelta | None = None) -> None: ...

    def long_press(self, *, timeout: timedelta | None = None) -> None: ...

    @overload
    def tap_then(
        self,
        target: type[DestinationT],
        *,
        timeout: timedelta | None = None,
    ) -> DestinationT: ...

    @overload
    def tap_then(
        self,
        target: ScreenTarget[DestinationT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenChoice[DestinationT]: ...


class TextField(Element[ScopeT], Protocol[ScopeT]):
    """An editable text control."""

    def fill(self, value: str, *, timeout: timedelta | None = None) -> None: ...

    def clear(self, *, timeout: timedelta | None = None) -> None: ...

    def press(self, key: str, *, timeout: timedelta | None = None) -> None: ...


class Checkbox(Element[ScopeT], Protocol[ScopeT]):
    """A binary state-setting control."""

    def check(self, *, timeout: timedelta | None = None) -> None: ...

    def uncheck(self, *, timeout: timedelta | None = None) -> None: ...


class Choice(Element[ScopeT], Protocol[ScopeT]):
    """A selectable choice control."""

    def select(self, *, timeout: timedelta | None = None) -> None: ...

    @overload
    def select_then(
        self,
        target: type[DestinationT],
        *,
        timeout: timedelta | None = None,
    ) -> DestinationT: ...

    @overload
    def select_then(
        self,
        target: ScreenTarget[DestinationT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenChoice[DestinationT]: ...


class Scrollable(Element[ScopeT], Protocol[ScopeT]):
    """A control that supports explicit viewport movement."""

    def swipe(
        self,
        direction: Direction,
        *,
        percent: float = 0.75,
        timeout: timedelta | None = None,
    ) -> None: ...

    def scroll(
        self,
        direction: Direction,
        *,
        percent: float = 0.75,
        timeout: timedelta | None = None,
    ) -> None: ...


class AsyncElement(Protocol[ScopeT]):
    """Awaitable operations common to every asynchronous bound control."""

    def and_(self, other: AsyncElement[ScopeT]) -> AsyncElement[ScopeT]: ...

    def or_(self, other: AsyncElement[ScopeT]) -> AsyncElement[ScopeT]: ...

    async def probe(
        self,
        *,
        timeout: timedelta | None = None,
    ) -> ElementSnapshot | None: ...

    async def is_visible(self) -> bool: ...

    async def is_enabled(self) -> bool: ...

    async def is_selected(self) -> bool: ...

    async def is_checked(self) -> bool: ...

    async def text_content(self) -> str: ...

    async def accessible_name(self) -> str: ...

    async def bounds(self) -> Rect: ...

    async def screenshot(
        self,
        path: Path | None = None,
        *,
        timeout: timedelta | None = None,
    ) -> Screenshot: ...

    async def wait_for(self, *, timeout: timedelta | None = None) -> None: ...

    async def raw_action(
        self,
        request: ActionRequest,
        *,
        timeout: timedelta | None = None,
        auto_scroll: bool = False,
    ) -> ActionReceipt: ...


class AsyncButton(AsyncElement[ScopeT], Protocol[ScopeT]):
    """Awaitable press-like control."""

    async def tap(self, *, timeout: timedelta | None = None) -> None: ...

    async def long_press(self, *, timeout: timedelta | None = None) -> None: ...

    @overload
    async def tap_then(
        self,
        target: type[AsyncDestinationT],
        *,
        timeout: timedelta | None = None,
    ) -> AsyncDestinationT: ...

    @overload
    async def tap_then(
        self,
        target: ScreenTarget[AsyncDestinationT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenChoice[AsyncDestinationT]: ...


class AsyncTextField(AsyncElement[ScopeT], Protocol[ScopeT]):
    """Awaitable editable text control."""

    async def fill(self, value: str, *, timeout: timedelta | None = None) -> None: ...

    async def clear(self, *, timeout: timedelta | None = None) -> None: ...

    async def press(self, key: str, *, timeout: timedelta | None = None) -> None: ...


class AsyncCheckbox(AsyncElement[ScopeT], Protocol[ScopeT]):
    """Awaitable binary state-setting control."""

    async def check(self, *, timeout: timedelta | None = None) -> None: ...

    async def uncheck(self, *, timeout: timedelta | None = None) -> None: ...


class AsyncChoice(AsyncElement[ScopeT], Protocol[ScopeT]):
    """Awaitable selectable choice control."""

    async def select(self, *, timeout: timedelta | None = None) -> None: ...

    @overload
    async def select_then(
        self,
        target: type[AsyncDestinationT],
        *,
        timeout: timedelta | None = None,
    ) -> AsyncDestinationT: ...

    @overload
    async def select_then(
        self,
        target: ScreenTarget[AsyncDestinationT],
        *,
        timeout: timedelta | None = None,
    ) -> ScreenChoice[AsyncDestinationT]: ...


class AsyncScrollable(AsyncElement[ScopeT], Protocol[ScopeT]):
    """Awaitable explicit viewport movement control."""

    async def swipe(
        self,
        direction: Direction,
        *,
        percent: float = 0.75,
        timeout: timedelta | None = None,
    ) -> None: ...

    async def scroll(
        self,
        direction: Direction,
        *,
        percent: float = 0.75,
        timeout: timedelta | None = None,
    ) -> None: ...


class ControlKind(StrEnum):
    """Runtime discriminator used by binders to select a control adapter."""

    ELEMENT = "element"
    BUTTON = "button"
    TEXT_FIELD = "text_field"
    CHECKBOX = "checkbox"
    CHOICE = "choice"
    SCROLLABLE = "scrollable"


AppControlT = TypeVar("AppControlT")
DeviceControlT = TypeVar("DeviceControlT", default=AppControlT)
AsyncAppControlT = TypeVar("AsyncAppControlT", default=AppControlT)
AsyncDeviceControlT = TypeVar("AsyncDeviceControlT", default=DeviceControlT)


@dataclass(frozen=True, slots=True)
class ElementDescriptor(
    Generic[
        AppControlT,
        DeviceControlT,
        AsyncAppControlT,
        AsyncDeviceControlT,
    ]
):
    """An immutable unbound selector and its requested control capability."""

    selector: Selector
    control_kind: ControlKind

    @overload
    def __get__(self, instance: None, owner: type[object] | None = None) -> Self: ...

    @overload
    def __get__(
        self,
        instance: Screen[AppScope],
        owner: type[object] | None = None,
    ) -> AppControlT: ...

    @overload
    def __get__(
        self,
        instance: Screen[DeviceScope],
        owner: type[object] | None = None,
    ) -> DeviceControlT: ...

    @overload
    def __get__(
        self,
        instance: AsyncScreen[AppScope],
        owner: type[object] | None = None,
    ) -> AsyncAppControlT: ...

    @overload
    def __get__(
        self,
        instance: AsyncScreen[DeviceScope],
        owner: type[object] | None = None,
    ) -> AsyncDeviceControlT: ...

    def __get__(
        self,
        instance: (
            Screen[AppScope]
            | Screen[DeviceScope]
            | AsyncScreen[AppScope]
            | AsyncScreen[DeviceScope]
            | None
        ),
        owner: type[object] | None = None,
    ) -> Self | AppControlT | DeviceControlT | AsyncAppControlT | AsyncDeviceControlT:
        del owner
        if instance is None:
            return self
        return cast(
            AppControlT | DeviceControlT | AsyncAppControlT | AsyncDeviceControlT,
            instance.binder.bind(self),
        )


class ElementBinder(Protocol):
    """Runtime hook used by a bound screen to materialize descriptors."""

    def bind(
        self,
        descriptor: ElementDescriptor[Any, Any, Any, Any],
    ) -> Any: ...


def element(
    selector: Selector,
) -> ElementDescriptor[
    Element[AppScope],
    Element[DeviceScope],
    AsyncElement[AppScope],
    AsyncElement[DeviceScope],
]:
    return ElementDescriptor(selector=selector, control_kind=ControlKind.ELEMENT)


def button(
    selector: Selector,
) -> ElementDescriptor[
    Button[AppScope],
    Button[DeviceScope],
    AsyncButton[AppScope],
    AsyncButton[DeviceScope],
]:
    return ElementDescriptor(selector=selector, control_kind=ControlKind.BUTTON)


def text_field(
    selector: Selector,
) -> ElementDescriptor[
    TextField[AppScope],
    TextField[DeviceScope],
    AsyncTextField[AppScope],
    AsyncTextField[DeviceScope],
]:
    return ElementDescriptor(selector=selector, control_kind=ControlKind.TEXT_FIELD)


def checkbox(
    selector: Selector,
) -> ElementDescriptor[
    Checkbox[AppScope],
    Checkbox[DeviceScope],
    AsyncCheckbox[AppScope],
    AsyncCheckbox[DeviceScope],
]:
    return ElementDescriptor(selector=selector, control_kind=ControlKind.CHECKBOX)


def choice(
    selector: Selector,
) -> ElementDescriptor[
    Choice[AppScope],
    Choice[DeviceScope],
    AsyncChoice[AppScope],
    AsyncChoice[DeviceScope],
]:
    return ElementDescriptor(selector=selector, control_kind=ControlKind.CHOICE)


def scrollable(
    selector: Selector,
) -> ElementDescriptor[
    Scrollable[AppScope],
    Scrollable[DeviceScope],
    AsyncScrollable[AppScope],
    AsyncScrollable[DeviceScope],
]:
    return ElementDescriptor(selector=selector, control_kind=ControlKind.SCROLLABLE)


def by_id(value: str) -> Selector:
    """Select an element by Android resource ID."""

    return Selector.resource_id(value)


def by_accessibility_id(value: str) -> Selector:
    """Select an element by Android content description."""

    return Selector.content_description(value)


def by_text(value: str, *, case_sensitive: bool = True) -> Selector:
    """Select an element whose accessible text is an exact match."""

    return Selector.text(
        TextMatcher(
            value=value,
            mode=MatchMode.EXACT,
            case_sensitive=case_sensitive,
        )
    )


def text_contains(value: str, *, case_sensitive: bool = True) -> Selector:
    """Select an element whose accessible text contains a value."""

    return Selector.text(
        TextMatcher(
            value=value,
            mode=MatchMode.CONTAINS,
            case_sensitive=case_sensitive,
        )
    )


def by_role(
    role: Role,
    *,
    name: str | None = None,
    exact: bool = True,
    case_sensitive: bool = True,
) -> Selector:
    """Select an element by role and, optionally, accessible text."""

    matcher = None
    if name is not None:
        matcher = TextMatcher(
            value=name,
            mode=MatchMode.EXACT if exact else MatchMode.CONTAINS,
            case_sensitive=case_sensitive,
        )
    return Selector.by_role(role, matcher)
