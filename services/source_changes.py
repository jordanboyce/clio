"""The Updates feed: what changed in a collection's sources, and who hears about it.

Every sync of a remembered source (folder or git repository) that actually
adds, replaces or removes something leaves one event here. The feed is
per-collection, newest last, capped, and stored through the same key/value
config table as the rest of the sync memory, so it works on SQLite and
Postgres alike and needs no schema migration.

Clio has no accounts, so "unread" is the browser's business: it remembers the
last event id it showed. The server's job is a stable, monotonically
increasing id per collection that a client (the UI bell, an MCP agent, a
script) can ask "anything after N?" about.

Delivery beyond the feed is one optional webhook (``ALERT_WEBHOOK_URL``).
Posting never blocks or fails a sync.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import settings
from services.app_database import app_db

logger = logging.getLogger(__name__)

MAX_EVENTS = 200
MAX_NAMES = 50
MAX_COMMITS = 20

_lock = threading.Lock()


def _key(collection_id: str) -> str:
    return f"source_changes:{collection_id}"


def _load(collection_id: str) -> Dict[str, Any]:
    data = app_db.get_config(_key(collection_id), None)
    if not isinstance(data, dict):
        return {"next_id": 1, "events": []}
    data.setdefault("next_id", 1)
    data.setdefault("events", [])
    return data


def _summary(added: int, changed: int, removed: int) -> str:
    bits = []
    if added:
        bits.append(f"{added} added")
    if changed:
        bits.append(f"{changed} changed")
    if removed:
        bits.append(f"{removed} removed")
    return ", ".join(bits)


def record(
    collection_id: str,
    *,
    source: str,
    label: str,
    added: List[str],
    changed: List[str],
    removed: List[str],
    commits: Optional[List[Dict[str, str]]] = None,
    head: Optional[str] = None,
    previous_head: Optional[str] = None,
    branch: Optional[str] = None,
    trigger: str = "manual",
) -> Optional[Dict[str, Any]]:
    """Append an event for a sync that changed something; None when nothing did."""
    if not (added or changed or removed):
        return None
    with _lock:
        data = _load(collection_id)
        event = {
            "id": data["next_id"],
            "at": datetime.utcnow().isoformat() + "Z",
            "source": source,
            "label": label,
            "trigger": trigger,
            "summary": _summary(len(added), len(changed), len(removed)),
            "added": added[:MAX_NAMES], "added_count": len(added),
            "changed": changed[:MAX_NAMES], "changed_count": len(changed),
            "removed": removed[:MAX_NAMES], "removed_count": len(removed),
            "commits": (commits or [])[:MAX_COMMITS],
            "head": head, "previous_head": previous_head, "branch": branch,
        }
        data["next_id"] += 1
        data["events"] = (data["events"] + [event])[-MAX_EVENTS:]
        try:
            app_db.set_config(_key(collection_id), data)
        except Exception as e:  # noqa: BLE001 - the feed is a convenience; the sync already happened
            logger.warning(f"Could not store change event for {collection_id}: {e}")
            return None
    _deliver_async(collection_id, event)
    return event


def list_events(collection_id: str, since_id: int = 0, limit: int = 50) -> Dict[str, Any]:
    """Events with id > ``since_id``, oldest first, plus the newest id for cheap polling."""
    data = _load(collection_id)
    events = [e for e in data["events"] if e["id"] > since_id]
    limit = max(1, min(int(limit), MAX_EVENTS))
    return {
        "collection_id": collection_id,
        "latest_id": data["next_id"] - 1,
        "events": events[-limit:],
    }


# ── Webhook ──────────────────────────────────────────────────────────────

def _deliver_async(collection_id: str, event: Dict[str, Any]) -> None:
    if not settings.alert_webhook_url:
        return
    threading.Thread(target=_post_webhook, args=(collection_id, event), daemon=True,
                     name="alert-webhook").start()


def _post_webhook(collection_id: str, event: Dict[str, Any]) -> None:
    try:
        from services.collection_service import collection_service

        collection = collection_service.get_collection(collection_id) or {}
        body = json.dumps({
            "event": "source.changed",
            "collection_id": collection_id,
            "collection_name": collection.get("name") or collection_id,
            # Chat-style webhooks (Slack, Teams, ntfy) read `text`.
            "text": f"{event['label']}: {event['summary']} in {collection.get('name') or collection_id}",
            "change": event,
        }).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "Clio-Alerts"}
        if settings.alert_webhook_secret:
            digest = hmac.new(settings.alert_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-Clio-Signature"] = f"sha256={digest}"
        req = urllib.request.Request(settings.alert_webhook_url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 - operator-configured URL
            resp.read(1024)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Change alert webhook failed: {e}")
