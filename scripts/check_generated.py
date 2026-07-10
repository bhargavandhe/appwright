"""Verify generated API metadata and facade parity."""

import inspect
from pathlib import Path

from appwright.api.generated.async_api import Android as AsyncAndroid
from appwright.api.generated.async_api import App as AsyncApp
from appwright.api.generated.async_api import Appwright as AsyncAppwright
from appwright.api.generated.async_api import Device as AsyncDevice
from appwright.api.generated.async_api import Keyboard as AsyncKeyboard
from appwright.api.generated.async_api import Locator as AsyncLocator
from appwright.api.generated.async_api import LocatorAssertions as AsyncLocatorAssertions
from appwright.api.generated.async_api import Screen as AsyncScreen
from appwright.api.generated.async_api import Touchscreen as AsyncTouchscreen
from appwright.api.generated.surface import API_SURFACE
from appwright.api.generated.sync_api import Android as SyncAndroid
from appwright.api.generated.sync_api import App as SyncApp
from appwright.api.generated.sync_api import Appwright as SyncAppwright
from appwright.api.generated.sync_api import Device as SyncDevice
from appwright.api.generated.sync_api import Keyboard as SyncKeyboard
from appwright.api.generated.sync_api import Locator as SyncLocator
from appwright.api.generated.sync_api import LocatorAssertions as SyncLocatorAssertions
from appwright.api.generated.sync_api import Screen as SyncScreen
from appwright.api.generated.sync_api import Touchscreen as SyncTouchscreen
from appwright.api.generator import render_manifest
from appwright.api.specification import SPECIFICATION


def async_class_for_name(name: str) -> type[object]:
    if name == "Appwright":
        return AsyncAppwright
    if name == "Android":
        return AsyncAndroid
    if name == "Device":
        return AsyncDevice
    if name == "App":
        return AsyncApp
    if name == "Locator":
        return AsyncLocator
    if name == "Screen":
        return AsyncScreen
    if name == "LocatorAssertions":
        return AsyncLocatorAssertions
    if name == "Keyboard":
        return AsyncKeyboard
    if name == "Touchscreen":
        return AsyncTouchscreen
    raise ValueError(f"unknown API class: {name}")


def sync_class_for_name(name: str) -> type[object]:
    if name == "Appwright":
        return SyncAppwright
    if name == "Android":
        return SyncAndroid
    if name == "Device":
        return SyncDevice
    if name == "App":
        return SyncApp
    if name == "Locator":
        return SyncLocator
    if name == "Screen":
        return SyncScreen
    if name == "LocatorAssertions":
        return SyncLocatorAssertions
    if name == "Keyboard":
        return SyncKeyboard
    if name == "Touchscreen":
        return SyncTouchscreen
    raise ValueError(f"unknown API class: {name}")


def main() -> int:
    if API_SURFACE != SPECIFICATION:
        print("generated API manifest is stale")
        return 1
    expected = render_manifest(SPECIFICATION)
    actual = Path("src/appwright/api/generated/surface.py").read_text(encoding="utf-8")
    if actual != expected:
        print("generated API source is stale")
        return 1
    generated_files = (
        (
            Path("scripts/templates/async_api.py.txt"),
            Path("src/appwright/api/generated/async_api.py"),
        ),
        (
            Path("scripts/templates/sync_api.py.txt"),
            Path("src/appwright/api/generated/sync_api.py"),
        ),
    )
    for template, generated in generated_files:
        if template.read_text(encoding="utf-8") != generated.read_text(encoding="utf-8"):
            print(f"generated facade is stale: {generated}")
            return 1
    for api_class in SPECIFICATION.classes:
        async_implementation = async_class_for_name(api_class.name)
        sync_implementation = sync_class_for_name(api_class.name)
        for method in api_class.methods:
            if not hasattr(async_implementation, method):
                print(f"missing async API member: {api_class.name}.{method}")
                return 1
            if not hasattr(sync_implementation, method):
                print(f"missing sync API member: {api_class.name}.{method}")
                return 1
            async_signature = inspect.signature(getattr(async_implementation, method))
            sync_signature = inspect.signature(getattr(sync_implementation, method))
            if async_signature != sync_signature:
                print(
                    f"sync/async signature mismatch: {api_class.name}.{method}: "
                    f"{async_signature} != {sync_signature}"
                )
                return 1
    return 0


raise SystemExit(main())
