"""Collection sharing service.

Allows users to create shareable links to their indexed collections.
Other users on the same deployment can accept shares and access collections
with read or read-write permissions.
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from services.app_database import app_db
from config import settings

logger = logging.getLogger(__name__)


class SharingService:
    """Manages collection sharing between users."""

    def create_share(
        self,
        collection_id: str,
        owner_id: str,
        permission: str = "read",
        expires_days: Optional[int] = None,
        invited_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a share link for a collection.

        Args:
            collection_id: Collection to share
            owner_id: User creating the share (must own the collection)
            permission: 'read' or 'readwrite'
            expires_days: Optional expiry in days (None = never expires)
            invited_email: Address this share was emailed to, recorded so
                revoking it can also withdraw the recipient's edge admission

        Returns:
            Share details including the share token
        """
        # Verify ownership
        collection = app_db.get_collection(collection_id)
        if not collection:
            raise ValueError(f"Collection {collection_id} not found")
        if collection.get("owner_id") != owner_id:
            raise PermissionError("Only the collection owner can create shares")

        # A restricted collection is the one label that is a boundary rather
        # than a hint: it never leaves its owner by share link. Re-label it
        # first if sharing is really intended.
        from services.governance import is_restricted

        if is_restricted(collection):
            raise PermissionError(
                "This collection is labelled restricted and cannot be shared. "
                "Change its sensitivity label first if sharing is intended."
            )

        expires_at = None
        if expires_days:
            expires_at = (datetime.utcnow() + timedelta(days=expires_days)).isoformat()

        share_id = app_db.create_share(
            collection_id=collection_id,
            owner_id=owner_id,
            permission=permission,
            expires_at=expires_at,
            invited_email=invited_email,
        )

        logger.info(f"Created share {share_id} for collection {collection_id} (permission={permission})")

        return {
            "share_id": share_id,
            "collection_id": collection_id,
            "permission": permission,
            "expires_at": expires_at,
            "invited_email": invited_email,
        }

    def accept_share(self, share_token: str, user_id: str) -> Dict[str, Any]:
        """Accept a share link.

        Args:
            share_token: The share ID/token from the share link
            user_id: User accepting the share

        Returns:
            Accepted share details
        """
        if not user_id:
            raise ValueError(
                "Accepting a share needs an identity — sign in with your "
                "account rather than the shared password."
            )
        share = app_db.get_share(share_token)
        if not share:
            raise ValueError("Share link not found")
        if not share.get("is_active"):
            raise ValueError("Share link has expired or been revoked")
        if share["owner_id"] == user_id:
            raise ValueError("Cannot accept your own share")

        # Auto-provision user
        app_db.upsert_user(user_id)

        app_db.accept_share(share_token, user_id)

        collection = app_db.get_collection(share["collection_id"])
        logger.info(f"User {user_id} accepted share {share_token} for collection {share['collection_id']}")

        return {
            "share_id": share_token,
            "collection_id": share["collection_id"],
            "collection_name": collection["name"] if collection else "Unknown",
            "permission": share["permission"],
            "owner_id": share["owner_id"],
        }

    def get_shares_for_collection(self, collection_id: str, owner_id: str) -> List[Dict[str, Any]]:
        """List active shares for a collection.

        Args:
            collection_id: Collection ID
            owner_id: Must be the collection owner

        Returns:
            List of share details
        """
        collection = app_db.get_collection(collection_id)
        if not collection or collection.get("owner_id") != owner_id:
            raise PermissionError("Only the collection owner can view shares")

        return app_db.get_shares_for_collection(collection_id)

    def revoke_share(self, share_id: str, owner_id: str) -> Dict[str, Any]:
        """Revoke a share link.

        Args:
            share_id: Share to revoke
            owner_id: Must be the share owner

        Returns:
            The revoked share, so the caller can withdraw any edge admission
            that was granted for its invited address.
        """
        share = app_db.get_share(share_id)
        if not share:
            raise ValueError("Share not found")
        if share["owner_id"] != owner_id:
            raise PermissionError("Only the share creator can revoke it")

        app_db.revoke_share(share_id)
        logger.info(f"Revoked share {share_id}")
        return share

    def check_collection_access(self, collection_id: str, user_id: Optional[str]) -> Optional[str]:
        """Check if a user has access to a collection.

        Returns the permission level: 'owner', 'readwrite', 'read', or None.
        With private collections off, always returns 'owner' (shared appliance:
        everyone sees everything).

        With private collections on:
          - Team collections (owner_id empty or "default" — everything created
            before the mode existed, plus anything created by anonymous
            password callers) grant 'owner' to every authenticated caller.
          - The recorded owner gets 'owner'.
          - A published collection grants 'read' to everyone else: it has been
            reviewed and released, so the whole deployment can search it and
            reach it over MCP, but only its owner decides how it is built.
            Publishing is what makes a collection reachable *and* safe from
            edits; it is refused on team collections precisely because those
            have no single owner to keep that authority (see api/collections).
          - An accepted share grants its 'read'/'readwrite' permission.
          - Anonymous callers (user_id None) reach team and published
            collections only.
        """
        if not settings.private_collections:
            return "owner"

        collection = app_db.get_collection(collection_id)
        if not collection:
            return None

        owner = (collection.get("owner_id") or "").strip()
        if owner in ("", settings.default_user_id):
            return "owner"

        if user_id and owner == user_id:
            return "owner"

        # A share is the stronger grant (it can carry write), so it wins over
        # the blanket read that publishing gives everyone.
        share = app_db.check_share_access(collection_id, user_id) if user_id else None
        if share:
            return share

        if collection.get("published"):
            return "read"

        return None


# Global instance
sharing_service = SharingService()
