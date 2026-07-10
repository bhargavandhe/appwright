"""Immutable, discriminated selector abstract syntax tree."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field

from appwright.models.base import StrictModel
from appwright.models.enums import MatchMode, Role, SelectorKind


class TextMatcher(StrictModel):
    value: str = Field(min_length=1)
    mode: MatchMode = MatchMode.EXACT
    case_sensitive: bool = True


class ResourceIdNode(StrictModel):
    kind: Literal[SelectorKind.RESOURCE_ID] = SelectorKind.RESOURCE_ID
    value: str = Field(min_length=1)


class ContentDescriptionNode(StrictModel):
    kind: Literal[SelectorKind.CONTENT_DESCRIPTION] = SelectorKind.CONTENT_DESCRIPTION
    value: str = Field(min_length=1)


class ClassNameNode(StrictModel):
    kind: Literal[SelectorKind.CLASS_NAME] = SelectorKind.CLASS_NAME
    value: str = Field(min_length=1)


class TextNode(StrictModel):
    kind: Literal[SelectorKind.TEXT] = SelectorKind.TEXT
    matcher: TextMatcher


class LabelNode(StrictModel):
    """An Android accessibility label exposed as a content description."""

    kind: Literal[SelectorKind.LABEL] = SelectorKind.LABEL
    matcher: TextMatcher


class PlaceholderNode(StrictModel):
    kind: Literal[SelectorKind.PLACEHOLDER] = SelectorKind.PLACEHOLDER
    value: str = Field(min_length=1)


class TestIdNode(StrictModel):
    kind: Literal[SelectorKind.TEST_ID] = SelectorKind.TEST_ID
    value: str = Field(min_length=1)


class RoleNode(StrictModel):
    kind: Literal[SelectorKind.ROLE] = SelectorKind.ROLE
    role: Role


class AndNode(StrictModel):
    kind: Literal[SelectorKind.AND] = SelectorKind.AND
    left: SelectorNode
    right: SelectorNode


class OrNode(StrictModel):
    kind: Literal[SelectorKind.OR] = SelectorKind.OR
    left: SelectorNode
    right: SelectorNode


class DescendantNode(StrictModel):
    kind: Literal[SelectorKind.DESCENDANT] = SelectorKind.DESCENDANT
    left: SelectorNode
    right: SelectorNode


class HasNode(StrictModel):
    kind: Literal[SelectorKind.HAS] = SelectorKind.HAS
    left: SelectorNode
    right: SelectorNode


class HasNotNode(StrictModel):
    kind: Literal[SelectorKind.HAS_NOT] = SelectorKind.HAS_NOT
    left: SelectorNode
    right: SelectorNode


class HasTextNode(StrictModel):
    """Match a node whose own or descendant accessible text matches."""

    kind: Literal[SelectorKind.HAS_TEXT] = SelectorKind.HAS_TEXT
    left: SelectorNode
    matcher: TextMatcher


class HasNotTextNode(StrictModel):
    """Match a node without matching own or descendant accessible text."""

    kind: Literal[SelectorKind.HAS_NOT_TEXT] = SelectorKind.HAS_NOT_TEXT
    left: SelectorNode
    matcher: TextMatcher


class NthNode(StrictModel):
    kind: Literal[SelectorKind.NTH] = SelectorKind.NTH
    left: SelectorNode
    index: int


SelectorNode: TypeAlias = Annotated[
    ResourceIdNode
    | ContentDescriptionNode
    | ClassNameNode
    | TextNode
    | LabelNode
    | PlaceholderNode
    | TestIdNode
    | RoleNode
    | AndNode
    | OrNode
    | DescendantNode
    | HasNode
    | HasNotNode
    | HasTextNode
    | HasNotTextNode
    | NthNode,
    Field(discriminator="kind"),
]


class Selector(StrictModel):
    """Public immutable wrapper around a typed selector node."""

    node: SelectorNode

    @classmethod
    def resource_id(cls, value: str) -> Selector:
        return cls(node=ResourceIdNode(value=value))

    @classmethod
    def content_description(cls, value: str) -> Selector:
        return cls(node=ContentDescriptionNode(value=value))

    @classmethod
    def class_name(cls, value: str) -> Selector:
        return cls(node=ClassNameNode(value=value))

    @classmethod
    def text(cls, matcher: TextMatcher) -> Selector:
        return cls(node=TextNode(matcher=matcher))

    @classmethod
    def label(cls, matcher: TextMatcher) -> Selector:
        return cls(node=LabelNode(matcher=matcher))

    @classmethod
    def placeholder(cls, value: str) -> Selector:
        return cls(node=PlaceholderNode(value=value))

    @classmethod
    def test_id(cls, value: str) -> Selector:
        return cls(node=TestIdNode(value=value))

    @classmethod
    def by_role(cls, role: Role, name: TextMatcher | None = None) -> Selector:
        base = cls(node=RoleNode(role=role))
        if name is None:
            return base
        return base.and_selector(cls.text(name))

    def and_selector(self, other: Selector) -> Selector:
        return Selector(node=AndNode(left=self.node, right=other.node))

    def or_selector(self, other: Selector) -> Selector:
        return Selector(node=OrNode(left=self.node, right=other.node))

    def descendant(self, other: Selector) -> Selector:
        return Selector(node=DescendantNode(left=self.node, right=other.node))

    def has(self, other: Selector) -> Selector:
        return Selector(node=HasNode(left=self.node, right=other.node))

    def has_not(self, other: Selector) -> Selector:
        return Selector(node=HasNotNode(left=self.node, right=other.node))

    def has_text(self, matcher: TextMatcher) -> Selector:
        return Selector(node=HasTextNode(left=self.node, matcher=matcher))

    def has_not_text(self, matcher: TextMatcher) -> Selector:
        return Selector(node=HasNotTextNode(left=self.node, matcher=matcher))

    def nth(self, index: int) -> Selector:
        return Selector(node=NthNode(left=self.node, index=index))


AndNode.model_rebuild()
OrNode.model_rebuild()
DescendantNode.model_rebuild()
HasNode.model_rebuild()
HasNotNode.model_rebuild()
HasTextNode.model_rebuild()
HasNotTextNode.model_rebuild()
NthNode.model_rebuild()
Selector.model_rebuild()
