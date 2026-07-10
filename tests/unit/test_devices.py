"""Bounded Android device discovery tests."""

import asyncio
from datetime import timedelta

import pytest

from appwright.core.devices import discover_android_devices
from appwright.core.errors import AppiumUnavailableError


class HangingAdbProcess:
    def __init__(self) -> None:
        self.returncode = 0
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(10)
        return b"", b""

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return 0


@pytest.mark.asyncio
async def test_adb_discovery_timeout_kills_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = HangingAdbProcess()

    async def create_process(
        executable: str,
        command: str,
        option: str,
        *,
        stdout: int,
        stderr: int,
    ) -> HangingAdbProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    with pytest.raises(AppiumUnavailableError, match="timed out"):
        await discover_android_devices(timedelta(milliseconds=1))
    assert process.killed
