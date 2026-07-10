"""Appwright command-line interface."""

import argparse
import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from appwright.backends.appium.service import uiautomator2_version
from appwright.core.devices import discover_android_devices
from appwright.models.config import CompatibilityManifest
from appwright.models.data import TraceManifest
from appwright.models.diagnostics import (
    CommandOutput,
    ConfigurationReport,
    DevicesReport,
    DoctorCheck,
    DoctorReport,
    TraceInspection,
)
from appwright.models.project import load_configuration


def run_command(
    executable: str,
    arguments: tuple[str, ...],
    timeout_seconds: float = 30,
) -> CommandOutput:
    path = shutil.which(executable)
    if path is None:
        return CommandOutput(
            exit_code=127,
            standard_output="",
            standard_error=f"{executable} was not found on PATH",
        )
    try:
        result = subprocess.run(
            (path, *arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        return CommandOutput(
            exit_code=124,
            standard_output="" if error.stdout is None else str(error.stdout),
            standard_error=f"command timed out after {timeout_seconds:g}s",
            timed_out=True,
        )
    return CommandOutput(
        exit_code=result.returncode,
        standard_output=result.stdout.strip(),
        standard_error=result.stderr.strip(),
    )


def command_detail(output: CommandOutput) -> str:
    return output.standard_output or output.standard_error


def doctor_report(timeout_seconds: float = 30) -> DoctorReport:
    appium_result = run_command("appium", ("--version",), timeout_seconds)
    appium_output = command_detail(appium_result)
    appium_version_ok = appium_result.succeeded and appium_output.startswith("3.")
    driver_result = run_command("appium", ("driver", "list", "--installed"), timeout_seconds)
    driver_output = command_detail(driver_result)
    driver_version = uiautomator2_version(driver_output)
    uiautomator_ok = (
        driver_result.succeeded and driver_version is not None and driver_version.startswith("7.")
    )
    driver_doctor_result = run_command(
        "appium", ("driver", "doctor", "uiautomator2"), timeout_seconds
    )
    driver_doctor_output = command_detail(driver_doctor_result)
    adb_result = run_command("adb", ("devices", "-l"), timeout_seconds)
    adb_output = command_detail(adb_result)
    return DoctorReport(
        checks=(
            DoctorCheck(
                name="Appium 3",
                passed=appium_version_ok,
                detail=appium_output,
                remediation=None if appium_version_ok else "Install Appium 3 and add it to PATH.",
            ),
            DoctorCheck(
                name="UiAutomator2 driver",
                passed=uiautomator_ok,
                detail=driver_output,
                remediation=(
                    None
                    if uiautomator_ok
                    else (
                        "Run: appium driver install uiautomator2, then "
                        "appium driver doctor uiautomator2"
                    )
                ),
            ),
            DoctorCheck(
                name="UiAutomator2 prerequisites",
                passed=driver_doctor_result.succeeded,
                detail=driver_doctor_output,
                remediation=(
                    None
                    if driver_doctor_result.succeeded
                    else "Run: appium driver doctor uiautomator2"
                ),
            ),
            DoctorCheck(
                name="ADB",
                passed=adb_result.succeeded,
                detail=adb_output,
                remediation=(
                    None
                    if adb_result.succeeded
                    else (
                        "Install Android SDK platform-tools or fix permissions so the ADB "
                        "server can start."
                    )
                ),
            ),
        )
    )


def render_doctor(report: DoctorReport) -> None:
    for check in report.checks:
        marker = "PASS" if check.passed else "FAIL"
        print(f"[{marker}] {check.name}: {check.detail}")
        if check.remediation is not None:
            print(f"       {check.remediation}")


def doctor_command(json_output: bool, timeout_seconds: float) -> int:
    report = doctor_report(timeout_seconds)
    if json_output:
        print(report.model_dump_json(indent=2))
    else:
        render_doctor(report)
    return 0 if report.passed else 1


def devices_command(json_output: bool) -> int:
    devices = asyncio.run(discover_android_devices())
    if json_output:
        print(DevicesReport(devices=devices).model_dump_json(indent=2))
    else:
        for device in devices:
            model = f" ({device.model})" if device.model else ""
            print(f"{device.serial}\t{device.state.value}{model}")
    return 0


def inspect_config_command() -> int:
    report = ConfigurationReport(
        configuration=load_configuration(),
        compatibility=CompatibilityManifest(),
    )
    print(report.model_dump_json(indent=2))
    return 0


def inspect_trace_command(path: Path, json_output: bool) -> int:
    try:
        with ZipFile(path) as archive:
            manifest = TraceManifest.model_validate_json(archive.read("manifest.json"))
            events = archive.read("events.jsonl").decode().splitlines()
            artifacts = tuple(name for name in archive.namelist() if name.startswith("artifacts/"))
    except (BadZipFile, KeyError, OSError, ValueError) as error:
        print(f"Unable to inspect trace: {error}")
        return 1
    inspection = TraceInspection(
        path=path,
        manifest=manifest,
        event_count=len(events),
        artifacts=artifacts,
    )
    if json_output:
        print(inspection.model_dump_json(indent=2))
    else:
        print(f"Trace: {path}")
        print(f"Events: {inspection.event_count}")
        print(f"Artifacts: {len(inspection.artifacts)}")
    return 0


def init_command(path: Path) -> int:
    path.mkdir(parents=True, exist_ok=True)
    test_path = path / "test_mobile.py"
    if test_path.exists():
        print(f"Refusing to overwrite existing file: {test_path}")
        return 1
    test_path.write_text(
        """from appwright.sync_api import expect\n\n\ndef test_welcome(mobile_app):\n    expect(mobile_app.get_by_text(\"Welcome\")).to_be_visible()\n""",
        encoding="utf-8",
    )
    print(f"Created {test_path}")
    return 0


def server_command(host: str, port: int) -> int:
    executable = shutil.which("appium")
    if executable is None:
        print("Appium was not found on PATH")
        return 1
    result = subprocess.run(
        (executable, "--address", host, "--port", str(port)),
        check=False,
    )
    return result.returncode


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="appwright")
    root.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="WARNING",
    )
    commands = root.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="validate Appium and Android prerequisites")
    doctor.add_argument("--json", action="store_true", dest="json_output")
    doctor.add_argument("--timeout", type=float, default=30, dest="timeout_seconds")
    devices = commands.add_parser("devices", help="list locally connected Android devices")
    devices.add_argument("--json", action="store_true", dest="json_output")
    commands.add_parser("inspect-config", help="print validated configuration")
    server = commands.add_parser("server", help="run the installed Appium server")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", default=4723, type=int)
    trace = commands.add_parser("trace", help="inspect Appwright traces")
    trace_commands = trace.add_subparsers(dest="trace_command", required=True)
    trace_inspect = trace_commands.add_parser("inspect", help="inspect a trace archive")
    trace_inspect.add_argument("path", type=Path)
    trace_inspect.add_argument("--json", action="store_true", dest="json_output")
    initialize = commands.add_parser("init", help="create a starter Appwright test")
    initialize.add_argument("path", type=Path, nargs="?", default=Path("tests"))
    return root


def main() -> int:
    arguments = parser().parse_args()
    logging.basicConfig(level=str(arguments.log_level))
    if arguments.command == "doctor":
        return doctor_command(bool(arguments.json_output), float(arguments.timeout_seconds))
    if arguments.command == "devices":
        return devices_command(bool(arguments.json_output))
    if arguments.command == "inspect-config":
        return inspect_config_command()
    if arguments.command == "server":
        return server_command(str(arguments.host), int(arguments.port))
    if arguments.command == "trace" and arguments.trace_command == "inspect":
        return inspect_trace_command(Path(arguments.path), bool(arguments.json_output))
    if arguments.command == "init":
        return init_command(Path(arguments.path))
    return 1
