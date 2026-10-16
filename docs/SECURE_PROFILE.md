# Secure profile and deployment check

For environments that have to be able to *show* their posture, not just claim
it. One switch hardens the defaults, one command proves the result.

```bash
CLIO_PROFILE=secure
```

## What the profile does

**Defaults it sets** (any of them can still be overridden explicitly):

| Setting | Secure default | Why |
|---|---|---|
| `OFFLINE_MODE` | on | No cloud AI providers, hosted embeddings, web fetching or model downloads |
| `MCP_AUDIT_TOOL_CALLS` | on | Every agent read is attributable, not just writes |
| `LINK_INDEXING_ENABLED` | off | The server does not fetch arbitrary web pages |

**Things it refuses to start with.** The standard profile logs a warning for
these; the secure profile exits with a message that names each problem:

- listening beyond loopback with no `AUTH_PASSWORD` and no identity provider;
- `CORS_ALLOW_ORIGINS=*` with no authentication requirement;
- `OFFLINE_MODE` explicitly turned off;
- a hosted `EMBEDDING_PROVIDER` or `AI_PROVIDER` (only `local`, `ollama` and
  `openai_compatible` pointing at your own endpoint are accepted).

A hardened deployment therefore cannot quietly come up in a weaker shape than
it was declared to have.

## Proving it: `scripts/preflight.py`

Run it with the same environment the server will use, once on the build
machine before the image is carried across, and again on arrival:

```bash
python scripts/preflight.py                 # readable report
python scripts/preflight.py --probe         # also contact the model endpoint
python scripts/preflight.py --json > evidence.json
python scripts/preflight.py --strict        # warnings fail the run too

docker run --rm --env-file .env clio:local python scripts/preflight.py --probe
```

Exit status is `0` when ready and `1` when a check failed (or warned, with
`--strict`), so it slots into CI and deployment pipelines. It starts no server
and changes nothing.

The report covers:

- **Security posture**: offline mode, access control, cross-origin access,
  transport encryption, agent-read audit.
- **Models and capabilities**: the embedding model is in the local cache (so
  nothing downloads at run time), the reranker, OCR, audio transcription, git,
  and the chat model endpoint (reachability with `--probe`).
- **Storage**: the data directory is writable with room to grow, and SQLite
  has the FTS5 keyword engine.
- **Where data can go**: every outbound path the *current configuration*
  allows (embeddings, chat model, web links, git remotes, change-alert
  webhook, image OCR, model downloads), each marked `none`, `internal`,
  `blocked` or `open`. It is generated from the live settings, so it can be
  attached to an accreditation package without anyone retyping it.

The same report is available to administrators at
`GET /api/system/readiness` and from **About → Check this deployment** in the
app. `GET /health` reports the active `profile`.

## Typical secure install

1. On a connected machine: `docker compose build --build-arg OFFLINE_BUNDLE=1`,
   then `scripts/package_image.sh --offline-bundle` ([AIRGAP.md](AIRGAP.md)).
2. Configure at the target: `CLIO_PROFILE=secure`, `AUTH_PASSWORD`,
   `AI_PROVIDER=openai_compatible` with `AI_BASE_URL` / `AI_MODEL` for your
   private model server ([ONPREM.md](ONPREM.md)), optionally `GIT_ALLOWED_HOSTS`
   and `ALERT_WEBHOOK_URL` for your internal git server and chat.
3. `docker run --rm --env-file .env clio:local python scripts/preflight.py --probe`
   and keep the output with the deployment record.
4. Start it. If it refuses, the message says which line to fix.

The profile does not provide per-user isolation or encryption at rest: put
the data directory on an encrypted volume, and use `PRIVATE_COLLECTIONS` with
your own identity provider when people must not see each other's collections
([DEPLOYMENT.md](DEPLOYMENT.md), [IDENTITY.md](IDENTITY.md)).
