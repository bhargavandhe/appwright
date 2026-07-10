"""Opt-in real Android smoke test."""

import os

import pytest

from appwright.sync_api import sync_appwright


@pytest.mark.integration
def test_real_android_session() -> None:
    serial = os.getenv("APPWRIGHT_TEST_SERIAL")
    package = os.getenv("APPWRIGHT_TEST_PACKAGE")
    if serial is None or package is None:
        pytest.skip("set APPWRIGHT_TEST_SERIAL and APPWRIGHT_TEST_PACKAGE")
    with sync_appwright() as appwright:
        device = appwright.android.connect(serial=serial)
        app = device.launch_app(package=package)
        assert app.screenshot().content
