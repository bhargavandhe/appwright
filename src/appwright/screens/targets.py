"""Typed screen-race targets and matched screen choices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeAlias, TypeVar, overload

from appwright.screens.model import AppScope, AsyncScreen, DeviceScope, Screen

SyncScreenDefinition: TypeAlias = Screen[AppScope] | Screen[DeviceScope]
AsyncScreenDefinition: TypeAlias = AsyncScreen[AppScope] | AsyncScreen[DeviceScope]
ScreenDefinition: TypeAlias = SyncScreenDefinition | AsyncScreenDefinition
ScreenT = TypeVar("ScreenT", bound=ScreenDefinition)


@dataclass(frozen=True, slots=True)
class ScreenTarget(Generic[ScreenT]):
    """An ordered set of screen definitions evaluated from one observation."""

    screens: tuple[type[ScreenT], ...]


@dataclass(frozen=True, slots=True)
class ScreenChoice(Generic[ScreenT]):
    """The bound screen selected from a target and its observation sequence."""

    screen: ScreenT
    observation_sequence: int


ScreenA = TypeVar("ScreenA", bound=ScreenDefinition)
ScreenB = TypeVar("ScreenB", bound=ScreenDefinition)
ScreenC = TypeVar("ScreenC", bound=ScreenDefinition)
ScreenD = TypeVar("ScreenD", bound=ScreenDefinition)
ScreenE = TypeVar("ScreenE", bound=ScreenDefinition)
ScreenF = TypeVar("ScreenF", bound=ScreenDefinition)


@overload
def one_of(
    first: type[ScreenA],
    second: type[ScreenB],
    /,
) -> ScreenTarget[ScreenA | ScreenB]: ...


@overload
def one_of(
    first: type[ScreenA],
    second: type[ScreenB],
    third: type[ScreenC],
    /,
) -> ScreenTarget[ScreenA | ScreenB | ScreenC]: ...


@overload
def one_of(
    first: type[ScreenA],
    second: type[ScreenB],
    third: type[ScreenC],
    fourth: type[ScreenD],
    /,
) -> ScreenTarget[ScreenA | ScreenB | ScreenC | ScreenD]: ...


@overload
def one_of(
    first: type[ScreenA],
    second: type[ScreenB],
    third: type[ScreenC],
    fourth: type[ScreenD],
    fifth: type[ScreenE],
    /,
) -> ScreenTarget[ScreenA | ScreenB | ScreenC | ScreenD | ScreenE]: ...


@overload
def one_of(
    first: type[ScreenA],
    second: type[ScreenB],
    third: type[ScreenC],
    fourth: type[ScreenD],
    fifth: type[ScreenE],
    sixth: type[ScreenF],
    /,
) -> ScreenTarget[ScreenA | ScreenB | ScreenC | ScreenD | ScreenE | ScreenF]: ...


def one_of(
    first: type[Any],
    second: type[Any],
    /,
    *others: type[Any],
) -> ScreenTarget[Any]:
    """Build a two-to-six-screen target while retaining declaration order."""

    screens = (first, second, *others)
    if len(screens) > 6:
        raise ValueError("one_of accepts at most six screen types")
    return ScreenTarget(screens=screens)
