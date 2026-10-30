# The name and the brand

Clio (Greek *Kleio*, from *kleos*, renown: the one who makes things known) is
the Muse of history. The facts below are the brand's source material.
Everything the product says about itself should square with them.

## Key facts

| | |
|---|---|
| **Parents** | Zeus, king of the gods, and Mnemosyne, the Titaness of memory |
| **Abode** | Mount Helicon, or Mount Parnassos, alongside her eight sister Muses |
| **Symbols** | An open scroll, a chest of books, tablets and a stylus, or a clarion (a trumpet) |
| **Role** | She inspired writers, poets and historians to record heroic deeds, great events and the achievements of the past so they would not be forgotten |
| **Offspring** | Mother of Hyacinthus (with King Pierus, or Oebalus) and of Hymenaeus |

## What they mean in the product

| The Muse | Clio the tool |
|---|---|
| Daughter of Memory | The index is memory made searchable. Every passage is kept, and every answer is traced back to the passage it came from. |
| The open scroll | The passage, open at the page. A citation leads to the source itself, never to a paraphrase of it. |
| The chest of books | A collection: the unit you organise, share, export, and scope a token to. |
| Tablets and a stylus | People and agents can write back (notes, summaries, saved instructions), and every write is attributed and audited. |
| The clarion | The Updates feed, the bell and the webhook. When a source changes, the record says so. |
| Helicon, with eight sisters | One server, many agents. A single endpoint serves every MCP client side by side, each seeing only what its token allows. |
| Her role | The tagline. Clio's job is to make sure what was recorded can be found and verified, not to invent what was never written down. |

Her children have no product meaning; they are recorded for completeness.

## Voice

- **Tagline:** *The record remembers.* **Strapline:** *Ask your sources. Check
  the evidence. Connect your AI tools.*
- Clio is the keeper of the record, not an oracle. The product says what the
  sources establish and says plainly when they do not. It cites; it never
  claims to know.
- Capable, simple, intelligent (see [.impeccable.md](../.impeccable.md)). No
  performative dashboards, no narration, no "cyber AI" aesthetic.

## Where the name is explained

One sentence each, where the name is first met, and nowhere in a working
surface:

- the README introduction,
- the About dialog (`frontend/src/components/AboutDialog.vue`),
- the API description on `/docs` (`main.py`),
- the Open Graph description in `frontend/index.html`.

## Iconography and assets

- Imagery, when any is needed, draws on her symbols: scroll, tablet, stylus,
  chest of books. Never robots, brains, sparkles or neon.
- The Muse herself is not drawn. The mark is abstract gradient artwork; the
  sources live in `frontend/brand/` (not served) and
  `scripts/build_brand_assets.py` derives the favicon, the light and dark
  marks, the maskable icon and the Open Graph image from them.
- The mark must read in every DaisyUI theme, which is why it is keyed to
  transparency and lifted for dark surfaces rather than recoloured.
