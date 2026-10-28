"""Test support for the retrieval coverage report: a synthetic collection built
on the real VectorStore / MetadataStore / DocumentIndexer, with a small
deterministic bag-of-words embedder instead of a downloaded model.

Nothing here is real user data: every document is invented filler on a
distinct everyday subject.
"""

import hashlib
import re
from unittest.mock import MagicMock

import numpy as np

from models.schemas import ChunkMetadata
from services.indexing import DocumentIndexer
from services.vector_store import VectorStore

DIM = 256

_TOKEN = re.compile(r"[a-z]{3,}")


class BagEmbedder:
    """Hashed bag-of-words vectors: cosine similarity tracks shared words."""

    model_name = "bag-of-words-test"
    embedding_dim = DIM

    @staticmethod
    def _vec(text: str) -> np.ndarray:
        v = np.zeros(DIM, dtype=np.float32)
        for tok in _TOKEN.findall(text.lower()):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % DIM] += 1.0
        v[0] += 1e-3        # never an all-zero vector (normalisation divides by the norm)
        return v

    def embed_texts(self, texts, progress_callback=None):
        return np.stack([self._vec(t) for t in texts])

    def embed_query(self, query):
        return self._vec(query)


# Six invented subjects, three chunks each. Each chunk carries its own nouns.
TOPICAL = {
    "hydroponics.txt": [
        "Hydroponic lettuce grows in a nutrient reservoir where an air pump keeps the roots oxygenated. "
        "Check the reservoir pH every morning and top up the nutrient solution weekly.",
        "Seedlings begin in rockwool cubes under a fluorescent grow light. Transplant them into the "
        "net pots once the first true leaves open, and keep the light eighteen hours daily.",
        "Tipburn on lettuce leaves usually means calcium is not reaching the growing tips. Slow the "
        "reservoir temperature and add a small fan to move air across the canopy.",
    ],
    "bridge-inspection.txt": [
        "Bridge inspectors examine the steel girders for corrosion near the bearing seats. Record "
        "section loss in millimetres and photograph every rivet showing rust bleed.",
        "Concrete decks are sounded with a chain drag to find delamination. Mark each hollow area "
        "with paint and note the square metres affected in the deck survey form.",
        "Scour around the river piers is measured with a sonar sled after each flood. Compare the "
        "channel profile with last year's survey before signing off the pier foundations.",
    ],
    "sourdough.txt": [
        "A sourdough starter is fed equal weights of flour and water every twelve hours. When it "
        "doubles within six hours the culture is strong enough to leaven a loaf.",
        "Shape the dough into a tight boule and proof it in a floured banneton overnight in the "
        "refrigerator. Slash the crust with a razor just before it goes into the Dutch oven.",
        "Bake the loaf covered for twenty minutes to trap steam, then uncovered until the crust is "
        "deep brown. Let it cool completely on a rack before slicing the crumb.",
    ],
    "telescope-optics.txt": [
        "A Newtonian telescope collimation starts with centring the secondary mirror under the "
        "focuser. Use a Cheshire eyepiece to line the primary mirror clips with the optical axis.",
        "Star testing shows spherical aberration as unequal rings inside and outside focus. Defocus "
        "a bright star at high magnification and compare the diffraction patterns on both sides.",
        "Dew shields stop condensation on the corrector plate during humid nights. A heater strap "
        "powered by a twelve volt battery keeps the eyepiece clear until dawn.",
    ],
    "beekeeping.txt": [
        "Inspect the brood frames every ten days in spring and look for fresh eggs standing upright. "
        "A queen cell hanging from the bottom bar can signal an approaching swarm.",
        "Treat the hive for varroa mites with oxalic acid vapour while the colony is broodless in "
        "winter. Count the mite drop on the sticky board two days later.",
        "Honey supers are added when seven frames of the brood box are covered with bees. Extract "
        "only capped honey, since uncapped honey ferments and spoils in the jar.",
    ],
    "canal-locks.txt": [
        "A canal lock raises a boat by filling the chamber through paddles in the lower gates. Close "
        "the tail gate first and wind the ground paddles slowly to avoid turbulence.",
        "Lock keepers grease the balance beams each autumn and replace worn leather cup seals on the "
        "ground paddle spindles. Check the cill for debris before closing the gates.",
        "Towpath bridges on the narrow canal have rope grooves worn by horses. The grooves are "
        "recorded as heritage features during the annual canal conservation survey.",
    ],
}

# Boilerplate every topical document also carries, as a final chunk of its own.
NOTICE = [
    "This notice applies to every page. Please read the notice carefully before relying on the "
    "information given, and contact the office if the notice is unclear.",
]


def build_collection(tmp_path, docs, *, quarantined=(), embedder=None):
    """Index ``docs`` ({filename: [chunk texts]}) and return the DocumentIndexer.

    Document ids are ``doc-<n>`` in insertion order. Chunks go straight into
    the real vector store, BM25 index and metadata store - no chunker - so each
    test controls exactly which text is a chunk.
    """
    embedder = embedder or BagEmbedder()
    store = VectorStore(index_dir=tmp_path / "indexes", embedding_dim=DIM)
    chunks, rows = [], []
    for n, (filename, texts) in enumerate(docs.items(), start=1):
        doc_id = f"doc-{n}"
        for i, text in enumerate(texts, start=1):
            chunks.append(ChunkMetadata(
                chunk_id=f"{doc_id}#{i}", document_id=doc_id, filename=filename,
                page_number=i, chunk_index=0, text=text,
                source_format="txt", extraction_method="text"))
        rows.append(dict(
            document_id=doc_id, filename=filename, num_pages=len(texts), num_chunks=len(texts),
            upload_timestamp=f"2026-04-{n:02d}T09:00:00",
            policy_status="quarantined" if filename in quarantined else "clear",
            source_format="txt"))
    store.add_chunks(chunks, embedder.embed_texts([c.text for c in chunks])) if chunks else None
    store.metadata_store.add_documents(rows)
    return DocumentIndexer(vector_store=store, embedding_service=embedder,
                           document_extractor=MagicMock(), text_chunker=MagicMock())
