# Engineering conventions

The rules the code follows, so that a change can be checked against them.
Each one is already applied across the codebase; the last section lists what
was found and deliberately left for later.

## HTTP

- **Status codes say what happened.** 401 means not authenticated (with a
  `WWW-Authenticate` challenge); 403 means authenticated but not allowed; 404
  means the resource is missing or the caller may not know it exists; 409
  means the request conflicts with current state (cancelling a finished job,
  publishing a team collection, suspending yourself); 410 means a share link
  that was revoked or expired; 422 means well-formed input that fails
  validation (including a configuration save that the config manager
  rejects); 413 and 429 carry `Retry-After` where it applies. A client
  mistake is never reported as a 500.
- **Creation returns 201**, including MCP tokens. Long-running work returns
  202 while a job was started and 200 when there was nothing to do.
- **`except Exception` never swallows an `HTTPException`.** Re-raise it
  first, then handle the rest.
- **Authorisation runs before any check that could reveal server state.**
  Path existence checks come after the write and acceptable-use checks.
- **GET does not write.** The remaining exceptions are audit entries on
  exports and a one-time user upsert on first sight, both idempotent.
- **PUT and DELETE are safe to repeat.** A second DELETE answering 404 is
  acceptable (the state is the same); a DELETE that returns a useful body
  keeps 200.

## Input

- **Request bodies are Pydantic models with constraints**: `Literal` for
  fixed sets (chat roles and scopes, artifact types, share permissions,
  research job kinds and depths), `max_length` on free text and on lists,
  `ge`/`le` on numbers. FastAPI then returns 422 on its own and hand-written
  checks are not needed.
- **Every SQL value is a parameter.** Identifiers come only from constants or
  from the table's own schema (aggregate columns are checked against it).
- **Model-written SQL is sandboxed**: a read-only connection, one statement,
  an authorizer that permits reads of the structured-data tables only (not
  `documents`, `chunks` or `sqlite_master`), and a five-second progress-handler
  deadline.
- **Secrets never reach the log.** Share tokens are logged as a six-character
  prefix.
- **Indexes cover the hot predicates**: `documents(content_hash)` for upload
  de-duplication, `upload_jobs(status, collection_id)` for the pollers. An
  index that duplicates a `UNIQUE` constraint is dropped.

## Code

- **Named constants, not repeated literals**: `MCP_MAX_TOP_K`,
  `MCP_MAX_PER_COLLECTION_K`, `DEFAULT_SEMANTIC_WEIGHT`,
  `DEFAULT_OLLAMA_MODEL`, `_AUDIT_COLLECTION_LIMIT`,
  `MAX_LOCAL_FILES_PER_REQUEST`. A clamp that appears twice becomes a
  function.
- **Guard clauses over nesting.** Deep loops are extracted into a function
  that returns early.
- **Shared implementation lives in the base class.** `DatabaseBackend` holds
  the methods that are identical for SQLite and Postgres; the parity test
  checks that the two backends implement the same abstract surface.
- **Dead code is deleted**, not kept for later. A function with no reference
  anywhere in the repository, tests included, goes.
- **Duplicated construction is extracted**: one `_provider_from_headers`
  serves both the key-validation and model-listing endpoints.

## Frontend

- **List keys are identities.** Chat messages carry ids; search results key
  on `chunk_id`; history on its cache key; selected paths on the path. An
  index key is used only for a static, position-based list (citation
  numbers, key-combination parts).
- **A stream writes to the message it started**, not to whatever is active.
  `addStreamingMessage` returns the binding and every later write takes it;
  the fetch is aborted on unmount.
- **The newest request wins.** Debounced lookups carry a sequence number and
  a late reply is dropped (see `ModelPicker.vue`, `SupersedeDialog.vue`,
  `ResearchTab.vue`, `SearchTab.vue`).
- **An element that says `role="dialog"` behaves like one.** Native `<dialog>`
  with `showModal()` through `useModal()` where possible; otherwise
  `useOverlayFocus()` supplies focus-in, Tab containment, Escape and focus
  restore.
- **Every field has a name**: a visible `<label>` or an `aria-label`, never a
  placeholder alone.
- **Strict equality and block scoping** are enforced by ESLint (`eqeqeq`
  with the `== null` idiom allowed, `no-var`).
- Props are read-only, listeners are removed on unmount, static layout is
  utility classes rather than inline styles.

## Left for later

Found in the same review and not applied, because each is a larger change
than a convention fix. In rough order of value:

1. **Local-path ingestion allowlist.** `index-local-async`, `upload-repo` and
   `sync-folder` accept any server path from a writer. A `LOCAL_SOURCE_ROOTS`
   setting, or admin-only for local paths, would close it.
2. **Bundle import hardening.** An imported `metadata.db` keeps its triggers,
   views and policy verdicts; drop the former and re-scan the latter.
3. **Link fetcher DNS pinning.** The host is validated, then resolved again by
   the HTTP client; pin the connection to the validated address.
4. **One error envelope** (`{"detail", "code", ...}`) and no raw exception
   text in 500 bodies; a global request-body size limit in middleware.
5. **API key on `POST /api/agent/config` moves from the query string to the
   body.**
6. **Refactors**: `execute_tool_calls` to a dispatch table, `create_provider`
   to a registry like `embedding_providers.py`, `_run_turn` and
   `DocumentIndexer.search` split along their stage markers, then
   `UploadService`, `mcp_server.py`, `MetadataStore` and `CodeExtractor`
   split by the sections they already have.
7. **Judgement calls**: PATCH for the partial updates that use PUT; PUT for
   the settings endpoints that use POST; 503 or 429 instead of 409 for a full
   queue; 401 with a challenge from `require_admin` when there is no identity
   at all; 204 for the delete acknowledgements the frontend does not read.
