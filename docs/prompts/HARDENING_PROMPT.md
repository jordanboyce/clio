# Work order: make Clio dependable enough to demo and to run in an enterprise

This file is a prompt. Hand it to a coding agent working in this repository, whole or one workstream at a time. It was written on 2026-11-18 from a pass over the code and a live walk through the app at commit `c2439d7`.

## Your job

Clio is a self-hosted knowledge base: people add sources to a collection, ask questions answered with citations, and hand the same knowledge to AI agents over MCP. It is used at sites that block model hubs and AI providers (a national lab is the reference case), and it is shown to prospective users in live demos.

Two things are still shaky, and they are what this work order is about:

1. **Doing research in the Library tab.** The checking parts (citations, quote verification, the draft checker) are solid. The paper records underneath them are often wrong and are shown with full confidence, and the flow around them has rough edges.
2. **Getting and using models in a given environment.** What a person sees when a model cannot be downloaded, when Ollama is the only option, or when the site runs its own endpoint is incomplete or misleading in specific, reproducible ways.

Around those two sit a set of shell, feedback and operational gaps that would embarrass a demo or fail an enterprise security review.

Fix the items below. Each has an id, the evidence for it, the files to start in, and a "done when" test. Do not add features that are not listed. Where an item overlaps `docs/ROADMAP.md`, the roadmap id is given and the roadmap's design stands; this file adds what was found since and sets the order.

## Evidence labels

Every item says how it is known. Treat them differently.

- **Seen live**: observed on 2026-11-18 in the running app (backend from this checkout on a throwaway data directory, Vite frontend, Chromium at 1280x720).
- **Read**: read in the code by the author of this file and checked a second time.
- **Reported**: found by a single read-only audit pass. Reproduce it before you change anything; if it does not reproduce, say so in your report and skip it.

## Rules for all of this work

- Read `docs/ROADMAP.md` and `docs/ENGINEERING.md` first. The roadmap's three rules apply: every change leaves `master` deployable with the simple install as the default; everything must still work with hubs, catalogues and providers blocked; a changed MCP tool name or shape keeps the old form for one release.
- Every fix ships with a regression test. Backend: `venv/Scripts/python.exe -m pytest` (the system Python lacks dependencies). Frontend: `cd frontend && npm test`.
- Every source file is CRLF. Keep it that way or the diff becomes the whole file.
- Commit straight to `master` in small commits, one item or one tight group per commit, with the item id in the message. Do not push unless asked.
- User-facing copy: plain words, sentence case, no environment-variable names or internal terms in text an end user reads. Say what happened, what still works, and what to do next.
- Anything a signed-in non-admin can read must not contain filesystem paths, internal addresses or keys.
- If an item turns out to need a product decision, stop on that item, write the options in your report, and carry on with the next one.

### How to check your work for real

Reading the code has been wrong about this app before; run it.

- **Local run.** Start the backend from an empty working directory so it cannot read or rewrite the checkout's `.env` (see E8): `cd <scratch>/run && PORT=8474 DATA_DIR=<scratch>/data PYTHONPATH=<repo> <repo>/venv/Scripts/python.exe <repo>/main.py`. Copy `data/models/fastembed` into `<scratch>/data/models/` first when you want the model present. Frontend: `cd frontend && VITE_API_URL=http://localhost:8474 npm run dev`.
- **Library fixtures.** `tests/fixtures/research_bench/smoke/*.pdf` are eight small invented papers built to trip the record reader. Upload them and press Analyze to reproduce every L item below. `scripts/research_bench.py` gives the accuracy numbers.
- **Blocked model hub.** Empty `models` directory plus `HF_ENDPOINT=http://10.255.255.1:81` (a firewall that drops packets). Also test `--network none` in the published image, and `OFFLINE_MODE=1` on top of that. Do not set `HF_HOME` in a probe.
- **Demo resolution.** Check every UI change at 1280x720 with the Sources panel open, and at 375 wide.

---

## Workstream A: a Library you can show to a researcher

