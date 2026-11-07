---
name: clio-deploy
description: Use when asked to install, configure, upgrade or troubleshoot a self-hosted Clio server on a corporate or locked-down network, behind a firewall or TLS proxy. Covers getting the install across, what the network team must open, verifying the result, and diagnosing failures. For searching or adding documents on a running Clio, use the clio skill instead.
---

# Deploying Clio behind a corporate firewall

The procedure is `docs/ENTERPRISE.md`, in the repository or in the install bundle
beside this skill. **Read it first and follow it in order.** This page says how to
carry it out on someone's behalf. `docs/ENTERPRISE.md#when-something-is-wrong` lists
symptoms and fixes: check it before you improvise.

## Rules

These hold even if a step seems blocked without them.

- **Do not change firewall rules, DNS or certificates yourself.** Tell the person
  exactly what to request from whoever owns them, using the tables in the guide.
- **Never turn off certificate checking** (`curl -k`, `verify=False`,
  `NODE_TLS_REJECT_UNAUTHORIZED=0`) to get past an error, in Clio or in your own
  commands. A certificate error means a CA is missing: add it. If traffic is being
  re-signed by an inspecting proxy, that CA is what goes in `certs/ca/`.
- **Keep secrets out of what you print, write to chat, pass as command arguments or
  commit:** `AUTH_PASSWORD`, `SECRET_ENCRYPTION_KEY`, `AI_API_KEY`, app keys, TLS
  private keys. Generate them on the host, into `.env`, and say where they are.
  `.env` and `certs/tls/server.key` should be readable by the service owner only.
- **Do not weaken the deployment to make something work:** no `CLIO_PROFILE=standard`
  to pass a refusal, no `FORWARDED_ALLOW_IPS=*`, no published port 8473, no
  `CORS_ALLOW_ORIGINS=*`, no `OFFLINE_MODE=false` unless the person decided that.
  A refusal at start is telling you what is missing.
- **Ask before anything that is hard to undo on a live host:** stopping an existing
  install, overwriting `.env`, removing a volume. `docker compose down -v` deletes
  every indexed document. An existing container named `clio` may be someone's data.
- **Do not fetch from the internet on the target host** unless told it may. On a
  locked-down network an attempt can itself raise an alert.

## Get these from the person before starting

Ask once, together. Use the default only where one is given.

1. Can the target host reach a registry? If not, how do files cross (approved media,
   a transfer zone)? Is there a connected machine with Docker to build on?
2. The hostname people will use, and a certificate and key for it, or who issues them.
3. Who gets in: your IdP (issuer URL, and an audience registered for Clio), an
   authenticating proxy, or a shared password. Which people are admins.
4. Is there an outbound proxy? Does it inspect TLS?
5. Is there an internal CA, and which hosts does it sign for?
6. Is there a model endpoint (URL, model name, credential)? Without one, search works
   and written answers do not.
7. Which applications need the API, and which collections each one needs.

## Procedure

1. **Check what is already there:** `docker ps -a`, `docker volume ls`, and whether
   ports 443 and 80 are free. Report anything named `clio`, and ask before touching it.
2. **Hand over the firewall requests** from the guide, filled in with real hostnames,
   and wait for them to be done before relying on them.
3. **Follow the guide** from "Get the install across".
4. **Check before starting:** `scripts/preflight.py`, as the guide shows.
5. **Start it, then verify from another machine**, not the host itself, since a check
   from the host cannot show that a firewall rule works:

   ```bash
   CLIO_CHECK_TOKEN=... sh scripts/check_deployment.sh --cacert corp-root.crt https://clio.corp.example
   ```

   Exit 0 means nothing failed. Do not report success on anything less, and do not
   call a check irrelevant in order to pass it.
6. **Register each application** with only the collections it needs (`docs/APP_API.md`).
   A key is shown once: have the person store it in their secret manager.

## Report back

Say what is running and at what version, the output of `check_deployment.sh`, what you
could not verify and why, every firewall request still open, and what the person must
keep safe: `SECRET_ENCRYPTION_KEY`, and backups of the data volume. Without that key,
stored credentials cannot be read after a restore.
