"""Load test the /mcp endpoint the way a fleet of agents would use it.

Opens N concurrent MCP sessions (streamable HTTP, the same transport Claude,
ChatGPT, Hermes, Grok and the SDKs use), and for a fixed duration has every
session fire tool calls back to back from a realistic mix: quick searches,
document reads, research calls, table listings and health checks. Meanwhile a
probe hits GET /health every 250 ms — its latency is the event loop's
responsiveness, which is what every *other* client experiences while the
tools run. Reports throughput, per-tool latency percentiles and errors.

    python scripts/mcp_load.py --url http://127.0.0.1:8473/mcp --sessions 32 --duration 60
    python scripts/mcp_load.py --url https://clio.example.org/mcp --token clio_xxx ...

Needs only the `mcp` package (already in requirements.txt). Exit code 1 when
any call failed or the health probe's p95 went over --health-budget seconds,
so it can gate a deployment check.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DEFAULT_QUERIES = [
    "what does this collection cover",
    "how is access controlled",
    "summarize the main procedure",
    "what are the deadlines and dates mentioned",
    "who is responsible for approvals",
    "list the key definitions",
    "what happens when something fails",
    "where are the configuration settings described",
]


class Stats:
    def __init__(self) -> None:
        self.latency: dict[str, list[float]] = defaultdict(list)
        self.errors: dict[str, list[str]] = defaultdict(list)

    def record(self, tool: str, seconds: float, error: str | None = None) -> None:
        self.latency[tool].append(seconds)
        if error:
            self.errors[tool].append(error)

    @staticmethod
    def pct(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        k = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
        return ordered[k]


def _text_of(result: Any) -> dict:
    """The JSON payload of a tool result (structured content when the server
    sends it, else the first text block)."""
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except ValueError:
                return {"text": text}
    return {}


async def session_worker(idx: int, args, stats: Stats, deadline: float, doc_ids: list[str],
                         ready: asyncio.Event) -> None:
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else None
    rng = random.Random(idx)
    try:
        async with streamablehttp_client(args.url, headers=headers, timeout=args.timeout) as (read, write, _):
            async with ClientSession(read, write) as session:
                t0 = time.perf_counter()
                await session.initialize()
                stats.record("initialize", time.perf_counter() - t0)
                ready.set()
                while time.perf_counter() < deadline:
                    roll = rng.random()
                    if roll < args.mix_search:
                        tool, params = "search_collection", {"query": rng.choice(args.queries), "top_k": 8}
                    elif roll < args.mix_search + args.mix_context and doc_ids:
                        doc_id, doc_cid = rng.choice(doc_ids)
                        tool, params = "get_document_context", {"document_id": doc_id, "max_chars": 4000}
                        if doc_cid:
                            params["collection_id"] = doc_cid
                    elif roll < args.mix_search + args.mix_context + args.mix_research:
                        q = rng.choice(args.queries)
                        tool, params = "research_documents", {"query": q, "top_k": 6, "max_context_chars": 6000}
                    elif roll < args.mix_search + args.mix_context + args.mix_research + args.mix_tables:
                        tool, params = "list_tables", {}
                    else:
                        tool, params = "health_check", {}
                    if args.collection and tool != "health_check":
                        params.setdefault("collection_id", args.collection)
                    t0 = time.perf_counter()
                    error = None
                    try:
                        result = await session.call_tool(tool, params)
                        if getattr(result, "isError", False):
                            error = _text_of(result).get("text", "tool error")[:200]
                    except Exception as e:  # noqa: BLE001 - counted, not raised
                        error = f"{type(e).__name__}: {e}"[:200]
                    stats.record(tool, time.perf_counter() - t0, error)
    except Exception as e:  # noqa: BLE001 - a session that never came up
        stats.record("session", 0.0, f"{type(e).__name__}: {e}"[:200])
        ready.set()


async def health_probe(args, stats: Stats, deadline: float, ready: asyncio.Event) -> None:
    base = args.url.split("/mcp")[0]
    await ready.wait()
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        while time.perf_counter() < deadline:
            t0 = time.perf_counter()
            error = None
            try:
                r = await client.get(f"{base}/health")
                if r.status_code != 200:
                    error = f"HTTP {r.status_code}"
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"[:120]
            stats.record("GET /health (probe)", time.perf_counter() - t0, error)
            await asyncio.sleep(0.25)


async def discover_documents(args) -> list[str]:
    """A few document ids to read during the run, via the MCP tools themselves."""
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else None
    async with streamablehttp_client(args.url, headers=headers, timeout=args.timeout) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            params = {"limit": 20}
            if args.collection:
                params["collection_id"] = args.collection
            try:
                payload = _text_of(await session.call_tool("list_recent_documents", params))
            except Exception:  # noqa: BLE001 - reads are then skipped
                return []
            docs = payload.get("documents") or payload.get("results") or []
            # Each document is read in the collection it lives in; across
            # collections the listing names it per row.
            return [(d["document_id"], d.get("collection_id") or args.collection or None)
                    for d in docs if isinstance(d, dict) and d.get("document_id")]


async def run(args) -> int:
    doc_ids = await discover_documents(args)
    stats = Stats()
    deadline = time.perf_counter() + args.duration
    ready = asyncio.Event()
    started = time.perf_counter()
    workers = [session_worker(i, args, stats, deadline, doc_ids, ready) for i in range(args.sessions)]
    await asyncio.gather(*workers, health_probe(args, stats, deadline, ready))
    elapsed = time.perf_counter() - started

    calls = sum(len(v) for k, v in stats.latency.items() if k not in ("initialize", "session", "GET /health (probe)"))
    errors = sum(len(v) for v in stats.errors.values())
    print(f"\n{args.sessions} sessions x {args.duration}s against {args.url}")
    print(f"documents available for reads: {len(doc_ids)}")
    print(f"tool calls: {calls}  ({calls / max(elapsed, 1e-9):.1f}/s)   errors: {errors}\n")
    print(f"{'tool':28} {'n':>6} {'p50 s':>8} {'p95 s':>8} {'p99 s':>8} {'max s':>8} {'err':>5}")
    for tool in sorted(stats.latency, key=lambda t: (t.startswith("GET"), t)):
        lat = stats.latency[tool]
        print(f"{tool:28} {len(lat):6d} {Stats.pct(lat, 50):8.3f} {Stats.pct(lat, 95):8.3f} "
              f"{Stats.pct(lat, 99):8.3f} {max(lat):8.3f} {len(stats.errors.get(tool, [])):5d}")
    for tool, errs in stats.errors.items():
        sample = statistics.mode(errs) if errs else ""
        print(f"\n{tool}: {len(errs)} error(s), most common: {sample}")

    probe = stats.latency.get("GET /health (probe)", [])
    probe_p95 = Stats.pct(probe, 95)
    verdict = 0
    if errors:
        verdict = 1
    if probe and probe_p95 > args.health_budget:
        print(f"\nevent loop responsiveness: /health p95 {probe_p95:.3f}s exceeds the {args.health_budget}s budget")
        verdict = 1
    elif probe:
        print(f"\nevent loop responsiveness: /health p95 {probe_p95:.3f}s under load (budget {args.health_budget}s)")
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8473/mcp", help="the /mcp endpoint")
    parser.add_argument("--token", default="", help="personal MCP token or AUTH_PASSWORD (Bearer)")
    parser.add_argument("--collection", default="", help="collection id (default: the server's default)")
    parser.add_argument("--sessions", type=int, default=16, help="concurrent MCP sessions")
    parser.add_argument("--duration", type=float, default=30.0, help="seconds to run")
    parser.add_argument("--timeout", type=float, default=120.0, help="per-call HTTP timeout")
    parser.add_argument("--health-budget", type=float, default=1.0,
                        help="fail when the /health probe's p95 exceeds this many seconds")
    parser.add_argument("--queries", default="", help="file with one query per line")
    parser.add_argument("--mix-search", type=float, default=0.45)
    parser.add_argument("--mix-context", type=float, default=0.25)
    parser.add_argument("--mix-research", type=float, default=0.10)
    parser.add_argument("--mix-tables", type=float, default=0.10)
    args = parser.parse_args()
    if args.queries:
        with open(args.queries, encoding="utf-8") as fh:
            args.queries = [line.strip() for line in fh if line.strip()] or DEFAULT_QUERIES
    else:
        args.queries = DEFAULT_QUERIES
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
