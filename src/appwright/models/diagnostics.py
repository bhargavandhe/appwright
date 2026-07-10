"""CLI diagnostic models."""

from pathlib import Path

from appwright.models.base import StrictModel
from appwright.models.config import CompatibilityManifest
from appwright.models.data import DeviceInfo, TraceManifest
from appwright.models.project import AppwrightConfiguration


class CommandOutput(StrictModel):
    exit_code: int
    standard_output: str
    standard_error: str
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class DoctorCheck(StrictModel):
    name: str
    passed: bool
    detail: str
    remediation: str | None = None


class DoctorReport(StrictModel):
    checks: tuple[DoctorCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


class DevicesReport(StrictModel):
    devices: tuple[DeviceInfo, ...]


class ConfigurationReport(StrictModel):
    configuration: AppwrightConfiguration
    compatibility: CompatibilityManifest


class TraceInspection(StrictModel):
    path: Path
    manifest: TraceManifest
    event_count: int
    artifacts: tuple[str, ...]
