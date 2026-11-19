"""E7: hold the size.

Settings went from 162 to 193 and MCP tools from 29 to 32 in three days,
unnoticed. These tests make every addition a deliberate act: change the
number here, in the same commit, and say why in the commit message."""

import asyncio
import json
import re
from pathlib import Path

import config
import services.mcp_server as mcp

ROOT = Path(__file__).resolve().parent.parent

# Raise only together with a reason in the commit message.
SETTINGS_BUDGET = 193
MCP_TOOLS_BUDGET = 32
# Characters of tools/list, about four to a token.
FULL_TOOLS_CHARS_BUDGET = 70_000
PROFILE_CHARS_BUDGET = {"notes": 16_000, "core": 16_000}


def _documented() -> set:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^\s*#?\s*([A-Z][A-Z0-9_]+)=", text, re.M))


def test_every_setting_has_a_line_in_the_example_file():
    missing = sorted(f.upper() for f in config.Settings.model_fields if f.upper() not in _documented())
    assert not missing, f"add these to .env.example with a one-line explanation: {missing}"


def test_the_number_of_settings_is_held():
    count = len(config.Settings.model_fields)
    assert count <= SETTINGS_BUDGET, (
        f"{count} settings, budget {SETTINGS_BUDGET}: remove one, or raise the budget in this file "
        "and give the reason in the commit message")


def _tools(profile=None):
    token = mcp._request_mcp_tools.set(profile)
    try:
        return asyncio.run(mcp._listed_tools()).tools
    finally:
        mcp._request_mcp_tools.reset(token)


def _chars(tools) -> int:
    return len(json.dumps([t.model_dump(exclude_none=True, mode="json") for t in tools]))


def test_the_mcp_tool_surface_is_held():
    # The pra_ tools exist only while ENABLE_PRA is on (the tests turn it on).
    tools = [t for t in _tools() if not t.name.startswith("pra_")]
    assert len(tools) <= MCP_TOOLS_BUDGET, f"{len(tools)} MCP tools, budget {MCP_TOOLS_BUDGET}"
    assert _chars(tools) <= FULL_TOOLS_CHARS_BUDGET, _chars(tools)
    for profile, budget in PROFILE_CHARS_BUDGET.items():
        assert _chars(_tools(profile)) <= budget, (profile, _chars(_tools(profile)))
