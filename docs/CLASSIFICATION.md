# Classification markings

A collection, or a single source in one, can carry a classification marking: export controlled, CUI, proprietary, or a vocabulary of your own. A marking is either a label only, or **local only**. Local-only data is not given to anything outside the deployment.

This is separate from the sensitivity label (public, internal, confidential, restricted), which is about which people should see something. A marking is about which machines may be given it.

The code is in [services/data_controls.py](../services/data_controls.py).

## Setting it up

- **The markings on offer** are edited in Admin, under Classification. The built-in list is Export controlled, CUI and Proprietary, all local only. Each marking has a name, an optional description, and a "Local only" box.
- **Marking a collection**: edit the collection and choose a Classification. Owner only.
- **Marking a source**: select it in the Sources panel and choose from "Classify…". Anyone who can add sources can mark one. Taking a local-only marking off a source is the owner's call.
- **Internal servers**: a model or embedding server on your own network that is reached by name is listed under "Servers that count as inside" (`CONTROLLED_DATA_HOSTS`).

Every change of marking is in the audit trail (`collection.classification`, `document.classification`), and so is every refused call (`classification.blocked`).

## What counts as outside

| Service | Outside when |
|---|---|
| AI model (chat, reports, research, search with AI, starter questions) | Any hosted provider. Ollama or an OpenAI-compatible server whose address is not inside |
| Embedding provider | Any hosted provider. Ollama or an OpenAI-compatible server whose address is not inside |
| Vision OCR, figure descriptions, the model's content review | The model they use is outside |
| MCP clients and registered apps on `/api/v1` | Always. Where an agent sends what it reads is not this server's to know |
| Scholarly catalogue lookups | Always |
| Share and report emails | Always |
| Change webhook | Its address is not inside |

An address is inside when it is loopback, a private address written as numbers, a single-word name such as a compose service, or listed in `CONTROLLED_DATA_HOSTS`. A name is never looked up to decide, because the answer could change between the check and the call. `host.docker.internal` and `llm.corp.example` therefore need listing.

## The rule

**A collection that holds anything local only is closed to outside services as a whole.** That is the case when the collection is marked, and also when one source in it is.

A marked source is not picked out and the rest served. A collection reaches a model in many ways besides its passages (file lists, tables, figures, overviews, statistics), and one missed path would be a release. To use outside services with the rest, move the marked sources to a collection of their own.

What that means in practice:

- Chat, reports and research with an outside model are refused with a 403 that says why and what would work. Nothing is read first and no provider is built. Asking across all collections leaves the closed ones out.
- Search with AI rerank or synthesis still answers, without the AI steps.
- MCP clients and registered apps do not see the collection at all, whatever their token or allowlist says. It answers as not found.
- With an outside embedding provider, the collection is indexed and searched by keyword only.
- Scanned pages are read by the local OCR engine, figures are kept without a description, and the model's content review is skipped, when the model for those is outside.
- Catalogue lookups, share invitation emails, report emails and change webhooks are skipped. The share is still created and the report still reaches the review queue.

Everything in the app itself keeps working: search, reading, tables, and chat with a model on your own network.

## What it does not do

- **It cannot recall what was already sent.** Text indexed before it was marked, on a server with a hosted embedding provider or vision model, has been sent to that provider. Mark the collection before adding sources to it.
- **It does not stop people.** Someone who can read a collection can copy from it, download a source, or export a bundle. Sharing with another person on the same server is not an outside service.
- **It trusts an inside address to be inside.** A gateway on your network that forwards to a hosted model looks internal.
- **A marking removed from the list still holds.** Data carrying a marking that is no longer on offer stays local only until someone changes its marking. A list that cannot be read makes every marking local only.
- **Offline mode is separate** and stricter: it turns hosted services off for the whole deployment.

## Bundles and copies

An exported bundle carries its collection's marking and its sources' markings. An imported one keeps them, known on the importing server or not, and the marking is in place before anything is embedded. A clone copies the marking the same way.

The export for a static website ([STATIC_SEARCH.md](STATIC_SEARCH.md)) publishes what it holds, so it counts as an outside service: a collection that is marked local only, or holds one source that is, cannot be exported that way at all.
