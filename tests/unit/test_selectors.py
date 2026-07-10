"""Selector compilation tests."""

import pytest
from hypothesis import given, strategies

from appwright.models.enums import LocatorStrategy, MatchMode, Role
from appwright.selectors.compiler import (
    SelectorCompilationError,
    compile_selector,
    xpath_literal,
)
from appwright.selectors.models import Selector, TextMatcher


def test_resource_id_uses_native_strategy() -> None:
    plan = compile_selector(Selector.resource_id("submit"), "com.example")
    assert plan.strategy is LocatorStrategy.ID
    assert plan.value == "com.example:id/submit"
    assert not plan.uses_xpath


def test_content_description_uses_accessibility_id() -> None:
    plan = compile_selector(Selector.content_description("Continue"))
    assert plan.strategy is LocatorStrategy.ACCESSIBILITY_ID
    assert plan.value == "Continue"


def test_composed_selector_uses_internal_xpath() -> None:
    selector = Selector.by_role(Role.BUTTON).and_selector(
        Selector.text(TextMatcher(value="Sign in", mode=MatchMode.EXACT))
    )
    plan = compile_selector(selector, "com.example")
    assert plan.strategy is LocatorStrategy.XPATH
    assert plan.uses_xpath
    assert "android.widget.Button" in plan.value
    assert "com.example" in plan.value


def test_descendant_selector_targets_child() -> None:
    parent = Selector.class_name("android.widget.ListView")
    child = Selector.text(TextMatcher(value="Item", mode=MatchMode.EXACT))
    selector = parent.descendant(child)
    plan = compile_selector(selector)
    assert "ancestor::*" in plan.value


def test_composition_categories_compile_from_typed_nodes() -> None:
    parent = Selector.class_name("android.widget.ListView")
    child = Selector.text(TextMatcher(value="Item", mode=MatchMode.CONTAINS))
    alternatives = child.or_selector(Selector.content_description("Fallback"))
    plan = compile_selector(parent.has(alternatives).has_not(Selector.test_id("disabled")).nth(-1))
    assert plan.strategy is LocatorStrategy.XPATH
    assert "descendant::*" in plan.value
    assert "not(descendant::*" in plan.value
    assert "last()" in plan.value


def test_native_locator_plan_retains_application_scope() -> None:
    plan = compile_selector(Selector.content_description("Continue"), "com.example")
    assert plan.strategy is LocatorStrategy.ACCESSIBILITY_ID
    assert plan.package == "com.example"


def test_placeholder_and_case_insensitive_text_use_xpath() -> None:
    placeholder = compile_selector(Selector.placeholder("Email"))
    text = compile_selector(
        Selector.text(
            TextMatcher(
                value="WELCOME",
                mode=MatchMode.CONTAINS,
                case_sensitive=False,
            )
        )
    )
    assert "@hint='Email'" in placeholder.value
    assert "translate(" in text.value


def test_label_matches_only_android_accessibility_label() -> None:
    plan = compile_selector(
        Selector.label(TextMatcher(value="Email address", mode=MatchMode.EXACT))
    )
    assert plan.strategy is LocatorStrategy.XPATH
    assert plan.value == "//*[@content-desc='Email address']"
    assert "@text" not in plan.value


def test_has_text_matches_own_and_descendant_accessible_text() -> None:
    selector = Selector.class_name("android.view.View").has_text(
        TextMatcher(value="Premium", mode=MatchMode.CONTAINS)
    )
    plan = compile_selector(selector)
    assert "descendant::*" in plan.value
    assert "contains(@text, 'Premium')" in plan.value
    assert "contains(@content-desc, 'Premium')" in plan.value


def test_has_not_text_excludes_own_and_descendant_accessible_text() -> None:
    selector = Selector.class_name("android.view.View").has_not_text(
        TextMatcher(value="Disabled", mode=MatchMode.CONTAINS)
    )
    plan = compile_selector(selector)
    assert "and not(" in plan.value
    assert "descendant::*" in plan.value


@pytest.mark.parametrize("role", [Role.MENU, Role.MENU_ITEM, Role.TAB])
def test_roles_without_reliable_android_mapping_are_rejected(role: Role) -> None:
    with pytest.raises(SelectorCompilationError, match="no reliable Android"):
        compile_selector(Selector.by_role(role))


def test_regex_is_rejected_instead_of_emitting_nonportable_xpath() -> None:
    selector = Selector.text(TextMatcher(value="Sign (in|up)", mode=MatchMode.REGEX))
    with pytest.raises(SelectorCompilationError, match=r"XPath 1\.0"):
        compile_selector(selector)


@given(
    strategies.text(
        alphabet=strategies.characters(
            codec="utf-8",
            blacklist_categories=("Cc", "Cs"),
        )
    )
)
def test_xpath_literals_quote_valid_unicode_and_adversarial_text(value: str) -> None:
    literal = xpath_literal(value)
    assert literal.startswith(("'", '"', "concat("))
    assert literal.endswith(("'", '"', ")"))


@pytest.mark.parametrize("value", ["\x00", "\x01", "\x08", "\x0b", "\x0c", "\ud800"])
def test_xpath_literals_reject_xml_forbidden_characters(value: str) -> None:
    with pytest.raises(SelectorCompilationError, match=r"forbidden by XML 1\.0"):
        xpath_literal(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("simple", "'simple'"),
        ("it's", '"it\'s"'),
        ('say "hello"', "'say \"hello\"'"),
        ("both ' and \"", "concat('both ', \"'\", ' and \"')"),
        ("emoji: 🧪", "'emoji: 🧪'"),
    ],
)
def test_xpath_literal_encoding_is_deterministic(value: str, expected: str) -> None:
    assert xpath_literal(value) == expected
