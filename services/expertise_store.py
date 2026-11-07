"""Expertise Library store — CRUD for expertise packs and collection attachments.

Each expertise pack is a named, markdown-body blob that advisors write once
and attach to one or more collections.  When the chat engine composes a prompt
for a collection it loads the attached packs and places their bodies in the
system prompt as delimited, labelled context (see :mod:`authored_text`): the
text is written by people, so it is data about how to analyse, never a way to
change the assistant's rules.  A pack is scanned for prompt-injection wording
on save; the verdict is stored in ``flag`` (JSON, ``''`` = clean, ``NULL`` =
written before scanning existed).

Storage lives in the same ``DATA_DIR/app.db`` SQLite used by :mod:`app_database`
so no extra migration machinery is needed.
"""

import logging
import sqlite3
import uuid
from datetime import datetime
from services.clock import utcnow
from pathlib import Path
from typing import Optional

from config import settings
from services import authored_text
from services.sqlite_utils import sqlite_connect
from models.schemas import (
    ExpertisePack,
    ExpertisePackCreate,
    ExpertisePackUpdate,
)

logger = logging.getLogger(__name__)

# Where packs lived before the store followed DATA_DIR: relative to the working
# directory, whatever DATA_DIR said.
_OLD_DB_PATH = Path("data/app.db")


