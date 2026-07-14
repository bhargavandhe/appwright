"""Hierarchy parsing and local selector evaluation tests."""

from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree

import pytest
from pydantic import ValidationError

from appwright.models.data import ElementSnapshot, Rect
from appwright.models.enums import MatchMode, Role
from appwright.observations import (
    Observation,
    ObservedElement,
    evaluate_selector,
    parse_hierarchy,
)
from appwright.selectors.compiler import SelectorCompilationError
from appwright.selectors.models import Selector, TextMatcher

XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="com.example:id/root"
        class="android.widget.FrameLayout" package="com.example" content-desc="App root"
        checkable="false" checked="false" clickable="false" enabled="true"
        focusable="false" focused="false" selected="false" heading="false"
        text-has-clickable-span="false" bounds="[0,0][1080,1920]" displayed="true">
    <node index="0" text="Continue" resource-id="com.example:id/submit"
          class="android.widget.Button" package="com.example" content-desc="Primary action"
          checkable="false" checked="false" clickable="true" enabled="true"
          focusable="true" focused="false" selected="false" heading="false"
          text-has-clickable-span="false" bounds="[10,10][210,90]" displayed="true" />
    <node index="1" text="" resource-id="com.example:id/username"
          class="android.widget.EditText" package="com.example" content-desc="Username"
          hint="Email address" checkable="false" checked="false" clickable="true"
          enabled="true" focusable="true" focused="true" selected="false" heading="false"
          text-has-clickable-span="false" bounds="[10,100][510,180]" displayed="true" />
    <node index="2" text="" resource-id="com.example:id/card"
          class="android.view.View" package="com.example" content-desc=""
          checkable="false" checked="false" clickable="false" enabled="true"
          focusable="false" focused="false" selected="false" heading="false"
          text-has-clickable-span="false" bounds="[10,200][510,500]" displayed="true">
      <node index="0" text="Premium Plan" resource-id="com.example:id/plan_name"
            class="android.widget.TextView" package="com.example" content-desc=""
            checkable="false" checked="false" clickable="false" enabled="true"
            focusable="false" focused="false" selected="false" heading="true"
            text-has-clickable-span="false" bounds="[20,220][300,280]" displayed="true" />
    </node>
    <node index="3" text="Continue" resource-id="com.example:id/hidden_continue"
          class="android.widget.TextView" package="com.example" content-desc=""
          checkable="false" checked="false" clickable="false" enabled="true"
          focusable="false" focused="false" selected="false" heading="false"
          text-has-clickable-span="false" bounds="[0,0][0,0]" displayed="true" />
    <node index="4" text="Terms" resource-id="com.example:id/terms"
          class="android.widget.TextView" package="com.example" content-desc=""
          checkable="false" checked="false" clickable="true" enabled="true"
          focusable="true" focused="false" selected="false" heading="false"
          text-has-clickable-span="false" bounds="[20,520][200,580]" displayed="true" />
    <node index="5" text="Privacy" resource-id="com.example:id/privacy"
          class="android.widget.TextView" package="com.example" content-desc=""
          checkable="false" checked="false" clickable="true" enabled="true"
          focusable="false" focused="false" selected="false" heading="false"
          text-has-clickable-span="true" bounds="[20,600][200,660]" displayed="true" />
  </node>
  <node index="1" text="Don't allow"
        resource-id="com.android.permissioncontroller:id/permission_deny_button"
        class="android.widget.Button" package="com.android.permissioncontroller"
        content-desc="Deny permission" checkable="false" checked="false" clickable="true"
        enabled="true" focusable="true" focused="false" selected="false" heading="false"
        text-has-clickable-span="false" bounds="[40,1600][500,1700]" displayed="true" />
