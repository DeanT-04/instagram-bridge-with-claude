# Contributing to Heliograph

Thanks for helping. Heliograph is early (v0.1.0), so issues, design feedback and small focused PRs are all welcome. Please read the [Code of Conduct](CODE_OF_CONDUCT.md) first, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before touching drivers or the MCP layer.

## Development setup

Heliograph uses [uv](https://docs.astral.sh/uv/) and Python 3.11+ (3.12 is the development version, see `.python-version`).

```bash
git clone https://github.com/DeanT-04/instagram-bridge-with-claude.git heliograph
cd heliograph
uv sync                       # creates .venv with runtime + dev dependencies
uv run heliograph doctor      # check your environment
```

You will also want `ffmpeg` on your `PATH` and Microsoft Edge or Google Chrome installed. The live-app driver needs Windows and the Microsoft Store Instagram app.

## Checks

Run these before opening a PR. CI will run the same set once it is set up.

```bash
uv run ruff format .          # format
uv run ruff check .           # lint (rules in pyproject.toml)
uv run mypy                   # strict type checking
uv run pytest                 # unit tests
uv run pip-audit              # dependency vulnerability scan (occasionally)
```

## Tests

- Unit tests live in `tests/` and use fixtures and fakes. They must not touch the network, a browser or a real Instagram account.
- Tests that touch the real device, network or Instagram are marked **`@pytest.mark.live`**. They are **deselected by default** (`addopts = "-m 'not live'"` in `pyproject.toml`).
- To run live tests deliberately, on your own account:

  ```bash
  uv run pytest -m live
  ```

- Live tests must be **read-only** unless they are clearly named and documented, and must never send DMs, comment, post or follow.
- Never commit real cookies, tokens, usernames of other people, or downloaded media. Fixtures should be synthetic or thoroughly anonymised.

## Branches and commits

- Branch from `main` using a short prefix: `feat/…`, `fix/…`, `docs/…`, `refactor/…`, `test/…`, `chore/…`.
- Use [Conventional Commits](https://www.conventionalcommits.org/) for messages, with an optional scope:

  ```text
  feat(cdp): list saved collections via web API
  fix(eye): redact csrftoken in request headers
  docs(i18n): update Japanese README
  ```

- Keep PRs focused. One logical change per PR is easier to review than a bundle.

## Pull requests

- Fill in the PR template, including how you tested.
- Add or update tests for behaviour changes.
- Any new **write action** must require `confirm=True` at the MCP layer, be rate-limited, and be recorded by the Eye.
- Never log secrets. Route new logging through `heliograph.eye` so redaction applies.
- Update docs when behaviour changes. If you change `README.md`, note it in the PR so translations in `docs/i18n/` can be refreshed. The English README is the source of truth; translation PRs are very welcome.

## Brand

Docs and assets use the "Solar Brass" palette: ink navy `#0B1026`, brass `#E0A526`, signal coral `#FF6B5A`, parchment `#F4EBD9`, slate `#2A3150`. SVGs in `docs/assets/` are hand-written: no external fonts or scripts, so they render on GitHub.

## Reporting security issues

Please don't open public issues for vulnerabilities. See [docs/SECURITY.md](docs/SECURITY.md).
