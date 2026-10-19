#!/usr/bin/env python
"""Choose who can reach Clio, and write that choice to .env.

Clio has no accounts. Who gets in is decided by two settings: the interface
the server listens on, and an optional shared password (AUTH_PASSWORD). This
script asks one question and sets both consistently, so a fresh install
never ends up with a password prompt on a machine only you use, or open on a
network where other people can reach it.

    python scripts/setup.py                 # ask (creates .env from .env.example)
    python scripts/setup.py --open          # only this machine, no password
    python scripts/setup.py --password      # others on the network; generates a password
    python scripts/setup.py --password XYZ  # ... with a password you choose
    python scripts/setup.py --show          # report the current choice and stop

run.sh and run.bat call it on first run. Docker users run it before
`docker compose up -d` (compose reads the same .env), and again whenever the
choice changes - the container picks up the new file on `docker compose up -d`.

Without a terminal and without flags it chooses the open, loopback-only shape
and says so, so an unattended first run never blocks on a prompt. Standard
library only; it must run with whatever Python the host has.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import shutil
import sys
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")
TEMPLATE_PATH = os.path.join(ROOT, ".env.example")
BACKUP_PATH = os.path.join(ROOT, ".env.bak")

LOOPBACK = "127.0.0.1"
ALL_INTERFACES = "0.0.0.0"
_LOOPBACK_VALUES = {"", "127.0.0.1", "::1", "localhost"}

# The keys this script owns. Everything else in .env is left exactly as it is.
#   HOST          what `python main.py` listens on
#   BIND_ADDRESS  the host interface docker compose publishes the port on
#   AUTH_PASSWORD the shared secret; empty = no password
_OPEN = {"HOST": LOOPBACK, "BIND_ADDRESS": LOOPBACK, "AUTH_PASSWORD": ""}


# ── .env editing ────────────────────────────────────────────────────────────

def _active_pattern(key: str) -> "re.Pattern[str]":
    # The lookahead keeps a CRLF file's "\r" out of the match, so replacing
    # the line leaves the line ending as it was.
    return re.compile(r"^" + re.escape(key) + r"=[^\r\n]*(?=\r?$)", re.MULTILINE)


def _commented_pattern(key: str) -> "re.Pattern[str]":
    # A disabled assignment ("# KEY=value") and nothing else on the line. A
    # prose comment that merely starts with the key ("# HOST=127.0.0.1 (the
    # default) accepts ...") has more after the value and is left alone.
    return re.compile(r"^#\s*" + re.escape(key) + r"=[^\s#]*[ \t]*(?=\r?$)", re.MULTILINE)


def set_key(text: str, key: str, value: str) -> str:
    """Return ``text`` with ``KEY=value`` set.

    The first active ``KEY=`` line is replaced in place; failing that, the
    first commented-out assignment is re-enabled in place, so the file keeps
    its shape; failing that, the assignment is appended. Line endings are
    preserved, so a CRLF file stays CRLF.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    line = f"{key}={value}"
    for pattern in (_active_pattern(key), _commented_pattern(key)):
        if pattern.search(text):
            return pattern.sub(lambda _m: line, text, count=1)
    if text and not text.endswith(("\n", "\r")):
        text += newline
    return text + line + newline