</hierarchy>
"""


def observation() -> Observation:
    return parse_hierarchy(XML, sequence=7, package=None)


def resource_ids(selector: Selector, *, package: str | None = "com.example") -> list[str]:
    return [
        element.resource_id
        for element in evaluate_selector(observation(), selector, package=package).elements
    ]


def tree_element(
    identity: str,
    *,
    parent: int | None,
    children: tuple[int, ...],
) -> ObservedElement:
    return ObservedElement(
        snapshot=ElementSnapshot(
            identity=identity,
            displayed=True,
            enabled=True,
            selected=False,
            checked=False,
            checkable=False,
            focusable=False,
            focused=False,
            editable=False,
            bounds=Rect(x=0, y=0, width=1, height=1),
        ),
        parent=parent,
        children=children,
    )


def model_observation(elements: tuple[ObservedElement, ...]) -> Observation:
    return Observation(
        sequence=1,
        captured_at=datetime.now(UTC),
        elapsed=timedelta(),
        package=None,
        elements=elements,
    )


def test_parse_hierarchy_builds_immutable_document_order_tree() -> None:
    parsed = observation()

    assert parsed.sequence == 7
    assert parsed.captured_at.utcoffset() is not None
    assert [element.snapshot.identity for element in parsed.elements] == [
        f"observation-7-{index}" for index in range(9)
    ]
    assert parsed.elements[0].parent is None
    assert parsed.elements[0].children == (1, 2, 3, 5, 6, 7)
    assert parsed.elements[3].children == (4,)
    assert parsed.elements[4].parent == 3
    assert parsed.elements[8].parent is None


def test_parse_hierarchy_maps_snapshot_and_raw_android_attributes() -> None:
    parsed = observation()

    assert parsed.elements[1].clickable
    assert parsed.elements[2].hint == "Email address"
    assert parsed.elements[2].snapshot.editable
    assert parsed.elements[2].snapshot.focused
    assert parsed.elements[4].heading
    assert parsed.elements[7].text_has_clickable_span
    assert not parsed.elements[5].snapshot.displayed
    assert parsed.elements[5].snapshot.bounds.width == 0


def test_observation_rejects_naive_capture_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Observation(
            sequence=7,
            captured_at=datetime(2026, 7, 11),
            elapsed=timedelta(),
            package=None,
            elements=(),
        )


def test_observation_is_frozen() -> None:
    parsed = observation()

    with pytest.raises(ValidationError, match="frozen"):
        parsed.sequence = 8  # type: ignore[misc]


def test_parser_accepts_appium_class_name_tags() -> None:
    parsed = parse_hierarchy(
        """
        <hierarchy rotation="0">
          <android.widget.Button index="0" text="Log in"
            resource-id="com.example:id/login_submit" package="com.example"
            clickable="true" enabled="true" bounds="[10,20][110,70]" />
        </hierarchy>
        """,
        sequence=9,
        package="com.example",
    )

    assert len(parsed.elements) == 1
    snapshot = parsed.elements[0].snapshot
    assert snapshot.class_name == "android.widget.Button"
    assert snapshot.resource_id == "com.example:id/login_submit"
    assert snapshot.displayed


@pytest.mark.parametrize(
    "elements",
    [
        (tree_element("root", parent=1, children=()),),
        (tree_element("root", parent=-1, children=()),),
    ],
)
def test_observation_rejects_out_of_bounds_parent_indices(
    elements: tuple[ObservedElement, ...],
) -> None:
    with pytest.raises(ValidationError, match=r"parent index .* out of bounds"):
        model_observation(elements)


@pytest.mark.parametrize("child", [-1, 1])
def test_observation_rejects_out_of_bounds_child_indices(child: int) -> None:
    elements = (tree_element("root", parent=None, children=(child,)),)

    with pytest.raises(ValidationError, match=r"child index .* out of bounds"):
        model_observation(elements)


@pytest.mark.parametrize(
    "elements",
    [
        (tree_element("root", parent=0, children=()),),
        (tree_element("root", parent=None, children=(0,)),),
    ],
)
def test_observation_rejects_self_references(
    elements: tuple[ObservedElement, ...],
) -> None:
    with pytest.raises(ValidationError, match="self-reference"):
        model_observation(elements)


def test_observation_rejects_duplicate_children() -> None:
    elements = (
        tree_element("root", parent=None, children=(1, 1)),
        tree_element("child", parent=0, children=()),
    )

    with pytest.raises(ValidationError, match="duplicate child index"):
        model_observation(elements)


@pytest.mark.parametrize(
    "elements",
    [
        (
            tree_element("root", parent=None, children=()),
            tree_element("child", parent=0, children=()),
        ),
        (
            tree_element("root", parent=None, children=(1,)),
            tree_element("child", parent=None, children=()),
        ),
    ],
)
def test_observation_rejects_nonreciprocal_parent_child_relationships(
    elements: tuple[ObservedElement, ...],
) -> None:
    with pytest.raises(ValidationError, match="parent/child relationship"):
        model_observation(elements)


def test_observation_rejects_parent_child_cycles() -> None:
    elements = (
        tree_element("first", parent=1, children=(1,)),
        tree_element("second", parent=0, children=(0,)),
    )

    with pytest.raises(ValidationError, match="cycle"):
        model_observation(elements)


def test_parse_hierarchy_rejects_malformed_xml() -> None:
    with pytest.raises(ElementTree.ParseError):
        parse_hierarchy("<hierarchy><node></hierarchy>", sequence=1, package=None)


@pytest.mark.parametrize("bounds", ["invalid", "[10,10][5,5]"])
def test_parse_hierarchy_rejects_invalid_bounds(bounds: str) -> None:
    source = f'<hierarchy><node bounds="{bounds}" /></hierarchy>'

    with pytest.raises(ValueError, match="invalid Android bounds"):
        parse_hierarchy(source, sequence=1, package=None)


def test_parse_hierarchy_uses_safe_boolean_defaults() -> None:
    parsed = parse_hierarchy(
        '<hierarchy><node bounds="[0,0][1,1]" /></hierarchy>',
        sequence=1,
        package=None,
    )
    element = parsed.elements[0]

    assert element.snapshot.displayed
    assert not element.snapshot.enabled
    assert not element.snapshot.selected
    assert not element.snapshot.checked
    assert not element.snapshot.checkable
    assert not element.snapshot.focusable
    assert not element.snapshot.focused
    assert not element.clickable
    assert not element.heading
    assert not element.text_has_clickable_span


def test_core_selector_queries_use_one_observation() -> None:
    parsed = observation()

    assert parsed.sequence == 7
    assert (
        evaluate_selector(
            parsed,
            Selector.resource_id("com.example:id/submit"),
            package="com.example",
        ).count
        == 1
    )
    assert (
        evaluate_selector(
            parsed,
            Selector.text(TextMatcher(value="Continue", mode=MatchMode.EXACT)),
            package="com.example",
        ).visible_count
        == 1
    )
    assert (
        evaluate_selector(
            parsed,
            Selector.class_name("android.widget.EditText"),
            package="com.example",
        )
        .elements[0]
        .editable
    )
    assert (
        evaluate_selector(
            parsed,
            Selector.resource_id("com.android.permissioncontroller:id/permission_deny_button"),
            package=None,
        ).count
        == 1
    )


def test_and_selector_intersects_matches() -> None:
    selector = Selector.by_role(Role.BUTTON).and_selector(Selector.resource_id("submit"))

    assert resource_ids(selector) == ["com.example:id/submit"]


def test_or_selector_removes_duplicates_and_retains_document_order() -> None:
    selector = Selector.text(TextMatcher(value="Continue")).or_selector(
        Selector.resource_id("submit")
    )

    assert resource_ids(selector) == [
        "com.example:id/submit",
        "com.example:id/hidden_continue",
    ]


def test_descendant_selector_returns_matching_descendants() -> None:
    selector = Selector.resource_id("card").descendant(
        Selector.text(TextMatcher(value="Premium Plan"))
    )

    assert resource_ids(selector) == ["com.example:id/plan_name"]


def test_has_selector_returns_ancestor_with_matching_descendant() -> None:
    selector = Selector.resource_id("card").has(Selector.resource_id("plan_name"))

    assert resource_ids(selector) == ["com.example:id/card"]


def test_has_not_selector_excludes_ancestor_with_matching_descendant() -> None:
    selector = Selector.resource_id("card").has_not(Selector.resource_id("missing"))

    assert resource_ids(selector) == ["com.example:id/card"]


def test_has_text_matches_own_or_descendant_accessible_text() -> None:
    selector = Selector.resource_id("card").has_text(
        TextMatcher(value="Premium", mode=MatchMode.CONTAINS)
    )

    assert resource_ids(selector) == ["com.example:id/card"]


def test_has_not_text_excludes_own_or_descendant_accessible_text() -> None:
    selector = Selector.resource_id("card").has_not_text(
        TextMatcher(value="Continue", mode=MatchMode.EXACT)
    )

    assert resource_ids(selector) == ["com.example:id/card"]


def test_first_selects_first_match() -> None:
    assert resource_ids(Selector.class_name("android.widget.TextView").nth(0)) == [
        "com.example:id/plan_name"
    ]


def test_last_selects_last_match() -> None:
    assert resource_ids(Selector.class_name("android.widget.TextView").nth(-1)) == [
        "com.example:id/privacy"
    ]


def test_other_negative_nth_uses_python_indexing() -> None:
    assert resource_ids(Selector.class_name("android.widget.TextView").nth(-2)) == [
        "com.example:id/terms"
    ]
    assert resource_ids(Selector.class_name("android.widget.TextView").nth(-20)) == []


def test_role_mapping_uses_android_classes_and_raw_attributes() -> None:
    assert resource_ids(Selector.by_role(Role.BUTTON)) == ["com.example:id/submit"]
    assert resource_ids(Selector.by_role(Role.TEXTBOX)) == ["com.example:id/username"]
    assert resource_ids(Selector.by_role(Role.HEADING)) == ["com.example:id/plan_name"]
    assert resource_ids(Selector.by_role(Role.LINK)) == ["com.example:id/privacy"]


@pytest.mark.parametrize("role", [Role.MENU, Role.MENU_ITEM, Role.TAB])
def test_unsupported_roles_raise_selector_compilation_error(role: Role) -> None:
    with pytest.raises(SelectorCompilationError, match="no reliable Android"):
        evaluate_selector(observation(), Selector.by_role(role), package="com.example")


def test_case_sensitive_exact_text_does_not_fold_case() -> None:
    selector = Selector.text(
        TextMatcher(value="continue", mode=MatchMode.EXACT, case_sensitive=True)
    )

    assert resource_ids(selector) == []


def test_case_insensitive_contains_text_folds_case() -> None:
    selector = Selector.text(
        TextMatcher(value="PREMIUM", mode=MatchMode.CONTAINS, case_sensitive=False)
    )

    assert resource_ids(selector) == ["com.example:id/plan_name"]


def test_regex_text_is_evaluated_locally() -> None:
    selector = Selector.text(TextMatcher(value=r"Premium\s+Plan", mode=MatchMode.REGEX))

    assert resource_ids(selector) == ["com.example:id/plan_name"]


def test_label_placeholder_and_test_id_map_to_observed_attributes() -> None:
    assert resource_ids(Selector.label(TextMatcher(value="Username"))) == [
        "com.example:id/username"
    ]
    assert resource_ids(Selector.placeholder("Email address")) == ["com.example:id/username"]
    assert resource_ids(Selector.test_id("submit")) == ["com.example:id/submit"]


def test_content_description_matches_directly() -> None:
    assert resource_ids(Selector.content_description("Username")) == ["com.example:id/username"]


def test_package_scope_filters_every_selector_category() -> None:
    parsed = observation()
    selector = Selector.by_role(Role.BUTTON)

    assert evaluate_selector(parsed, selector, package="com.example").count == 1
    assert evaluate_selector(parsed, selector, package=None).count == 2
    assert (
        evaluate_selector(
            parsed,
            Selector.resource_id("com.android.permissioncontroller:id/permission_deny_button"),
            package="com.example",
        ).count
        == 0
    )


def test_structural_selectors_do_not_cross_package_scope() -> None:
    source = """\
