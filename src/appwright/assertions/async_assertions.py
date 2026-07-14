"""Retrying asynchronous locator assertions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from appwright.backends.base import BackendError, RecoverableBackendError
from appwright.core.errors import ExpectationError
from appwright.core.runtime import AsyncLocator, error_details, trace_event
from appwright.models.data import CallLogEntry, ElementSnapshot, QueryResult
from appwright.models.enums import ErrorCode, TraceEventKind
from appwright.operations import OperationDeadline

ElementPredicate = Callable[[tuple[ElementSnapshot, ...]], bool]


def single_element(elements: tuple[ElementSnapshot, ...]) -> ElementSnapshot | None:
    if len(elements) != 1:
        return None
    return next(iter(elements))


class AsyncLocatorAssertions:
    def __init__(self, locator: AsyncLocator, negated: bool = False) -> None:
        self.locator = locator
        self.negated = negated

    @property
    def not_(self) -> AsyncLocatorAssertions:
        return AsyncLocatorAssertions(self.locator, not self.negated)

    def expectation_failure(
        self,
        expected: str,
        received: str,
        deadline: OperationDeadline,
        call_log: tuple[CallLogEntry, ...],
    ) -> ExpectationError:
        plan = self.locator.plan()
        details = self.locator.device.enrich_error(
            error_details(
                code=ErrorCode.EXPECTATION_FAILED,
                api_name="expect",
                message=(f"expected {plan.description} to be {expected}; received {received}"),
                plan=plan,
                elapsed=deadline.elapsed(),
                expected=expected,
                received=received,
                call_log=call_log,
            )
        )
        self.locator.device.tracing.record(
            trace_event(
                TraceEventKind.ERROR,
                "expect",
                (("details", details.model_dump_json()),),
            )
        )
        return ExpectationError(details)

    async def poll(
        self,
        predicate: ElementPredicate,
        expected: str,
        *,
        timeout: timedelta | None = None,
        strict: bool = True,
    ) -> None:
        selected_timeout = timeout if timeout is not None else self.locator.device.timeouts.wait
        deadline = OperationDeadline.start(selected_timeout)
        delay = self.locator.device.timeouts.retry.initial_delay
        call_log: list[CallLogEntry] = []
        last_result: QueryResult | None = None
        received = "no elements"
        effective_expected = f"not {expected}" if self.negated else expected
        while True:
            if deadline.expired():
                if strict and last_result is not None and len(last_result.elements) > 1:
                    self.locator.strict_element(
                        last_result,
                        "expect",
                        self.locator.plan(),
                        elapsed=deadline.elapsed(),
                        call_log=tuple(call_log),
                    )
                raise self.expectation_failure(
                    effective_expected,
                    received,
                    deadline,
                    tuple(call_log),
                )
            try:
                result = await self.locator.query_once(deadline.remaining())
                observation_valid = True
            except RecoverableBackendError as error:
                received = error.failure.message
                call_log.append(
                    CallLogEntry(
                        message=received,
                        elapsed=deadline.elapsed(),
                    )
                )
                if deadline.expired():
                    result = None
                    observation_valid = False
                else:
                    await self.locator.wait_before_retry(delay, deadline)
                    delay = self.locator.next_delay(delay)
                    continue
            except BackendError as error:
                raise self.locator.translate_backend_error(
                    error,
                    "expect",
                    self.locator.plan(),
                ) from error
            if result is not None:
                last_result = result
                received = self.describe(result.elements)
                ambiguous = strict and len(result.elements) > 1
                if ambiguous:
                    call_log.append(
                        CallLogEntry(
                            message=f"locator resolved to {len(result.elements)} elements",
                            elapsed=deadline.elapsed(),
                        )
                    )
                    matched = False
                else:
                    matched = predicate(result.elements)
            else:
                ambiguous = False
                matched = False
            satisfied = (
                False
                if ambiguous or not observation_valid
                else (not matched if self.negated else matched)
            )
            if satisfied:
                self.locator.device.tracing.record(
                    trace_event(
                        TraceEventKind.ASSERTION,
                        "expect",
                        (
                            ("locator", self.locator.plan().description),
                            ("expected", effective_expected),
                            ("received", received),
                        ),
                    )
                )
                return
            if deadline.expired():
                if strict and last_result is not None and len(last_result.elements) > 1:
                    self.locator.strict_element(
                        last_result,
                        "expect",
                        self.locator.plan(),
                        elapsed=deadline.elapsed(),
                        call_log=tuple(call_log),
                    )
                raise self.expectation_failure(
                    effective_expected,
                    received,
                    deadline,
                    tuple(call_log),
                )
            await self.locator.wait_before_retry(delay, deadline)
            delay = self.locator.next_delay(delay)

    def describe(self, elements: tuple[ElementSnapshot, ...]) -> str:
        if not elements:
            return "no elements"
        if len(elements) > 1:
            return f"{len(elements)} elements"
        element = next(iter(elements))
        return (
            f"visible={element.displayed}, enabled={element.enabled}, "
            f"text={element.text!r}, checked={element.checked}"
        )

    async def to_be_visible(self, *, timeout: timedelta | None = None) -> None:
        await self.poll(
            lambda elements: (
                (element := single_element(elements)) is not None and element.displayed
            ),
            "visible",
            timeout=timeout,
        )

    async def not_to_be_visible(self, *, timeout: timedelta | None = None) -> None:
        await self.not_.to_be_visible(timeout=timeout)

    async def to_be_hidden(self, *, timeout: timedelta | None = None) -> None:
        await self.poll(
            lambda elements: (
                not elements
                or ((element := single_element(elements)) is not None and not element.displayed)
            ),
            "hidden",
            timeout=timeout,
        )

    async def to_be_enabled(self, *, timeout: timedelta | None = None) -> None:
        await self.poll(
            lambda elements: (element := single_element(elements)) is not None and element.enabled,
            "enabled",
            timeout=timeout,
        )

    async def to_be_disabled(self, *, timeout: timedelta | None = None) -> None:
        await self.poll(
            lambda elements: (
                (element := single_element(elements)) is not None and not element.enabled
            ),
            "disabled",
            timeout=timeout,
        )

    async def to_be_editable(self, *, timeout: timedelta | None = None) -> None:
        await self.poll(
            lambda elements: (element := single_element(elements)) is not None and element.editable,
            "editable",
            timeout=timeout,
        )

    async def to_be_checked(self, *, timeout: timedelta | None = None) -> None:
        await self.poll(
            lambda elements: (element := single_element(elements)) is not None and element.checked,
            "checked",
            timeout=timeout,
        )

    async def to_be_unchecked(self, *, timeout: timedelta | None = None) -> None:
        await self.poll(
            lambda elements: (
                (element := single_element(elements)) is not None and not element.checked
            ),
            "unchecked",
            timeout=timeout,
        )

    async def to_be_focused(self, *, timeout: timedelta | None = None) -> None:
        await self.poll(
            lambda elements: (element := single_element(elements)) is not None and element.focused,
            "focused",
            timeout=timeout,
        )

    async def to_be_selected(self, *, timeout: timedelta | None = None) -> None:
        await self.poll(
            lambda elements: (element := single_element(elements)) is not None and element.selected,
            "selected",
            timeout=timeout,
        )

    async def to_have_text(self, value: str, *, timeout: timedelta | None = None) -> None:
        await self.poll(
            lambda elements: (
                (element := single_element(elements)) is not None and element.text == value
            ),
            f"text {value!r}",
            timeout=timeout,
        )

    async def to_contain_text(
        self,
        value: str,
        *,
        timeout: timedelta | None = None,
    ) -> None:
        await self.poll(
            lambda elements: (
                (element := single_element(elements)) is not None and value in element.text
            ),
            f"text containing {value!r}",
            timeout=timeout,
        )

    async def to_have_accessible_name(
        self,
        value: str,
        *,
        timeout: timedelta | None = None,
    ) -> None:
        await self.poll(
            lambda elements: (
                (element := single_element(elements)) is not None
                and element.accessible_name == value
            ),
            f"accessible name {value!r}",
            timeout=timeout,
        )

    async def to_have_resource_id(
        self,
        value: str,
        *,
        timeout: timedelta | None = None,
    ) -> None:
        await self.poll(
            lambda elements: (
                (element := single_element(elements)) is not None and element.resource_id == value
            ),
            f"resource id {value!r}",
            timeout=timeout,
        )

    async def to_have_count(self, value: int, *, timeout: timedelta | None = None) -> None:
        await self.poll(
            lambda elements: len(elements) == value,
            f"count {value}",
            timeout=timeout,
            strict=False,
        )


def expect(locator: AsyncLocator) -> AsyncLocatorAssertions:
    return AsyncLocatorAssertions(locator)
