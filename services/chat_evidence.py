"""Turn-local citation numbering shared by initial retrieval and agent searches.

Only consumes results already authorized by the existing retrieval/tool gates.
It never fetches documents or interprets a model-authored citation as evidence.
"""

from types import SimpleNamespace


CITATION_INSTRUCTIONS = (
    "Cite document claims with [Source N], using only the numbers supplied in "
    "the context or the citation fields of tool results. Search ranks are not "
    "citation numbers. Never invent a source number. If evidence is incomplete "
    "or conflicting, explain the gap or conflict. Distinguish what a source "
    "states from your interpretation. A source marked OLDER VERSION (or a "
    "result with superseded_by) comes from a document that a newer one "
    "replaced: prefer the current version, and when you do cite the older "
    "one, say that it is an older version and name its replacement. Do not "
    "treat copies of the same text (\"also in\" / duplicates) as separate "
    "confirmations."
)


class ChatEvidence:
    def __init__(self, initial_results):
        # Preserve initial order, including duplicates: it defines prompt IDs.
        self.results = list(initial_results)
        self._numbers = {}
        for number, (result, collection_id) in enumerate(self.results, 1):
            self._numbers.setdefault(self._key(result, collection_id), number)

    @staticmethod
    def _key(result, collection_id):
        return (collection_id, result.document_id, result.page_number, result.text_snippet)

    def observe(self, tool_results):
        """Annotate authorized prose passages before returning them to the model.

        Tables deliberately keep their native provenance rather than receiving
        a made-up prose excerpt. Empty/failed results never become evidence.
        """
        for item in tool_results:
            payload = item.get("result")
            if item.get("error") or not isinstance(payload, dict):
                continue
            collection_id = payload.get("collection_id") or (item.get("args") or {}).get("collection_id")
            if not collection_id:
                continue
            if item.get("tool") in ("search_documents", "research_documents"):
                for passage in payload.get("results") or []:
                    self._register(passage, collection_id, passage, "excerpt")
            elif item.get("tool") == "find_in_documents":
                for passage in payload.get("matches") or []:
                    self._register(passage, collection_id, passage, "excerpt")
            elif item.get("tool") == "get_document_context":
                for passage in payload.get("chunks") or []:
                    self._register(passage, collection_id, payload, "text")

    def _register(self, passage, collection_id, document, text_field):
        text = passage.get(text_field)
        if not text or not document.get("document_id") or not document.get("filename"):
            return
        result = SimpleNamespace(
            document_id=document["document_id"], filename=document["filename"],
            page_number=passage.get("page_number") or 1, text_snippet=text,
            similarity_score=passage.get("similarity_score") or 0.0,
            sensitivity=document.get("sensitivity"),
            superseded_by=passage.get("superseded_by"),
            duplicates=passage.get("duplicates"),
            duplicate_count=passage.get("duplicate_count"),
        )
        key = self._key(result, collection_id)
        number = self._numbers.get(key)
        if number is None:
            self.results.append((result, collection_id))
            number = len(self.results)
            self._numbers[key] = number
        passage["citation"] = f"[Source {number}]"
