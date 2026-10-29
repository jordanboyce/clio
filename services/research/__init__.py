"""Research workspace: a library of papers on top of an ordinary collection.

Lab researchers asked for four things: decide quickly whether a paper is worth
reading, keep papers findable under descriptive names, pull in what a paper
cites, and come back to a prepared review instead of starting cold. Every one
of them is built on the same rule as the rest of Clio — the paper is the
evidence — and on a stricter one, because the first worry researchers raise is
fabrication:

* Metadata (title, authors, year, DOI, abstract, references) is never written
  by a model. It is read from the paper's own text (``extract``) or from a
  scholarly registry looked up by an identifier printed in the paper
  (``lookup``). A registry answer found by title search is accepted only when
  its title matches the paper's.
* Anything a model writes about a paper (``relevance``, ``briefs``) must quote
  the paper, and ``grounding`` checks every quote against the passage it cites
  before the statement is kept. A statement whose quote is not in the paper is
  dropped and counted, never shown as fact.
* The citation graph (``graph``) only draws an edge that a parsed reference
  supports, and says how each edge was matched.

``store`` keeps all of it in the collection's own metadata database, so a
collection export, backup or delete carries the research layer with it.
"""
