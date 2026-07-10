"""Regenerate checked-in API metadata and public facades."""

from pathlib import Path

from appwright.api.generator import render_manifest
from appwright.api.specification import SPECIFICATION


def main() -> int:
    destination = Path("src/appwright/api/generated/surface.py")
    destination.write_text(render_manifest(SPECIFICATION), encoding="utf-8")
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
        generated.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    return 0


raise SystemExit(main())
