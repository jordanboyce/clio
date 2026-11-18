# PRA: analysis and tools

Clio reads probabilistic risk assessment (PRA) models (SAPHIRE MAR-D exports,
Open-PSA MEF files) into one shape, `services/pra/model.py`. This page covers
what Clio computes from that shape and how people and agents reach it: the
calculations in `services/pra/analysis.py`, the REST routes under `/api/pra`
and the MCP tools `pra_*`. Both surfaces build their answers in
`services/pra/queries.py`, so they return the same shapes.

**Every number here is a screening estimate.** Clio quantifies cut sets with
point values, by the rare-event sum and the minimal cut set upper bound. It does
not propagate uncertainty, apply recovery or post-processing rules, flag sets,
mutually exclusive event rules or the tool's own quantification methods. The PRA
tool's own quantification governs; every response says so in `notice`.

## Definitions

A cut set is a list of event names; `/X` is the complement of `X` (its
success), with probability `1 - p(X)`.

| Quantity | Definition |
|---|---|
| Event probability | A basic event's point probability; a house event is 1 when true and 0 when false; an initiating event's frequency; `/X` is `1 - p(X)`. Anything else has no data and is reported as missing. |
| Cut set probability `P_j` | The product of its distinct events' probabilities. |
| Rare-event total | `Σ P_j` |
| MCUB total | `1 - Π (1 - P_j)`, the minimal cut set upper bound, computed in log space so tiny values keep their precision. |
| Share | `P_j / Σ P_j`, so the shares of a list add up to 1. |

A cut set whose events lack data has no probability. It is listed after the
others and left out of both totals, which are then lower bounds; the events
are named in `missing_data`. Sequence cut sets carry the initiating event's
frequency, so they have no MCUB; their totals and importance use the
rare-event sum, and `basis` says so.

### Importance

`F` is the total from the cut sets. **All four measures use the MCUB**
(`basis: "mcub"`), except for frequencies as above (`basis: "rare_event"`).
`F[p=x]` is `F` with the event's probability set to `x`. A complemented
occurrence `/i` then counts `1 - x`.

| Measure | Definition | Reads as |
|---|---|---|
| Fussell-Vesely | `(F - F[p=0]) / F` | The fraction of the total that involves the event |
| Risk achievement worth (RAW) | `F[p=1] / F` | How much the total rises if the event is certain |
| Risk reduction worth (RRW) | `F / F[p=0]` | How much the total falls if the event never occurs; `null` when unbounded (the event is in every cut set) |
| Birnbaum | `F[p=1] - F[p=0]` | The sensitivity `dF/dp` |

FV, RAW and RRW are `null` when `F` is 0. An event without data has `null` for
every measure. Differences are computed directly, not by subtracting two
nearly equal totals.

## Cut set generation

When a model carries no cut sets for a fault tree, `compute=true` generates
them (`source: "computed"`). This is MOCUS run top-down and depth first. An AND
adds all its inputs to a row. An OR or a k-of-n gate branches the row. A gate
the row already satisfies is dropped, and a row that contains a cut set already
found is absorbed. Shared gates are compiled once. Rows are sets of event
indices, and a subset index keeps absorption cheap.

- **Transfers** resolve to the referenced tree's top gate. A gate input that
  names a fault tree works the same way. An unresolvable transfer becomes an
  undeveloped event, with a warning.
- **House events** are constants. A true one drops out of an AND and satisfies
  an OR. A false one kills an AND and drops out of an OR. `/H` flips the
  constant.
