"""Evaluate typed selectors against a local immutable hierarchy."""

from __future__ import annotations

import re
from collections.abc import Iterator

from appwright.models.data import ElementSnapshot
from appwright.models.enums import MatchMode, Role
from appwright.observations.models import Observation, ObservationMatch, ObservedElement
from appwright.selectors.compiler import AndroidClass, SelectorCompilationError
from appwright.selectors.models import (
    AndNode,
    ClassNameNode,
    ContentDescriptionNode,
    DescendantNode,
    HasNode,
    HasNotNode,
    HasNotTextNode,
    HasTextNode,
    LabelNode,
    OrNode,
    PlaceholderNode,
    ResourceIdNode,
    RoleNode,
    Selector,
    SelectorNode,
    TestIdNode,
    TextMatcher,
    TextNode,
)

CLASS_ROLES: dict[Role, frozenset[str]] = {
    Role.BUTTON: frozenset((AndroidClass.BUTTON.value, AndroidClass.IMAGE_BUTTON.value)),
    Role.CHECKBOX: frozenset((AndroidClass.CHECKBOX.value,)),
    Role.DIALOG: frozenset((AndroidClass.DIALOG.value,)),
    Role.IMAGE: frozenset((AndroidClass.IMAGE_VIEW.value,)),
    Role.LIST: frozenset((AndroidClass.LIST_VIEW.value, AndroidClass.RECYCLER_VIEW.value)),
    Role.LIST_ITEM: frozenset((AndroidClass.CHECKED_TEXT.value,)),
    Role.PROGRESS_BAR: frozenset((AndroidClass.PROGRESS_BAR.value,)),
    Role.RADIO: frozenset((AndroidClass.RADIO_BUTTON.value,)),
    Role.SLIDER: frozenset((AndroidClass.SEEK_BAR.value,)),
    Role.SWITCH: frozenset((AndroidClass.SWITCH.value,)),
    Role.TEXTBOX: frozenset((AndroidClass.EDIT_TEXT.value,)),
    Role.TEXT: frozenset((AndroidClass.TEXT_VIEW.value,)),
}
UNSUPPORTED_ROLES = frozenset((Role.MENU, Role.MENU_ITEM, Role.TAB))


def _matches_string(candidate: str, matcher: TextMatcher) -> bool:
    if matcher.mode is MatchMode.REGEX:
        flags = 0 if matcher.case_sensitive else re.IGNORECASE
        return re.search(matcher.value, candidate, flags=flags) is not None
    if matcher.case_sensitive:
        actual = candidate
        expected = matcher.value
    else:
        actual = candidate.casefold()
        expected = matcher.value.casefold()
    if matcher.mode is MatchMode.EXACT:
        return actual == expected
    return expected in actual


def _matches_accessible_text(snapshot: ElementSnapshot, matcher: TextMatcher) -> bool:
    return _matches_string(snapshot.text, matcher) or _matches_string(
        snapshot.accessible_name, matcher
    )


def _resource_id(value: str, package: str | None) -> str:
    if package is not None and ":id/" not in value:
        return f"{package}:id/{value}"
    return value


def _matches_role(element: ObservedElement, role: Role) -> bool:
    if role in UNSUPPORTED_ROLES:
        unsupported = ", ".join(item.value for item in sorted(UNSUPPORTED_ROLES))
        raise SelectorCompilationError(
            f"role {role.value!r} has no reliable Android accessibility mapping; "
            f"unsupported roles: {unsupported}"
        )
    if role is Role.HEADING:
        return element.heading
    if role is Role.LINK:
        return element.clickable and element.text_has_clickable_span
    return element.snapshot.class_name in CLASS_ROLES[role]


def _primitive_matches(
    element: ObservedElement,
    node: SelectorNode,
    *,
    package: str | None,
) -> bool:
    snapshot = element.snapshot
    if isinstance(node, (ResourceIdNode, TestIdNode)):
        return snapshot.resource_id == _resource_id(node.value, package)
    if isinstance(node, ContentDescriptionNode):
        return snapshot.accessible_name == node.value
    if isinstance(node, ClassNameNode):
        return snapshot.class_name == node.value
    if isinstance(node, TextNode):
        return _matches_accessible_text(snapshot, node.matcher)
    if isinstance(node, LabelNode):
        return _matches_string(snapshot.accessible_name, node.matcher)
    if isinstance(node, PlaceholderNode):
        return element.hint == node.value
    if isinstance(node, RoleNode):
        return _matches_role(element, node.role)
    raise TypeError(f"not a primitive selector node: {type(node).__name__}")


