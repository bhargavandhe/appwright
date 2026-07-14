"""Typed screen-definition model tests."""

from abc import ABC, abstractmethod
from dataclasses import FrozenInstanceError
from inspect import isabstract
from typing import Any, TypeVar, cast

import pytest

from appwright.models.enums import MatchMode
from appwright.screens import (
    AllOf,
    AnyOf,
    AppScope,
    DeviceScope,
    DeviceScreen,
    ElementDescriptor,
    Interruption,
    Screen,
    ScreenChoice,
    Visible,
    all_of,
    any_of,
    button,
    by_accessibility_id,
    by_id,
    by_text,
    one_of,
    text_contains,
    text_field,
    visible,
)
from appwright.selectors.models import TextNode


class Login(Screen):
    ready = visible(by_id("login_submit"))

    user_id = text_field(by_accessibility_id("User I.D"))
    submit = button(by_id("login_submit"))


class Home(Screen):
    ready = all_of(
        visible(by_id("menu_home")),
        visible(by_id("menu_actions")),
    )

    actions = button(by_id("menu_actions"))


class Permission(DeviceScreen):
    ready = visible(by_id("permission_deny_button"))

    deny = button(by_id("permission_deny_button"))


class PasskeyInterruption(Interruption):
    ready = visible(by_id("maybe_later"))
    priority = 100

    maybe_later = button(by_id("maybe_later"))

    def dismiss(self) -> None:
        self.maybe_later.tap()


class LowPriorityInterruption(Interruption):
    ready = visible(by_id("low_priority"))
    priority = 10

    def dismiss(self) -> None:
        return None


class DevicePermissionInterruption(DeviceScreen, Interruption[DeviceScope]):
    ready = visible(by_id("permission_allow_button"))
    priority = 200

    allow = button(by_id("permission_allow_button"))

    def dismiss(self) -> None:
        self.allow.tap()


BoundAppT = TypeVar("BoundAppT")
BoundDeviceT = TypeVar("BoundDeviceT")


class RecordingBinder:
    def __init__(self, bound: object) -> None:
        self.bound = bound
        self.calls: list[ElementDescriptor[Any, Any]] = []

    def bind(
        self,
        descriptor: ElementDescriptor[BoundAppT, BoundDeviceT],
    ) -> BoundAppT | BoundDeviceT:
        self.calls.append(descriptor)
        return cast(BoundAppT | BoundDeviceT, self.bound)


def test_descriptor_class_access_returns_the_descriptor() -> None:
    descriptor = Login.submit

    assert isinstance(descriptor, ElementDescriptor)
    assert descriptor is Login.submit
    assert descriptor.selector == by_id("login_submit")


def test_descriptor_instance_access_asks_the_supplied_binder() -> None:
    bound = object()
    binder = RecordingBinder(bound)
    login = Login(binder)

    assert login.submit is bound
    assert binder.calls == [Login.submit]


def test_screen_scope_is_explicit_at_runtime() -> None:
    assert Login.scope is AppScope
    assert Home.scope is AppScope
    assert Permission.scope is DeviceScope


def test_readiness_nodes_are_immutable_and_preserve_order() -> None:
    login = visible(by_id("login_submit"))
    home = visible(by_id("menu_home"))
    conjunction = all_of(login, home)
    disjunction = any_of(login, home)

    assert isinstance(login, Visible)
    assert isinstance(conjunction, AllOf)
    assert conjunction.conditions == (login, home)
    assert isinstance(disjunction, AnyOf)
    assert disjunction.conditions == (login, home)
    with pytest.raises(FrozenInstanceError):
        login.selector = by_id("replacement")  # type: ignore[misc]


def test_selector_helpers_build_exact_and_contains_matchers() -> None:
    exact_node = by_text("Continue").node
    contains_node = text_contains("continue", case_sensitive=False).node

    assert isinstance(exact_node, TextNode)
    assert exact_node.matcher.mode is MatchMode.EXACT
    assert exact_node.matcher.case_sensitive
    assert isinstance(contains_node, TextNode)
    assert contains_node.matcher.mode is MatchMode.CONTAINS
    assert not contains_node.matcher.case_sensitive


def test_interruption_priorities_are_ordered_integers() -> None:
    ordered = sorted(
        (LowPriorityInterruption, PasskeyInterruption),
        key=lambda interruption: interruption.priority,
        reverse=True,
    )

    assert ordered == [PasskeyInterruption, LowPriorityInterruption]
    assert all(isinstance(interruption.priority, int) for interruption in ordered)


