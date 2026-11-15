# Agent instructions

## Cut a new build after every turn

After each turn, cut a new build in git **unless something is unresolved**.

- Run the checks first (see [Checks](#checks)).
- Bump the patch version in `pyproject.toml`, `sextant_control/__init__.py`, and
  `sextant_agent/__init__.py`.
- Add a matching entry to `CHANGELOG.md`.
- Rebuild the wheel and sdist and refresh `dist/SHA256SUMS`:
  `.venv/bin/python -m build --no-isolation --outdir dist`
- Commit everything with a clear message.
- Push the commit(s) to GitHub (`git push origin main`) once the build is done.

If a turn is blocked, incomplete, or waiting on a decision, do **not** build,
commit, or push. Leave the working tree as-is and state clearly what is
unresolved.

Changes that do not affect the shipped package (for example, this file or other
documentation) do not need a version bump or rebuilt artifacts; commit those
without a release.

## Checks

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pyflakes sextant_control sextant_agent tests
.venv/bin/python -m compileall -q sextant_control sextant_agent
node --check sextant_control/static/app.js

cd ui-tests && node ui-smoke.js
```
