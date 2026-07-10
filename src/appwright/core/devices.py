"""Local Android device discovery."""

import asyncio
from contextlib import suppress
from datetime import timedelta

from appwright.core.errors import AppiumUnavailableError
from appwright.models.data import DeviceInfo, ErrorDetails
from appwright.models.enums import DeviceState, ErrorCode


def device_state(value: str) -> DeviceState:
    if value == "device":
        return DeviceState.ONLINE
    if value == "offline":
        return DeviceState.OFFLINE
    if value == "unauthorized":
        return DeviceState.UNAUTHORIZED
    return DeviceState.UNKNOWN


def token_value(tokens: tuple[str, ...], prefix: str) -> str | None:
    for token in tokens:
        if token.startswith(prefix):
            return token.removeprefix(prefix)
    return None


def parse_adb_devices(output: str) -> tuple[DeviceInfo, ...]:
    devices: list[DeviceInfo] = []
    for line in output.splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        tokens = tuple(stripped.split())
        if len(tokens) < 2:
            continue
        devices.append(
            DeviceInfo(
                serial=tokens[0],
                state=device_state(tokens[1]),
                model=token_value(tokens[2:], "model:"),
                product=token_value(tokens[2:], "product:"),
                transport_id=token_value(tokens[2:], "transport_id:"),
            )
        )
    return tuple(devices)


async def discover_android_devices(
    timeout: timedelta = timedelta(seconds=10),
) -> tuple[DeviceInfo, ...]:
    try:
        process = await asyncio.create_subprocess_exec(
            "adb",
            "devices",
            "-l",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise AppiumUnavailableError(
            ErrorDetails(
                code=ErrorCode.APPIUM_UNAVAILABLE,
                api_name="android.devices",
                message="adb is not installed or is not on PATH",
            )
        ) from error
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout.total_seconds(),
        )
    except TimeoutError as error:
        process.kill()
        with suppress(Exception):
            await process.wait()
        raise AppiumUnavailableError(
            ErrorDetails(
                code=ErrorCode.APPIUM_UNAVAILABLE,
                api_name="android.devices",
                message=f"adb device discovery timed out after {timeout.total_seconds():g}s",
            )
        ) from error
    if process.returncode != 0:
        message = stderr.decode(errors="replace").strip() or "adb device discovery failed"
        raise AppiumUnavailableError(
            ErrorDetails(
                code=ErrorCode.APPIUM_UNAVAILABLE,
                api_name="android.devices",
                message=message,
            )
        )
    return parse_adb_devices(stdout.decode(errors="replace"))