The demo story is: add PDFs, see what they are, screen them against a question, read reviews, cite. Today the second step undermines the rest.

**A1. Do not present a wrong record as a fact. (Roadmap R3; do first.)** *Seen live.*
After Analyze on the eight fixtures, three of seven records were wrong and shown exactly like the right ones: a title that is the first 140 characters of the abstract ("We count nesting gulls on city rooftops from fixed cameras…"); a title that is two body sentences ("The survey covered forty-two stations… Each station logged…"); the venue "Imaginary Urban Ecology" read as the author, cited as "Ecology, I. U. (2022)"; and "Office" as an author for a report.
Give each field a confidence, as R3 describes. A low-confidence title shows the file name as the display title with a "needs checking" mark; a low-confidence author list shows "Authors need checking", not a name. Add a "Needs checking" filter and count to the Papers view. A record corrected by hand is never overwritten.
Start in `services/research/extract.py`, `services/research/library.py`, `frontend/src/components/ResearchTab.vue`, `ResearchPaperPanel.vue`.
*Done when:* on the smoke fixtures no wrong title or author is displayed as if certain, and the benchmark reports zero "confidently wrong" fields at whatever threshold you set.

**A2. Refuse implausible values offline. (Roadmap R2.)** *Seen live.*
The same four failures are R2's targets: a title that contains a sentence break or runs past about 150 characters, a title that starts mid-sentence, an "author" that is an organisation word or a journal name, a year taken from the wrong place. Implement R2's rules in the order the roadmap gives, measuring with `scripts/research_bench.py` before and after, and record the new baseline in the roadmap.
*Done when:* offline title accuracy and author F1 on manifest v1 both improve on the recorded baseline (64% and 0.36), with identifiers invented still at 0.

**A3. Bulk rename must not default to renaming files after bad records.** *Seen live.*
"Rename to descriptive names (7)" opens with every source ticked, including `2022 - Ecology - We count nesting gulls on city rooftops from fixed cameras across two breeding seasons.pdf` and `2025 - The survey covered forty-two stations along the northern shore over three winters. Each.pdf`. One click on "Rename 7" applies them. The paper panel offers the same wrong name as its primary button.
Pre-tick only sources whose title and authors are confident (A1) or corrected by hand. List the rest unticked under "Check these first", each with a link that opens the source. Add "Undo all" to the success toast for a bulk rename.
Start in `ResearchTab.vue` (`renameCandidates`, `refreshPreview`), `api/research.py` (rename).
*Done when:* on the smoke fixtures the dialog opens with only the correct records ticked.

**A4. Analyze when a source is indexed. (Roadmap R5.)** *Seen live.*
Seven freshly indexed PDFs all read "Not analyzed" until someone presses "Analyze 7". Analysis needs no model. Run it after indexing and after a reference download, as a low-priority background step that cannot fail the index job. With catalogue lookups blocked it must finish from the text alone without waiting out a timeout per source (see A6).
*Done when:* a PDF uploaded through the browser path shows its record in the Library without a click, with the network blocked and unblocked.

**A5. One job strip for the Library. (Roadmap R8b.)** *Seen live.*
While analysis ran, the only signal was a spinner inside a button whose label counted down ("Analyze 4", "Analyze 2"). There is no "n of m", no Stop, and a reload loses the spinner while the job carries on (`analyzing` and `screening` are component refs in `ResearchTab.vue`). Build the strip R8b describes: one line at the top of the tab for Analyze, Screen and Reviews, with n of m, Stop, the failure count, and re-attachment to running jobs when the tab is opened or reloaded (the job list is already at `GET /api/research/jobs`).
*Done when:* start Analyze, reload the page, and the strip is back with the right count.

