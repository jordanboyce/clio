#!/usr/bin/env python3
"""Write the /api/v1 description to docs/api/openapi-v1.json.

That file is the contract consuming apps build against, and tests/test_api_v1.py
fails while the live API differs from it. Run this only for a change you mean to
make, and read the diff first: see "Compatibility" in docs/APP_API.md.

    python scripts/export_api_spec.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SPEC_PATH = ROOT / "docs" / "api" / "openapi-v1.json"


def main() -> int:
    from api.v1 import v1_app

    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPEC_PATH.write_text(json.dumps(v1_app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {SPEC_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
