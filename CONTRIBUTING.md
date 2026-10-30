# Contributing to Clio

Clio is open source under the [MIT License](LICENSE) and is maintained by one
person. Contributions take one form: **issues**. Pull requests are not
accepted. A pull request opened from a fork is closed automatically by a
workflow, with a comment pointing back here, and its branch is not reviewed.

That is not a judgement on the work. Reviewing, testing and then owning
outside code for the life of the project is more than a single maintainer can
take on, and a precise issue is the fastest way for a change to land: it can be
fixed in the maintainer's own branch, with the tests and docs that go with it,
in the same sitting.

## Open an issue for

- **A bug.** Something does not do what the documentation says, or crashes.
- **A feature or a change.** Say what you are trying to do and why the current
  behaviour gets in the way. A proposed design is welcome; a patch pasted into
  the issue is fine too, and may be used.
- **A documentation mistake.** Wrong, missing or stale text anywhere in the
  README or `docs/`.
- **A question about running it.** Deployment, identity providers, air-gapped
  installs, MCP clients. Check the [README](README.md) and
  [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) first; if the answer is not there,
  that is a documentation gap worth reporting.

Use the templates at
[github.com/jordanboyce/clio/issues/new/choose](https://github.com/jordanboyce/clio/issues/new/choose).
Search existing issues before opening one.

## What makes a bug report easy to act on

- The version: the About dialog, `GET /health`, or the image tag.
- How you run it: `docker compose`, the prebuilt image, `python main.py`, a
  PaaS, or the offline bundle, and the operating system.
- The exact steps, what you expected, and what happened instead.
- The relevant log lines (`docker compose logs clio`). Remove passwords,
  tokens and anything from your documents before pasting.
- For a search or answer problem: the query and, if you can share it, a small
  document that reproduces it.

## Security problems

Do not open a public issue. Follow [SECURITY.md](SECURITY.md).

## Forks

The MIT license lets you fork Clio and change it however you like, including
for commercial use, as long as the copyright notice and the license text stay
with the code. You do not need permission, and a fork that goes its own way is
a perfectly good outcome.
