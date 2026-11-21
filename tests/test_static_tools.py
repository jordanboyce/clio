"""F3: passages and vectors for other tools, and the script in the docs."""

import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

from services import static_export as se
from tests.static_real import real_env, real_model_cache, sample_collection  # noqa: F401
from tests.test_collection_bundle import env  # noqa: F401
from tests.test_static_export import _clio_scores, _public, site  # noqa: F401

DOC = Path(__file__).resolve().parent.parent / "docs" / "STATIC_SEARCH.md"


def _doc_script():
    text = DOC.read_text(encoding="utf-8")
    section = text.split("### Passages and vectors for other tools", 1)[1]
    return re.search(r"```python\n(.*?)```", section, re.S).group(1)


def test_lines_and_rows_line_up_with_the_full_precision_vectors(site, tmp_path):
    cid, _ = _public(site)
    out = tmp_path / "out"
    manifest, _ = se.write_export(cid, out, confirm_public=True, include="tools")
    entry = manifest["additions"]["tools"]
    rows = [json.loads(line) for line in (out / entry["passages"]).read_text(encoding="utf-8").splitlines()]
    vectors = np.load(out / entry["vectors"])
    assert vectors.dtype == np.float32 and vectors.shape == (len(rows), manifest["embedding"]["dimension"])
    assert [r["row"] for r in rows] == list(range(len(rows))) and rows[0]["title"]
    described = json.loads((out / entry["embedding"]).read_text(encoding="utf-8"))
    assert described["model"] == "fake-embed" and described["pooling"] == "mean" and "query_prefix" in described
    from tests.test_governance import FakeEmbedder

    q = np.asarray(FakeEmbedder().embed_query("holiday allowance"), dtype=np.float32)
    ours = vectors @ (q / np.linalg.norm(q))
    theirs = _clio_scores(cid, "holiday allowance")
    assert max(abs(float(s) - theirs[r["id"]]) for r, s in zip(rows, ours)) < 1e-5


def test_the_script_in_the_docs_reproduces_clios_scores(real_env, tmp_path):
    cid, _ = sample_collection()
    out = tmp_path / "out"
    se.write_export(cid, out, confirm_public=True, include="tools")
    script = _doc_script()
    assert len([line for line in script.strip().splitlines() if line.strip()]) <= 10
    probe = script + "\nimport json as _j\nprint('SCORES', _j.dumps({rows[i]['id']: float(scores[i]) for i in range(len(rows))}))\n"
    result = subprocess.run([sys.executable, "-c", probe], cwd=out, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]
    scores = json.loads(result.stdout.split("SCORES ", 1)[1])
    query = re.search(r'"([^"]+\?)"', script).group(1)
    theirs = _clio_scores(cid, query)
    assert set(scores) == set(theirs)
    # The docs state the tolerance: 0.001 for full-precision vectors.
    assert max(abs(scores[k] - theirs[k]) for k in scores) < 0.001
