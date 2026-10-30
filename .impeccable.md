## Design Context

### Users
Mixed audience — solo researchers, developers exploring their own notes and code, and small teams in regulated environments all share the same surface. They come from different contexts but want the same thing: fast, trustworthy answers grounded in documents they already own. Clio runs as a web app, from a local Python install or Docker through to on-prem and air-gapped deployments, so the UI has to feel at home in a browser tab next to other tools as well as on a locked-down shared server.

### Job To Be Done
Get answers from your own resources, faster. The product is measured by time-to-answer, not time-on-task. Every interaction should either (a) advance the user toward a specific passage, citation, or synthesized answer, or (b) get out of the way. Secondary jobs — managing collections, tuning OCR, inspecting tokenization — are tools in service of that primary goal and should be discoverable without competing with it.

### Brand Personality
Three words: **Capable. Simple. Intelligent.**

- **Capable** — the product does real work (hybrid retrieval, OCR, multi-format ingest, MCP, enterprise deploy). The UI should telegraph competence without flexing. No performative dashboards.
- **Simple** — surfaces are quiet; affordances earn their place. Reading matters more than chrome. A new user should be able to ignore 80% of the shell and still get an answer.
- **Intelligent** — the product understands the user's intent and meets it with the right tool. The UI respects that by not over-explaining, not repeating itself, and not wrapping every action in tutorial copy.

Emotional target: trust, focus, quiet confidence. The user should feel like they have a capable collaborator, not a product demo.

### The Name
Clio is the Muse of history: daughter of Zeus and of Mnemosyne, the Titaness of memory; at home on Mount Helicon (or Parnassos) with her eight sisters; shown with an open scroll, a chest of books, tablets and a stylus, or a clarion. Her role was to inspire writers and historians to record great deeds and events so they would not be forgotten. Tagline: *The record remembers.* The product is the keeper of the record, not an oracle: it says what the sources establish and says when they do not. Iconography, when it is needed, draws on her symbols (scroll, tablet, stylus, chest of books), never on robots, brains or sparkles; the Muse herself is not drawn, and the mythology is explained in one sentence where the name is introduced (README, About dialog), not narrated in working surfaces. Full notes: `docs/BRAND.md`.

### Aesthetic Direction
**Reference**: NotebookLM — a study/research companion, not a SaaS dashboard. What to borrow from NotebookLM specifically: the document is the center of attention; source citations are first-class and always reachable; chrome recedes; the product feels like a reading room with tools on the wall rather than an admin console.

**Anti-reference**: generic SaaS admin dashboard. That means no hero metric grids, no card-grid-of-cards, no "welcome back, here's your activity" landing pages, no big rounded stat tiles, no sidebar of gradient nav pills. Also no "cyber AI" aesthetic — neon-on-black with glowing cyan accents is the opposite of a research companion.

**Theme**: DaisyUI's full theme catalog stays enabled — theme choice is a user preference, not a brand statement. The implication is that Clio's own design must be *theme-agnostic*: it cannot rely on a specific accent color, contrast story, or dark/light assumption to look good. Everything has to work in `cupcake` and `dracula` alike. This is a meaningful constraint — it means the design language comes from layout, rhythm, typography, and restraint, not from palette.

### Design Principles

1. **The document is the subject; the app is the room.** Chrome, navigation, and controls should recede to let source content, answers, and citations lead. When in doubt, remove a border, not add one.

2. **Earn every pixel.** Before adding a panel, badge, icon, or header, ask whether it moves the user closer to an answer. If not, cut it. Density is fine; ornament is not.

3. **Theme-agnostic by construction.** Use DaisyUI semantic tokens (`base-100/200/300`, `base-content`, `primary`, `accent`, etc.) — never hard-coded colors. Never assume light or dark. Never rely on a specific accent hue for meaning; use shape, weight, position, and iconography instead.

4. **Reading-first typography and rhythm.** This is a product for people who read for a living. Generous line-height in long-form areas (chat, answers, passages), tight and functional in tool surfaces. A clear type hierarchy with real contrast between steps. No monospace-as-personality.

5. **Progressive disclosure over tabs of tabs.** The primary job (ask → answer → cite) should be reachable in one surface. Everything else — search, generation, expertise packs, MCP — supports that job and should stay visually secondary to the chat surface.

6. **Intelligent by silence.** The product shouldn't narrate itself. No redundant headers, no "Welcome to X" banners, no onboarding copy on return visits, no button that says "Click to search" next to a search box. Fewer words, more signal.
