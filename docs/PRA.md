# PRA models

Clio reads probabilistic risk assessment (PRA) models: fault trees, event
trees, basic events and the cut sets a tool computed. A model becomes one
source in a collection, and that source answers two kinds of question:

- **in words**, through Ask, Find and the MCP search tools: "what feeds gate
  SYS-B-FAILS", "what is the probability of the AFW pump failing to start",
  "which sequences end in core damage". Every fault tree, gate, event tree and
  sequence is a passage you can open and cite;
- **as SQL**, through the MCP `list_tables`, `get_table_schema` and
  `query_table` tools: the model's basic events, gates, sequences and cut sets
  are typed tables.

Clio does not quantify a model on its own: probabilities, frequencies and cut
sets are the ones the model carries. What a reader cannot represent is listed
as a warning on the model's first page, never dropped silently.

## Turning it on

PRA models are off unless an administrator turns them on: Settings, Features,
**PRA models**, or `ENABLE_PRA=true`. Off, nobody sees them. A SAPHIRE or
Open-PSA file indexes as the plain text it is (a zip export is refused), no
model is built, `/api/pra` answers 404, the `pra_*` MCP tools are not listed
and the server instructions do not mention them, and the app offers no model
viewer. Switching it on or off takes effect at once, without a restart. A
file added while it was off becomes a model only when it is indexed again
(re-add it, or re-index the collection).

## Which tools

| Tool | How to bring the model | Status |
|---|---|---|
| Open-PSA Model Exchange Format (XFTA, SCRAM, RiskSpectrum and other tools that export MEF) | The MEF `.xml` file(s); `.opsa`, `.mef` and `.opsa-mef` are read too. Several files of one model (a model split across files, a report beside it) go in one `.zip` | Supported |
| SAPHIRE | Its MAR-D export (NUREG/CR-7039 Volume 7): the project's `.FTL`, `.BEI`, `.BED`, `.ETL`, ... files, one at a time or zipped together | Supported |
| CAFTA | Convert it to SAPHIRE (caf2sap) and upload the MAR-D export, or upload MEF where your CAFTA toolchain exports it | Through SAPHIRE or MEF |
| CAFTA native `.caf`, `.rr` | | Not yet: the formats are proprietary and undocumented. Sample files are needed before a reader can be written |

## What each file becomes

A file is a PRA model by its content, not its name alone:

- `.xml` is read as MEF only when its root element is `<opsa-mef>` (or an MEF
  `<report>` holding `<sum-of-products>`); any other `.xml` is indexed as XML
  code, as before.
- `.sql` is read as MAR-D sequence logic only when it starts with a
  `PROJECT, EVENT TREE, SEQUENCE=` header and has `^EOS` lines; any other
  `.sql` is SQL code, as before.
- The other MAR-D extensions (`.ftl .bei .bed .ftd .gtd .etl .etd .esd .sqc
  .ftc .esc`, any case) are read as MAR-D, and a file that does not start like
  MAR-D is refused with the reason.
- A `.zip` is accepted only when every file in it (folders, a README and macOS
  litter aside) is an MEF file or a MAR-D file of one model. The MAR-D kinds a
  SAPHIRE export also carries (`.BEA`, `.FTA`, `.GTA`, `.SQA`, `.MARD`, ...)
  are accepted and listed as not read. Any other zip is refused as before.
  The zip becomes **one** source holding the combined model.

Each MAR-D kind can be read alone, and a set combines:

| MAR-D file | Read into |
|---|---|
| `.FTL` fault tree logic | Fault trees and their gates (AND, OR, NAND, NOR, `k/n` as at-least-k, `TRAN` transfers); events the logic names become basic events |
| `.BEI` basic event data | Basic events: `Calc. Prob` as the probability, `Lambda` as the failure rate, `Mission` as the mission time, `FdT` as the calculation type (verbatim), the uncertainty distribution as a summary (`beta b=2780`, `lognormal EF=3`); `FdT` T/F as house events; `Init` I as initiating events (frequency) |
| `.BED`, `.GTD`, `.FTD`, `.ETD`, `.ESD` | Descriptions of basic events, gates, fault trees, event trees and end states |
| `.ETL` event tree logic | Event trees: the initiating event, the tops in column order with their descriptions, and one sequence per `^LOGIC` row with its path (success/failure per top), end state, transfer, frequency and comment |
| `.SQL` sequence logic | Each sequence's logic (`/X` is success of top X) |
| `.SQC`, `.FTC`, `.ESC` | Sequence, fault tree and end state cut sets |