<hierarchy>
  <node resource-id="com.example:id/app_root" class="android.view.View"
        package="com.example" bounds="[0,0][10,10]">
    <node resource-id="com.android.systemui:id/system_child" class="android.widget.Button"
          package="com.android.systemui" bounds="[0,0][5,5]" />
  </node>
</hierarchy>
"""
    parsed = parse_hierarchy(source, sequence=1, package=None)
    app_root = Selector.resource_id("com.example:id/app_root")
    system_child = Selector.resource_id("com.android.systemui:id/system_child")

    assert (
        evaluate_selector(
            parsed,
            app_root.descendant(system_child),
            package="com.example",
        ).count
        == 0
    )
    assert (
        evaluate_selector(
            parsed,
            app_root.has(system_child),
            package="com.example",
        ).count
        == 0
    )
    assert evaluate_selector(parsed, app_root.descendant(system_child), package=None).count == 1
    assert evaluate_selector(parsed, app_root.has(system_child), package=None).count == 1


def test_deep_hierarchy_parsing_and_evaluation_are_iterative() -> None:
    depth = 1_200
    opening_nodes = "".join(
        (
            f'<node resource-id="com.example:id/node-{index}" '
            'class="android.view.View" package="com.example" bounds="[0,0][1,1]">'
        )
        for index in range(depth)
    )
    source = f"<hierarchy>{opening_nodes}{'</node>' * depth}</hierarchy>"

    parsed = parse_hierarchy(source, sequence=1, package="com.example")
    selector = Selector.resource_id("com.example:id/node-0").has(
        Selector.resource_id(f"com.example:id/node-{depth - 1}")
    )

    assert len(parsed.elements) == depth
    assert evaluate_selector(parsed, selector, package="com.example").count == 1