- **k-of-n** (`atleast`, SAPHIRE's `3/4`) expands into its combinations. A gate
  with more than 10,000 combinations is refused.
- **NOT, NAND, NOR, XOR and complemented gate inputs** are pushed down to the
  events by De Morgan's laws and expanded exactly. A product holding both `X`
  and `/X` is dropped as impossible. Then:
  - `success_terms=drop` (the default, and what SCRAM does for minimal cut sets)
    deletes the `/X` terms and minimises again. This is the coherent
    approximation, an upper bound.
  - `keep` leaves them in the products, at probability `1 - p`. Those products
    are minimised by subset absorption only, so they are not necessarily prime
    implicants.

  Either way `non_coherent` is true and a warning says which was done.
- **Undefined names and gate types Clio cannot analyse** become events without
  data, with a warning.

### Guards

| Guard | Default (REST/MCP) | What happens |
|---|---|---|
| Truncation | `1e-15` absolute (`truncation=`) | A row is dropped once an upper bound on any cut set it can still produce falls below the cutoff. The bound multiplies the row's probability by the largest cut set probability of each pending gate whose events are disjoint from the row's and from each other, so it never drops a cut set at or above the cutoff, even when trains share support events. |
| Max order | none (`max_order=`) | Longer cut sets are not produced. |
| Max cut sets | 50,000 | Past this, the cutoff is raised to keep the most probable ones (`truncation_raised`, `truncation` is the cutoff in force). |
| Time budget | 20 s of wall clock | The search runs at cutoffs 1e-3, 1e-5, ... down to the one asked for, each pass exact for its cutoff. When time runs out, the last finished pass is returned with `stopped: "time_budget"`, and `truncation` says how far it got. If not even the first pass finished, its partial list comes back with `complete: false`. |
| Concurrency | 2 generations at once | Further requests get 429 with `Retry-After`. |

With `complete: true`, the list holds every minimal cut set at or above the
returned `truncation` (and up to `max_order`). A computation that hits the time
budget before finding any cut set answers 409. Finished results are cached per
model and parameters.

## REST API

All routes are `GET` with `?collection_id=` (default `default`). Collection
access is checked as for every read: a hidden collection is 404. A held or
quarantined document is 404 to everyone but a reviewer. A document whose
`source_format` does not start with `pra-` is 404. Bad parameters are 422.
Names may contain any character the formats allow, including `/`.

| Route | Returns |
|---|---|
| `/api/pra/models` | `models: [{document_id, filename, name, source_format, counts, warnings}]` |
| `/api/pra/models/{document_id}` | `counts`, `fault_trees: [{name, top, description, gate_count, has_cut_sets}]`, `event_trees: [{name, initiating_event, functional_events, sequences}]`, `end_states`, `cut_set_lists`, `warnings` |
| `/api/pra/models/{id}/fault-trees/{tree}?depth=3` | `structure`: `gate {name, type, k, description, children}` holding `basic_event {name, description, probability}`, `house_event {name, state}`, `transfer {name, tree}`, `undefined`. Gates deeper than `depth` (1-10), or past 2,000 nodes, carry `more: true` and `input_count`. Another tree's top is a `transfer`. A gate met again below itself is `cycle: true`. |
| `/api/pra/models/{id}/event-trees/{name}` | Initiating event and its frequency, functional events, `sequences: [{name, end_state, path, logic, frequency, has_cut_sets}]` |
| `/api/pra/models/{id}/events/{name}` | The event's data and `kind`, `used_in_gates`, `fault_trees`, `event_trees`, `sequences` (with branch), `stored_cut_sets: {total, by_context}` |
| `/api/pra/models/{id}/cut-sets/{context}?limit=50&compute=false` | For a fault tree, gate, sequence or end state: `cut_sets: [{rank, events, order, probability, share}]`, `totals: {rare_event, mcub}`, `cut_set_count`, `events` (descriptions), `missing_data`, `source`, `truncation`, `computation` (when computed), `warnings`. With no stored list: 404 telling you to pass `compute=true`, which works for fault trees and gates. `truncation`, `max_order` and `success_terms` apply to `compute=true`. |
| `/api/pra/models/{id}/importance/{context}?limit=50&sort=fv` | `events: [{rank, event, description, probability, cut_sets, fussell_vesely, raw, rrw, birnbaum}]` sorted by `fv`, `raw`, `rrw` or `birnbaum`, plus `basis`, `total`, `definitions` |

`limit` is 1-1000. Routes run in the threadpool.

## MCP tools

All read-only, scoped like every other tool. A collection the credential
cannot see, or a held document, reads as not found. The shapes are the REST
shapes.

| Tool | Arguments |
|---|---|
| `pra_models` | `collection_id?`, `document_id?` (list, or one model's summary) |
| `pra_fault_tree` | `document_id`, `tree`, `depth` 1-6 |
| `pra_cut_sets` | `document_id`, `context`, `limit` 1-100, `compute` |
| `pra_importance` | `document_id`, `context`, `limit` 1-100, `sort`, `compute` |
| `pra_event` | `document_id`, `name` |

There is no tool-profile mechanism yet (ROADMAP M4). These belong in a `pra`
or `full` profile when there is one. The in-app chat's tool catalogue
(`services/agent_tools.py`) does not list them: every tool there is sent on
every chat turn, and M7 plans one registry for both.

## Validation

The generator was checked against two independent references, kept out of the
repository.

- **SAPHIRE.** A generic PWR MAR-D export: 239 fault trees and 862 gates with
  OR, AND, TRAN and `3/4` gates. Clio's cut sets were compared with SAPHIRE's
  stored fault tree cut sets (`.FTC`) for the 16 trees that have them, at the
  truncation SAPHIRE used. For 12 trees (IEFT-LOCCW, the ISL and EQ trees and
  others) the lists are identical. For ECA-FT, LPI-FT, SGI-FT and SSC-FT they
  differ, and a Boolean check of the exported logic explains every
  difference:
  - All 1,402 SAPHIRE cut sets that Clio lacks are not cut sets of the exported
    logic. SAPHIRE's stored lists reflect settings the logic export does not
    carry.
  - Every SAPHIRE cut set that is a cut set of the exported logic was found.
  - Every Clio cut set was verified minimal. 269 of the extras are removed by
    the project's delete rules (`.FAS`, disallowed test-and-maintenance
    combinations), which Clio does not apply.

  Totals agree to 0.2% on ECA-FT.
- **SCRAM.** Its benchmark inputs, with SCRAM's expected results. Cut set lists
  and totals match on TwoTrain, Theatre, Lift, SmallTree, ThreeMotor and the
  Chinese tree (392 cut sets; RAW, RRW, FV and Birnbaum match to 6 digits on
  the rare-event basis). The non-coherent cases (NOT, NAND/NOR children,
  constant gates) match SCRAM's minimal cut sets with success terms dropped,
  and its prime implicants with them kept. Baobab2 (4,805 cut sets, 47 s),
  the 200-event tree (287) and ne574 (7) also match.
- **Limits.** MOCUS is not a BDD. On Baobab1 and CEA9601, run with no
  probability truncation and only an order limit, the search did not finish
  in 120 s. SCRAM's own benchmarks run those trees that way. On the generic
  PWR at the default `1e-15` cutoff, AFW-FT, LPI-FT and ECA-FT complete down
  to `1e-13` within the 20 s budget, using under 80 MB.
