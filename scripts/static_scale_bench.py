#!/usr/bin/env python
"""How big a static export can get before downloading the whole index is
too much (docs/STATIC_SEARCH.md, "How far it scales"; work order E1).

    python scripts/static_scale_bench.py --sizes 10000 50000 200000 --out <scratch folder>

For each size it writes an export in the real format (the writer's own
quantisation and manifest), from generated text of about 800 characters a
passage and random unit vectors of 384 dimensions, then opens it in a
headless Chromium (tests/browser_harness.py) and records:

- the bytes of each file, and gzipped (what a server that compresses sends);
- with the connection throttled (--mbps, --latency-ms; the server here does
  not compress, so this is the worst case), the time from asking to a
  keyword result, and to a result ranked by meaning (a query vector given
  directly, so no model download is in the figure);
- the JavaScript heap once the index is loaded.

No network beyond 127.0.0.1. Prints a table and writes results.json.
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORDS = ("staff leave policy travel expense claim budget report review office equipment checkout return "
         "approval manager team project safety incident training record register payment invoice contract "
         "supplier meeting schedule notice period pension salary overtime remote working hours building "
         "access card visitor guest parking desk room booking laptop password support request form").split()

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>bench</title></head><body>
<script type="module">
import { loadIndex, keywordRank, rank } from './clio-static-search.js'
window.bench = async (query, vector) => {
  const t0 = performance.now()
  const index = await loadIndex('./search/', { verify: false })
  const loaded = performance.now()
  const kw = keywordRank(index, query, { k: 8 })
  const t1 = performance.now()
  const sem = rank(index, vector, { k: 8 })
  const t2 = performance.now()
  window.keep = index
  return { load_ms: loaded - t0, keyword_ms: t1 - loaded, meaning_ms: t2 - t1,
           to_keyword_ms: t1 - t0, to_meaning_ms: (loaded - t0) + (t2 - t1),
           keyword_hits: kw.length, meaning_hits: sem.length }
}
window.ready = true
</script></body></html>
"""


def write_export(folder: Path, n: int, dim: int = 384, per_file: int = 2000, seed: int = 0) -> dict:
    from services.static_export import quantize
    from services.static_export.writer import _manifest_text

    rng = np.random.default_rng(seed)
    data = folder / "search"
    data.mkdir(parents=True, exist_ok=True)
    files = []
    import hashlib

    for start in range(0, n, per_file):
        rows = []
        for i in range(start, min(n, start + per_file)):
            words = rng.choice(WORDS, size=120)
            text = " ".join(words)[:800]
            rows.append(json.dumps({"id": f"d{i // 50:06d}_{i % 50}_0", "document_id": f"d{i // 50:06d}",
                                    "page": 1, "text": text}, separators=(",", ":")))
        name = "passages.json" if n <= per_file else f"passages-{start // per_file + 1:04d}.json"
        raw = ("[\n" + ",\n".join(rows) + "\n]\n").encode()
        (data / name).write_bytes(raw)
        files.append({"file": name, "count": len(rows), "sha256": hashlib.sha256(raw).hexdigest()})
    vectors = rng.standard_normal((n, dim)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    blob, scale = quantize(vectors, "int8")
    (data / "vectors.bin").write_bytes(blob)
    manifest = {"format": "clio-static-search", "version": 1, "exported_at": "2026-11-20T00:00:00+00:00",
                "collection": {"name": f"Bench {n}", "description": ""},
                "embedding": {"model": "all-MiniLM-L6-v2", "dimension": dim, "pooling": "mean", "normalization": "l2",
                              "metric": "inner_product", "query_prefix": "", "search": "meaning",
                              "browser": {"model": "Xenova/all-MiniLM-L6-v2"}, "probe": None},
                "vectors": {"file": "vectors.bin", "count": n, "dimension": dim, "quantization": "int8",
                            "dtype": "int8", "scale": scale, "bytes": len(blob),
                            "sha256": hashlib.sha256(blob).hexdigest()},
                "passages": {"count": n, "fields": ["id", "document_id", "page", "text"], "files": files},
                "documents": [{"id": f"d{d:06d}", "name": f"source-{d}.txt", "title": f"source {d}", "pages": 1,
                               "passages": 50} for d in range(-(-n // 50))]}
    (data / "manifest.json").write_text(_manifest_text(manifest), encoding="utf-8")
    for name in ("clio-common.js", "clio-static-search.js"):
        shutil.copyfile(ROOT / "docs" / "static-search" / name, folder / name)
    (folder / "bench.html").write_text(PAGE, encoding="utf-8")
    sizes = {}
    for p in sorted(data.iterdir()):
        raw = p.read_bytes()
        kind = "vectors" if p.name == "vectors.bin" else ("manifest" if p.name == "manifest.json" else "passages")
        sizes.setdefault(kind, [0, 0])
        sizes[kind][0] += len(raw)
        sizes[kind][1] += len(gzip.compress(raw, 6))
    return {k: {"bytes": v[0], "gzip": v[1]} for k, v in sizes.items()}


def measure(folder: Path, mbps: float, latency_ms: float) -> dict:
    from tests.browser_harness import chromium, static_server

    vector = np.random.default_rng(1).standard_normal(384).tolist()
    with static_server(folder) as server, chromium() as browser:
        page = browser.page()
        page.goto(server.url("bench.html"))
        page.wait_for("window.ready === true")
        page.throttle(latency_ms=latency_ms, down_bytes_per_s=mbps * 1e6 / 8)
        started = time.time()
        result = page.eval(f"bench('travel expense claim', {json.dumps(vector)})", timeout=3600)
        result["wall_s"] = round(time.time() - started, 1)
        heap = page.send("Runtime.getHeapUsage")
        result["heap_mb"] = round(heap.get("usedSize", 0) / 1e6, 1)
        page.close()
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[10000, 50000, 200000])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mbps", type=float, default=20.0, help="throttled download, megabits a second")
    parser.add_argument("--latency-ms", type=float, default=40.0)
    args = parser.parse_args(argv)
    results = []
    for n in args.sizes:
        folder = args.out / f"bench-{n}"
        shutil.rmtree(folder, ignore_errors=True)
        sizes = write_export(folder, n)
        timing = measure(folder, args.mbps, args.latency_ms)
        results.append({"passages": n, "sizes": sizes, **timing})
        total = sum(s["bytes"] for s in sizes.values()) / 1e6
        gz = sum(s["gzip"] for s in sizes.values()) / 1e6
        print(f"{n:>7} passages: {total:7.1f} MB ({gz:6.1f} MB gzipped); at {args.mbps:g} Mbit/s, first keyword "
              f"result {timing['to_keyword_ms'] / 1000:6.1f} s, by meaning {timing['to_meaning_ms'] / 1000:6.1f} s "
              f"(ranking {timing['meaning_ms']:.0f} ms); heap {timing['heap_mb']} MB", flush=True)
    (args.out / "results.json").write_text(json.dumps({"mbps": args.mbps, "latency_ms": args.latency_ms,
                                                       "results": results}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
