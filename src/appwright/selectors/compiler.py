"""Compile typed selectors to Appium locator plans."""

from enum import StrEnum

from pydantic import Field

from appwright.models.base import StrictModel
from appwright.models.enums import LocatorStrategy, MatchMode, Role
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
    NthNode,
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


class AndroidAttribute(StrEnum):
    CHECKABLE = "checkable"
    CHECKED = "checked"
    CLASS = "class"
    CLICKABLE = "clickable"
    CONTENT_DESCRIPTION = "content-desc"
    ENABLED = "enabled"
    FOCUSABLE = "focusable"
    FOCUSED = "focused"
    HEADING = "heading"
    HINT = "hint"
    PACKAGE = "package"
    RESOURCE_ID = "resource-id"
    SELECTED = "selected"
    TEXT = "text"
    TEXT_HAS_CLICKABLE_SPAN = "text-has-clickable-span"


class AndroidClass(StrEnum):
    BUTTON = "android.widget.Button"
    CHECKBOX = "android.widget.CheckBox"
    CHECKED_TEXT = "android.widget.CheckedTextView"
    DIALOG = "android.app.Dialog"
    EDIT_TEXT = "android.widget.EditText"
    IMAGE_BUTTON = "android.widget.ImageButton"
    IMAGE_VIEW = "android.widget.ImageView"
    LIST_VIEW = "android.widget.ListView"
    PROGRESS_BAR = "android.widget.ProgressBar"
    RADIO_BUTTON = "android.widget.RadioButton"
    RECYCLER_VIEW = "androidx.recyclerview.widget.RecyclerView"
    SEEK_BAR = "android.widget.SeekBar"
    SWITCH = "android.widget.Switch"
    TEXT_VIEW = "android.widget.TextView"


class LocatorPlan(StrictModel):
    strategy: LocatorStrategy
    value: str = Field(min_length=1)
    description: str = Field(min_length=1)
    uses_xpath: bool
    package: str | None = None


class SelectorCompilationError(ValueError):
    """Raised when a typed selector cannot be represented reliably by Appium."""


def validate_xpath_text(value: str) -> None:
    """Reject characters forbidden by XML 1.0 before building an XPath."""

    for character in value:
        codepoint = ord(character)
        valid = (
            codepoint in {0x09, 0x0A, 0x0D}
            or 0x20 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        )
        if not valid:
            raise SelectorCompilationError(
                f"selector text contains a character forbidden by XML 1.0: U+{codepoint:04X}"
            )


def xpath_literal(value: str) -> str:
    validate_xpath_text(value)
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    encoded_parts: list[str] = []
    for index, part in enumerate(parts):
        if part:
            encoded_parts.append(f"'{part}'")
        if index < len(parts) - 1:
            encoded_parts.append('"\'"')
    return f"concat({', '.join(encoded_parts)})"


def matcher_predicate(attribute: AndroidAttribute, matcher: TextMatcher) -> str:
    if matcher.mode is MatchMode.REGEX:
        raise SelectorCompilationError(
            "regular-expression selectors are not supported by the Android XPath 1.0 "
            "backend; use exact or contains matching"
        )
    reference = f"@{attribute.value}"
    expected = xpath_literal(matcher.value)
    if matcher.case_sensitive:
        normalized_reference = reference
        normalized_expected = expected
    else:
        uppercase = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        lowercase = "abcdefghijklmnopqrstuvwxyz"
        normalized_reference = (
            f"translate({reference}, {xpath_literal(uppercase)}, {xpath_literal(lowercase)})"
        )
        normalized_expected = xpath_literal(matcher.value.lower())
    if matcher.mode is MatchMode.EXACT:
        return f"{normalized_reference}={normalized_expected}"
    return f"contains({normalized_reference}, {normalized_expected})"


def role_predicate(role: Role) -> str:
    attribute = AndroidAttribute.CLASS.value
    if role is Role.BUTTON:
        return (
            f"@{attribute}={xpath_literal(AndroidClass.BUTTON)} or "
            f"@{attribute}={xpath_literal(AndroidClass.IMAGE_BUTTON)}"
        )
    if role is Role.CHECKBOX:
        return f"@{attribute}={xpath_literal(AndroidClass.CHECKBOX)}"
    if role is Role.DIALOG:
        return f"@{attribute}={xpath_literal(AndroidClass.DIALOG)}"
    if role is Role.HEADING:
        return f"@{AndroidAttribute.HEADING.value}='true'"
    if role is Role.IMAGE:
        return f"@{attribute}={xpath_literal(AndroidClass.IMAGE_VIEW)}"
    if role is Role.LINK:
        return (
            f"@{AndroidAttribute.CLICKABLE.value}='true' and "
            f"@{AndroidAttribute.TEXT_HAS_CLICKABLE_SPAN.value}='true'"
        )
    if role is Role.LIST:
        return (
            f"@{attribute}={xpath_literal(AndroidClass.LIST_VIEW)} or "
            f"@{attribute}={xpath_literal(AndroidClass.RECYCLER_VIEW)}"
        )
    if role is Role.LIST_ITEM:
        return f"@{attribute}={xpath_literal(AndroidClass.CHECKED_TEXT)}"
    if role is Role.PROGRESS_BAR:
        return f"@{attribute}={xpath_literal(AndroidClass.PROGRESS_BAR)}"
    if role is Role.RADIO:
        return f"@{attribute}={xpath_literal(AndroidClass.RADIO_BUTTON)}"
    if role is Role.SLIDER:
        return f"@{attribute}={xpath_literal(AndroidClass.SEEK_BAR)}"
    if role is Role.SWITCH:
        return f"@{attribute}={xpath_literal(AndroidClass.SWITCH)}"
    if role is Role.TEXTBOX:
        return f"@{attribute}={xpath_literal(AndroidClass.EDIT_TEXT)}"
    if role is Role.TEXT:
        return f"@{attribute}={xpath_literal(AndroidClass.TEXT_VIEW)}"
    unsupported_roles = ", ".join(
        unsupported.value for unsupported in (Role.MENU, Role.MENU_ITEM, Role.TAB)
    )
    raise SelectorCompilationError(
        f"role {role.value!r} has no reliable Android accessibility mapping; "
        f"unsupported roles: {unsupported_roles}"
    )


