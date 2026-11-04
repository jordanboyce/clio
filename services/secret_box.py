"""Encryption at rest for credentials the app stores in its database.

Team API keys and the credential fields of the config table used to sit in the
database as plain text, so a database dump, a Postgres replica or a stray copy
of app.db handed over every provider key. They are now sealed with Fernet
(AES-128-CBC with HMAC-SHA256, from `cryptography`, a core dependency) before
they reach either backend, and opened again on the way out, so everything above
the database layer keeps seeing the plain value.

Where the key comes from, in order:

  SECRET_ENCRYPTION_KEY   The operator's own key, kept in a secret manager or
                          the service's environment. This is the only setting
                          that separates the key from the data, so it is the
                          one a deployment with a security review should use.
  DATA_DIR/secret.key     Generated on first use, so the zero-configuration
                          install needs no setup. It protects a database that
                          leaves on its own (a Postgres dump, a copied app.db)
                          but not a stolen data directory, which holds both.

A value written before this existed has no prefix and is returned as it is;
`DatabaseBackend.seal_stored_secrets` seals those at startup. A sealed value
the current key cannot open reads as empty, with a warning: the person enters
the key again, rather than the app failing or handing ciphertext to a provider.
"""

import base64
import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

SEALED_PREFIX = "clio-enc:v1:"
KEY_FILE_NAME = "secret.key"
MIN_OPERATOR_KEY_LENGTH = 16

# Config fields holding a credential: masked by GET /api/config, sealed in the
# database, and never mirrored into .env. Lives here rather than in
# config_manager because the database backends need it and that module imports
# the database.
SECRET_CONFIG_FIELDS = frozenset({
    "vision_ocr_api_key", "ollama_cloud_api_key", "embedding_api_key", "ai_api_key",
})

_lock = threading.Lock()
# (where the key came from, the Fernet built from it). Rebuilt when the source
# changes, which in practice means tests pointing DATA_DIR somewhere new.
_cached: Optional[Tuple[str, Any]] = None


def _settings():
    from config import settings

    return settings


def key_path() -> Path:
    return Path(_settings().data_dir) / KEY_FILE_NAME


def _derive(operator_key: str) -> bytes:
    """Turn any operator-supplied string into a Fernet key."""
    digest = hashlib.sha256(b"clio-secret-box-v1:" + operator_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _read_or_create_key_file(path: Path) -> bytes:
    """The generated key, creating it exclusively so two starters agree on one."""
    from cryptography.fernet import Fernet

    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    try:
        descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return path.read_bytes().strip()
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key)
    logger.info(
        f"Generated {path} to encrypt stored credentials. Set SECRET_ENCRYPTION_KEY "
        "to keep the key apart from the data."
    )
    return key


def _fernet():
    global _cached
    from cryptography.fernet import Fernet

    operator_key = (_settings().secret_encryption_key or "").strip()
    source = f"operator:{hashlib.sha256(operator_key.encode()).hexdigest()}" if operator_key else f"file:{key_path()}"
    with _lock:
        if _cached is None or _cached[0] != source:
            raw = _derive(operator_key) if operator_key else _read_or_create_key_file(key_path())
            _cached = (source, Fernet(raw))
        return _cached[1]


def validate_config() -> None:
    """Refuse a weak operator key at startup, before anything is sealed with it."""
    operator_key = (_settings().secret_encryption_key or "").strip()
    if operator_key and len(operator_key) < MIN_OPERATOR_KEY_LENGTH:
        raise RuntimeError(
            f"SECRET_ENCRYPTION_KEY must be at least {MIN_OPERATOR_KEY_LENGTH} characters. "
            "Generate one with: openssl rand -base64 32"
        )


def is_sealed(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(SEALED_PREFIX)


def seal(value: Any) -> Any:
    """Encrypt a credential. Empty values and already-sealed values pass through."""
    if not isinstance(value, str) or not value or is_sealed(value):
        return value
    return SEALED_PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def unseal(value: Any) -> Any:
    """Decrypt a stored credential. Values with no prefix predate encryption."""
    if not is_sealed(value):
        return value
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(value[len(SEALED_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning(
            "A stored credential cannot be decrypted with the current key. "
            "SECRET_ENCRYPTION_KEY or secret.key has changed; enter the credential again."
        )
        return ""
