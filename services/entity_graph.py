"""Entity-graph retrieval augmentation.

A lightweight substitute for full GraphRAG: no LLM extraction pass, no graph
database. Entities are noun-phrase-ish spans found heuristically in each chunk
at index time (proper nouns, acronyms, Capitalized Phrases) and stored in the
collection's SQLite database — the same file that already holds chunk
metadata. At query time, chunks that mention the same entities as the query
are surfaced from across documents and blended into the semantic/hybrid
ranking in ``indexer.search``.

This is what answers cross-document questions ("who worked with X on Y?")
that pure vector similarity misses: the decisive chunk rarely resembles the
question, it just names the same two entities.

The extractor is deliberately conservative — precision over recall. A garbage
entity only adds noise; a missed one just means the query falls back to plain
vector/BM25 behavior, which is today's status quo.
"""

import re
import string
from typing import Dict, Iterable, List, Set


# ── normalization ────────────────────────────────────────────────────────────

_PUNCT_STRIP = string.punctuation.strip("'\"") + "—–-…“”‘’"
_PLURAL_S = re.compile(r"(?<=[a-z]{2})(?:'s|s)$")


def normalize_entity(text: str) -> str:
    """Canonical form for matching: lowercase, punctuation stripped,
    trailing plural 's' removed. 'Alice' and 'alice's' collide; 'IT' stays
    'it' — the ALL_CAPS rule in the extractor keeps real acronyms apart from
    the word 'it' in the candidate phase, so normalization can be simple."""
    t = text.strip().strip(_PUNCT_STRIP).lower()
    t = _PLURAL_S.sub("", t)
    return t


# ── extraction ───────────────────────────────────────────────────────────────

# A compact English stopword list (~300 words) in the tradition of the
# classic Snowball set: closed-class words, common auxiliaries and generic
# verbs/adverbs that would otherwise head false "entities".
_STOPWORDS = frozenset("""
a about above after again against all am an and any are aren't as at be because
been before being below between both but by can can't cannot could couldn't did
didn't do does doesn't doing don't down during each few for from further get
got had hadn't has hasn't have haven't having he he'd he'll he's her here
here's hers herself him himself his how how's i i'd i'll i'm i've if in into is
isn't it it's its itself just let's me more most mustn't my myself no nor not
now of off on once only or other ought our ours ourselves out over own same
shan't she she'd she'll she's should shouldn't so some such than that that's
the their theirs them themselves then there there's these they they'd they'll
they're they've this those through to too under until up very was wasn't we
we'd we'll we're we've were weren't what what's when when's where where's
which while who who's whom why why's will with won't would wouldn't you you'd
you'll you're you've your yours yourself yourselves
also always anything anyone anywhere around away became become becomes becoming
better big came come comes coming day days did done end ends even every
everyone everything everywhere far first found gave gets getting give given
gives go goes going gone good got great had happen happens has here however
keep keeps kept know known knows large last later least less let like liked
likes little long look looked looking looks lot made make makes making many
may might much must near nearly need needed needs never new next nothing old
one ones only open opened opens others part parts people place places point
points put right said saw say says second see seem seemed seems seen set sets
several shall should show showed shows side sides small something sometimes
still sure take takes taken tell than thing things think thinks third though
thought thoughts three time times today together took toward towards turn
turned turns two under until upon use used uses usually want wanted wants way
ways well went whole without work works world year years yet
""".split())

# Sentence start: the first token of a sentence is capitalized for grammar,
# not because it's an entity — see _is_sentence_start.


def _find_acronyms(text: str) -> List[str]:
    """ALL-CAPS tokens of 2+ chars that contain a letter: 'NASA', 'RAG',
    'v2' fails (too short), 'API' passes."""
    return re.findall(r"\b(?:[A-Z0-9]+[-_])+[A-Z0-9]+\b|\b[A-Z][A-Z0-9]*[A-Z]\b", text)


_TEST_DOW_MONTHS = frozenset("""
monday tuesday wednesday thursday friday saturday sunday
january february march april may june july august september october november december
""".split())


def _find_capitalized_phrases(text: str) -> List[str]:
    """Maximal runs of Capitalized words: 'Project Titan', 'Alice Johnson'.

    A word is treated as an entity candidate when it's capitalized and
    either (a) mid-sentence — capitalization is meaningful there — or
    (b) at a sentence start but part of a multi-word run, where the
    capitalization is more likely a name than grammar. A lone capitalized
    word at a sentence start is dropped: 'The' and 'Tuesday' never become
    entities. Stopword heads are stripped from any run: 'The Board' →
    'Board'.
    """
    phrases: List[str] = []
    words = [(m.group(), m.start()) for m in re.finditer(r"[A-Za-z][\w'-]*", text)]
    i = 0
    n = len(words)
    while i < n:
        word, pos = words[i]
        if not word[0].isupper():
            i += 1
            continue
        # Collect the maximal capitalized run starting here. A run may span a
        # single space or hyphen; anything wider (punctuation, newline,
        # sentence structure) ends it.
        run = [word]
        end_pos = pos + len(word)
        j = i + 1
        while j < n:
            w2, p2 = words[j]
            gap = text[end_pos:p2]
            if w2[0].isupper() and "\n" not in gap and len(gap) <= 1:
                run.append(w2)
                end_pos = p2 + len(w2)
                j += 1
            else:
                break
        at_sentence_start = _is_sentence_start(text, pos)
        # A multi-word run at a sentence start is trusted as a name; drop
        # only the head if it's closed-class ("The Board" → "Board"). A lone
        # word at a sentence start is grammar-capitalized — drop it entirely.
        # Calendar terms (days, months) are so common they swamp the graph;
        # treat them as stopwords for extraction purposes.
        run_words = list(run)
        if at_sentence_start and len(run_words) == 1:
            run_words = []
        while run_words and (
            run_words[0].lower() in _STOPWORDS
            or run_words[0].lower() in _TEST_DOW_MONTHS
        ):
            run_words = run_words[1:]
        # A single remaining calendar word mid-sentence is also noise.
        if len(run_words) == 1 and run_words[0].lower() in _TEST_DOW_MONTHS:
            run_words = []
        if run_words:
            phrases.append(" ".join(run_words))
        i = max(j, i + 1)
    return phrases


