# Security

## Reporting a vulnerability

Please do not report security problems in a public issue.

Use GitHub's private vulnerability reporting:
[github.com/jordanboyce/clio/security/advisories/new](https://github.com/jordanboyce/clio/security/advisories/new).
The report is visible only to the maintainer until a fix is published.

Include what you can of the following:

- The version (About dialog, `GET /health`, or the image tag) and how it is
  deployed (`docker compose`, prebuilt image, Python, PaaS, offline bundle).
- The configuration that matters: `AUTH_PASSWORD` set or not, bind address,
  `IDENTITY_PROVIDER`, `PRIVATE_COLLECTIONS`, whether MCP tokens are in use.
- Steps to reproduce, and what an attacker gains.

Reports are acknowledged as soon as possible, normally within a week, and the
fix lands on `master` and in a new image tag. Credit is given in the release
note unless you prefer otherwise.

## Supported versions

Fixes go to the current `master` branch and the `latest` image tag only. There
are no long-term support branches.

## Deploying safely

Clio has no accounts of its own. The default install binds to loopback with no
password; set `AUTH_PASSWORD` before anyone else can reach it, put TLS in
front, and read the security section of the README and these guides:

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) covers shared deployments, the
  reverse-proxy setup, content governance and the hardened container.
- [docs/SECURE_PROFILE.md](docs/SECURE_PROFILE.md) covers `CLIO_PROFILE=secure`
  and the preflight check that reports every place data can go.
- [docs/IDENTITY.md](docs/IDENTITY.md) covers single sign-on and per-person
  collections.
- [docs/SECURITY_AUTHORED_TEXT.md](docs/SECURITY_AUTHORED_TEXT.md) covers how
  text written by people is kept apart from instructions to a model.

## Secrets in this repository

There are none. The example environment files contain placeholders only, and
the full git history was audited for credentials in October 2026 with no
findings. Never commit a real `.env`; it is ignored by `.gitignore`.
