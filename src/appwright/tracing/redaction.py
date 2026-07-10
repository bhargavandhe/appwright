"""Central redaction for diagnostic text and structured trace values."""

from __future__ import annotations

import re

from pydantic import Field, field_validator

from appwright.models.base import StrictModel
from appwright.models.config import AdditionalCapability, CapabilityValue
from appwright.models.enums import CapabilityValueKind


class RedactionOptions(StrictModel):
    replacement: str = "[REDACTED]"
    patterns: tuple[str, ...] = ()
    minimum_secret_length: int = Field(default=4, ge=1)

    @field_validator("patterns")
    @classmethod
    def validate_patterns(cls, patterns: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in patterns:
            re.compile(pattern)
        return patterns


class DiagnosticRedactor:
    def __init__(self, options: RedactionOptions | None = None) -> None:
        self.options = options if options is not None else RedactionOptions()
        self.secret_values: set[str] = set()
        self.compiled_patterns = tuple(re.compile(pattern) for pattern in self.options.patterns)

    def register_secret(self, value: str) -> None:
        if len(value) >= self.options.minimum_secret_length:
            self.secret_values.add(value)

    def register_pattern(self, pattern: str) -> None:
        self.compiled_patterns = (*self.compiled_patterns, re.compile(pattern))

    def register_capability(self, capability: AdditionalCapability) -> None:
        if capability.sensitive:
            self.register_capability_value(capability.value)
        if capability.value.kind is CapabilityValueKind.OBJECT:
            for entry in capability.value.entries:
                self.register_capability(entry)

    def register_capability_value(self, value: CapabilityValue) -> None:
        if value.string_value is not None:
            self.register_secret(value.string_value)
        for item in value.items:
            self.register_capability_value(item)
        for entry in value.entries:
            self.register_capability_value(entry.value)

    def sanitize_text(self, value: str) -> str:
        sanitized = value
        for secret in sorted(self.secret_values, key=len, reverse=True):
            sanitized = sanitized.replace(secret, self.options.replacement)
        for pattern in self.compiled_patterns:
            sanitized = pattern.sub(self.options.replacement, sanitized)
        return sanitized

    def sanitize_bytes(self, content: bytes, media_type: str) -> bytes:
        if not (
            media_type.startswith("text/")
            or media_type in {"application/json", "application/x-ndjson", "application/xml"}
        ):
            return content
        return self.sanitize_text(content.decode("utf-8", errors="replace")).encode("utf-8")