**A6. Catalogues that fail honestly and quickly. (Roadmap R6, R7.)** *Read.*
Still open as the roadmap describes: a blocked catalogue waits out 15 seconds per request, "Find open copies" is one synchronous request over every reference, and 429 is not honoured. Do R6 then R7. Add to R6: when catalogues were unreachable during analysis, say so once in the Library ("Catalogues could not be reached, so details come from each source's own text. Try again") instead of only in the per-source provenance.
*Done when:* with `api.openalex.org`, `api.crossref.org`, `export.arxiv.org` and `openlibrary.org` black-holed, analysing 20 sources takes no longer than about twice the offline time, and the Library shows one notice.

**A7. The step strip breaks at demo width.** *Seen live.*
At 1280x720 with the Sources panel and a paper panel both open, the four steps are squeezed into four columns about 60px wide; "Cite & check / Citations and draft checks · no model" wraps to five lines and "Analyzed 7 of 7" splits across lines. Make the strip respond to its container, not the viewport (`.library-steps` in `ResearchTab.vue` switches to four columns at a 48rem viewport): two columns, or a compact one-line form, when the list column is narrow.
*Done when:* a screenshot at 1280x720 with both panels open shows each step on at most two lines.

**A8. Small defects in the paper panel.** *Seen live.*
- The Abstract section prints the same sentence twice: "No abstract, preface or introduction was found." and directly under it "No abstract, preface or introduction was found in the first pages."
- It says "No scholarly registry record could be confirmed" where every other screen says "catalogue". Use "catalogue".
- A journal article with no DOI is cited as "Internal document, collection “Default”, file smoke-title-image.pdf". That wording is for reports and memos. Apply it only when the record's type is not article, book or chapter, or when the user turned it on.
- A reference whose printed arXiv id does not exist still gets an "Open PDF" link. Label it "Link as printed" until a lookup or a download has confirmed it.
- "Screening needs a model (Settings)" and "Reviews need a model. Connect one in Settings first." are text. Make "Connect a model" a button that opens the provider section (see C3).
- "Rename to descriptive names (0)" is disabled with no reason before analysis. Say "Analyze first".

**A9. Give the tab an end product. (Roadmap R11.)** Not started. After A1 to A6, build "Write up" as the roadmap describes. This is the item that makes the Library demo land: a cited summary built only from statements the reviews already verified.

**A10. Durable Library jobs. (Roadmap R8.)** Needs the owner's decision recorded in the roadmap (how a job resumes without a stored key). Do not start until it is made; A5 does not depend on it.

---

## Workstream B: models that behave in every environment

Test matrix for this workstream. Each cell must be run, not reasoned about, and the result recorded in your report:

| Environment | Search model | Answer model |
|---|---|---|
| Full image, open network | baked | hosted key in the browser |
| Slim image, `--network none` | baked | none |
| Full image, hub black-holed (`HF_ENDPOINT=http://10.255.255.1:81`), custom `EMBEDDING_MODEL` not baked | cannot download | local Ollama |
| `:bge` image on a data volume made with MiniLM, `OFFLINE_MODE=1` | one collection's model missing | OpenAI-compatible endpoint on the network |
| Bare metal on Windows, no model cached, hub reachable through a mirror (`HF_ENDPOINT`) | downloads from mirror | none |
| Any image, site embedding server (`EMBEDDING_PROVIDER=openai_compatible`) that is down at start | pending | site endpoint |

For each: first-run screen, the banner, the footer's feature strip, Settings, and `GET /api/system/readiness` must tell the same true story, and search must work by keyword throughout.

**B1. The first-run search-index step hides the reason "On this server" is unavailable.** *Seen live.*
With the hub blocked and no model on disk, the "On this server" card is greyed out and reads "Private. MiniLM L6 — fast, small, a 90 MB download." The explanation ("This server cannot reach …, so the model cannot be downloaded here. Pick Ollama or a hosted provider, pre-seed the model, or set HF_ENDPOINT…") is rendered only when that card is the selected choice, and a disabled card cannot be selected (`EmbeddingSetup.vue`: `v-if="choice === 'local'"` against `disabled: … huggingface_reachable === false`).
Put the reason on the disabled card itself, in plain words, naming the host that could not be reached, with "Check again". Keep the operator detail (pre-seed, mirror) behind "How to fix this" and do not name `HF_ENDPOINT` or a docs path in the first line.
*Done when:* in the black-holed run the card says why it is unavailable without any click.

