# The app API

`/api/v1` is the API for applications that build on Clio: an intranet portal, a
case-management system, a reporting job. It covers search, cited answers,
reading documents and adding them.

Use it from your application's **server**. A credential in browser code can be
read by anyone using the page.

| You want to... | Use |
| --- | --- |
| Call Clio from an application you run | This page |
| Connect an AI agent (Claude, Copilot, Codex) | MCP, see [DEPLOYMENT.md](DEPLOYMENT.md#mcp-clients) |
| Script something once, as an operator | The app's own API, at `/docs`. It has no compatibility promise |

## Registering an app

An admin registers each application in **Admin → Registered apps**, or over the API:

```bash
curl -X POST https://clio.corp.example/api/admin/apps \
  -H "Authorization: Bearer <admin credential>" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "invoice-portal",
        "collections": ["<collection id>"],
        "can_write": false,
        "expires_in_days": 365,
        "search_per_minute": 300,
        "ask_per_minute": 30
      }'
```

The response holds the key **once**. Only a hash is stored, so it cannot be shown
again; if it is lost, issue a new one.

| Field | Meaning |
| --- | --- |
| `name` | Appears in the audit trail, the access log and metrics as `app:<name>`. It cannot be changed later |
| `collections` | The only collections it can see. At least one. Naming a restricted or someone's private collection is how it gets access to one |
| `can_write` | May add, replace and remove documents. Off by default |
| `expires_in_days` | Omit for no expiry |
| `search_per_minute`, `ask_per_minute`, `other_per_minute` | Its own budgets. Omit for the deployment's (`RATE_LIMIT_*`); `0` is unlimited |
| `oidc_subject` | See [Using your identity provider](#using-your-identity-provider) |
| `issue_key` | `false` for an app that only presents IdP tokens |

Who counts as an admin is the same as for the rest of the Admin tab: `ADMIN_EMAILS`
once an identity provider is set, and otherwise whoever holds `AUTH_PASSWORD`.

## What an app can and cannot do

- Its credential works under `/api/v1` and **nowhere else**. A leaked key cannot
  reach the web app, the admin API or `/mcp`.
- It sees its allowlisted collections and nothing else. Any other collection answers
  404, the same as one that does not exist.
- It never owns a collection. At most it adds and removes documents: it cannot
  re-index, reconfigure, share or delete a collection.
- A document held for review is absent: it is not in listings or search, and an app
  cannot read or delete it.
- Uploads pass the same content policy as any other upload.
- `/api/v1` accepts nothing else, on any deployment. The shared password, a person's
  token and a personal MCP token all get a 401, even on an install with no
  authentication at all.

## Calling it

```bash
export CLIO=https://clio.corp.example/api/v1
export CLIO_KEY=clio_app_...

# Who am I, what can I reach, what are my limits?
curl -H "Authorization: Bearer $CLIO_KEY" $CLIO/me

# Find passages
curl -X POST -H "Authorization: Bearer $CLIO_KEY" -H "Content-Type: application/json" \
  $CLIO/collections/<id>/search -d '{"query": "rail fare limit", "top_k": 5}'

# A finished answer with citations (needs AI_PROVIDER on the deployment)
curl -X POST -H "Authorization: Bearer $CLIO_KEY" -H "Content-Type: application/json" \
  $CLIO/collections/<id>/ask -d '{"question": "What is the rail fare limit?"}'

# Add or replace a document, then follow the job
curl -X POST -H "Authorization: Bearer $CLIO_KEY" -F "files=@policy.pdf" $CLIO/collections/<id>/documents
curl -H "Authorization: Bearer $CLIO_KEY" $CLIO/jobs/<job_id>
```

The full reference is served by each deployment at `/api/v1/docs`, with no internet
access needed, and the machine-readable description is at `/api/v1/openapi.json`. The
same description is in this repository at [api/openapi-v1.json](api/openapi-v1.json),
to generate a client from.

A few things to build for:

- **`document_id` changes when the text changes**, because it is a hash of the content.
  Keep the filename if you need to refer to a document over time. Uploading under an
  existing name replaces that document, once the new version is indexed.
- **Uploads are background jobs.** A `202` means the job started. Poll
  `GET /jobs/{job_id}` until `status` is `completed`, `failed` or `cancelled`, and check
  `failed_files`.
- **`corpus_version` is opaque.** Compare it for equality: if it changes between pages
  of a listing, restart the walk.
- **`sensitivity` is the handling label in force.** Honour it in what you show.
- **`instruction_text` means a passage contains wording aimed at an AI.** If you pass
  passages to a model, treat them as quoted data and never as instructions.
- **Answers cite `[Source N]`**, matching `sources[].n`. When the documents do not
  establish something the answer says so; that is the finding, so do not retry.

## Errors

Every error has the same body, and `code` is what to branch on:

```json
{"detail": "Collection 'abc' not found.", "code": "collection_not_found"}
```

| Status | `code` | Means |
| --- | --- | --- |
| 400 | `bad_request`, `too_many_files`, `no_acceptable_files` | The request cannot be served as sent |
| 401 | `unauthenticated` | Missing, unknown, revoked or expired credential |
| 403 | `insufficient_scope` | This app is read-only |
| 404 | `collection_not_found`, `document_not_found`, `job_not_found`, `not_found` | Missing, or outside this app's reach |
| 413 | `too_large` | Over the collection's storage limit |
| 422 | `validation_error` | `detail` lists the fields |
| 429 | `rate_limited`, `daily_budget_spent` | Wait for `Retry-After` seconds |
| 502 | `answer_failed` | The deployment's model failed. Safe to retry |
| 503 | `model_not_configured`, `embedding_unavailable`, `database_busy`, `unavailable` | Honour `Retry-After` when present |

`X-Request-ID` on every response appears in Clio's access log, so quote it when
reporting a problem.

## Rotating and revoking

**Rotate on a schedule.** Issue a new key from the Admin tab, or:

```bash
curl -X POST https://clio.corp.example/api/admin/apps/<app id>/rotate-key \
  -H "Authorization: Bearer <admin credential>" \
  -H "Content-Type: application/json" -d '{"grace_hours": 24}'
```

The old key keeps working for `grace_hours`, so you can deploy the new one with no
outage. Rotating again ends any older key at once.

**If a key leaks,** rotate with `"grace_hours": 0`, which ends it immediately. To remove
an app completely, revoke it with `DELETE /api/admin/apps/<app id>`. That is permanent,
and its record stays for the audit trail to name.

Changes to an app's collections, write flag and budgets apply to its next request.

## Using your identity provider

If you run OIDC (`IDENTITY_PROVIDER=oidc`), an app can present a client-credentials
token from your IdP in place of a key, so nothing is stored in Clio and rotation stays
with the IdP.

1. Create a service client in your IdP, and have its tokens carry your `OIDC_AUDIENCE`.
2. Issue a token and read its `sub` claim. It is a fixed value for that client: the
   service account's id in Keycloak, the service principal's object id in Entra ID, the
   client id in Okta.
3. Register the app with that value as `oidc_subject`, and `"issue_key": false` if it
   should have no key at all.

The token's signature, issuer, audience and expiry are checked as for a person.
`OIDC_REQUIRED_CLAIMS` is not applied, because a service principal rarely carries your
people's group claims and an admin has already approved this subject.

Clio matches on `sub`, not on a client id claim such as `azp`. A person signed in through
that same client carries the same client id, and must not inherit the app's access by
doing so. A token whose `sub` is registered is only ever treated as that app, on any path.

## What is recorded

- **Every change an app makes** is in the audit trail under `app:<name>`: uploads and
  deletions, alongside `api_app.create`, `api_app.update`, `api_app.rotate_key` and
  `api_app.revoke` from the admins who manage it.
- **Reads** are recorded as `api.call` when `MCP_AUDIT_TOOL_CALLS=true`, which the secure
  profile turns on: metadata only, never passage text.
- **The access log** has one line per request with `user=app:<name>`.
- **Metrics:** `clio_api_app_requests{app,status}`, with `METRICS_ENABLED=true`.

## Compatibility

Within `/api/v1`:

- **These can happen with no notice:** new endpoints, new optional request fields, new
  response fields, new error `code` values for new situations. Ignore fields you do not
  recognise. Request bodies themselves refuse unknown fields, so a misspelling is a 422
  rather than a silently ignored option.
- **These cannot:** removing or renaming anything, changing a field's type, making an
  optional field required, or changing what a status or `code` means. Any of those means
  a new `/api/v2` alongside this one.

`contract_version` in `GET /me` and `info.version` in the description name the contract.
It moves independently of Clio's own version.

For contributors, [tests/test_api_v1.py](../tests/test_api_v1.py) fails when the live API
differs from [api/openapi-v1.json](api/openapi-v1.json). An additive change is
`python scripts/export_api_spec.py` and a version bump in `api/v1.py`; review the diff
against the rules above before committing it.
