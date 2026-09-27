## Summary

<!-- What does this change and why? Link related issues (e.g. Closes #12). -->

## Type

- [ ] feat
- [ ] fix
- [ ] docs / i18n
- [ ] refactor / chore
- [ ] test

## How I tested

<!-- Commands you ran and what you checked. Mention if you ran live tests (`uv run pytest -m live`) on your own account. -->

## Checklist

- [ ] `uv run ruff format .` and `uv run ruff check .` pass
- [ ] `uv run mypy` passes
- [ ] `uv run pytest` passes (unit tests; live tests stay behind `@pytest.mark.live`)
- [ ] No secrets, cookies, tokens or other people's data in code, fixtures, logs or screenshots
- [ ] New write actions require `confirm=True`, are rate-limited and are traced by the Eye
- [ ] Docs updated if behaviour changed (README changes flagged so `docs/i18n/` can be refreshed)
