"""Share invitations by email, sent through Resend.

One narrow job: when a collection owner shares by email, deliver the share
token and a join link to the recipient. Configuration is two settings
(RESEND_API_KEY, RESEND_FROM); with no key the feature reports itself as
unavailable rather than failing sends. OFFLINE_MODE disables it entirely —
the app promises no outbound connections beyond explicitly configured
endpoints, and this one is only "configured" when the key is set AND the
deployment isn't air-gapped.
"""

import json
import logging
import urllib.error
import urllib.request
from html import escape
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"


def share_email_enabled() -> bool:
    return bool(settings.resend_api_key) and not settings.offline_mode


def send_share_email(
    to: str,
    *,
    share_token: str,
    collection_name: str,
    permission: str,
    shared_by: str,
    app_url: str,
    expires_at: Optional[str] = None,
    collection_id: Optional[str] = None,
) -> None:
    """Send one share invitation. Raises RuntimeError with a readable message
    on any failure — the caller surfaces it to the person clicking Send."""
    from services import data_controls

    # The invitation carries the collection's name and the token that opens it,
    # through an outside mail service.
    if collection_id and not data_controls.external_allowed(collection_id):
        raise RuntimeError(
            "The share was created, but not emailed: this collection holds data marked local only, and "
            "invitations go through an outside mail service. Give them the share token yourself."
        )
    if not share_email_enabled():
        raise RuntimeError(
            "Email sending is not configured: set RESEND_API_KEY in .env "
            "(and RESEND_FROM to a sender on your verified Resend domain)."
        )

    app_url = app_url.rstrip("/")
    join_url = f"{app_url}/?share_token={share_token}"
    perm_label = "read and write" if permission == "readwrite" else "read-only"
    expiry_line = f"This invitation expires on {expires_at[:10]}." if expires_at else ""

    subject = f"{shared_by} shared \"{collection_name}\" with you on Clio"
    text = (
        f"{shared_by} shared the collection \"{collection_name}\" with you "
        f"({perm_label} access).\n\n"
        f"Open {join_url} and sign in — the share token is filled in for you.\n\n"
        f"Or open {app_url}, click \"Join shared\", and paste this token:\n"
        f"{share_token}\n\n"
        f"{expiry_line}".strip()
    )
    html = f"""
<div style="font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 520px; margin: 0 auto; color: #1f2937;">
  <h2 style="font-size: 18px; margin-bottom: 4px;">You've been given access to a collection</h2>
  <p style="line-height: 1.6;">
    <strong>{escape(shared_by)}</strong> shared
    <strong>&ldquo;{escape(collection_name)}&rdquo;</strong> with you
    ({perm_label} access).
  </p>
  <p style="margin: 24px 0;">
    <a href="{escape(join_url)}"
       style="background: #2563eb; color: #ffffff; padding: 10px 18px; border-radius: 6px; text-decoration: none; font-weight: 600;">
      Open the collection
    </a>
  </p>
  <p style="line-height: 1.6; font-size: 13px; color: #6b7280;">
    Sign in with your usual account and the share token below is applied
    automatically. If the button doesn't work, open
    <a href="{escape(app_url)}">{escape(app_url)}</a>, click
    &ldquo;Join shared&rdquo;, and paste:
  </p>
  <p style="font-family: monospace; background: #f3f4f6; padding: 10px 12px; border-radius: 6px; font-size: 13px; word-break: break-all;">
    {escape(share_token)}
  </p>
  {f'<p style="font-size: 12px; color: #9ca3af;">{escape(expiry_line)}</p>' if expiry_line else ''}
</div>
"""

    _deliver(to, subject=subject, text=text, html=html)


def send_plain_email(to: str, *, subject: str, text: str) -> None:
    """One plain-text message through the same Resend configuration.

    Used for operator notifications (a reported document). Same rules as
    invitations: unavailable without a key or in OFFLINE_MODE, and any
    failure raises a readable RuntimeError for the caller to log.
    """
    if not share_email_enabled():
        raise RuntimeError("Email sending is not configured: set RESEND_API_KEY in .env.")
    html = f"<pre style=\"font-family: -apple-system, Segoe UI, Roboto, sans-serif; white-space: pre-wrap;\">{escape(text)}</pre>"
    _deliver(to, subject=subject, text=text, html=html)


def _deliver(to: str, *, subject: str, text: str, html: str) -> None:
    payload = json.dumps({
        "from": settings.resend_from,
        "to": [to],
        "subject": subject,
        "text": text,
        "html": html,
    }).encode("utf-8")

    req = urllib.request.Request(
        _RESEND_ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
        logger.info(f"Email sent to {to} (resend id {body.get('id', '?')})")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("message", "")
        except Exception:
            detail = ""
        raise RuntimeError(
            f"Resend rejected the email ({e.code}): {detail or e.reason}. "
            f"Check RESEND_FROM is a sender on a domain verified in Resend."
        ) from e
    except Exception as e:
        raise RuntimeError(f"Could not reach Resend: {e}") from e
