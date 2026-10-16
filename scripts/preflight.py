#!/usr/bin/env python
"""Check that a Clio deployment is ready to run where it is going.

Run it with the same environment the server will use, before the image is
carried into a secure environment and again on arrival:

    python scripts/preflight.py                 # human-readable report
    python scripts/preflight.py --probe         # also contact the model endpoint
    python scripts/preflight.py --json          # machine-readable, for CI/monitoring
    python scripts/preflight.py --strict        # warnings fail the run too

    docker run --rm --env-file .env clio:local python scripts/preflight.py

Exit status: 0 ready, 1 at least one check failed (or warned, with --strict).
It reads the live settings and the local model caches; it starts no server and
changes nothing. The same checks back GET /api/system/readiness and the
startup guard of CLIO_PROFILE=secure (services/readiness.py).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_MARK = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
_GROUPS = [("posture", "Security posture"), ("models", "Models and capabilities"),
           ("storage", "Storage"), ("errors", "Check errors")]


def render(report: dict) -> str:
    lines = [f"Clio deployment readiness  (profile: {report['profile']}, "
             f"offline mode: {'on' if report['offline_mode'] else 'off'})", ""]
    for group, title in _GROUPS:
        rows = [c for c in report["checks"] if c["group"] == group]
        if not rows:
            continue
        lines.append(title)
        for c in rows:
            lines.append(f"  [{_MARK[c['status']]}] {c['title']}: {c['detail']}")
            if c["fix"] and c["status"] != "pass":
                lines.append(f"         fix: {c['fix']}")
        lines.append("")
    lines.append("Where data can go (from the live configuration)")
    for e in report["egress"]:
        lines.append(f"  {e['state']:<9} {e['kind']:<22} {e['destination']}")
        if e["note"]:
            lines.append(f"            {e['note']}")
    c = report["counts"]
    lines += ["", f"{c['pass']} passed, {c['warn']} warnings, {c['fail']} failed -> "
                  + ("READY" if report["ready"] else "NOT READY")]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--probe", action="store_true", help="also contact the configured model endpoint")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    from services import readiness

    report = readiness.run_checks(probe=args.probe)
    print(json.dumps(report, indent=2) if args.json else render(report))
    if not report["ready"]:
        return 1
    if args.strict and report["counts"]["warn"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