**B2. The same Ollama model is offered twice, once as "will be pulled".** *Seen live.*
With `nomic-embed-text:latest` installed, the dropdown lists "nomic-embed-text:latest" and "nomic-embed-text · will be pulled". The suggested name is compared to installed names without normalising the `:latest` tag (`ollamaChoices` and `applyLabel` in `EmbeddingSetup.vue`; `model_catalog.suggested`). Choosing the second starts a needless pull.
*Done when:* an installed model never appears as "will be pulled", with a test for `name` against `name:latest`.

**B3. Pulling an Ollama model from the first-run screen races its own readiness probe.** *Read; reproduce first.*
`apply()` posts `/api/embedding/ollama/pull`, which returns at once, then posts `/api/embedding/select` with `warm: true`. At that moment the saved provider is still `local`, so `pull_ollama_model` runs with `tracks = False` (`services/embedding_status.py`): the pull's progress never reaches the status, and the finished pull does not re-run the warm-up. Meanwhile the warm-up probes a model that is not there yet and should land on `error` within ten seconds, which stops the page's polling.
Fix the order on the server: one call that saves the choice, tracks the pull as `downloading` with its byte counts, and warms when the pull ends.
*Done when:* choosing a model Ollama does not have shows a progress bar that ends in "Search index ready" without a second click. Test with a stubbed pull stream.

**B4. `missing` is a silent state on the setup screen.** *Read.*
`refreshStatus` stops polling on `missing`, and the template has branches for `downloading`, `loading`, `error` and `ready` only. In offline mode with no model on disk the button stops spinning and nothing is said. Add the branch, with the same wording as the app banner.