def _is_sentence_start(text: str, pos: int) -> bool:
    """True when the word at ``pos`` opens the text or follows sentence
    punctuation. Quotes/brackets between the punctuation and the word are
    transparent."""
    prefix = text[:pos]
    if not prefix.strip():
        return True
    # Skip leading/trailing quote and bracket characters on both sides of the
    # boundary, then look for sentence-ending punctuation.
    m = re.search(r"[.!?][\"')\]}]*\s*[\"'([{]*$", prefix)
    return bool(m)



def extract_entities(text: str,
                     max_per_chunk: int = 20) -> List[str]:
    """Extract normalized entities from one chunk's text.

    ``max_per_chunk`` caps per-chunk output; the doc-frequency filtering
    (which entities are common enough to link, rare enough to be meaningful)
    is the caller's job — corpus_filter, with counts from the store.

    Returns a deduped, stable-ordered list of normalized entities.
    """
    candidates = _find_acronyms(text) + _find_capitalized_phrases(text)
    seen: Set[str] = set()
    out: List[str] = []
    for cand in candidates:
        ent = normalize_entity(cand)
        if len(ent) < 2 or ent in _STOPWORDS or ent in seen:
            continue
        seen.add(ent)
        out.append(ent)
        if len(out) >= max_per_chunk:
            break
    return out


from collections import Counter as _Counter

# An entity is "ubiquitous" — too common to distinguish one chunk from the
# corpus at large — when it appears in more than this fraction of all chunks.
# The floor of max(2, …) inside corpus_filter keeps tiny collections from
# capping at 1 and excluding everything.
CORPUS_MAX_DOC_FREQUENCY = 0.30


def related_entities(df: Dict[str, int], max_doc_frequency: float,
                     query_entities: List[str]) -> Dict[str, str]:
    """Map each query entity to the corpus entity it can stand for.

    People don't phrase queries in the exact register of the corpus: they say
    "Alice" for "Alice Johnson" or reorder words — "Project Titan" for the
    corpus's "Titan Renewal Project". Matching is **word-overlap** between
    the query mention and the corpus entity: they must share at least one
    content word (a non-stopword token of length >= 2). That catches both
    subset ("alice" ⊂ "alice johnson") and reordered/spread mentions
    ("project titan" ↔ "titan renewal project" share "titan").

    Word-order-insensitive comparison risks collisions for very common
    surnames, so when several corpus forms overlap, the least ubiquitous
    (lowest df) wins — it's the most specific match for this query.
    Returns query_entity -> corpus_entity.
    """
    corpus = {e: c for e, c in df.items() if c >= 2}
    if not corpus:
        return {}

    def content_words(s: str) -> Set[str]:
        return {w for w in re.findall(r"[a-z0-9'-]+", s)
                if len(w) >= 2 and w not in _STOPWORDS}

    mapping: Dict[str, str] = {}
    for qe in query_entities:
        if qe in corpus:
            mapping[qe] = qe
            continue
        qw = content_words(qe)
        if not qw:
            continue
        # Overlap with any corpus entity's content words.
        candidates = [e for e in corpus
                      if content_words(e) & qw and e != qe]
        if candidates:
            mapping[qe] = min(candidates, key=lambda e: (df[e], e))
    return mapping


def corpus_filter(entity_counts: "_Counter",
                  num_chunks: int,
                  max_doc_frequency: float = CORPUS_MAX_DOC_FREQUENCY) -> Set[str]:
    """Entities kept for graph boosting: those appearing in at least 2 chunks
    (a single-chunk 'entity' can't bridge documents) and at most
    ``max_doc_frequency`` of all chunks (ubiquitous terms like the product
    name would link everything to everything)."""
    if num_chunks <= 0:
        return set()
    cap = max(2, int(num_chunks * max_doc_frequency))
    return {e for e, c in entity_counts.items() if 2 <= c <= cap}


# ── scoring ──────────────────────────────────────────────────────────────────

def graph_boost(shared_entities: Iterable[str],
                entity_scores: Dict[str, float],
                weight: float = 0.25) -> float:
    """Score boost for a chunk sharing entities with the query.

    ``entity_scores`` maps entity -> rarity weight (1 - df/max_df style,
    supplied by the store: rarer entities are more informative). The boost
    saturates with count so one spammy chunk can't dominate:

        boost = weight * Σ rarity(e) / (1 + Σ rarity(e))
    """
    s = sum(entity_scores.get(e, 0.0) for e in shared_entities)
    if s <= 0:
        return 0.0
    return weight * (s / (1.0 + s))