class ExpertiseStore:
    """Thin CRUD layer over the app SQLite database for expertise packs."""

    def __init__(self, db_path: Optional[Path] = None):
        follows_data_dir = db_path is None
        self.db_path = Path(settings.data_dir) / "app.db" if follows_data_dir else db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        first_start_here = not self._has_tables()
        self._ensure_tables()
        if follows_data_dir and first_start_here:
            self._copy_packs_from(_OLD_DB_PATH)

    def _has_tables(self) -> bool:
        with sqlite_connect(self.db_path) as conn:
            return conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'expertise_packs'"
            ).fetchone() is not None

    def _copy_packs_from(self, old_path: Path) -> None:
        """Bring packs across from the old location, once.

        Runs only when this database has never held the store's tables, so a
        pack deleted later does not come back. The old file is only read: it
        may be someone's backup, or another install's working database. A
        failure here costs the old packs, not the server's start.
        """
        if not old_path.is_file() or old_path.resolve() == self.db_path.resolve():
            return
        try:
            old = sqlite3.connect(f"{old_path.resolve().as_uri()}?mode=ro", uri=True)
            try:
                tables = {row[0] for row in old.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )}
                if "expertise_packs" not in tables:
                    return
                packs = old.execute(
                    f"SELECT {self._COLUMNS} FROM expertise_packs"
                ).fetchall()
                links = old.execute(
                    "SELECT collection_id, pack_id, attached_at FROM collection_expertise"
                ).fetchall()
            finally:
                old.close()
            with sqlite_connect(self.db_path) as conn:
                conn.executemany(
                    f"INSERT OR IGNORE INTO expertise_packs ({self._COLUMNS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    packs,
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO collection_expertise VALUES (?, ?, ?)",
                    links,
                )
                conn.commit()
        except sqlite3.Error as e:
            logger.warning(f"Could not copy expertise packs from {old_path}: {e}")
            return
        if packs:
            logger.info(f"Copied {len(packs)} expertise pack(s) from {old_path} to {self.db_path}")

    def _ensure_tables(self) -> None:
        """Create expertise tables if they don't exist yet.

        Safe to call on every startup — all statements use IF NOT EXISTS.
        """
        with sqlite_connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS expertise_packs (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    description TEXT,
                    body        TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)
            try:
                conn.execute("ALTER TABLE expertise_packs ADD COLUMN flag TEXT")
            except sqlite3.OperationalError:
                pass  # already there
            conn.execute("""
                CREATE TABLE IF NOT EXISTS collection_expertise (
                    collection_id TEXT NOT NULL,
                    pack_id       TEXT NOT NULL,
                    attached_at   TEXT NOT NULL,
                    PRIMARY KEY (collection_id, pack_id),
                    FOREIGN KEY (pack_id) REFERENCES expertise_packs(id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_collection_expertise_collection
                ON collection_expertise(collection_id)
            """)
            conn.commit()

    # ── internal helpers ─────────────────────────────────────────────────────

    _COLUMNS = "id, name, description, body, created_at, updated_at, flag"

    def _row_to_pack(self, row: tuple) -> ExpertisePack:
        """Convert a DB row tuple into an ExpertisePack model.

        A pack saved before scanning existed has no verdict (NULL) and is
        scanned here, so old text is not a blind spot.
        """
        id_, name, description, body, created_at, updated_at, raw_flag = row
        flag = authored_text.effective_flag(
            authored_text.pack_scan_text(name, description, body),
            authored_text.load_flag(raw_flag),
            scanned=raw_flag is not None,
        )
        return ExpertisePack(
            id=id_,
            name=name,
            description=description,
            body=body,
            created_at=datetime.fromisoformat(created_at),
            updated_at=datetime.fromisoformat(updated_at),
            flag=flag,
        )

    # ── pack CRUD ─────────────────────────────────────────────────────────────

    def list_packs(self) -> list[ExpertisePack]:
        """Return all expertise packs ordered by name."""
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                f"SELECT {self._COLUMNS} "
                "FROM expertise_packs ORDER BY name COLLATE NOCASE"
            )
            return [self._row_to_pack(row) for row in cursor.fetchall()]

    def get_pack(self, pack_id: str) -> Optional[ExpertisePack]:
        """Return a single pack or None if not found."""
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                f"SELECT {self._COLUMNS} "
                "FROM expertise_packs WHERE id = ?",
                (pack_id,),
            )
            row = cursor.fetchone()
            return self._row_to_pack(row) if row else None

    def create_pack(self, data: ExpertisePackCreate) -> ExpertisePack:
        """Insert a new expertise pack and return it."""
        now = utcnow().isoformat()
        pack_id = str(uuid.uuid4())
        flag = authored_text.scan(
            authored_text.pack_scan_text(data.name, data.description, data.body), f"expertise pack {pack_id}",
        )
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO expertise_packs (id, name, description, body, created_at, updated_at, flag) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pack_id, data.name, data.description, data.body, now, now, authored_text.dump_flag(flag)),
            )
            conn.commit()
        logger.info("Created expertise pack %s (%s)", pack_id, data.name)
        return ExpertisePack(
            id=pack_id,
            name=data.name,
            description=data.description,
            body=data.body,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
            flag=flag,
        )

    def update_pack(self, pack_id: str, data: ExpertisePackUpdate) -> Optional[ExpertisePack]:
        """Apply partial updates to an existing pack and return the updated record."""
        pack = self.get_pack(pack_id)
        if pack is None:
            return None

        now = utcnow().isoformat()
        new_name = data.name if data.name is not None else pack.name
        new_desc = data.description if data.description is not None else pack.description
        new_body = data.body if data.body is not None else pack.body

        flag = authored_text.scan(
            authored_text.pack_scan_text(new_name, new_desc, new_body), f"expertise pack {pack_id}",
        )
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                "UPDATE expertise_packs SET name=?, description=?, body=?, updated_at=?, flag=? WHERE id=?",
                (new_name, new_desc, new_body, now, authored_text.dump_flag(flag), pack_id),
            )
            conn.commit()

        logger.info("Updated expertise pack %s", pack_id)
        return ExpertisePack(
            id=pack_id,
            name=new_name,
            description=new_desc,
            body=new_body,
            created_at=pack.created_at,
            updated_at=datetime.fromisoformat(now),
            flag=flag,
        )

    def delete_pack(self, pack_id: str) -> bool:
        """Delete a pack (and its collection links via CASCADE). Returns True if deleted."""
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM expertise_packs WHERE id = ?", (pack_id,)
            )
            conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("Deleted expertise pack %s", pack_id)
        return deleted

    # ── collection attachment ─────────────────────────────────────────────────

    def get_packs_for_collection(self, collection_id: str) -> list[ExpertisePack]:
        """Return all packs currently attached to a collection."""
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT ep.id, ep.name, ep.description, ep.body, ep.created_at, ep.updated_at, ep.flag
                FROM expertise_packs ep
                JOIN collection_expertise ce ON ce.pack_id = ep.id
                WHERE ce.collection_id = ?
                ORDER BY ep.name COLLATE NOCASE
                """,
                (collection_id,),
            )
            return [self._row_to_pack(row) for row in cursor.fetchall()]

    def get_attached_pack_ids(self, collection_id: str) -> list[str]:
        """Return only the pack IDs attached to a collection (lighter than full objects)."""
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT pack_id FROM collection_expertise WHERE collection_id = ?",
                (collection_id,),
            )
            return [row[0] for row in cursor.fetchall()]

    def collections_using(self, pack_id: str) -> list[str]:
        """Ids of the collections a pack is attached to (audit detail)."""
        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT collection_id FROM collection_expertise WHERE pack_id = ?", (pack_id,)
            )
            return [row[0] for row in cursor.fetchall()]

    def set_packs_for_collection(self, collection_id: str, pack_ids: list[str]) -> None:
        """Replace the full set of packs attached to a collection atomically."""
        now = utcnow().isoformat()
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM collection_expertise WHERE collection_id = ?",
                (collection_id,),
            )
            conn.executemany(
                "INSERT INTO collection_expertise (collection_id, pack_id, attached_at) VALUES (?, ?, ?)",
                [(collection_id, pid, now) for pid in pack_ids],
            )
            conn.commit()
        logger.info(
            "Set expertise packs for collection %s: %s",
            collection_id,
            pack_ids,
        )