def test_concrete_screen_missing_ready_fails_at_definition_time() -> None:
    with pytest.raises(TypeError, match=r"MissingReadyScreen.*ready"):

        class MissingReadyScreen(Screen):  # pyright: ignore[reportUnusedClass]
            pass


@pytest.mark.parametrize("readiness", [None, "visible", True])
def test_concrete_screen_rejects_direct_non_readiness_values(readiness: object) -> None:
    invalid_readiness = readiness
    with pytest.raises(TypeError, match=r"InvalidReadyScreen.*ready.*Readiness"):

        class InvalidReadyScreen(Screen):  # pyright: ignore[reportUnusedClass]
            ready = invalid_readiness  # type: ignore[assignment]


def test_concrete_screen_rejects_inherited_non_readiness_value() -> None:
    class InvalidReadyBase(Screen, ABC):
        ready = None  # type: ignore[assignment]

        @abstractmethod
        def marker(self) -> None: ...

    with pytest.raises(TypeError, match=r"InheritedInvalidReadyScreen.*ready.*Readiness"):

        class InheritedInvalidReadyScreen(  # pyright: ignore[reportUnusedClass]
            InvalidReadyBase
        ):
            def marker(self) -> None:
                return None


def test_concrete_interruption_missing_ready_fails_at_definition_time() -> None:
    with pytest.raises(TypeError, match=r"MissingReadyInterruption.*ready"):

        class MissingReadyInterruption(  # pyright: ignore[reportUnusedClass]
            Interruption
        ):
            priority = 20

            def dismiss(self) -> None:
                return None


def test_concrete_interruption_missing_priority_fails_at_definition_time() -> None:
    with pytest.raises(TypeError, match=r"MissingPriorityInterruption.*priority"):

        class MissingPriorityInterruption(  # pyright: ignore[reportUnusedClass]
            Interruption
        ):
            ready = visible(by_id("dismiss"))

            def dismiss(self) -> None:
                return None


@pytest.mark.parametrize("priority", [True, "high"])
def test_interruption_rejects_bool_and_non_integer_priorities(priority: object) -> None:
    invalid_priority = priority
    with pytest.raises(TypeError, match=r"InvalidPriorityInterruption.*priority.*integer"):

        class InvalidPriorityInterruption(  # pyright: ignore[reportUnusedClass]
            Interruption
        ):
            ready = visible(by_id("dismiss"))
            priority = invalid_priority  # type: ignore[assignment]

            def dismiss(self) -> None:
                return None


def test_framework_and_abstract_screen_bases_may_omit_ready() -> None:
    class AbstractScreen(Screen, ABC):
        @abstractmethod
        def marker(self) -> None: ...

    assert issubclass(DeviceScreen, Screen)
    assert isabstract(Interruption)
    assert isabstract(AbstractScreen)


def test_concrete_definitions_may_inherit_readiness_and_priority() -> None:
    inherited_ready = visible(by_id("inherited"))

    class ReadyBase(Screen):
        ready = inherited_ready

    class ReadyChild(ReadyBase):
        pass

    class InterruptionBase(Interruption):
        ready = inherited_ready
        priority = 30

        def dismiss(self) -> None:
            return None

    class InterruptionChild(InterruptionBase):
        pass

    assert ReadyChild.ready is inherited_ready
    assert InterruptionChild.ready is inherited_ready
    assert InterruptionChild.priority == 30


def test_device_interruption_multiple_inheritance_preserves_device_scope() -> None:
    assert DevicePermissionInterruption.scope is DeviceScope
    assert DevicePermissionInterruption.priority == 200


def test_one_of_preserves_the_exact_screen_types() -> None:
    target = one_of(Home, Login)

    assert target.screens == (Home, Login)


def test_one_of_accepts_at_most_six_screens() -> None:
    with pytest.raises(ValueError, match="at most six"):
        one_of(Home, Login, Home, Login, Home, Login, Home)  # type: ignore[call-overload]


def test_screen_choice_records_the_matching_observation_sequence() -> None:
    home = Home(RecordingBinder(object()))
    choice = ScreenChoice(screen=home, observation_sequence=27)

    assert choice.screen is home
    assert choice.observation_sequence == 27
