"""Export a collection so a static website can search it with no server.

The export is a folder (or a zip of one):

    manifest.json    which model made the vectors, how to read the files
    passages.json    one passage per line: id, document_id, page, text
                     (passages-0001.json, ... when there are many)
    vectors.bin      one vector per passage, in the same order, 8-bit

A page loads the three, embeds the visitor's query in the browser with the
same model, and ranks by the dot product. docs/static-search/ holds a
reference client; docs/STATIC_SEARCH.md is the guide.

With ``site=True`` the export is a whole site instead, ready to serve: the
reference page, the files above under search/, and everything the page needs
to embed a query with no outside service: transformers.js and its
WebAssembly runtime under vendor/ (baked into the image at build time, see
scripts/vendor_static_search.mjs), and under models/ the very ONNX file Clio
embeds with. That is what a network that blocks Hugging Face and the CDNs
needs, and since it is the same file, the probe matches exactly.

Three things make this different from a collection bundle
(services/collection_bundle.py):

Same model on both sides. A query vector only means something next to
passage vectors from the same model, so the export is refused unless the
collection was embedded on this machine with a model that also has a browser
build whose vectors were checked against Clio's (``BROWSER_MODELS``). The
manifest records the model, its pooling and its query prefix, and carries a
probe: a fixed sentence with the vector Clio's own model gives it. A client
embeds the sentence itself and refuses to search by meaning when the two
disagree, which catches a wrong model, wrong pooling, a missing prefix and a
quantised model build alike.

Everything in it is published. The text goes to every visitor and the vectors
give away its gist, so this is the most outside of all outside services
(services/data_controls.py). A collection that holds anything local only is
refused whole, as everywhere else. Beyond that the collection must be
labelled Public, sources with a stricter label of their own and held sources
are left out, and the caller has to say the collection is cleared for
release. Nothing here calls a network: it reads the index on disk.

Reviewable. Passages are ordered by source name, page and position, their ids
are Clio's chunk ids, and each sits on its own line, so a year's export
diffs cleanly against the last one in the site's repository.

An asset for a website. Each source in the manifest says where it lives on
the web (``link``): the address someone gave it, the page a link source came
from, the file at its commit for a source synced from a public repository, or
the collection's base address for files. The client turns that into a link to
the passage (a PDF's page, a web page's text, a file's lines). A collection
whose model has no browser build is exported for keyword search (format v2,
no vectors) rather than refused, so any collection that may be published can
be. A whole site also carries <clio-search>, the element a host site drops
into its own pages, and settings for the common web servers.
"""

from __future__ import annotations

# The package's parts, each importable on its own. Everything a caller used to
# import from the single module is still here. Functions a test replaces
# (site_runtime_dirs, site_model, _repositories) are looked up through this
# package by the parts that call them, so replacing them here takes effect.
from services.static_export.common import (  # noqa: F401
    BROWSER_MODELS, DEFAULT_PASSAGES_PER_FILE, DESTINATION, FORMAT, KEYWORD_VERSION, MANIFEST_FILE,
    PASSAGES_FILE, PROBE_TEXT, QUANTIZATIONS, SITE_DATA_DIR, SITE_HOSTING_DIR, SITE_HOSTING_FILES,
    SITE_MODEL_FILES, SITE_PAGE_DIR, SITE_PAGE_FILES, SITE_RUNTIME_FILES, SITE_RUNTIME_IMAGE_DIR, VECTORS_FILE,
    VERSION, StaticExportError, _sha256_file, _supported, _title, browser_model,
)
from services.static_export.addresses import (  # noqa: F401
    MAX_ADDRESS_CHARS, _repositories, _repository_link, address_settings, addresses, clean_public_url,
    document_link, repository_file_link, save_addresses,
)
from services.static_export.site import (  # noqa: F401
    _page_bytes, _site_offer, _write_site, site_model, site_runtime, site_runtime_dirs,
)
from services.static_export.gate import _index_info, _open, _survey, preview  # noqa: F401
from services.static_export.vectors import _probe, _read_vectors, _semantic_entries, quantize  # noqa: F401
from services.static_export.writer import (  # noqa: F401
    _document_entry, _library_titles, _manifest_text, _passage_file_name, clear_previous, export_zip,
    write_export,
)
