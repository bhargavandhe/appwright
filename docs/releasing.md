# Releasing Appwright

Releases use Semantic Versioning and tags formatted as `vX.Y.Z`.

## Release checklist

1. Confirm all required CI and Android compatibility jobs pass.
2. Review dependency and security scan results.
3. Move relevant entries from `Unreleased` into a dated changelog section.
4. Update the version in `pyproject.toml`.
5. Run the full local quality suite and build wheel and sdist.
6. Install both artifacts in clean environments and run import, CLI, and pytest-plugin smoke tests.
7. Commit the release preparation with DCO sign-off.
8. Create and push the signed `vX.Y.Z` tag.
9. Verify the tag-build workflow and generated GitHub artifact.
10. Publish through PyPI Trusted Publishing only after the project and environment are configured.
11. Create release notes and verify documentation links.
12. If a release is unsafe, yank it on PyPI and publish a corrected patch release.

The current GitHub workflow builds tagged artifacts but intentionally does not publish to PyPI.
