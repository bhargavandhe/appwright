"""Opt-in Android behavior tests for a configured application."""

import os

import pytest

from appwright.models import Key
from appwright.sync_api import sync_appwright


def android_target() -> tuple[str, str]:
    serial = os.getenv("APPWRIGHT_TEST_SERIAL")
    package = os.getenv("APPWRIGHT_TEST_PACKAGE")
    if serial is None or package is None:
        pytest.skip("set APPWRIGHT_TEST_SERIAL and APPWRIGHT_TEST_PACKAGE")
    return serial, package


@pytest.mark.integration
def test_lifecycle_hierarchy_keyboard_and_restart() -> None:
    serial, package = android_target()
    with sync_appwright() as appwright:
        device = appwright.android.connect(serial=serial)
        assert device.hierarchy().content
        app = device.launch_app(package=package)
        assert app.screenshot().content
        device.keyboard.press(Key.BACK)
        app.activate()
        app.terminate()
        app.activate()


@pytest.mark.integration
def test_configured_editable_resource_id() -> None:
    serial, package = android_target()
    resource_id = os.getenv("APPWRIGHT_TEST_INPUT_RESOURCE_ID")
    if resource_id is None:
        pytest.skip("set APPWRIGHT_TEST_INPUT_RESOURCE_ID")
    with sync_appwright() as appwright:
        device = appwright.android.connect(serial=serial)
        app = device.launch_app(package=package)
        field = app.get_by_resource_id(resource_id)
        field.fill("Appwright")
        assert field.text_content() == "Appwright"