def accessible_text_predicate(matcher: TextMatcher) -> str:
    text_match = matcher_predicate(AndroidAttribute.TEXT, matcher)
    description_match = matcher_predicate(AndroidAttribute.CONTENT_DESCRIPTION, matcher)
    return f"({text_match}) or ({description_match})"


def selector_predicate(node: SelectorNode) -> str:
    if isinstance(node, (ResourceIdNode, TestIdNode)):
        return f"@{AndroidAttribute.RESOURCE_ID.value}={xpath_literal(node.value)}"
    if isinstance(node, ContentDescriptionNode):
        return f"@{AndroidAttribute.CONTENT_DESCRIPTION.value}={xpath_literal(node.value)}"
    if isinstance(node, ClassNameNode):
        return f"@{AndroidAttribute.CLASS.value}={xpath_literal(node.value)}"
    if isinstance(node, PlaceholderNode):
        return f"@{AndroidAttribute.HINT.value}={xpath_literal(node.value)}"
    if isinstance(node, TextNode):
        return accessible_text_predicate(node.matcher)
    if isinstance(node, LabelNode):
        return matcher_predicate(AndroidAttribute.CONTENT_DESCRIPTION, node.matcher)
    if isinstance(node, RoleNode):
        return role_predicate(node.role)
    if isinstance(node, AndNode):
        return f"({selector_predicate(node.left)}) and ({selector_predicate(node.right)})"
    if isinstance(node, OrNode):
        return f"({selector_predicate(node.left)}) or ({selector_predicate(node.right)})"
    if isinstance(node, DescendantNode):
        parent = selector_predicate(node.left)
        child = selector_predicate(node.right)
        return f"({child}) and ancestor::*[{parent}]"
    if isinstance(node, HasNode):
        parent = selector_predicate(node.left)
        child = selector_predicate(node.right)
        return f"({parent}) and descendant::*[{child}]"
    if isinstance(node, HasNotNode):
        parent = selector_predicate(node.left)
        child = selector_predicate(node.right)
        return f"({parent}) and not(descendant::*[{child}])"
    if isinstance(node, HasTextNode):
        parent = selector_predicate(node.left)
        matching_text = accessible_text_predicate(node.matcher)
        return f"({parent}) and (({matching_text}) or descendant::*[{matching_text}])"
    if isinstance(node, HasNotTextNode):
        parent = selector_predicate(node.left)
        matching_text = accessible_text_predicate(node.matcher)
        return f"({parent}) and not((({matching_text}) or descendant::*[{matching_text}]))"
    return selector_predicate(node.left)


def selector_expression(node: SelectorNode, package: str | None) -> str:
    if isinstance(node, NthNode):
        base = selector_expression(node.left, package)
        if node.index >= 0:
            return f"({base})[{node.index + 1}]"
        if node.index == -1:
            return f"({base})[last()]"
        return f"({base})[last()-{abs(node.index) - 1}]"
    predicate = selector_predicate(node)
    if package is not None:
        package_predicate = f"@{AndroidAttribute.PACKAGE.value}={xpath_literal(package)}"
        predicate = f"({predicate}) and ({package_predicate})"
    return f"//*[{predicate}]"


def describe_node(node: SelectorNode) -> str:
    if isinstance(
        node,
        (
            ResourceIdNode,
            ContentDescriptionNode,
            ClassNameNode,
            PlaceholderNode,
            TestIdNode,
        ),
    ):
        return f"{node.kind.value}={node.value!r}"
    if isinstance(node, (TextNode, LabelNode)):
        return f"text({node.matcher.mode.value})={node.matcher.value!r}"
    if isinstance(node, (HasTextNode, HasNotTextNode)):
        return (
            f"{node.kind.value}({describe_node(node.left)}, "
            f"{node.matcher.mode.value}={node.matcher.value!r})"
        )
    if isinstance(node, RoleNode):
        return f"role={node.role.value}"
    if isinstance(node, NthNode):
        return f"{describe_node(node.left)}.nth({node.index})"
    return f"{node.kind.value}({describe_node(node.left)}, {describe_node(node.right)})"


def compile_selector(selector: Selector, package: str | None = None) -> LocatorPlan:
    node = selector.node
    description = describe_node(node)
    if isinstance(node, (ResourceIdNode, TestIdNode)):
        value = node.value
        if package is not None and ":id/" not in value:
            value = f"{package}:id/{value}"
        return LocatorPlan(
            strategy=LocatorStrategy.ID,
            value=value,
            description=description,
            uses_xpath=False,
            package=package,
        )
    if isinstance(node, ContentDescriptionNode):
        return LocatorPlan(
            strategy=LocatorStrategy.ACCESSIBILITY_ID,
            value=node.value,
            description=description,
            uses_xpath=False,
            package=package,
        )
    if isinstance(node, ClassNameNode):
        return LocatorPlan(
            strategy=LocatorStrategy.CLASS_NAME,
            value=node.value,
            description=description,
            uses_xpath=False,
            package=package,
        )
    return LocatorPlan(
        strategy=LocatorStrategy.XPATH,
        value=selector_expression(node, package),
        description=description,
        uses_xpath=True,
        package=package,
    )
