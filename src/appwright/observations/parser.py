"""Parse Appium Android hierarchy XML into immutable observations."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree

from appwright.models.data import ElementSnapshot, Rect
from appwright.observations.models import Observation, ObservedElement

BOUNDS_PATTERN = re.compile(
    r"^\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]"
    r"\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]$"
)


def _android_boolean(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.casefold() == "true"


def _parse_bounds(value: str) -> Rect:
    match = BOUNDS_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid Android bounds: {value!r}")
    left, top, right, bottom = (float(part) for part in match.groups())
    width = right - left
    height = bottom - top
    if width < 0 or height < 0:
        raise ValueError(f"invalid Android bounds: {value!r}")
    return Rect(x=left, y=top, width=width, height=height)


def _is_android_node(element: ElementTree.Element) -> bool:
    local_name = element.tag.rsplit("}", maxsplit=1)[-1]
    if local_name == "node":
        return True
    # UiAutomator2 may serialize the widget class as the XML tag instead of
    # using a generic <node class="..."> element. Both forms carry bounds and
    # describe the same Android accessibility node.
    return "bounds" in element.attrib and (
        "class" in element.attrib or local_name.startswith(("android.", "com."))
    )


def _flatten_nodes(
    root: ElementTree.Element,
) -> tuple[list[tuple[ElementTree.Element, int | None]], list[list[int]]]:
    records: list[tuple[ElementTree.Element, int | None]] = []
    children: list[list[int]] = []
    pending: list[tuple[ElementTree.Element, int | None]] = [(root, None)]

    while pending:
        element, parent = pending.pop()
        current_parent = parent
        if _is_android_node(element):
            index = len(records)
            records.append((element, parent))
            children.append([])
            if parent is not None:
                children[parent].append(index)
            current_parent = index
        pending.extend((child, current_parent) for child in reversed(element))
    return records, children


def _snapshot(element: ElementTree.Element, *, sequence: int, index: int) -> ElementSnapshot:
    attributes = element.attrib
    bounds = _parse_bounds(attributes.get("bounds", "[0,0][0,0]"))
    local_name = element.tag.rsplit("}", maxsplit=1)[-1]
    class_name = attributes.get("class", "")
    if not class_name and local_name != "node":
        class_name = local_name
    displayed = _android_boolean(attributes.get("displayed"), default=True)
    displayed = displayed and bounds.width > 0 and bounds.height > 0
    return ElementSnapshot(
        identity=f"observation-{sequence}-{index}",
        text=attributes.get("text", ""),
        accessible_name=attributes.get("content-desc", ""),
        resource_id=attributes.get("resource-id", ""),
        class_name=class_name,
        package_name=attributes.get("package", ""),
        displayed=displayed,
        enabled=_android_boolean(attributes.get("enabled")),
        selected=_android_boolean(attributes.get("selected")),
        checked=_android_boolean(attributes.get("checked")),
        checkable=_android_boolean(attributes.get("checkable")),
        focusable=_android_boolean(attributes.get("focusable")),
        focused=_android_boolean(attributes.get("focused")),
        editable=class_name.endswith("EditText"),
        bounds=bounds,
        window_id=attributes.get("window-id", attributes.get("windowId", "")),
    )


def parse_hierarchy(
    source: str,
    *,
    sequence: int,
    package: str | None,
    captured_at: datetime | None = None,
    elapsed: timedelta = timedelta(),
) -> Observation:
    """Parse one Android hierarchy capture in deterministic document order."""

    root = ElementTree.fromstring(source)
    records, child_indices = _flatten_nodes(root)
    elements = tuple(
        ObservedElement(
            snapshot=_snapshot(element, sequence=sequence, index=index),
            parent=parent,
            children=tuple(child_indices[index]),
            hint=element.attrib.get("hint", ""),
            clickable=_android_boolean(element.attrib.get("clickable")),
            heading=_android_boolean(element.attrib.get("heading")),
            text_has_clickable_span=_android_boolean(element.attrib.get("text-has-clickable-span")),
        )
        for index, (element, parent) in enumerate(records)
    )
    return Observation(
        sequence=sequence,
        captured_at=captured_at if captured_at is not None else datetime.now(UTC),
        elapsed=elapsed,
        package=package,
        elements=elements,
    )
