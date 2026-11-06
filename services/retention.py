"""Periodic retention for the two unbounded log tables.

search_history stores full result snippets — document text — so keeping it
forever is a privacy liability, not a feature; before this task existed it
was written on every search and never deleted (or read). chat_usage is
small per row but grows with every turn. The same sweep clears uploads that
were staged but never given a job.

One asyncio task, started from main.py's lifespan: first sweep 60 s after
boot (not during startup, which is already busy loading models), then every
24 h. Deletes are cheap timestamp-indexed range scans on both backends.
"""

import asyncio
import logging

from config import settings

logger = logging.getLogger(__name__)

_FIRST_SWEEP_DELAY_S = 60
_SWEEP_INTERVAL_S = 24 * 3600


def _sweep() -> None:
    from services.app_database import app_db

    try:
        removed = app_db.delete_old_search_history(settings.search_history_retention_days)
        if removed:
            logger.info(f"Retention: removed {removed} search_history rows older than "
                        f"{settings.search_history_retention_days}d")
    except Exception as e:
        logger.warning(f"Retention sweep of search_history failed: {e}")

    try:
        removed = app_db.delete_old_chat_usage(settings.usage_retention_days)
        if removed:
            logger.info(f"Retention: removed {removed} chat_usage rows older than "
                        f"{settings.usage_retention_days}d")
    except Exception as e:
        logger.warning(f"Retention sweep of chat_usage failed: {e}")

    # The audit trail is the accountability record, so it has its own,
    # much longer window — and 0 keeps it forever.
    if settings.audit_retention_days and settings.audit_retention_days > 0:
        try:
            removed = app_db.delete_old_audit_events(settings.audit_retention_days)
            if removed:
                logger.info(f"Retention: removed {removed} audit_events rows older than "
                            f"{settings.audit_retention_days}d")
        except Exception as e:
            logger.warning(f"Retention sweep of audit_events failed: {e}")

    from services.upload_service import upload_service

    try:
        upload_service.sweep_staged_uploads()
    except Exception as e:
        logger.warning(f"Retention sweep of staged uploads failed: {e}")


async def retention_loop() -> None:
    await asyncio.sleep(_FIRST_SWEEP_DELAY_S)
    while True:
        await asyncio.to_thread(_sweep)
        await asyncio.sleep(_SWEEP_INTERVAL_S)