**B5. No way to choose the site's own embedding server at first run.** *Read.*
`/api/embedding/options` builds `hosted` from `CLOUD_EMBEDDING_PROVIDERS`, which excludes `openai_compatible`. A locked-down site whose only option is its own endpoint (vLLM, TEI, a gateway) sees three choices, none of which it can use, and has to find Settings. Add a fourth card, "Our own embedding server", with address, model and optional key; probe one embedding to read the dimension (roadmap P4's last bullet) and show the result before saving.
*Done when:* the sixth row of the matrix can be configured entirely from the first-run screen, and the card is offered in offline mode.

**B6. The footer counts features that are deliberately off as not working.** *Seen live.*
A healthy default install reads "Features 5/12" and "5 of 12 fully working". Reranker switched off, OCR off, PRA off and Trust no one off are choices, not faults; in a demo the number reads as a broken product. Count and colour only what is wrong: "All working" when nothing is `limited` or `unavailable`, otherwise "2 need attention". Keep the per-feature list, grouped as *Needs attention*, *On*, *Switched off*. `FeatureStatus.vue` also has no short name for `pra` ("PRA models" is the only long label in the strip).
*Done when:* a default install with a model shows no warning colour in the footer.

**B7. The footer says nothing useful when the search model failed.** *Read.*
`_semantic()` in `services/feature_status.py` gives one generic line for `error`, `missing` and `unconfigured`, and its fix text ("Use the full or slim image…") is wrong for a bare-metal install, for a mirror that failed, and for a remote provider that is down. Branch on the status and on the provider: say whether it could not download (and from which host), was never configured, or the embedding server did not answer; give the fix that matches. No paths.

**B8. A collection whose own model is missing is invisible until a search fails.** *Read; reproduce in the fourth matrix row.*
`docs/AIRGAP.md` describes it: on a `:bge` image, a collection recorded as MiniLM answers 503 for search by meaning while the footer, driven by the server's configured model, says Semantic search is on. Show it where the person is: a notice on that collection ("This collection was indexed with a model this server does not have, so it is searched by keyword. Copy it to the current model") with a button that starts the clone the docs describe as a curl command (`POST /api/collections/{id}/clone` with `embedding_model`). Mark such collections in the collection list. Remove the need to use curl for both the clone and the in-place re-index, and make the in-place path refuse to leave a collection with one model's vectors under another model's label.
*Done when:* the fourth matrix row can be recovered from the UI alone, and no state exists in which queries are embedded with a different model than the stored vectors without an error.

**B9. Status responses leak a server path.** *Seen live.*
`GET /health` and `GET /api/embedding/status` return `progress.file` as an absolute path, here `C:\Users\<name>\…\models\fastembed\models--Qdrant--all-MiniLM-L6-v2-onnx`. `feature_status.py` states that what any signed-in user reads names no paths. Return the model's name there; keep the path for the admin readiness report.

**B10. Know every host a model download contacts.** *Seen live; needs investigation.*
With `HF_ENDPOINT` black-holed, fastembed logged "Could not download model from HuggingFace: timed out. Falling back to other sources." Find out which other hosts fastembed tries (it has carried a second download source), whether the mirror setting covers them, and whether `OFFLINE_MODE` stops them. The readiness report's outbound-path list and `docs/AIRGAP.md` must name every one, or the fallback must be disabled when a mirror is set.
*Done when:* a packet capture or a deny-all proxy log in the mirror row of the matrix shows only the mirror's host.

**B11. First load during a blocked download shows a half-built app.** *Seen live.*
The first `/api/bootstrap` took 5.5 s while the download attempt was timing out. Until it answered, the page showed a Sources spinner and no model picker, footer or onboarding. Find what in bootstrap waits on the model (it should read status only) and remove the wait; show a skeleton for the shell while bootstrap is pending.
*Done when:* bootstrap answers in under 500 ms in the black-holed run, measured from the first request after start.

**B12. The answer-model picker fails silently.** *Read.*
`ModelPicker.vue` `loadModels` catches every error and leaves an empty list, so a blocked or misconfigured provider shows only "Provider default" with no reason. Show "Could not list models from <provider>: <short reason>. Try again", and still let a model name be typed.

**B13. Finish the roadmap's model items.** P2, P4 and P5 were partly shipped in `a4e88a3` after the roadmap was written. Check each against the code, update the roadmap to what is true, and finish what is left, in particular P5 ("Test connection" runs the job the model was picked for) and P4's warning when chunk size exceeds the model's input limit.

---

## Workstream C: first five minutes and the shell

**C1. Onboarding swallows the deep link.** *Seen live.*
Opening `/?tab=library` in a browser with no provider saved shows the full-screen onboarding; after "Skip for now" the app is on Find, not Library. `?tab=` is read and stripped (`App.vue`, three `replaceState` calls) before onboarding finishes. Keep the requested tab through onboarding. Better, do roadmap U8 (real URLs) now: it also fixes Back leaving the app and makes a demo link shareable.

**C2. Onboarding assumes a single user with their own key.** *Seen live and Read.*
The gate is "no provider in this browser's localStorage" (`checkOnboardingNeeded` in `App.vue`). On a shared deployment every new browser gets a full-screen takeover whose first choices are Anthropic and OpenAI, even where both are blocked and even when the collection already holds sources. Decide from the server instead: skip the takeover when the deployment has its own model or the person is not an admin of a shared deployment; in offline mode do not offer hosted providers at all; when sources already exist, show a dismissible card, not a takeover. The dialog also declares `aria-modal` without trapping focus or handling Escape (*Reported*: `WelcomeOnboarding.vue`); use the same `useOverlayFocus` or `<dialog>` pattern as the rest of the app.

**C3. "Connect a model" opens Settings on whatever section was open last.** *Reported.*
`App.vue:58`, `App.vue:800`, `ChatTab.vue:150` and `AISettingsDrawer.vue:51` switch to Settings without naming a section; only `openSettingsSection()` targets one. Route every "connect a model" and "choose another provider" entry point, including the new ones from A8, to the provider section.

**C4. A quieter default shell. (Roadmap U5.)** *Seen live.*
Confirmed as the roadmap says: nine unlabeled icons in the ribbon, eight more above Sources, and a twelve-item status strip. Do U5. With B6 done the status strip reduces to jobs and problems.

**C5. Example questions on an empty Find are generic.** *Seen live.*
"What are the key requirements?", "Important dates and deadlines", "Definitions of key terms" are shown for any collection, including seven ecology papers. Either draw suggestions from the collection (titles, headings, the collection guide) or remove them when the collection is not the sample one.

**C6. Side panels are not clamped to the window.** *Reported.*
`App.vue:1490` and `:1551` restore panel widths from localStorage (up to 600px each) with no clamp and no NaN guard. Widths saved on a large monitor leave almost no answer column on a projector. Clamp on load and on resize so the centre column keeps a minimum width.

**C7. One vocabulary. (Roadmap U4.)** *Reported, partly Seen live.*
Still drifting: Ask / "Chat tab" / "chat session" / "conversation"; Find / "Search sources" / "New search"; Reports / "Generate" / "artifact"; sources / docs / documents / files / papers; passages / chunks / "indexed text"; two different controls called "Answer depth"; catalogue / registry. Write the glossary into `docs/ENGINEERING.md` (one word per concept, as U4 lists, plus "passage" for what the user sees and "paper" only inside the Library), then apply it to every user-visible string, aria-label and download name. Add a test that fails on the banned words in the built string table or the component templates.

---

## Workstream D: failures the user can see and act on

**D1. A failed upload gives no reason in the API answer.** *Seen live.*
`POST /documents/upload` with eight files answered "Indexed 7 document(s) to collection 'default'. Failed: smoke-scan.pdf" and nothing else (`api/documents.py:351`). The file is a scanned PDF and OCR was off; the reason exists and is dropped. Return a `failed` list of `{filename, reason, fix}` on every upload path and show it. Then do roadmap U2: a persistent "N failed" row in Sources with the reason and Retry.

**D2. No path for an expired or missing sign-in.** *Reported.*
`utils/http.js` has no 401 handling; the user gets an "Unauthorized" toast while job polling carries on every 1.5 s (`backgroundJobsStore.js:627`, which also never backs off or pauses on a hidden tab). On 401 stop all polling and show one sign-in prompt; on repeated network failure back off and show the existing offline banner; pause polling when the tab is hidden.

**D3. Validation and proxy errors lose their message.** *Reported.*
`http.js:118-124` reads `detail` only when it is a string, so FastAPI's 422 list becomes "Unprocessable Entity"; `ChatTab.vue:1496` turns a proxy's HTML 502 into "HTTP 502". Map 422 lists to "field: message", and map 502, 503 and 504 to a sentence that says the server or the model did not answer and that retrying is safe.

**D4. Settings that fail to save say nothing.** *Reported.*
`SettingsTab.vue:1452` (Ollama context window keeps the unsaved value), `:1461` (toggle flips back silently), `:1638` (OCR settings, console only), `:2070` (the key is "Connected" in the browser but was not stored for server-side work). Each must show what was not saved and why.

**D5. Replace native confirms; confirm the one destructive action that has none.** *Reported.* (Roadmap U9 mentions the confirms.)
`confirm()` at `ChatTab.vue:1044` and `:1613`, `SearchTab.vue:562`, `AdminTab.vue:758, 780, 856, 989`. MCP token revoke (`MCPTab.vue:247`) has no confirmation at all. One confirm component, used for all of them.

**D6. Copy buttons on a plain-HTTP intranet host.** *Reported; reproduce on `http://<lan-ip>`.*
The clipboard API is unavailable outside a secure context. `ChatTab.vue:1268` only logs, `ArtifactsTab.vue:381` has no handler, `MCPTab.vue:1067` says "Failed to copy" with no fallback. `ShareModal.vue:329` already does it properly by showing the text selected for a manual copy; use that everywhere, most of all for the MCP token, which is shown once.

**D7. Raw exception text in responses.** *Reported.*
About forty sites pass `str(e)` into a 4xx or 502 detail (`api/chat.py:1365`, `:1812` are examples), and unhandled exceptions have no handler: `main.py:397-401` registers three, so anything else is a plain-text 500 with no JSON, no request id and none of the security headers (`middleware/request_meta.py:96-116` re-raises before setting them). Add the catch-all handler with the `{detail, code}` envelope `docs/ENGINEERING.md` already lists as left for later, a request id on every error, and a short fixed message for upstream failures with the raw text in the log only. Extend `tests/test_server_error_detail.py` to cover an unhandled exception and a password-only deployment, where today's redaction never applies.

---

## Workstream E: what an enterprise review will ask

Everything here is *Reported* unless marked. Reproduce, then fix in this order. Several are decisions for the owner; those are listed at the end and must not be decided by you.

**E1. Audit trail that can attribute.** Rows have no source address, request id or sign-in method (`services/app_database.py:368-377`); password callers are recorded with no actor; sign-in successes, failures and lockouts are not audit events (`services/audit.py:28-83`); `admin.injection_tuning` is recorded but missing from `ACTIONS`; the trail is only readable by polling an endpoint capped at 2000 rows with no cursor, and the Admin view loads 200 with no paging (`AdminTab.vue:929`). Add the columns, the events, a cursor, paging in the UI, and emit each audit row to the log stream as one JSON line so a SIEM can collect it.

**E2. Suspending a person must suspend them.** `services/governance.py:818` revokes MCP tokens only; nothing else reads a suspension, so a suspended identity still signs in through the browser. Enforce it where identity is resolved.

**E3. Model endpoints a user may name.** `ai_allowed_base_urls` is empty by default, meaning any address (`config.py:199-204`); only the secure profile sets `none`. On a server reachable from the network, any chat user can make the server call an address of their choosing. Default to the configured endpoints only whenever the bind address is not loopback, with a clear startup line saying so.

**E4. Settings saved in the UI silently beat the environment.** `services/indexer_manager.py:78-83` applies every saved field over `settings` at start and swallows failures, so an operator who changes `.env` or the container environment can be overridden by an old database row with no sign of it. Log each override at start ("X is set in the environment but the value saved in Settings is in force"), show it in Settings, and do roadmap U10 (a read-only "Set by environment" list) so both sources are visible in one place.

**E5. Validate configuration at start.** Numeric settings have no ranges (`config.py`); `chunk_overlap >= chunk_size` or a zero limit fails at use time. Add range validation with messages that name the setting and the allowed range. Document the eighteen settings that appear in no doc or example file, `POSTGRES_URL` first, or remove the dead ones (`schema_version` reads "3.0" while the stores are at 3.9; `mcp_ai_provider` per roadmap M10). Add `LOG_LEVEL`.

**E6. Upgrades and backups an operator can trust.** `services/metadata_store.py:233-293` re-stamps an unknown newer schema version instead of refusing, so starting an older image on a newer data volume silently damages the version record; `app.db` has no version table and migrates by catching `ALTER TABLE` errors (`services/app_database.py:408-412`); `scripts/backup.py` needs the server stopped for a consistent vector index, records no app or schema version in its manifest, and carries `secret.key` beside the database it protects. Refuse to start on a newer database with a message that names both versions; version `app.db`; snapshot before migrating into the `data/backups` directory that is created and never used; stamp and check versions in the backup manifest; add an online-consistent backup of the vector index and an option to leave the key out.

**E7. Jobs across a restart.** Index, artifact and Library jobs are failed or dropped on restart (`services/upload_service.py:381-383`, `services/artifact_jobs.py`); there is no stall detection, so a hung job holds its collection's slot until restart. Re-queue what was queued, mark what was running as interrupted at start with a one-click "Run again", add a per-job stall timeout, and give `docker-compose.proxy.yml` the same stop grace period as the default compose file. (Library jobs: roadmap R8, see A10.)

**E8. Settings are written to `.env` in the working directory.** *Read.* `ConfigManager.__init__` defaults to `Path(".env")` and is built with no argument (`services/config_manager.py:99`, `:536`), ignoring `CLIO_ENV_FILE`, which `config.py:970` reads from. A second instance started in the same directory with another `DATA_DIR` reads and rewrites the first one's file. Write to the file the settings were read from, and skip the mirror with one clear log line when that file is not writable.

**E9. Capacity and health.** `/readyz` does not look at free disk and nothing checks it before ingest; there is no cap on total storage or collection count; metrics have no series for failed sign-ins, lockouts, job failures, free disk or provider errors (`services/metrics.py`), and the request id is on the access line only. Add a free-disk check to readiness and before an upload is accepted, the missing metrics, and the request id on every log line of a request.

**E10. Browser state on a shared workstation.** Provider keys (`aiProviders.js:171`, plain text), chat history, search history and notes sit in localStorage under keys with no user in them, so on a shared profile the next person inherits them. Namespace every key by the signed-in identity when there is one, and add "Sign out and clear this browser" to the user menu. Server-side chat history is roadmap U9.

**E11. Sharper lockout behind a proxy.** *Reported, likely.* Failed sign-ins are counted per peer address (`main.py:507-510`), and only `docker-compose.proxy.yml` sets `FORWARDED_ALLOW_IPS`; behind any other proxy one guesser locks everyone out. Detect "every request comes from one private address" and say so in the readiness report with the setting to change.

### Decisions for the owner (write up options, do not choose)

- **Password-only deployments have no admin separation and no attribution.** Everyone who knows `AUTH_PASSWORD` can change deployment settings and read the audit trail (`api/deps.py:98`). Options: require an identity provider for anything called "enterprise"; add a separate admin secret; or keep it and say so prominently in the docs and the readiness report.
- **No browser sign-in flow of its own.** OIDC is bearer-token only (`docs/IDENTITY.md`), so browser single sign-on needs the authenticating proxy; there is no logout or idle timeout in password mode. Options: make the proxy compose file the documented enterprise default; or add a small session layer.
- **Roadmap M9**: should open and password callers be read-only over MCP by default.
- **Roadmap R8**: how a Library job resumes without a stored key.
- **Admin by group claim**: admins are a static list of emails read at start (`services/admins.py:19`).

---

## Acceptance: the demo, run end to end

When the workstreams are done, run this script three times and record the result of each step: on the full image with an open network; on the slim image with `--network none`; and on a full image with the hub black-holed, Ollama as the only model source. At 1280x720. No step may need a terminal, a page reload, or an explanation from the presenter.

1. Open the app for the first time. Within two seconds the screen says what Clio is and offers sample documents. Nothing offered is unavailable in this environment, and anything unavailable says why.
2. Choose the sample documents, ask a suggested question, open a citation.
3. Add ten real PDFs by dragging them in (roadmap U6), one of them a scan. Progress is visible; the scan either indexes or shows why not, with a fix.
4. Open the Library from a link (`?tab=library`). Records are there without pressing anything. Any uncertain record is marked. Nothing wrong is stated as fact.
5. Screen against a question, then prepare reviews. One strip shows progress and survives a reload.
6. Open Cite & check, paste a paragraph with one altered quote, and see it caught. Copy the reference list.
7. Open the footer status. It shows no warning colour unless something is wrong, and what is wrong names its fix.
8. Open Connect, create a token in three clicks, and make one tool call from an agent.
9. Stop the model source mid-session (stop Ollama, or drop the network). The next question fails with one plain sentence and search still works. Start it again; the app recovers without a reload.
10. Restart the server. Every job that was running is listed as interrupted with "Run again"; nothing is silently gone.

## What to report back

For each item: done, skipped (and why: did not reproduce, or needs a decision), or changed in scope. For each "Reported" item, the reproduction you ran. The filled-in model test matrix. The benchmark numbers before and after A1 and A2. Screenshots at 1280x720 of the Library with a paper open, the first-run search-index step with the hub blocked, and the footer on a default install. Then update `docs/ROADMAP.md`: mark what shipped with its date, and move anything you found and did not fix into the right phase.
