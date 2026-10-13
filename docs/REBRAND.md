# Rebrand: Asymptote → Clio

The project was renamed from **Asymptote** to **Clio** to align with the DOE
Prometheus ecosystem. Clio — Muse of history, daughter of Mnemosyne — keeps the
record; Prometheus brings the fire. Tagline: *The record remembers.*

## What changed

| Surface | Before | After |
|---|---|---|
| Product / app name, FastAPI title, log lines | Asymptote | Clio |
| UI wordmark / `<title>` / PWA manifest | Asymptote | Clio |
| MCP `serverInfo.name` | `Asymptote` | `Clio` |
| `MCP_SERVER_ID` default | `asymptote` | `clio` |
| Basic-auth and Bearer realm | `Asymptote` | `Clio` |
| `RESEND_FROM` default sender name | Asymptote | Clio |
| Test env-file override | `ASYMPTOTE_ENV_FILE` | `CLIO_ENV_FILE` (old name still honoured) |
| Docker image default | `asymptote:local` | `clio:local` |
| Compose service / `container_name` | `asymptote` | `clio` |
| Image env vars | `ASYMPTOTE_IMAGE`, `ASYMPTOTE_PULL_POLICY` | `CLIO_IMAGE`, `CLIO_PULL_POLICY` |
| Image label / version env | `ASYMPTOTE_VERSION` | `CLIO_VERSION` |
| Packaged image tarball | `asymptote-<tag>.tar.gz` | `clio-<tag>.tar.gz` |
| Container registry path | `ghcr.io/jordanboyce/asymptote` | `ghcr.io/jordanboyce/clio` (see below) |
| Browser storage keys | `asymptote_*` | `clio_*` (migrated automatically) |

## What did **not** change

- **Port** — still `8473`.
- **Data directory** — `./data` layout is untouched; existing indexes, uploads,
  and settings carry over with no migration.
- **Named volumes** — a deployment that used the `asymptote-data` named volume
  keeps it. The volume key is deliberately not renamed; renaming it would
  orphan the corpus.
- **API paths** — no brand in URLs; every endpoint is unchanged.
- **Python module names** — already generic (`services/`, `api/`, …).
- **Git history** — the `finn-archive` tag and all prior history are preserved.
- **Deployed hostnames** — an instance served at `asymptote.<domain>` keeps
  that hostname until you choose to move it; nothing in the app depends on it.

## Compatibility shims

- **Browser state.** On first load the frontend copies every `asymptote_*`
  localStorage / sessionStorage key to its `clio_*` name (never overwriting a
  value already there), then removes the old key. Chat history, current
  collection, search settings, provider selection and per-surface overrides
  survive the upgrade. See `frontend/src/utils/storageMigration.js`.
- **`ASYMPTOTE_ENV_FILE`** is still read when `CLIO_ENV_FILE` is unset.

## Still tied to the GitHub repository name

The image is published to `ghcr.io/${{ github.repository }}`, and the image's
`org.opencontainers.image.source` label points at the repository URL. Both
read `…/clio` in the docs and Dockerfile, which becomes true once the GitHub
repository is renamed from `asymptote` to `clio` (GitHub redirects the old
repository URL; the registry path does not redirect). Until then, published
images live under `ghcr.io/jordanboyce/asymptote`.

## Migrating an existing deployment

1. Pull the new image: `docker pull ghcr.io/jordanboyce/clio:latest`
   (or `docker load < clio-<tag>.tar.gz` for air-gapped installs).
2. If your `.env` sets `MCP_SERVER_ID=asymptote`, update it to `clio` (or delete
   the line to take the new default). Existing values keep working — this is
   cosmetic.
3. If you reference `ASYMPTOTE_IMAGE` / `ASYMPTOTE_PULL_POLICY` in your
   environment, rename them to `CLIO_IMAGE` / `CLIO_PULL_POLICY`.
4. `docker compose up -d` — the service is now named `clio`, so use
   `docker compose logs clio` (or `docker compose logs -f` for all services).
   Compose recreates the container under the new name; the data mount is the
   same.
5. Reconnect MCP clients if you pinned the old server name; the endpoint URL
   (`/mcp`) is unchanged.
6. If a reverse proxy or tunnel of your own addresses the container by its
   old service name (`asymptote`), point it at `clio` (port `8473` is
   unchanged).

Your `data/` directory carries over untouched — no reindexing required.
