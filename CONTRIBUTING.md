# Contributing to Appwright

Thank you for helping improve Appwright. The project welcomes focused bug reports, design
discussions, documentation improvements, and tested code contributions.

## Before opening a change

- Use GitHub Discussions for usage questions and early design proposals.
- Search existing issues before filing a bug or feature request.
- Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
- Open an issue before undertaking a large API or architectural change.

## Development environment

Appwright requires Python 3.11 or newer. Install the locked development environment with:

```shell
uv sync --locked --all-extras --dev
```

Run the complete local quality suite:

```shell
uv run ruff format --check .
uv run ruff check .
uv run python scripts/check_no_private_state.py
uv run python scripts/check_generated.py
uv run mypy src
uv run mypy tests/typing/public_api.py
uv run pyright
uv run pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest \
  -p pytest_cov -p asyncio -p pytester \
  --cov=appwright --cov-branch
uv run python -m build --no-isolation
```

Android integration tests additionally require Appium 3, UiAutomator2 7.x, and a configured
emulator or device. Run `uv run appwright doctor` before them.

## Project rules

- Every framework-owned structured record is a strict, frozen Pydantic model.
- Framework-owned states, strategies, actions, and commands use enums.
- Public APIs do not expose WebDriver objects, raw capabilities, dictionaries, or XPath.
- Project-authored state variables must not begin with an underscore.
- Do not vendor third-party code.
- Add regression tests for every behavior change.
- Keep sync and async public APIs aligned.

The no-private-state policy checker is mandatory and has no suppression mechanism.

## Generated APIs

Generated facades are sourced from `scripts/templates`. After changing a public signature, run:

```shell
uv run python scripts/generate_api.py
uv run python scripts/check_generated.py
```

Commit the specification, templates, and generated output together.

## Commit sign-off

Appwright uses the [Developer Certificate of Origin](https://developercertificate.org/) rather
than a contributor license agreement. Sign off every commit with:

```shell
git commit --signoff
```

The sign-off certifies that you have the right to submit the contribution under the project's
license. The pull-request DCO check rejects unsigned commits.

## Pull requests

- Keep each pull request focused on one coherent outcome.
- Explain user-visible behavior and important design decisions.
- Include tests and documentation where applicable.
- Call out breaking changes explicitly while the project is pre-1.0.
- Do not update version numbers as part of ordinary pull requests.
- Ensure all required checks pass before requesting review.

By participating, you agree to follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