Sequences are named by SAPHIRE's sequence number (`01`, `02`, ...), the
names its `.SQL` and `.SQC` files use; the ETL's "Sequence Name" column and
comment are kept as the sequence's label.

From MEF: fault trees (with components and private names, which keep their
full path `FT.COMPONENT.NAME`), gates with any formula (and, or, not, xor,
nand, nor, atleast, iff, imply, cardinality; a nested formula becomes a
sub-gate named `GATE.1`, `GATE.2`, ...), basic and house events, parameters,
event trees (forks, paths, named branches, sequences, `<collect-formula>` of
single events as the sequence logic, `<event-tree>` links as transfers),
initiating events, CCF group members, and `<sum-of-products>` cut sets from a
report. Expressions are evaluated to a point value: numbers, parameters,
add/sub/mul/div/neg, `exponential(λ, t)` as 1 − e^−λt (with λ and t kept as
failure rate and mission time), and the mean of a lognormal, normal, uniform,
gamma or beta deviate (the distribution is kept as the uncertainty summary).

## In the index

One source per model, with source format `pra-mef` or `pra-mard`, in the
**Data** family (a `.sql` or `.xml` model keeps its suffix's family, Code,
because families are decided by suffix).

**Pages.** Page 1 is an overview: the model, the files it came from, counts,
the fault and event trees, end states, and every warning. Then each fault tree
(40 gates a page), each line a gate with its inputs described in place:

> Gate AFW-TRAIN-A (OR) in fault tree AFW — AFW TRAIN A FAILS — fails if any
> of: AFW-MDP-A-FS — AFW MOTOR-DRIVEN PUMP A FAILS TO START, p=7.94E-04; ...
> Feeds gate AFW-BOTH-TRAINS.

Then each event tree (its initiating event, tops, which sequences end in each
end state, and every sequence with its path and logic), the basic events with
their data, house and initiating events, and the fault tree and end state cut
sets (the first ten of each list; all of them are in the table).

**Tables.** Four per model, named `csv_data_<document>_<table>`, listed by
`list_tables` under the file's name:

| Table | Columns |
|---|---|
| `basic_events` | name, kind (basic, house, initiating), description, probability, failure_rate, mission_time, calc_type, uncertainty |
| `gates` | name, type, k, tree, inputs (space separated, `/X` = NOT X), description |
| `sequences` | event_tree, name, end_state, transfer, path, logic, frequency, description |
| `cut_sets` | context, kind (fault_tree, sequence, end_state), event_tree, rank, events (`A * B`), order, probability, source |

A cut set's `probability` is the product of its events' probabilities
(1 − p for `/X`), empty when one is unknown: a screening number, not a
quantification.

**The model.** The parsed model is saved as
`<collection>/indexes/pra/<document>.json` and removed with the document;
`services/pra/ingest.py` `model_for_document` returns it, and parses the
stored source again if the file is missing.

## Limits and safety

Parsing runs in the extraction helper process (`EXTRACT_ISOLATION`), with
its deadline and memory cap. MEF files that declare a DTD or entities are
refused; zips are checked with the archive limits (`ARCHIVE_MAX_*`) before
anything inside is read, and nothing is unpacked to disk.

| Setting | Default | What it limits |
|---|---|---|
| `PRA_MAX_FILE_MB` | 64 | One model's files, in MB (a zip: everything in it, unpacked) |
| `PRA_MAX_ELEMENTS` | 2000000 | XML elements of an MEF file; lines of the MAR-D files |

