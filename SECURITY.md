# Security policy

Sextant is a self-hosted remote operations console. An operator account can run
commands as the agent's OS user, which is root on Linux and SYSTEM on Windows by
default. Treat the console as a privileged administrative system.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.3.x | Yes |
| Older | No |

## Reporting a vulnerability

Do **not** open a public issue for a security problem.

Use GitHub's private vulnerability reporting for this repository:

1. Open the repository's **Security** tab.
2. Choose **Advisories**.
3. Select **Report a vulnerability**.

Include the affected version, reproduction steps, impact, and any suggested
mitigation. Do not include live credentials, private keys, or production data.

## Deployment expectations

- Run Sextant behind HTTPS and a VPN or trusted admin network.
- Do not expose the server directly to the public internet.
- Keep `SEXTANT_*` environment files mode 0600.
- Review the agent-local `allow_shell`, `allow_power`, `allow_service`, and
  `allow_packages` flags.
- Treat package-store artifacts as privileged supply-chain inputs until Tier 1
  signing is implemented.

See [docs/SECURITY.md](docs/SECURITY.md) for the full security model and known
limitations.
