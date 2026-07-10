"""Strict model tests."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from appwright.backends.appium.adapter import (
    capability_value_from_python,
    capability_value_to_python,
)
from appwright.models.config import (
    AdditionalCapability,
    AndroidConnectionOptions,
    AppiumServer,
    AppiumTimeouts,
    ApplicationOptions,
    CapabilityValue,
    SessionCapabilities,
)
from appwright.models.data import ActionRequest, Deadline
from appwright.models.enums import ActionKind, CapabilityValueKind, ServerMode


def test_local_server_model() -> None:
    server = AppiumServer.local(port=4723)
    assert server.mode is ServerMode.LOCAL
    assert server.port == 4723


def test_remote_server_requires_url() -> None:
    with pytest.raises(ValidationError):
        AppiumServer(mode=ServerMode.REMOTE)


def test_models_are_strict() -> None:
    with pytest.raises(ValidationError):
        AppiumTimeouts(action="30")  # type: ignore[arg-type]


def test_application_requires_package() -> None:
    with pytest.raises(ValidationError):
        ApplicationOptions.model_validate_json("{}")


def test_timeout_accepts_timedelta() -> None:
    timeout = AppiumTimeouts(action=timedelta(seconds=12))
    assert timeout.action == timedelta(seconds=12)


def test_nested_capabilities_round_trip_through_typed_model() -> None:
    source = {"vendor:options": {"enabled": True, "names": ["one", "two"]}}
    value = capability_value_from_python(source)
    assert value.kind is CapabilityValueKind.OBJECT
    assert capability_value_to_python(value) == source


def test_empty_capability_containers_are_valid() -> None:
    array = capability_value_from_python([])
    object_value = capability_value_from_python({})
    assert array.kind is CapabilityValueKind.ARRAY
    assert object_value.kind is CapabilityValueKind.OBJECT


def test_capability_factories_and_recursive_redaction() -> None:
    secret = AdditionalCapability.string("vendor:access-key", "secret-value")
    nested = AdditionalCapability.object("vendor:options", entries=(secret,))
    capabilities = SessionCapabilities(entries=(nested,)).redacted()
    redacted = capabilities.entries[0].value.entries[0]
    assert redacted.value.string_value == "[REDACTED]"


def test_capability_value_factories_build_typed_nested_values() -> None:
    values = CapabilityValue.array(
        (
            CapabilityValue.string("android"),
            CapabilityValue.integer(26),
            CapabilityValue.number(0.5),
            CapabilityValue.boolean(True),
            CapabilityValue.null(),
        )
    )
    assert values.kind is CapabilityValueKind.ARRAY
    assert tuple(value.kind for value in values.items) == (
        CapabilityValueKind.STRING,
        CapabilityValueKind.INTEGER,
        CapabilityValueKind.NUMBER,
        CapabilityValueKind.BOOLEAN,
        CapabilityValueKind.NULL,
    )


def test_connection_rejects_unnamespaced_duplicate_and_framework_capabilities() -> None:
    with pytest.raises(ValidationError, match="vendor namespace"):
        AndroidConnectionOptions(capabilities=(AdditionalCapability.boolean("video", True),))
    duplicate = AdditionalCapability.boolean("vendor:video", True)
    with pytest.raises(ValidationError, match="must be unique"):
        AndroidConnectionOptions(capabilities=(duplicate, duplicate))
    with pytest.raises(ValidationError, match="cannot be overridden"):
        AndroidConnectionOptions(
            capabilities=(AdditionalCapability.string("appium:udid", "other-device"),)
        )


def test_action_request_rejects_impossible_field_combinations() -> None:
    with pytest.raises(ValidationError):
        ActionRequest(kind=ActionKind.FILL)
    with pytest.raises(ValidationError):
        ActionRequest(kind=ActionKind.TAP, text="unexpected")


def test_deadline_never_reports_negative_remaining_time() -> None:
    deadline = Deadline(started_at=0, expires_at=0)
    assert deadline.expired()
    assert deadline.remaining() == timedelta(0)
