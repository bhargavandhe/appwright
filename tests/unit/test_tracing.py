"""Trace archive tests."""

import os
from pathlib import Path
from zipfile import ZipFile

from appwright.core.runtime import trace_event
from appwright.models.config import AdditionalCapability
from appwright.models.data import TraceArtifact, TraceEvent, TraceField, TraceLimits
from appwright.models.enums import TraceArtifactKind, TraceEventKind
from appwright.tracing import DiagnosticRedactor, RedactionOptions, TraceRecorder


def test_trace_recorder_writes_versioned_archive(tmp_path: Path) -> None:
    recorder = TraceRecorder()
    recorder.start()
    recorder.record(trace_event(TraceEventKind.ACTION, "tap", (("locator", "text"),)))
    recorder.attach(
        TraceArtifact(
            kind=TraceArtifactKind.SCREENSHOT,
            name="screenshot-1.png",
            media_type="image/png",
            content=b"png",
        )
    )
    path = recorder.stop(tmp_path / "trace.zip")
    with ZipFile(path) as archive:
        assert set(archive.namelist()) == {
            "artifacts/screenshot-1.png",
            "artifacts.jsonl",
            "events.jsonl",
            "manifest.json",
        }
        assert '"event_count": 1' in archive.read("manifest.json").decode()
        assert '"artifact_count": 1' in archive.read("manifest.json").decode()
    assert recorder.working_path is None
    assert os.stat(path).st_mode & 0o077 == 0


def test_trace_recorder_streams_and_records_truncation(tmp_path: Path) -> None:
    recorder = TraceRecorder(
        limits=TraceLimits(
            maximum_events=1,
            maximum_artifacts=1,
            maximum_total_bytes=10_000,
            maximum_artifact_bytes=3,
        )
    )
    recorder.start()
    recorder.record(TraceEvent(kind=TraceEventKind.ACTION, name="first"))
    recorder.record(TraceEvent(kind=TraceEventKind.ACTION, name="second"))
    assert recorder.require_events_path().stat().st_size > 0
    recorder.attach(
        TraceArtifact(
            kind=TraceArtifactKind.SCREENSHOT,
            name="too-large.png",
            media_type="image/png",
            content=b"large",
        )
    )
    path = recorder.stop(tmp_path / "truncated.zip")
    with ZipFile(path) as archive:
        manifest = archive.read("manifest.json").decode()
        assert '"dropped_event_count": 1' in manifest
        assert '"dropped_artifact_count": 1' in manifest
        assert '"truncated": true' in manifest


def test_trace_redacts_registered_secrets(tmp_path: Path) -> None:
    recorder = TraceRecorder()
    recorder.register_secret("canary-secret")
    recorder.start()
    recorder.record(
        TraceEvent(
            kind=TraceEventKind.QUERY,
            name="query",
            fields=(
                TraceField(name="result", value='{"text":"Ada","identity":"one"}'),
                TraceField(name="details", value="token=canary-secret"),
            ),
        )
    )
    recorder.attach(
        TraceArtifact(
            kind=TraceArtifactKind.SERVER_LOG,
            name="server.jsonl",
            media_type="application/x-ndjson",
            content=b"credential=canary-secret",
        )
    )
    path = recorder.stop(tmp_path / "redacted.zip")
    assert b"canary-secret" not in path.read_bytes()
    with ZipFile(path) as archive:
        events = archive.read("events.jsonl").decode()
        assert "[REDACTED]" in events


def test_redactor_registers_nested_sensitive_capability_values() -> None:
    redactor = DiagnosticRedactor(RedactionOptions())
    secret = AdditionalCapability.string("vendor:opaque", "secret-value", sensitive=True)
    nested = AdditionalCapability.object("vendor:options", entries=(secret,))
    redactor.register_capability(nested)
    assert redactor.sanitize_text("received secret-value") == "received [REDACTED]"
