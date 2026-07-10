"""Playwright-style mobile automation powered by Appium."""

from importlib.metadata import version as distribution_version


def version() -> str:
    """Return the installed Appwright distribution version."""
    return distribution_version("appwright")
