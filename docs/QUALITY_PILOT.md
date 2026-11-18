# Supplier quality pilot

A paid, tightly scoped pilot for quality teams at aerospace and precision
manufacturing suppliers. The question it tests is not whether Clio beats a
general chat assistant. It is whether an engineering company will pay to make
its own controlled documents faster to answer from, with answers people can
check and AI tools can use.

Companion files:

| File | What it is |
|---|---|
| [tests/fixtures/quality_bench/](../tests/fixtures/quality_bench/) | The fictional quality system the demo and the benchmark share: 16 records, two of them superseded revisions |
| [demo/quality-evaluation.json](demo/quality-evaluation.json) | 15 answer-level questions with expected facts, sources and "not in the records" cases |
| [scripts/pilot_eval.py](../scripts/pilot_eval.py) | `export` the demo files, `run` the questions against a deployment, `score` the reviewer's marks |
| [scripts/retrieval_bench.py](../scripts/retrieval_bench.py) `--fixture tests/fixtures/quality_bench` | Retrieval benchmark with a revision-safety measure |

## Who it is for

- **Company:** AS9100 machining, assembly or special-process supplier, roughly
  20-300 people, building to customer drawings for several primes or tier 1s.
- **Buyer:** Quality Manager or Director of Quality. **User:** quality
  engineers, MRB members, the person who prepares for audits.
- **Signs of fit:** procedures, work instructions, ECNs, deviations, NCR logs
  and audit reports live in shared drives or SharePoint; people answer
  "which revision applies to this lot?" by opening several files; audit
  preparation takes days; informal copies of controlled values drift on the
  shop floor.
- **Not a fit for the first pilots:** companies that need the vendor to hold
  export-controlled data, a validated system under a regulator, or a
  replacement for their QMS or document-control software. Clio sits beside
  those systems; it does not replace them.

## The capability we sell first

**Revision-aware answers with evidence.** Ask a question of the controlled
records, get an answer that cites the passage it came from, from the released
revision, with the older revision labelled when it appears, and a plain "the
records do not say" when they do not. The same collection is available to the
team's AI tools over MCP, read-only.

What exists in Clio today and backs this up:

- `[Source N]` citations with a review panel showing each cited passage
  (README, "A look around").
- "Replaced by" marks: an owner records that rev D replaces rev C; search
  demotes the older document and labels every result from it
  ([DEPLOYMENT.md, old versions and copies](DEPLOYMENT.md)). Clio suggests
  the pairs itself from names such as `rev C` / `rev D`.
- Questions over spreadsheets such as an NCR log, answered with SQL rather
  than by reading rows into a model.
- Self-hosting, local search and reranking, and a choice of model, including
  one on the customer's own hardware ([ONPREM.md](ONPREM.md),
  [AIRGAP.md](AIRGAP.md), [SECURE_PROFILE.md](SECURE_PROFILE.md)).

## The 10-minute demonstration

Prepare: `python scripts/pilot_eval.py export --out demo-quality`, create a
collection named **Demo: supplier quality**, upload every file from the folder,
connect a model. Do **not** record the "Replaced by" marks yet.

| Min | Do | What the buyer should see |
|---|---|---|
| 0-1 | Show the 16 files. Say they are invented. | A familiar mess: QP-8.7 rev C and rev D, WI-2140 rev F and rev G, an ECN, a deviation, a shift note, an NCR log, an audit report |
| 1-3 | **Find:** "torque for 7731-204 bracket screws" | Before marks, the obsolete rev F ranks first (checked in the running app, keyword search) |
| 3-4 | Sources panel: accept the two suggested "Replaced by" pairs | Clio found both pairs from the file names; one click each |
| 4-5 | Run the same search again | Rev G first; rev F still there, labelled as replaced by rev G |
| 5-6 | **Ask:** "What torque do the mounting screws on the 7731-204 bracket get?" | 32 ±2 in-lb, cited to WI-2140 rev G / ECN-0419; open the citation and read the passage |
| 6-7 | **Ask:** "Lot 2608 of the 7731-204 bracket: which torque applied when it was built?" | 28 in-lb, because ECN-0419 is effective from lot 2611: the older value, correctly |
| 7-8 | **Ask:** "Can NCR-2026-0150 be dispositioned use-as-is under SD-2026-017?" | No: the deviation covers lots 2604-2606 and expired; NCR-0150 is lot 2609 |
| 8-9 | **Ask:** "Who at Aldermark approved SD-2026-017?" | The records name the company and date, not a person. No guessed name |
| 9-10 | **Connect** tab: the same collection in Claude Code or another MCP client with a read-only token | "Audit the open NCRs against QP-8.7 rev D and write the findings to a file" |

