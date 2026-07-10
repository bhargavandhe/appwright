"""Strict configuration models."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator

from appwright.models.base import StrictModel
from appwright.models.enums import CapabilityValueKind, FrameworkCapability, ServerMode


class RetryPolicy(StrictModel):
    initial_delay: timedelta = Field(default=timedelta(milliseconds=20), ge=timedelta(0))
    multiplier: float = Field(default=2, ge=1)
    maximum_delay: timedelta = Field(default=timedelta(milliseconds=500), gt=timedelta(0))


class AppiumTimeouts(StrictModel):
    action: timedelta = Field(default=timedelta(seconds=30), gt=timedelta(0))
    expectation: timedelta = Field(default=timedelta(seconds=5), gt=timedelta(0))
    stability: timedelta = Field(default=timedelta(milliseconds=200), gt=timedelta(0))
    transport: timedelta = Field(default=timedelta(seconds=120), gt=timedelta(0))
    server_start: timedelta = Field(default=timedelta(seconds=30), gt=timedelta(0))
    retry: RetryPolicy = Field(default_factory=RetryPolicy)


class AppiumSecurityOptions(StrictModel):
    username: str | None = None
    access_key: SecretStr | None = None
    verify_tls: bool = True


class AppiumServer(StrictModel):
    mode: ServerMode
    url: AnyHttpUrl | None = None
    host: str = "127.0.0.1"
    port: int | None = Field(default=None, ge=1, le=65535)
    executable: Path | None = None
    security: AppiumSecurityOptions = AppiumSecurityOptions()

    @model_validator(mode="after")
    def validate_mode(self) -> AppiumServer:
        if self.mode is ServerMode.REMOTE and self.url is None:
            raise ValueError("remote Appium servers require a URL")
        if self.mode is ServerMode.LOCAL and self.url is not None:
            raise ValueError("local Appium servers cannot define a remote URL")
        return self

    @classmethod
    def local(
        cls,
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        executable: Path | None = None,
    ) -> AppiumServer:
        return cls(mode=ServerMode.LOCAL, host=host, port=port, executable=executable)

    @classmethod
    def remote(
        cls,
        *,
        url: str,
        security: AppiumSecurityOptions | None = None,
    ) -> AppiumServer:
        selected_security = security if security is not None else AppiumSecurityOptions()
        return cls(mode=ServerMode.REMOTE, url=AnyHttpUrl(url), security=selected_security)


class AndroidDeviceSelector(StrictModel):
    serial: str | None = Field(default=None, min_length=1)
    platform_version: str | None = Field(default=None, min_length=1)
    emulator_name: str | None = Field(default=None, min_length=1)


class ApplicationOptions(StrictModel):
    package: str = Field(min_length=1)
    app_path: Path | None = None
    clear_data: bool = False


class CapabilityValue(StrictModel):
    kind: CapabilityValueKind
    string_value: str | None = None
    integer_value: int | None = None
    number_value: float | None = None
    boolean_value: bool | None = None
    items: tuple[CapabilityValue, ...] = ()
    entries: tuple[AdditionalCapability, ...] = ()

    @classmethod
    def string(cls, value: str) -> CapabilityValue:
        return cls(kind=CapabilityValueKind.STRING, string_value=value)

    @classmethod
    def integer(cls, value: int) -> CapabilityValue:
        return cls(kind=CapabilityValueKind.INTEGER, integer_value=value)

    @classmethod
    def number(cls, value: float) -> CapabilityValue:
        return cls(kind=CapabilityValueKind.NUMBER, number_value=value)

    @classmethod
    def boolean(cls, value: bool) -> CapabilityValue:
        return cls(kind=CapabilityValueKind.BOOLEAN, boolean_value=value)

    @classmethod
    def null(cls) -> CapabilityValue:
        return cls(kind=CapabilityValueKind.NULL)

    @classmethod
    def array(cls, items: tuple[CapabilityValue, ...]) -> CapabilityValue:
        return cls(kind=CapabilityValueKind.ARRAY, items=items)

    @classmethod
    def object(cls, entries: tuple[AdditionalCapability, ...]) -> CapabilityValue:
        return cls(kind=CapabilityValueKind.OBJECT, entries=entries)

    @model_validator(mode="after")
    def validate_value(self) -> CapabilityValue:
        scalar_values = (
            self.string_value,
            self.integer_value,
            self.number_value,
            self.boolean_value,
        )
        scalar_count = sum(value is not None for value in scalar_values)
        if self.kind is CapabilityValueKind.NULL:
            valid = scalar_count == 0 and not self.items and not self.entries
        elif self.kind is CapabilityValueKind.STRING:
            valid = (
                self.string_value is not None
                and scalar_count == 1
                and not self.items
                and not self.entries
            )
        elif self.kind is CapabilityValueKind.INTEGER:
            valid = (
                self.integer_value is not None
                and scalar_count == 1
                and not self.items
                and not self.entries
            )
        elif self.kind is CapabilityValueKind.NUMBER:
            valid = (
                self.number_value is not None
                and scalar_count == 1
                and not self.items
                and not self.entries
            )
        elif self.kind is CapabilityValueKind.BOOLEAN:
            valid = (
                self.boolean_value is not None
                and scalar_count == 1
                and not self.items
                and not self.entries
            )
        elif self.kind is CapabilityValueKind.ARRAY:
            valid = scalar_count == 0 and not self.entries
        else:
            valid = scalar_count == 0 and not self.items
        if not valid:
            raise ValueError("capability value kind does not match its populated field")
        return self


class AdditionalCapability(StrictModel):
    name: str = Field(min_length=1)
    value: CapabilityValue
    sensitive: bool = False

    @classmethod
    def string(
        cls,
        name: str,
        value: str,
        *,
        sensitive: bool = False,
    ) -> AdditionalCapability:
        return cls(
            name=name,
            value=CapabilityValue.string(value),
            sensitive=sensitive,
        )

    @classmethod
    def integer(cls, name: str, value: int) -> AdditionalCapability:
        return cls(
            name=name,
            value=CapabilityValue.integer(value),
        )

    @classmethod
    def number(cls, name: str, value: float) -> AdditionalCapability:
        return cls(
            name=name,
            value=CapabilityValue.number(value),
        )

    @classmethod
    def boolean(cls, name: str, value: bool) -> AdditionalCapability:
        return cls(
            name=name,
            value=CapabilityValue.boolean(value),
        )

    @classmethod
    def null(cls, name: str) -> AdditionalCapability:
        return cls(name=name, value=CapabilityValue.null())

    @classmethod
    def array(
        cls,
        name: str,
        items: tuple[CapabilityValue, ...],
    ) -> AdditionalCapability:
        return cls(
            name=name,
            value=CapabilityValue.array(items),
        )

    @classmethod
    def object(
        cls,
        name: str,
        entries: tuple[AdditionalCapability, ...],
    ) -> AdditionalCapability:
        return cls(
            name=name,
            value=CapabilityValue.object(entries),
        )


CapabilityValue.model_rebuild()

SENSITIVE_CAPABILITY_TERMS = ("accesskey", "password", "secret", "token")


def redacted_capability(capability: AdditionalCapability) -> AdditionalCapability:
    normalized_name = capability.name.casefold().replace("_", "").replace("-", "")
    sensitive = capability.sensitive or any(
        term in normalized_name for term in SENSITIVE_CAPABILITY_TERMS
    )
    if sensitive:
        return AdditionalCapability.string(capability.name, "[REDACTED]")
    return AdditionalCapability(
        name=capability.name,
        value=redacted_capability_value(capability.value),
        sensitive=capability.sensitive,
    )


def redacted_capability_value(value: CapabilityValue) -> CapabilityValue:
    if value.kind is CapabilityValueKind.ARRAY:
        return CapabilityValue(
            kind=CapabilityValueKind.ARRAY,
            items=tuple(redacted_capability_value(item) for item in value.items),
        )
    if value.kind is CapabilityValueKind.OBJECT:
        return CapabilityValue(
            kind=CapabilityValueKind.OBJECT,
            entries=tuple(redacted_capability(entry) for entry in value.entries),
        )
    return value


class AndroidSessionOptions(StrictModel):
    device: AndroidDeviceSelector
    timeouts: AppiumTimeouts = AppiumTimeouts()
    capabilities: tuple[AdditionalCapability, ...] = ()

    @model_validator(mode="after")
    def validate_capability_ownership(self) -> AndroidSessionOptions:
        validate_additional_capabilities(self.capabilities)
        return self


class AndroidConnectionOptions(StrictModel):
    selector: AndroidDeviceSelector = AndroidDeviceSelector()
    server: AppiumServer = AppiumServer.local()
    timeouts: AppiumTimeouts = AppiumTimeouts()
    capabilities: tuple[AdditionalCapability, ...] = ()

    @model_validator(mode="after")
    def validate_capability_ownership(self) -> AndroidConnectionOptions:
        validate_additional_capabilities(self.capabilities)
        return self


def validate_additional_capabilities(
    capabilities: tuple[AdditionalCapability, ...],
) -> None:
    names = tuple(capability.name for capability in capabilities)
    unnamespaced = tuple(name for name in names if ":" not in name)
    if unnamespaced:
        raise ValueError(
            "top-level additional capabilities require a W3C vendor namespace: "
            + ", ".join(unnamespaced)
        )
    if len(names) != len(set(names)):
        raise ValueError("additional capability names must be unique")
    reserved = {capability.value for capability in FrameworkCapability}
    conflicts = tuple(name for name in names if name in reserved)
    if conflicts:
        raise ValueError(
            "framework-owned capabilities cannot be overridden: " + ", ".join(conflicts)
        )


class CompatibilityManifest(StrictModel):
    appium_server: str = ">=3.0,<4"
    appium_python_client: str = ">=5.1.1,<6"
    selenium: str = ">=4.26,<5"
    uiautomator2_driver: str = ">=7.0,<8"
    minimum_android_api: int = 26


class SessionCapabilities(StrictModel):
    entries: tuple[AdditionalCapability, ...]

    def redacted(self) -> SessionCapabilities:
        return SessionCapabilities(
            entries=tuple(redacted_capability(entry) for entry in self.entries)
        )
