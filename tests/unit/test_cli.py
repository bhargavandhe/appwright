"""CLI output and artifact workflow tests."""

import json
import subprocess
from pathlib import Path

import pytest

from appwright.cli.main import (
    init_command,
    inspect_config_command,
    inspect_trace_command,
    run_command,
)
from appwright.tracing import TraceRecorder


def test_inspect_config_emits_one_json_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert inspect_config_command() == 0
    parsed = json.loads(capsys.readouterr().out)
    assert set(parsed) == {"compatibility", "configuration"}


def test_init_refuses_to_overwrite_existing_test(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    destination = tmp_path / "tests"
    assert init_command(destination) == 0
    assert init_command(destination) == 1
    assert "Refusing to overwrite" in capsys.readouterr().out


def test_trace_inspection_supports_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "trace.zip"
    recorder = TraceRecorder()
    recorder.start()
    recorder.stop(path)
    assert inspect_trace_command(path, json_output=True) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["manifest"]["format_version"] == 2


def test_command_timeout_becomes_typed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(executable: str) -> str:
        return "/bin/tool"

    monkeypatch.setattr("appwright.cli.main.shutil.which", fake_which)

    def timeout_command(*arguments: object, **keywords: object) -> None:
        raise subprocess.TimeoutExpired(cmd="tool", timeout=0.1)

    monkeypatch.setattr("appwright.cli.main.subprocess.run", timeout_command)
    result = run_command("tool", (), timeout_seconds=0.1)
    assert result.timed_out
    assert result.exit_code == 124