def _descendants(observation: Observation, index: int) -> Iterator[int]:
    pending = list(reversed(observation.elements[index].children))
    while pending:
        descendant = pending.pop()
        yield descendant
        pending.extend(reversed(observation.elements[descendant].children))


def _has_ancestor(observation: Observation, index: int, ancestors: set[int]) -> bool:
    parent = observation.elements[index].parent
    while parent is not None:
        if parent in ancestors:
            return True
        parent = observation.elements[parent].parent
    return False


def _tree_matches_text(
    observation: Observation,
    index: int,
    matcher: TextMatcher,
    eligible: set[int],
) -> bool:
    candidates = (index, *_descendants(observation, index))
    return any(
        candidate in eligible
        and _matches_accessible_text(observation.elements[candidate].snapshot, matcher)
        for candidate in candidates
    )


def _evaluate_node(
    observation: Observation,
    node: SelectorNode,
    *,
    package: str | None,
    eligible: tuple[int, ...],
) -> tuple[int, ...]:
    if isinstance(
        node,
        (
            ResourceIdNode,
            ContentDescriptionNode,
            ClassNameNode,
            TextNode,
            LabelNode,
            PlaceholderNode,
            TestIdNode,
            RoleNode,
        ),
    ):
        return tuple(
            index
            for index in eligible
            if _primitive_matches(observation.elements[index], node, package=package)
        )
    if isinstance(node, AndNode):
        left = _evaluate_node(observation, node.left, package=package, eligible=eligible)
        right = set(_evaluate_node(observation, node.right, package=package, eligible=eligible))
        return tuple(index for index in left if index in right)
    if isinstance(node, OrNode):
        selected = set(_evaluate_node(observation, node.left, package=package, eligible=eligible))
        selected.update(_evaluate_node(observation, node.right, package=package, eligible=eligible))
        return tuple(index for index in eligible if index in selected)
    if isinstance(node, DescendantNode):
        ancestor_indices = set(
            _evaluate_node(observation, node.left, package=package, eligible=eligible)
        )
        descendant_indices = _evaluate_node(
            observation, node.right, package=package, eligible=eligible
        )
        return tuple(
            index
            for index in descendant_indices
            if _has_ancestor(observation, index, ancestor_indices)
        )
    if isinstance(node, (HasNode, HasNotNode)):
        parent_indices = _evaluate_node(observation, node.left, package=package, eligible=eligible)
        child_indices = set(
            _evaluate_node(observation, node.right, package=package, eligible=eligible)
        )
        has_children = (
            (
                index,
                any(descendant in child_indices for descendant in _descendants(observation, index)),
            )
            for index in parent_indices
        )
        if isinstance(node, HasNode):
            return tuple(index for index, matches in has_children if matches)
        return tuple(index for index, matches in has_children if not matches)
    if isinstance(node, (HasTextNode, HasNotTextNode)):
        text_parent_indices = _evaluate_node(
            observation, node.left, package=package, eligible=eligible
        )
        eligible_set = set(eligible)
        text_matches = (
            (
                index,
                _tree_matches_text(
                    observation,
                    index,
                    node.matcher,
                    eligible_set,
                ),
            )
            for index in text_parent_indices
        )
        if isinstance(node, HasTextNode):
            return tuple(index for index, matches in text_matches if matches)
        return tuple(index for index, matches in text_matches if not matches)
    matches = _evaluate_node(observation, node.left, package=package, eligible=eligible)
    try:
        return (matches[node.index],)
    except IndexError:
        return ()


def evaluate_selector(
    observation: Observation,
    selector: Selector,
    *,
    package: str | None,
) -> ObservationMatch:
    """Evaluate a selector locally and return snapshots in document order."""

    eligible = tuple(
        index
        for index, element in enumerate(observation.elements)
        if package is None or element.snapshot.package_name == package
    )
    indices = _evaluate_node(
        observation,
        selector.node,
        package=package,
        eligible=eligible,
    )
    return ObservationMatch(
        elements=tuple(observation.elements[index].snapshot for index in indices)
    )
