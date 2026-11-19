"""B2: passages that know where they are, under a per-collection recipe.

Recipe 2 carries the heading from one page to the next, finishes a sentence
cut by a page break on the page it starts on, and embeds each passage under
a line naming its source. A collection indexed before recipes existed keeps
recipe 1 until it is rebuilt from empty, so its results do not change."""

from unittest.mock import MagicMock

import numpy as np

from models.schemas import ChunkMetadata
from services.chunker import TextChunker
from services.document_extractor import DocumentExtractor
from services.indexing import embedding_text
from services.indexing.indexer import DocumentIndexer
from services.vector_store import VectorStore

PAGES = {
    1: "Travel Policy\n\n3 Daily Allowances\n\nThe overnight allowance is 55 dollars. A day trip attracts",
    2: "18 dollars for lunch. Receipts are kept.\n\nThey must reach accounts payable within 60 days.",
}


def test_recipe_1_chunks_pages_as_before():
    chunks = TextChunker(1000, 200).chunk_document(PAGES, "d", "f.pdf")
    assert [c.text for c in chunks] == [PAGES[1], PAGES[2]]


def test_recipe_2_finishes_the_sentence_and_carries_the_heading():
    chunks = TextChunker(1000, 200, continuous=True).chunk_document(PAGES, "d", "f.pdf")

    assert chunks[0].page_number == 1
    assert chunks[0].text.endswith("A day trip attracts 18 dollars for lunch.")
    assert chunks[1].page_number == 2
    assert chunks[1].text.startswith("3 Daily Allowances\nReceipts are kept.")


def test_a_page_that_starts_with_a_heading_takes_no_sentence_from_it():
    pages = {1: "Intro text without an end", 2: "4 Mileage\n\nCars are repaid at 67 cents."}
    chunks = TextChunker(1000, 200, continuous=True).chunk_document(pages, "d", "f.pdf")
    assert [c.text for c in chunks] == ["Intro text without an end", "4 Mileage\n\nCars are repaid at 67 cents."]


def test_the_embedded_text_names_the_source_and_the_stored_text_does_not_change():
    chunk = ChunkMetadata(chunk_id="c", document_id="d", filename="harbor-travel_policy rev-c.pdf",
                          page_number=2, chunk_index=0, text="The limit is 30 days.")
    assert embedding_text.texts_for_embedding([chunk], recipe=1) == ["The limit is 30 days."]
    assert embedding_text.texts_for_embedding([chunk], recipe=2) == [
        "harbor travel policy rev c\nThe limit is 30 days."]
    assert embedding_text.texts_for_embedding([{"text": "x", "filename": "a.md"}], recipe=2) == ["a\nx"]
    assert chunk.text == "The limit is 30 days."


class _Recorder:
    model_name = "rec"
    embedding_dim = 8

    def __init__(self):
        self.seen = []

    def embed_texts(self, texts, progress_callback=None):
        self.seen.extend(texts)
        return np.ones((len(texts), self.embedding_dim), dtype=np.float32)

    def embed_query(self, text):
        return np.ones(self.embedding_dim, dtype=np.float32)


def _indexer(tmp_path, store=None):
    embedder = _Recorder()
    ix = DocumentIndexer(vector_store=store or VectorStore(tmp_path / "ix", embedding_dim=8),
                         embedding_service=embedder, document_extractor=DocumentExtractor(),
                         text_chunker=TextChunker(1000, 200))
    return ix, embedder


def test_a_new_collection_is_indexed_with_the_current_recipe(tmp_path):
    ix, embedder = _indexer(tmp_path)
    doc = tmp_path / "harbor-policy.md"
    doc.write_text("# Allowances\n\nThe overnight allowance is 55 dollars.\n", encoding="utf-8")

    ix.index_document(doc, doc.name, collection_id="c")

    assert ix.vector_store.metadata_store.get_index_info()["recipe"] == embedding_text.CURRENT_RECIPE
    assert embedder.seen and all(t.startswith("harbor policy\n") for t in embedder.seen)


def test_a_collection_indexed_before_recipes_keeps_recipe_1(tmp_path):
    store = VectorStore(tmp_path / "old", embedding_dim=8)
    old = ChunkMetadata(chunk_id="o#1", document_id="o", filename="old.md", page_number=1, chunk_index=0,
                        text="Indexed long ago.")
    store.add_chunks([old], np.ones((1, 8), dtype=np.float32))
    ix, embedder = _indexer(tmp_path, store)
    doc = tmp_path / "new.md"
    doc.write_text("Added today.\n", encoding="utf-8")

    ix.index_document(doc, doc.name, collection_id="c")
    ix.rebuild_vector_index()

    assert ix.index_recipe == embedding_text.PLAIN
    assert "recipe" not in store.metadata_store.get_index_info()
    assert all("\n" not in t for t in embedder.seen), embedder.seen


def test_the_reranker_reads_what_the_search_model_read(tmp_path):
    ix, _ = _indexer(tmp_path)
    embedding_text.write_recipe(ix.vector_store.metadata_store, embedding_text.HEADED)
    result = MagicMock(text_snippet="The limit is 30 days.", filename="harbor-policy.pdf")
    assert ix._rerank_text_getter()(result) == "harbor policy\nThe limit is 30 days."

    old, _ = _indexer(tmp_path / "o")
    store = old.vector_store
    store.add_chunks([ChunkMetadata(chunk_id="x", document_id="x", filename="x.md", page_number=1,
                                    chunk_index=0, text="t")], np.ones((1, 8), dtype=np.float32))
    assert old._rerank_text_getter() is None