Close with the measured numbers (next section) and the pilot offer.

Rehearse with `pilot_eval.py run` against the demo deployment first, and read
`summary.md`: answers depend on the model, and the demo should use one that
gets all four ask steps right on the day.

## What we measure

Two layers, both repeatable, both reported per model and configuration.

**Retrieval** (no model needed, `scripts/retrieval_bench.py --fixture
tests/fixtures/quality_bench --skip-concurrency`): recall@5/10 and MRR by
category (identifier, revision, negation, date, table), and **revision
safety**: the share of revision questions where the current passage is
retrieved and no superseded or informal passage stating the old value ranks
above it, with the "Replaced by" marks honoured and ignored. The difference is
the measured value of recording revisions.

**Answers** (`scripts/pilot_eval.py run`, then a reviewer marks
`review.csv`, then `pilot_eval.py score`):

| Measure | How it is decided |
|---|---|
| Supported | Reviewer: every material claim is backed by the passage it cites |
| Citations correct | Reviewer: each citation opens the passage that supports the sentence |
| "Not in the records" handled | Reviewer: on unanswerable questions, says so and invents nothing |
| Time to a checked answer | Reviewer's minutes with Clio, against minutes today for the same question |

The automatic checks in `pilot_eval.py` (expected facts mentioned, expected
source cited, stale values present, citations outside a selected scope) only
decide what the reviewer looks at first. They are never the score.

**Status, 2026-11-18.** The harness, fixture and question set are built and
tested offline. The demo flow was checked in a running instance without an
embedding model (keyword search): both "Replaced by" pairs were suggested,
and marking them moved rev G above rev F. With a stand-in hash embedder (not
a real model, so not a quality claim), revision safety was 100% with marks
honoured and 62.5% with them ignored. The real models give lower numbers,
below.

### Retrieval with the models the images bake (2026-11-18)

Synthetic fixture only (`tests/fixtures/quality_bench`, corpus v1, queries
v1: 16 documents, 51 chunks, 41 queries, 24 of them revision questions).
Commit `0543144`. One query at a time, `top_k=10`, each query run three times.
Reranker: `cross-encoder/ms-marco-MiniLM-L-6-v2` (fastembed), pool `top_k x 3`.
"Marks ignored" sets `SUPERSEDED_PENALTY=0`, as if nobody had recorded the
"Replaced by" marks. Latency is on one Windows 11 machine (i7-8750H, 12
logical CPUs, Python 3.12, fastembed 0.9.1, onnxruntime 1.31.1) and includes
embedding the query.

```
venv/Scripts/python scripts/retrieval_bench.py --fixture tests/fixtures/quality_bench \
  --skip-concurrency --multipliers 3 --repeats 3 --embedding-model all-MiniLM-L6-v2
venv/Scripts/python scripts/retrieval_bench.py --fixture tests/fixtures/quality_bench \
  --skip-concurrency --multipliers 3 --repeats 3 --embedding-model BAAI/bge-small-en-v1.5
```

| Embedding model | Reranker | Marks | Recall@5 | Recall@10 | MRR | Revision-safe | Stale passage first | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| all-MiniLM-L6-v2 | off | honoured | 0.959 | 0.959 | 0.872 | 75% | 12% | 21 | 35 |
| all-MiniLM-L6-v2 | off | ignored | 0.959 | 0.959 | 0.854 | 62% | 38% | 23 | 43 |
| all-MiniLM-L6-v2 | on | honoured | 1.000 | 1.000 | 0.901 | 75% | 12% | 391 | 446 |
| all-MiniLM-L6-v2 | on | ignored | 1.000 | 1.000 | 0.873 | 38% | 50% | 381 | 459 |
| BAAI/bge-small-en-v1.5 | off | honoured | 0.986 | 1.000 | 0.901 | 75% | 12% | 27 | 48 |
| BAAI/bge-small-en-v1.5 | off | ignored | 0.986 | 1.000 | 0.865 | 50% | 50% | 35 | 60 |
| BAAI/bge-small-en-v1.5 | on | honoured | 1.000 | 1.000 | 0.901 | 75% | 12% | 401 | 524 |
| BAAI/bge-small-en-v1.5 | on | ignored | 1.000 | 1.000 | 0.873 | 38% | 50% | 414 | 500 |

What this says, on this fixture:

- Recording revisions is what makes answers revision-safe. With the marks
  ignored, a stale passage ranked first on 38-50% of revision questions; with
  them honoured, on 12%.
- Marks honoured, both models are revision-safe on 75% of revision questions,
  not the 100% the stand-in embedder showed. A quarter still has a superseded
  or informal passage above the current one. That is the next thing to look
  at before quoting revision safety to a pilot.
