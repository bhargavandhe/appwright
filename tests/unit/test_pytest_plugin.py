"""End-to-end tests for pytest failure artifact capture."""

from pathlib import Path

import pytest


def test_failure_captures_screenshot_hierarchy_logs_and_trace(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", raising=False)
    pytester.makeini("[pytest]\nasyncio_default_fixture_loop_scope = function\n")
    pytester.makeconftest(
        """
from collections.abc import Generator
import pytest
from appwright.core.runtime import BackendFactory
from appwright.sync_api import Appwright, sync_appwright
from tests.fakes import FakeBackendFactory

@pytest.fixture
def appwright() -> Generator[Appwright, None, None]:
    context = sync_appwright(FakeBackendFactory())
    instance = context.__enter__()
    try:
        yield instance
    finally:
        context.__exit__(None, None, None)
"""
    )
    pytester.makepyfile(
        test_failure="""
def test_failure(mobile_app):
    assert False, "intentional"
"""
    )
    result = pytester.runpytest("--appwright-package=com.example", "-q")
    result.assert_outcomes(failed=1)
    artifact_root = Path(pytester.path) / ".appwright-artifacts"
    files = {path.name for path in artifact_root.rglob("*") if path.is_file()}
    assert {"appium-server.jsonl", "failure.png", "hierarchy.xml", "trace.zip"} <= files
