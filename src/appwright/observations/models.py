"""Immutable hierarchy observation records."""

from datetime import datetime, timedelta
from typing import Self

from pydantic import Field, field_validator, model_validator

from appwright.models.base import StrictModel
from appwright.models.data import ElementSnapshot


class ObservedElement(StrictModel):
    """One element in a flattened hierarchy tree."""

    snapshot: ElementSnapshot
    parent: int | None
    children: tuple[int, ...]
    hint: str = ""
    clickable: bool = False
    heading: bool = False
    text_has_clickable_span: bool = False


class Observation(StrictModel):
    """One atomic, immutable hierarchy capture."""

    sequence: int = Field(ge=0)
    captured_at: datetime
    elapsed: timedelta = Field(ge=timedelta())
    package: str | None
    elements: tuple[ObservedElement, ...]

    @field_validator("captured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_tree(self) -> Self:
        element_count = len(self.elements)
        child_sets: list[set[int]] = []

        for index, element in enumerate(self.elements):
            parent = element.parent
            if parent is not None:
                if parent < 0 or parent >= element_count:
                    raise ValueError(f"parent index {parent} for element {index} is out of bounds")
                if parent == index:
                    raise ValueError(f"element {index} has a parent self-reference")

            children = set(element.children)
            if len(children) != len(element.children):
                raise ValueError(f"element {index} has a duplicate child index")
            for child in children:
                if child < 0 or child >= element_count:
                    raise ValueError(f"child index {child} for element {index} is out of bounds")
                if child == index:
                    raise ValueError(f"element {index} has a child self-reference")
            child_sets.append(children)

        for index, element in enumerate(self.elements):
            parent = element.parent
            if parent is not None and index not in child_sets[parent]:
                raise ValueError(
                    f"parent/child relationship between elements {parent} and {index} "
                    "is not reciprocal"
                )
            for child in element.children:
                if self.elements[child].parent != index:
                    raise ValueError(
                        f"parent/child relationship between elements {index} and {child} "
                        "is not reciprocal"
                    )

        unseen, active, complete = 0, 1, 2
        states = [unseen] * element_count
        for start in range(element_count):
            if states[start] != unseen:
                continue
            path: list[int] = []
            current: int | None = start
            while current is not None and states[current] == unseen:
                states[current] = active
                path.append(current)
                current = self.elements[current].parent
            if current is not None and states[current] == active:
                raise ValueError(f"parent/child cycle detected at element {current}")
            for visited in path:
                states[visited] = complete

        return self


class ObservationMatch(StrictModel):
    """Snapshots selected from one observation."""

    elements: tuple[ElementSnapshot, ...]

    @property
    def count(self) -> int:
        return len(self.elements)

    @property
    def visible_count(self) -> int:
        return sum(element.displayed for element in self.elements)