def get_key(text: str, key: str) -> Optional[str]:
    """The value of the first *active* ``KEY=`` line, or None when unset."""
    match = re.search(r"^" + re.escape(key) + r"=(.*)$", text, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip()
    # `KEY=value   # comment` is how .env.example annotates a few lines.
    value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def apply_choice(text: str, password: Optional[str]) -> str:
    """Rewrite the access settings for one choice.

    ``password`` None means "only this machine": loopback, no password.
    A password means "others on the network": every interface plus the
    shared secret, which is the only combination the server does not warn
    about at startup.
    """
    values = dict(_OPEN)
    if password:
        values = {"HOST": ALL_INTERFACES, "BIND_ADDRESS": ALL_INTERFACES,
                  "AUTH_PASSWORD": password}
    for key, value in values.items():
        text = set_key(text, key, value)
    return text


def posture(text: str) -> dict:
    """Describe what the file currently asks for, plus a note when the two
    settings disagree with each other."""
    host = get_key(text, "HOST") or LOOPBACK
    bind = get_key(text, "BIND_ADDRESS") or LOOPBACK
    password = get_key(text, "AUTH_PASSWORD") or ""
    reachable = host not in _LOOPBACK_VALUES or bind not in _LOOPBACK_VALUES
    note = ""
    if password and not reachable:
        note = ("A password is set, but Clio only listens on this machine, so the "
                "prompt protects nothing. `python scripts/setup.py --open` removes it.")
    elif reachable and not password:
        note = ("Clio listens on the network with no password; anyone who can reach "
                "the port sees everything. `python scripts/setup.py --password` fixes "
                "that, or an identity provider (docs/IDENTITY.md).")
    return {"host": host, "bind_address": bind, "password_set": bool(password),
            "reachable": reachable, "note": note}


def describe(text: str) -> str:
    p = posture(text)
    who = "other people on the network" if p["reachable"] else "only this machine"
    gate = "a shared password" if p["password_set"] else "no password"
    lines = [f"Reachable from: {who}  (HOST={p['host']}, BIND_ADDRESS={p['bind_address']})",
             f"Access:         {gate}"]
    if p["note"]:
        lines.append(f"Note:           {p['note']}")
    return "\n".join(lines)


# ── Files ───────────────────────────────────────────────────────────────────

def read_env(path: str = ENV_PATH, template: str = TEMPLATE_PATH) -> "tuple[str, bool]":
    """The .env text and whether it already existed (else the template)."""
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as f:
            return f.read(), True
    with open(template, encoding="utf-8", newline="") as f:
        return f.read(), False


def write_env(text: str, path: str = ENV_PATH, backup: str = BACKUP_PATH) -> bool:
    """Write .env, keeping the previous file as .env.bak. Returns whether a
    backup was made."""
    backed_up = False
    if os.path.exists(path):
        shutil.copyfile(path, backup)
        backed_up = True
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return backed_up


def _display_path(path: str) -> str:
    try:
        rel = os.path.relpath(path, ROOT)
    except ValueError:  # another drive on Windows
        return path
    return path if rel.startswith("..") else rel


# ── Interaction ─────────────────────────────────────────────────────────────

def _ask(prompt: str, default: str) -> str:
    try:
        answer = input(prompt).strip()
    except EOFError:
        return default
    return answer or default


def ask_choice(existed: bool, current: str) -> "tuple[Optional[str], bool]":
    """Interactive flow. Returns (password, generated): the password to set,
    "" for open, or None to leave the file alone; and whether it was
    generated rather than typed."""
    if existed:
        print("Current .env:")
        print("  " + describe(current).replace("\n", "\n  "))
        print()
        if _ask("Change who can reach Clio? [y/N] ", "n").lower() not in ("y", "yes"):
            return None, False
        print()
    print("Who should be able to reach Clio?")
    print("  1) Only me, on this machine      - no password (default)")
    print("  2) Other people on my network    - shared password, listens on all interfaces")
    choice = _ask("Choice [1]: ", "1")
    if choice != "2":
        return "", False
    print()
    print("Browsers will prompt for this password (any username); API and MCP")
    print("clients send it as `Authorization: Bearer <password>`.")
    try:
        typed = getpass.getpass("Password (leave blank to generate one): ").strip()
    except EOFError:
        typed = ""
    if typed:
        return typed, False
    return secrets.token_urlsafe(24), True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--open", action="store_true",
                      help="only this machine: loopback, no password")
    mode.add_argument("--password", nargs="?", const="", metavar="PASSWORD",
                      help="others on the network: listen on all interfaces and "
                           "require this password (generated when omitted)")
    mode.add_argument("--show", action="store_true",
                      help="print the current choice and exit")
    parser.add_argument("--env", default=ENV_PATH, help=argparse.SUPPRESS)
    parser.add_argument("--template", default=TEMPLATE_PATH, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if not os.path.exists(args.template) and not os.path.exists(args.env):
        print(f"error: neither {args.env} nor {args.template} exists", file=sys.stderr)
        return 2

    text, existed = read_env(args.env, args.template)

    if args.show:
        print(describe(text) if existed else "No .env yet (defaults: only this machine, no password).")
        return 0

    generated = False
    if args.open:
        password = ""
    elif args.password is not None:
        password = args.password or secrets.token_urlsafe(24)
        generated = not args.password
    elif sys.stdin.isatty() and sys.stdout.isatty():
        password, generated = ask_choice(existed, text)
        if password is None:
            print("Left .env unchanged.")
            return 0
    else:
        if existed:
            # Unattended and nothing asked for: never rewrite a file silently.
            print(describe(text))
            return 0
        password = ""
        print("No terminal to ask on: choosing 'only this machine, no password'.")
        print("Re-run `python scripts/setup.py` to change it.")

    new_text = apply_choice(text, password or None)
    backed_up = write_env(new_text, args.env, BACKUP_PATH if args.env == ENV_PATH
                          else args.env + ".bak")

    print()
    print(f"{'Updated' if existed else 'Created'} {_display_path(args.env)}"
          + (" (previous copy kept as .env.bak)" if backed_up else ""))
    print("  " + describe(new_text).replace("\n", "\n  "))
    if password and generated:
        print()
        print(f"Generated password: {password}")
        print("It is stored in .env; this is the only time it is printed.")
    print()
    print("Next: `python main.py` or `./run.sh`, or for Docker `docker compose up -d`")
    print("(a running container is recreated with the new settings).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
