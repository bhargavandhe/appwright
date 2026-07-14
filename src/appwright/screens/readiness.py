"""Evaluate immutable screen-readiness expressions from one observation."""

from __future__ import annotations

from dataclasses import dataclass

from appwright.observations import Observation, evaluate_selector
from appwright.screens.model import AllOf, AnyOf, Readiness, Visible


@dataclass(frozen=True, slots=True)
class ReadinessDiagnostic:
    """Structured result for one node in a readiness expression."""

    kind: str
    ready: bool
    matched: int
    total: int
    selector: str | None = None
    match_count: int = 0
    visible_count: int = 0
    reason: str | None = None
    children: tuple[ReadinessDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class ReadinessEvaluation:
    """Aggregated readiness result and its immutable condition diagnostics."""

    ready: bool
    matched: int
    total: int
    diagnostics: tuple[ReadinessDiagnostic, ...]


def _visible_diagnostic(
    observation: Observation,
    condition: Visible,
    *,
    package: str | None,
) -> ReadinessDiagnostic:
    match = evaluate_selector(observation, condition.selector, package=package)
    ready = match.count == 1 and match.visible_count == 1
    if match.count == 0:
        reason = "no matches"
    elif match.count > 1:
        reason = "duplicate matches"
    elif match.visible_count == 0:
        reason = "match is not visible"
    else:
        reason = None
    return ReadinessDiagnostic(
        kind="visible",
        ready=ready,
        matched=int(ready),
        total=1,
        selector=repr(condition.selector),
        match_count=match.count,
        visible_count=match.visible_count,
        reason=reason,
    )


def _evaluate(
    observation: Observation,
    readiness: Readiness,
    *,
    package: str | None,
) -> ReadinessDiagnostic:
    if isinstance(readiness, Visible):
        return _visible_diagnostic(observation, readiness, package=package)

    if isinstance(readiness, (AllOf, AnyOf)):
        children = tuple(
            _evaluate(observation, condition, package=package) for condition in readiness.conditions
        )
        reason: str | None
        if not children:
            ready = False
            kind = "all_of" if isinstance(readiness, AllOf) else "any_of"
            reason = "readiness has no conditions"
        elif isinstance(readiness, AllOf):
            ready = all(child.ready for child in children)
            kind = "all_of"
            reason = None if ready else "one or more conditions not ready"
        else:
            ready = any(child.ready for child in children)
            kind = "any_of"
            reason = None if ready else "no alternative ready"
        return ReadinessDiagnostic(
            kind=kind,
            ready=ready,
            matched=sum(child.matched for child in children),
            total=sum(child.total for child in children),
            reason=reason,
            children=children,
        )

    raise TypeError(f"unsupported readiness condition: {type(readiness).__name__}")


def evaluate_readiness(
    observation: Observation,
    readiness: Readiness,
    *,
    package: str | None,
) -> ReadinessEvaluation:
    """Evaluate a readiness tree against one scoped immutable observation."""

    diagnostic = _evaluate(observation, readiness, package=package)
    return ReadinessEvaluation(
        ready=diagnostic.ready,
        matched=diagnostic.matched,
        total=diagnostic.total,
        diagnostics=(diagnostic,),
    )
