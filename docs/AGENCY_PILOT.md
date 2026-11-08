# Agency document research pilot

## The customer promise

Ask questions of a curated document collection, inspect the passages behind the
answer, and use the same knowledge from an MCP-compatible assistant. Deployment
and model choice remain with the operator. Hosted providers receive the content
they process; self-hosting the application alone does not make every AI step local.

Start with a bounded staff workflow: finding procedures, comparing revisions,
or preparing an evidence-backed internal briefing. Keep a reviewer responsible
for decisions and for checking dates, exceptions, and source authority.

## Five-minute demonstration

1. Load the fictional records: choose **Explore with sample documents** on the
   welcome screen, or **Try it with sample documents** on an empty collection's
   Ask tab. They land in a collection of their own, **Sample: equipment policy**:
   the policy, its amendment, a desk note that disagrees with it, and a checkout
   register. These are invented documents, not actual government policy, and
   stay separate from customer collections. (The files themselves are in
   `frontend/public/samples/`.)
2. Ask: **Under EQ-17, when must equipment be returned?** Show the answer and
   select its `[Source N]` references. Read the actual passage and open the original.
3. Ask: **Does the rule differ for field teams after July 1, 2026?** Explain the
   effective amendment and demonstrate follow-up document searches in Research
   activity. The answer should distinguish the general rule from the exception.
4. Ask: **Who approved the amendment?** The provided documents do not say. The
   assistant should acknowledge missing evidence rather than invent a name.
5. Select only the original policy and ask about field-team exceptions again.
   Show the visible scope and explain that excluded amendments cannot support
   the answer. Clear the selection afterward.
6. Use **Copy with evidence** to take the answer, numbered references, excerpts,
   and document links into a draft briefing. Links still require access to the
   deployment; the copied excerpt is a separate copy of the content.
7. Open **Connect** to show how an existing AI client can use the same collection
   over MCP. Use a read-only personal token for the demo and keep it off-screen.

## What this implementation adds

- One-click citation review with passages, locations, labels, and original links.
- Source numbering spans initial retrieval and follow-up agent searches/context
  fetches, including across collections. Search ranks are not citation numbers.
- Completed research activity collapses; failed steps stay visible.
- Source scope is always visible, with a direct return to a selected-source scope.
- New chat preferences start with hybrid retrieval; existing preferences persist.
- Streaming chat honors reranking, includes its token usage, and keeps earlier
  tool results available in the fallback agent loop.
- Agent turns bypass answer-cache writes because tool dependencies can extend
  beyond the initial query scope. Ordinary single-turn answers can still cache.

Retrieved passages are not a claim-by-claim verification score. Table outputs
retain their existing tool presentation and are not converted into fabricated
document excerpts. Saved older answers are not retroactively recited.

## Pilot acceptance, before a customer rollout

Use the questions in `docs/demo/agency-evaluation.json` for a smoke test, then
replace the fictional corpus with an agency-approved representative collection.
Record the corpus version, provider/model, retrieval settings, and each output.
Run answerable, unanswerable, exact-identifier, conflicting-revision, table, and
permission cases. A staff reviewer should mark whether every material claim is
supported, whether the citation opens the right passage, and whether the system
correctly says when evidence is missing. Measure time to a checked answer and
compare it with the agency's current process. Do not infer quality from similarity
scores or from passing application tests.

Deployment validation is separate: test owner/reader/outsider/revoked-token access,
actual provider egress, backup restoration, and keyboard/screen-reader workflows.
No government certification or agency-wide production readiness is asserted here.

## Prioritized expansion

| Order | Customer outcome | Completion evidence |
|---|---|---|
| 1 | Reliable evidence and recovery | Staff can inspect every document reference; stopped/failed turns preserve work and can be retried with the original scope. |
| 2 | Measured retrieval quality | Versioned agency evaluation set with recall, citation correctness, missing-answer behavior, and latency reported per model/configuration. |
| 3 | One dependable source connector | Pick the actual pilot repository; incremental updates, deletions, permissions, and sync failures are tested together. |
| 4 | Reviewable research briefs | Saved questions and evidence snapshots include source versions, reviewer notes, and export history. |
| 5 | Controlled agent actions | Add customer-requested actions with explicit scope, review, audit records, and recovery; prove each integration on a bounded task. |

The first pitch should demonstrate the checked-answer workflow. Expand tool
connections based on a real agency task and measurable pilot results.
