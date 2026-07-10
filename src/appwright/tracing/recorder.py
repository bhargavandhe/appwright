"""Bounded, file-backed trace recording."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from appwright.models.config import AdditionalCapability
from appwright.models.data import (
    TraceArtifact,
    TraceArtifactIndex,
    TraceEvent,
    TraceField,
    TraceLimits,
    TraceManifest,
)
from appwright.tracing.redaction import DiagnosticRedactor, RedactionOptions


class TraceRecorder:
    def __init__(
        self,
        limits: TraceLimits | None = None,
        redaction: RedactionOptions | None = None,
    ) -> None:
        self.limits = limits if limits is not None else TraceLimits()
        self.redactor = DiagnosticRedactor(redaction)
        self.active = False
        self.output_path: Path | None = None
        self.planned_output_path: Path | None = None
        self.working_path: Path | None = None
        self.events_path: Path | None = None
        self.artifacts_path: Path | None = None
        self.artifact_indexes: list[TraceArtifactIndex] = []
        self.event_count = 0
        self.artifact_count = 0
        self.total_bytes = 0
        self.dropped_event_count = 0
        self.dropped_artifact_count = 0
        self.seed_events: list[TraceEvent] = []
        self.seed_artifacts: list[TraceArtifact] = []

    def register_secret(self, value: str) -> None:
        self.redactor.register_secret(value)

    def register_pattern(self, pattern: str) -> None:
        self.redactor.register_pattern(pattern)

    def register_capability(self, capability: AdditionalCapability) -> None:
        self.redactor.register_capability(capability)

    def seed_event(self, event: TraceEvent) -> None:
        self.seed_events.append(event)

    def seed_artifact(self, artifact: TraceArtifact) -> None:
        self.seed_artifacts.append(artifact)

    def start(self, path: Path | None = None) -> None:
        self.cleanup_working_path()
        working_path = Path(tempfile.mkdtemp(prefix="appwright-trace-"))
        os.chmod(working_path, 0o700)
        artifacts_path = working_path / "artifacts"
        artifacts_path.mkdir(mode=0o700)
        events_path = working_path / "events.jsonl"
        events_path.touch(mode=0o600)
        self.working_path = working_path
        self.events_path = events_path
        self.artifacts_path = artifacts_path
        self.artifact_indexes.clear()
        self.event_count = 0
        self.artifact_count = 0
        self.total_bytes = 0
        self.dropped_event_count = 0
        self.dropped_artifact_count = 0
        self.output_path = None
        self.planned_output_path = path
        self.active = True
        for event in self.seed_events:
            self.record(event)
        for artifact in self.seed_artifacts:
            self.attach(artifact)

    def record(self, event: TraceEvent) -> None:
        if not self.active:
            return
        if self.event_count >= self.limits.maximum_events:
            self.dropped_event_count += 1
            return
        sanitized_fields = tuple(
            TraceField(
                name=field.name,
                value=self.redactor.sanitize_text(field.value),
            )
            for field in event.fields
        )
        sanitized_event = event.model_copy(update={"fields": sanitized_fields})
        content = f"{sanitized_event.model_dump_json()}\n".encode()
        if self.total_bytes + len(content) > self.limits.maximum_total_bytes:
            self.dropped_event_count += 1
            return
        events_path = self.require_events_path()
        with events_path.open("ab") as stream:
            stream.write(content)
        self.event_count += 1
        self.total_bytes += len(content)

    def attach(self, artifact: TraceArtifact) -> None:
        if not self.active:
            return
        sanitized_content = self.redactor.sanitize_bytes(artifact.content, artifact.media_type)
        if (
            self.artifact_count >= self.limits.maximum_artifacts
            or len(sanitized_content) > self.limits.maximum_artifact_bytes
            or self.total_bytes + len(sanitized_content) > self.limits.maximum_total_bytes
        ):
            self.dropped_artifact_count += 1
            return
        artifact_path = self.require_artifacts_path() / artifact.name
        if artifact_path.exists():
            raise ValueError(f"duplicate trace artifact name: {artifact.name}")
        artifact_path.write_bytes(sanitized_content)
        os.chmod(artifact_path, 0o600)
        sanitized_artifact = artifact.model_copy(update={"content": sanitized_content})
        self.artifact_indexes.append(sanitized_artifact.index())
        self.artifact_count += 1
        self.total_bytes += len(sanitized_content)

    def discard(self) -> None:
        self.active = False
        self.cleanup_working_path()
        self.output_path = None
        self.planned_output_path = None

    def stop(self, path: Path | None = None) -> Path:
        if not self.active:
            raise RuntimeError("trace recording is not active")
        selected_path = path if path is not None else self.planned_output_path
        if selected_path is None:
            raise ValueError("trace output path is required")
        selected_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = TraceManifest(
            event_count=self.event_count,
            artifact_count=self.artifact_count,
            total_bytes=self.total_bytes,
            dropped_event_count=self.dropped_event_count,
            dropped_artifact_count=self.dropped_artifact_count,
            truncated=bool(self.dropped_event_count or self.dropped_artifact_count),
        )
        artifact_lines = "\n".join(artifact.model_dump_json() for artifact in self.artifact_indexes)
        with ZipFile(selected_path, mode="w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", manifest.model_dump_json(indent=2))
            archive.write(self.require_events_path(), "events.jsonl")
            archive.writestr("artifacts.jsonl", artifact_lines)
            for artifact in self.artifact_indexes:
                archive.write(
                    self.require_artifacts_path() / artifact.name,
                    f"artifacts/{artifact.name}",
                )
        os.chmod(selected_path, 0o600)
        self.active = False
        self.output_path = selected_path
        self.planned_output_path = None
        self.cleanup_working_path()
        return selected_path

    def require_events_path(self) -> Path:
        if self.events_path is None:
            raise RuntimeError("trace recording is not active")
        return self.events_path

    def require_artifacts_path(self) -> Path:
        if self.artifacts_path is None:
            raise RuntimeError("trace recording is not active")
        return self.artifacts_path

    def cleanup_working_path(self) -> None:
        working_path = self.working_path
        self.working_path = None
        self.events_path = None
        self.artifacts_path = None
        if working_path is not None:
            shutil.rmtree(working_path, ignore_errors=True)
