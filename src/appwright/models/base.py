"""Shared Pydantic model configuration."""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Immutable, strict base for every structured Appwright record."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )
