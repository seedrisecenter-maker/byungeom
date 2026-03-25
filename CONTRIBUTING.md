# Contributing to byungeom

Thank you for your interest in contributing. This document covers everything you need to get started.

## How to Contribute

1. **Fork** the repository on GitHub
2. **Clone** your fork locally
3. **Create a branch** for your change:
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/the-bug-you-fixed
   ```
4. **Make your changes** — keep each PR focused on one concern
5. **Push** to your fork and **open a Pull Request** against `main`

Keep PRs small and reviewable. One feature or fix per PR.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/seedrisecenter-maker/byungeom.git
cd byungeom

# Install in editable mode with all dependencies
pip install -e ".[all,dev]"
```

If there is no `dev` extra yet, install manually:

```bash
pip install -e ".[all]"
pip install ruff bandit pytest pytest-asyncio
```

**Requirements:**
- Python 3.10+
- Claude CLI: `npm install -g @anthropic-ai/claude-cli` then `claude login`

## Code Style

- Formatter: **ruff format** (line length 88)
- Linter: **ruff check** — all warnings must pass before submitting
- Type hints are required on all public functions and methods
- No bare `except:` — always catch specific exceptions

Run before committing:

```bash
ruff format byungeom/
ruff check byungeom/
bandit -r byungeom/ -ll
```

## Testing

```bash
# Run all tests
pytest

# Run with asyncio mode
pytest --asyncio-mode=auto

# Run a specific file
pytest tests/test_verifier.py -v
```

Write tests for any new public API. Tests live in `tests/`. Use `pytest-asyncio` for async functions.

If you cannot run tests against live LLM APIs (no keys), mock the subprocess/API calls — the existing test patterns show how.

## Issue Templates

When opening an issue, include:

**Bug report:**
- Python version and OS
- Exact command or code that triggered the bug
- Full traceback
- Expected vs. actual behaviour

**Feature request:**
- What problem it solves
- Proposed API surface (function signature, CLI flag, etc.)
- Which model(s) it affects (Claude / GPT / Gemini / all)

## Good First Issues

- Add support for additional LLM backends (Mistral, Cohere, etc.)
- Implement the `byungeom generate` CLI entry point
- Add structured JSON output mode for CI pipelines
- Write integration tests with mocked subprocess calls
- Improve error messages when Claude CLI is not installed

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Mistral backend to StarChamber
fix: handle timeout on Windows subprocess
refactor: extract prompt templates to constants
test: add async mock tests for Verifier
docs: clarify GOOGLE_API_KEY vs GEMINI_API_KEY
```

## License

By contributing, you agree that your contributions will be licensed under the [Apache 2.0 License](LICENSE).
