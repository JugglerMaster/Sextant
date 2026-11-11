# Contributing

## Development setup

```bash
git clone git@github.com:JugglerMaster/Sextant.git
cd Sextant
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-dev.lock
.venv/bin/pip install --no-deps -e .
```

Run the server with a temporary data directory:

```bash
export SEXTANT_DATA_DIR="$PWD/data"
export SEXTANT_ADMIN_PASSWORD='a long local password'
.venv/bin/sextant serve --host 127.0.0.1 --port 8080
```

## Checks

```bash
.venv/bin/python -m pytest -p no:warnings
.venv/bin/python -m pyflakes sextant_control sextant_agent tests
.venv/bin/python -m compileall -q sextant_control sextant_agent
node --check sextant_control/static/app.js

cd ui-tests
npm ci
npm test
npx playwright install chromium
npm run test:visual
```

All changes should keep the Python suite, jsdom smoke test, browser tests,
`pyflakes`, `compileall`, and `node --check` passing.

## Style

- Keep the existing FastAPI, SQLite, and vanilla JavaScript structure.
- Prefer small, focused changes.
- Add tests for new API behavior and user-visible flows.
- Update the relevant file in `docs/` when behavior or configuration changes.
- Do not commit credentials, private keys, generated databases, browser
  artifacts, virtual environments, or dependency directories.
- Do not use inline `style` attributes or DOM inline styles; the CSP is
  `style-src 'self'`.

## Security-sensitive changes

Changes to authentication, session handling, command execution, agent policy,
remote install, package updates, or the package store need extra care. Explain
the threat model and add regression tests.

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