- The reranker brings recall@5 to 1.000 at roughly 15 times the latency. It
  does not improve revision safety, and with the marks ignored it makes it
  worse (38%), because it scores the old passage on its wording alone.
- bge-small edges MiniLM on recall without the reranker (0.986 against
  0.959 recall@5) at a few milliseconds more per query.

The corpus is 51 chunks, so absolute recall does not transfer to a real
collection; the comparison between configurations does. These are retrieval
numbers, not answer quality, and they make no conformance claim of any kind.

## The pilot offer

**$10,000 fixed, six weeks, one site, one document family.**

| | |
|---|---|
| Scope | One product line or cell: its procedures, work instructions, ECNs, deviations, NCR log and recent audit reports. Up to about 2,000 documents, up to 10 named users |
| Deployment | On the customer's server or VM, or air-gapped. Model of their choice; a local model where documents cannot leave the site |
| Week 1 | Install, load, record "Replaced by" marks with their document controller |
| Week 2 | Build a 40-question evaluation set with a quality engineer: their real questions, with today's answer time measured by hand |
| Week 3 | First scored run; fix configuration, not the scores |
| Weeks 4-5 | Team uses it; MCP access for one AI tool if wanted |
| Week 6 | Second scored run, readout, go / no-go |
| Success criteria (agreed in week 1, written into the order) | For example: at least 90% of answers supported; at least 95% of citations correct; every revision question answered from the released revision; every unanswerable question declined; median time to a checked answer at least 3x faster than today |
| After | Convert to an annual licence and support, or stop. Data and indexes deleted on request, with a written confirmation |

**Customer provides:** a quality engineer for about 4 hours a week, a document
controller for the marks, a server or VM, and a decision-maker at the readout.

**Pricing hypothesis.** $10,000 is small enough for a Quality Manager's
discretionary budget at many suppliers and large enough to test willingness
to pay. Test it; do not defend it. If three buyers in a row balk at price but
not value, try $5,000 with a smaller scope before changing anything else.

## What we do not claim

- No ITAR, EAR, CMMC, DFARS 7012, FedRAMP, SOC 2 or AS9100 conformance is
  claimed for Clio. A customer's own controls and deployment decide those.
- A hosted model receives the text it processes. Only a local model keeps
  every step on site.
- Clio does not decide dispositions or release product. A person checks the
  cited evidence and owns the decision.
- Demonstrations use fictional records only. Never load real
  customer-controlled or export-controlled data into a demo.

## Known gaps to close before the first pilot

- Revision safety is 75% with the real models and the marks honoured (above):
  find out why a quarter of revision questions still rank a stale passage
  above the current one.
- Part numbers in keyword search: `7731-204` is split into `7731` and `204`
  (`services/bm25_service.py`, `_TOKEN_RE`), so a sibling part such as
  7731-240 can rank second for a 7731-204 question. Benchmark query `id-04`
  measures it.
- The security and authorisation review in progress, handled privately per
  [SECURITY.md](../SECURITY.md); its outcome feeds the buyer's security
  questionnaire answers.

## Finding the first 10-15 design partners

Where to look: the IAQG OASIS database of AS9100-certified sites (filter by
region and size), regional Manufacturing Extension Partnership centres,
regional aerospace clusters and supplier days, and LinkedIn searches for
Quality Manager at AS9100 machine shops. Prefer companies within driving
distance for the first three.

Questions for a 30-minute discovery call, before any demo:

1. Walk me through the last time someone needed to know which revision applied to a lot. How long did it take?
2. Where do procedures, work instructions, ECNs, deviations and NCRs live today?
3. What did your last customer or registrar audit find about document control?
4. Who answers the shop floor's questions about specs, and how many a week?
5. Is anyone using ChatGPT, Copilot or similar on work documents today? What is the policy?
6. What would have to be true for you to spend $10,000 to fix this?
7. Which documents could never leave the building?

First message (short, specific, no attachment):

> Subject: Which revision applies to lot 2611?
>
> Hi {name}, I'm building a tool for supplier quality teams that answers
> questions from your own procedures, work instructions, ECNs and NCR logs,
> cites the exact passage, and always answers from the released revision.
> It runs on your own server.
>
> I'm looking for a few AS9100 shops to try it on one product line for six
> weeks, with agreed success criteria and a measured before/after. Could I
> show you a 10-minute demo on invented records, and ask how your team
> handles this today?

Track every conversation: company, size, current system, the pain in their
words, objection, price reaction, next step. Ten conversations decide whether
the offer, the price or the buyer needs to change.