For scale, G-PWR, a full generic PWR model in MAR-D (58 files, 22 MB, 1,312
events, 862 gates, 61 event trees, 157,000 cut sets), parses in about three
seconds.

## What is not modelled

Each of these is named in the model's warnings when it occurs:

- **Common-cause failure.** MEF CCF groups keep their members as independent
  basic events with the group's total probability; SAPHIRE's CCF events
  (calculation type R) keep SAPHIRE's calculated probability. Neither is
  expanded.
- **SAPHIRE calculation types** other than plain probabilities (compound,
  SPAR-H, seismic, fault tree results, ...) are taken as SAPHIRE calculated
  them (`Calc. Prob`); Clio does not recompute any.
- **SAPHIRE node substitutions** (`^NODESUBS`, a top quantified with another
  fault tree on some branches): paths show the top's name.
- **MEF expressions** beyond those listed above (periodic tests, Weibull, GLM,
  histograms, extern functions, conditionals): the probability is left empty.
- **MEF system mission time** is the analysis tool's setting, not the model's;
  Clio uses 8760 hours (SCRAM's default) and says so.
- **MEF path instructions** other than single-event `<collect-formula>` and
  `<event-tree>` links (collect-expression, set-house-event, if, complex
  formulas), substitutions, alignments and phases, and `<include>` (put the
  included files in the same zip instead).
- The other MAR-D kinds (attributes, recovery rules, phases, ...).

## In the app

Opening a PRA source (the Sources panel's eye or Open, a cited passage's
"Open the model", a search hit) shows the model's viewer in place of the
current tab; the arrow at its top goes back. It has the model's name, format,
counts and warnings at the top, then three lists, each beside a reader:

- **Fault trees**: the logic as an outline (gates with their type, `3 of 4`
  for k-of-n, basic events with their probability, house events with their
  state, transfers that open the other tree), loading deeper gates when one is
  opened. Arrow keys move through it as a tree. **Cut sets** and
  **Importance** for the tree, from the model or, when it stores none,
  computed on request within the time budget, with how complete the list is.
- **Event trees**: the sequences, each path a mark per functional event
  (filled failed, hollow succeeded, blank not asked), the end state or
  transfer, and the sequence's stored cut sets.
- **End states**: their stored cut sets and importance.

Any event or gate name opens where it is used. A link of the form
`/?pra=<document>&collection_id=<collection>&ft=<tree>` (or `et=`) opens the
viewer directly.

## Analysis and tools

Once a model is indexed, Clio can quantify its cut sets, rank basic events by
importance (Fussell-Vesely, RAW, RRW, Birnbaum) and generate cut sets for a
fault tree that carries none, through `/api/pra` and the read-only MCP tools
`pra_models`, `pra_fault_tree`, `pra_event_tree`, `pra_cut_sets`,
`pra_importance` and `pra_event`. Every number is a screening estimate from point values; the PRA
tool's own quantification governs. Definitions, guards and the validation
against SAPHIRE and SCRAM results are in [PRA_ANALYSIS.md](PRA_ANALYSIS.md).

### Event trees over MCP

`pra_event_tree(document_id, name)` returns what the viewer's event tree
shows, in the shape of `GET /api/pra/models/{id}/event-trees/{name}`:

- the initiating event and its frequency;
- the functional events, each with the fault tree of the same name when the
  model has one;
- every sequence, in order, with its `path` (each functional event it asks
  about and whether that branch is `success` or `failure`), its `end_state`
  or `transfer`, its `logic` and frequency when the model records them, and
  `has_cut_sets`.

A sequence's cut sets and importance come from `pra_cut_sets` and
`pra_importance` with the sequence name as `context` and `event_tree` set to
the tree's name. SAPHIRE numbers every event tree's sequences `01`, `02`, ...,
so without `event_tree` the first list of that name answers. Like the other
PRA tools it is read-only, sees only the collections and documents the
credential may see (a held document reads as not found), and carries the same
`notice`: the numbers are a screening estimate, and the PRA tool's own
quantification governs.
