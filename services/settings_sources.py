"""Where each setting in force came from: the environment, or Settings.

Most settings are read from the environment (or the env file) only. About
sixty can also be saved in Settings, and a saved value is applied over the
environment at every start (services/indexer_manager._apply_db_config). That
is deliberate, since it is how a container recreated without its env file
keeps its choices, but an operator who changes the environment and sees no
effect needs to be told why. So each such override is logged at start and
listed here, beside the read-only list of everything the environment set
(roadmap U10).

Admin only (api/system.py): values can be internal addresses and paths.
Credentials are never shown, only whether one is set.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Dict, List
from urllib.parse import urlsplit, urlunsplit

_SECRET_NAME = re.compile(r"(key|secret|token|password)", re.IGNORECASE)

_lock = threading.Lock()
_overrides: Dict[str, Dict[str, Any]] = {}
# The environment's own value of each field Settings has overridden, so a
# later save can tell whether it still differs. Never shown.
_environment_values: Dict[str, Any] = {}


def env_name(field: str) -> str:
    """The environment spelling of a setting (its first alias, if renamed)."""
    from config import Settings

    info = Settings.model_fields.get(field)
    alias = getattr(info, "validation_alias", None) if info else None
    choices = getattr(alias, "choices", None)
    if choices:
        return str(choices[0]).upper()
    return field.upper()


def is_secret(field: str) -> bool:
    from services.secret_box import SECRET_CONFIG_FIELDS

    return field in SECRET_CONFIG_FIELDS or bool(_SECRET_NAME.search(field))


def display(field: str, value: Any) -> Any:
    """A value fit to show an admin: credentials hidden, and a password
    inside an address (POSTGRES_URL) taken out."""
    if isinstance(value, str) and is_secret(field):
        return "(set)" if value else ""
    if isinstance(value, str) and "@" in value and "://" in value:
        try:
            parts = urlsplit(value)
            if parts.password or parts.username:
                host = parts.hostname or ""
                if parts.port:
                    host = f"{host}:{parts.port}"
                return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
        except ValueError:
            return "(set)"
    return value


def record_override(field: str, environment_value: Any, saved_value: Any) -> None:
    with _lock:
        _environment_values.setdefault(field, environment_value)
        _overrides[field] = {
            "name": env_name(field),
            "field": field,
            "environment": display(field, _environment_values[field]),
            "saved": display(field, saved_value),
        }


def note_save(field: str, current_value: Any, saved_value: Any, cfg=None) -> None:
    """A value saved in Settings for ``field``, about to replace
    ``current_value``. Records an override of the environment, or ends one
    when the saved value is the environment's again."""
    from config import settings

    cfg = cfg if cfg is not None else settings
    if field not in getattr(cfg, "_from_environment", frozenset()):
        return
    with _lock:
        environment_value = _environment_values.get(field, current_value)
    if str(environment_value) == str(saved_value):
        with _lock:
            _overrides.pop(field, None)
        return
    record_override(field, environment_value, saved_value)


def reset() -> None:
    """Testing hook."""
    with _lock:
        _overrides.clear()
        _environment_values.clear()


def overrides() -> List[Dict[str, Any]]:
    with _lock:
        return sorted(_overrides.values(), key=lambda o: o["name"])


def from_environment(cfg=None) -> List[Dict[str, Any]]:
    """Every setting the environment or the env file set, as in force now."""
    from config import settings

    cfg = cfg if cfg is not None else settings
    overridden = {o["field"] for o in overrides()}
    rows = []
    for field in sorted(getattr(cfg, "_from_environment", frozenset())):
        if field not in type(cfg).model_fields:
            continue
        rows.append({
            "name": env_name(field),
            "value": display(field, getattr(cfg, field, None)),
            "overridden_in_settings": field in overridden,
        })
    return sorted(rows, key=lambda r: r["name"])


def override_log_line(field: str, environment_value: Any, saved_value: Any) -> str:
    name = env_name(field)
    if is_secret(field):
        return (f"{name} is set in the environment, but the value saved in Settings is in force. "
                "Save it again in Settings to change it.")
    return (f"{name} is {display(field, environment_value)!r} in the environment, but "
            f"{display(field, saved_value)!r}, saved in Settings, is in force. Change it in "
            "Settings to use another value.")
