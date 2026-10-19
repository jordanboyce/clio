"""scripts/setup.py: the one-question access setup that writes .env."""

import importlib.util
import pathlib

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "setup.py"
_spec = importlib.util.spec_from_file_location("clio_setup", _SCRIPT)
setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup)

TEMPLATE = (
    "# header\n"
    "# BIND_ADDRESS=127.0.0.1\n"
    "DATA_DIR=./data\n"
    "HOST=127.0.0.1\n"
    "PORT=8473\n"
    "AUTH_PASSWORD=\n"
)


def test_set_key_replaces_active_line_in_place():
    out = setup.set_key(TEMPLATE, "HOST", "0.0.0.0")
    assert "HOST=0.0.0.0\n" in out
    assert out.count("HOST=") == 1
    assert out.index("HOST=") < out.index("PORT=")


def test_set_key_reenables_commented_line():
    out = setup.set_key(TEMPLATE, "BIND_ADDRESS", "0.0.0.0")
    assert "\nBIND_ADDRESS=0.0.0.0\n" in out
    assert "# BIND_ADDRESS" not in out


def test_set_key_keeps_crlf_when_replacing_and_appending():
    crlf = TEMPLATE.replace("\n", "\r\n")
    out = setup.set_key(crlf, "HOST", "0.0.0.0")          # active line
    out = setup.set_key(out, "BIND_ADDRESS", "0.0.0.0")   # commented line
    out = setup.set_key(out, "NEW_KEY", "x")              # appended
    assert "\r\nHOST=0.0.0.0\r\n" in out
    assert "\r\nBIND_ADDRESS=0.0.0.0\r\n" in out
    assert out.endswith("NEW_KEY=x\r\n")
    assert "\n" not in out.replace("\r\n", "")
    assert setup.get_key(out, "HOST") == "0.0.0.0"


def test_set_key_leaves_prose_comments_alone():
    text = "# HOST=127.0.0.1 (the default) accepts connections from this machine.\n"
    out = setup.set_key(text, "HOST", "0.0.0.0")
    assert out.startswith("# HOST=127.0.0.1 (the default)")
    assert out.endswith("HOST=0.0.0.0\n")


def test_get_key_ignores_comments_and_quotes():
    text = 'AUTH_PASSWORD="hunter2"   # trailing note\n# HOST=0.0.0.0\n'
    assert setup.get_key(text, "AUTH_PASSWORD") == "hunter2"
    assert setup.get_key(text, "HOST") is None


def test_apply_choice_open_and_password_are_consistent():
    open_text = setup.apply_choice(TEMPLATE, None)
    p = setup.posture(open_text)
    assert p == {"host": "127.0.0.1", "bind_address": "127.0.0.1",
                 "password_set": False, "reachable": False, "note": ""}

    shared = setup.apply_choice(open_text, "s3cret")
    p = setup.posture(shared)
    assert p["password_set"] and p["reachable"] and p["note"] == ""
    assert setup.get_key(shared, "HOST") == "0.0.0.0"
    assert setup.get_key(shared, "BIND_ADDRESS") == "0.0.0.0"

    # And back: the password line is cleared, not left behind.
    back = setup.apply_choice(shared, None)
    assert setup.get_key(back, "AUTH_PASSWORD") == ""
    assert not setup.posture(back)["reachable"]


def test_posture_flags_password_on_loopback():
    text = setup.set_key(TEMPLATE, "AUTH_PASSWORD", "leftover")
    note = setup.posture(text)["note"]
    assert "only listens on this machine" in note
    assert "--open" in note


def test_posture_flags_network_without_password():
    text = setup.set_key(TEMPLATE, "BIND_ADDRESS", "0.0.0.0")
    assert "no password" in setup.posture(text)["note"]


def test_main_creates_env_from_template_without_a_terminal(tmp_path, capsys, monkeypatch):
    template = tmp_path / ".env.example"
    template.write_text(TEMPLATE, encoding="utf-8")
    env = tmp_path / ".env"
    monkeypatch.setattr("sys.stdin", _NotATty())

    assert setup.main(["--env", str(env), "--template", str(template)]) == 0
    assert env.exists()
    assert setup.get_key(env.read_text(encoding="utf-8"), "AUTH_PASSWORD") == ""
    assert "only this machine" in capsys.readouterr().out


def test_main_password_flag_generates_and_prints_once(tmp_path, capsys):
    template = tmp_path / ".env.example"
    template.write_text(TEMPLATE, encoding="utf-8")
    env = tmp_path / ".env"

    assert setup.main(["--password", "--env", str(env), "--template", str(template)]) == 0
    text = env.read_text(encoding="utf-8")
    password = setup.get_key(text, "AUTH_PASSWORD")
    assert password and len(password) >= 24
    assert password in capsys.readouterr().out
    assert setup.get_key(text, "HOST") == "0.0.0.0"


def test_main_open_keeps_backup_and_other_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text(setup.apply_choice(TEMPLATE, "old") + "MY_OWN_KEY=kept\n", encoding="utf-8")
    template = tmp_path / ".env.example"
    template.write_text(TEMPLATE, encoding="utf-8")

    assert setup.main(["--open", "--env", str(env), "--template", str(template)]) == 0
    text = env.read_text(encoding="utf-8")
    assert setup.get_key(text, "AUTH_PASSWORD") == ""
    assert "MY_OWN_KEY=kept" in text
    assert (tmp_path / ".env.bak").exists()
    assert setup.get_key((tmp_path / ".env.bak").read_text(encoding="utf-8"), "AUTH_PASSWORD") == "old"


def test_main_unattended_never_rewrites_an_existing_env(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text(setup.apply_choice(TEMPLATE, "old"), encoding="utf-8")
    template = tmp_path / ".env.example"
    template.write_text(TEMPLATE, encoding="utf-8")
    monkeypatch.setattr("sys.stdin", _NotATty())

    assert setup.main(["--env", str(env), "--template", str(template)]) == 0
    assert setup.get_key(env.read_text(encoding="utf-8"), "AUTH_PASSWORD") == "old"
    assert "shared password" in capsys.readouterr().out


class _NotATty:
    def isatty(self):
        return False

    def readline(self):
        return ""
