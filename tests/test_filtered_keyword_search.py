"""B5: a filtered keyword search applies the filter inside the query.

It used to ask the keyword index for every match in the collection and
filter in Python. On a 200,000-chunk index a filtered search took 1.0-1.5 s;
with the filter joined into the query it takes 0.15-0.2 s. ("rowid IN
(subquery)" was tried and is far slower: FTS5 cannot use it.)"""

from services.bm25_service import BM25Index


def _index(tmp_path):
    ix = BM25Index(tmp_path / "bm25.db")
    ix.add_documents_batch([(f"c{i}", f"backup retention note {i}" + (" backup" * (i % 4))) for i in range(40)]
                           + [(f"x{i}", "nothing relevant here") for i in range(20)])
    return ix


def test_the_filter_gives_what_filtering_every_match_gave(tmp_path):
    ix = _index(tmp_path)
    allowed = {f"c{i}" for i in range(0, 40, 3)} | {"x1"}

    old = [(c, s) for c, s in ix.search("backup retention", 1000) if c in allowed][:5]
    new = ix.search("backup retention", 5, allowed_chunk_ids=allowed)

    assert [c for c, _ in new] == [c for c, _ in old]
    assert [round(s, 6) for _, s in new] == [round(s, 6) for _, s in old]


def test_an_empty_filter_finds_nothing_and_no_filter_finds_everything(tmp_path):
    ix = _index(tmp_path)
    assert ix.search("backup", 5, allowed_chunk_ids=set()) == []
    assert len(ix.search("backup", 50)) == 40


def test_repeated_filtered_searches_do_not_leak_into_each_other(tmp_path):
    ix = _index(tmp_path)
    assert {c for c, _ in ix.search("backup", 50, allowed_chunk_ids={"c1", "c2"})} == {"c1", "c2"}
    assert {c for c, _ in ix.search("backup", 50, allowed_chunk_ids={"c3"})} == {"c3"}
