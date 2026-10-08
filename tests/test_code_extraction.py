"""Lenient source-code extraction: broken files still index usefully.

`CodeExtractor` is pure (no embeddings, no database), so these drive it
on files in tmp_path. The folder-scan tests at the end exercise
`UploadService._scan_folder`, which is a static method.
"""

from pathlib import Path

import pytest

from services.code_extractor import (
    CODE_FILENAMES,
    CodeExtractor,
    CodeLanguage,
    SUPPORTED_CODE_EXTENSIONS,
    is_code_file,
)
from services.document_extractor import DocumentExtractor, is_supported_filename


@pytest.fixture
def extractor():
    return CodeExtractor()


@pytest.fixture
def doc_extractor():
    return DocumentExtractor()


def _chunks(doc_extractor, path: Path):
    return doc_extractor.extract_code_chunks(path, document_id="doc")


def _symbol_names(chunks):
    return [c.symbol_name for c in chunks if c.symbol_name]


# ── Lenient brace matching ──────────────────────────────────────────────


def test_unbalanced_braces_do_not_collapse_into_one_symbol(extractor):
    body = "\n".join(f"  const v{i} = compute({i});" for i in range(8))
    src = (
        "function first() {\n" + body + "\n  if (x) {\n    return 1;\n  // missing closing brace\n\n"
        "function second() {\n" + body + "\n  return 2;\n}\n\n"
        "function third() {\n" + body + "\n  return 3;\n}\n"
    )
    symbols = extractor.extract_symbols(src, CodeLanguage.JAVASCRIPT)
    names = [s.name for s in symbols]
    assert names == ["first", "second", "third"]
    total = len(src.split("\n"))
    assert all(s.line_end - s.line_start + 1 < total * 0.6 for s in symbols)


def test_unterminated_string_does_not_swallow_following_functions(extractor):
    src = (
        'function broken() {\n'
        '  const s = "oops no closing quote;\n'
        '  return s;\n'
        '}\n'
        '\n'
        'function fine() {\n'
        '  return 42;\n'
        '}\n'
    )
    symbols = extractor.extract_symbols(src, CodeLanguage.JAVASCRIPT)
    assert [s.name for s in symbols] == ["broken", "fine"]
    assert symbols[0].line_end == 4
    assert symbols[1].line_start == 6 and symbols[1].line_end == 8


def test_rust_lifetimes_do_not_break_brace_matching(extractor):
    src = (
        "pub struct Parser<'a> {\n"
        "    input: &'a str,\n"
        "}\n"
        "\n"
        "impl<'a> Parser<'a> {\n"
        "    pub fn new(input: &'a str) -> Parser<'a> {\n"
        "        let c = 'x';\n"
        "        let nl = '\\n';\n"
        "        Parser { input }\n"
        "    }\n"
        "}\n"
        "\n"
        "fn longest<'a>(x: &'a str, y: &'a str) -> &'a str {\n"
        "    if x.len() > y.len() { x } else { y }\n"
        "}\n"
    )
    symbols = extractor.extract_symbols(src, CodeLanguage.RUST)
    by_name = {s.name: s for s in symbols}
    assert set(by_name) == {"Parser", "longest"}
    assert by_name["Parser"].line_end == 11 or by_name["Parser"].line_end == 3
    assert by_name["longest"].line_start == 13 and by_name["longest"].line_end == 15


def test_python_with_mixed_tabs_and_syntax_error_still_yields_functions(extractor):
    src = (
        "def alpha(x):\n"
        "\tif x:\n"
        "\t\treturn x + 1)\n"      # stray paren: syntax error
        "    return 0\n"           # mixed indentation
        "\n"
        "def beta(y):\n"
        "    '''docstring with\n"
        "a dedented line inside\n"
        "    '''\n"
        "    return y * 2\n"
        "\n"
        "class Gamma:\n"
        "\tdef method(self):\n"
        "\t\treturn 'm'\n"
    )
    chunks = extractor.chunk_code(src, "doc", "broken.py", CodeLanguage.PYTHON)
    names = _symbol_names(chunks)
    assert names == ["alpha", "beta", "Gamma"]
    beta = next(c for c in chunks if c.symbol_name == "beta")
    assert beta.line_start == 6 and beta.line_end == 10


def test_multiline_python_signature_is_one_symbol(extractor):
    src = (
        "def wide(\n"
        "    a: int,\n"
        "    b: int,\n"
        ") -> int:\n"
        "    return a + b\n"
        "\n"
        "def narrow():\n"
        "    pass\n"
    )
    symbols = extractor.extract_symbols(src, CodeLanguage.PYTHON)
    assert [(s.name, s.line_start, s.line_end) for s in symbols] == [("wide", 1, 5), ("narrow", 7, 8)]


# ── New language families ───────────────────────────────────────────────


def test_shell_functions_become_symbols(extractor):
    src = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "\n"
        "log() {\n"
        "  echo \"[$(date)] $*\" >&2  # a '{' in a comment\n"
        "}\n"
        "\n"
        "function deploy {\n"
        "  log 'deploying'\n"
        "  rsync -a ./build/ \"$TARGET\"\n"
        "}\n"
        "\n"
        "deploy\n"
    )
    symbols = extractor.extract_symbols(src, CodeLanguage.SHELL)
    assert [(s.name, s.symbol_type, s.line_start, s.line_end) for s in symbols] == [
        ("log", "function", 4, 6),
        ("deploy", "function", 8, 11),
    ]


def test_sql_create_statements_become_symbols(extractor):
    src = (
        "-- schema\n"
        "CREATE TABLE users (\n"
        "  id serial PRIMARY KEY,\n"
        "  email text NOT NULL\n"
        ");\n"
        "\n"
        "CREATE OR REPLACE FUNCTION touch() RETURNS trigger AS $$\n"
        "BEGIN\n"
        "  NEW.updated_at = now();\n"
        "  RETURN NEW;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n"
        "\n"
        "CREATE INDEX IF NOT EXISTS users_email_idx ON users (email);\n"
        "CREATE VIEW active_users AS SELECT * FROM users WHERE active;\n"
    )
    symbols = extractor.extract_symbols(src, CodeLanguage.SQL)
    assert [(s.name, s.symbol_type) for s in symbols] == [
        ("users", "table"),
        ("touch", "function"),
        ("users_email_idx", "index"),
        ("active_users", "view"),
    ]
    assert symbols[1].line_start == 7 and symbols[1].line_end == 12


def test_vue_component_yields_script_functions(extractor):
    src = (
        "<template>\n"
        "  <button @click=\"increment\">{{ count }}</button>\n"
        "</template>\n"
        "\n"
        "<script setup lang=\"ts\">\n"
        "import { ref } from 'vue'\n"
        "const count = ref(0)\n"
        "function increment() {\n"
        "  count.value++\n"
        "}\n"
        "const reset = () => {\n"
        "  count.value = 0\n"
        "}\n"
        "</script>\n"
        "\n"
        "<style scoped>\n"
        "button { color: red; }\n"
        "</style>\n"
    )
    symbols = extractor.extract_symbols(src, CodeLanguage.VUE)
    assert [(s.name, s.line_start, s.line_end) for s in symbols] == [
        ("increment", 8, 10),
        ("reset", 11, 13),
    ]
    chunks = extractor.chunk_code(src, "doc", "Counter.vue", CodeLanguage.VUE)
    assert "increment" in _symbol_names(chunks)
    assert any("<template>" in c.text for c in chunks)


def test_dockerfile_and_makefile_recognised_by_name(extractor, doc_extractor, tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM node:20 AS build\nWORKDIR /app\nCOPY . .\nRUN npm ci && npm run build\n\n"
        "FROM nginx:alpine\nCOPY --from=build /app/dist /usr/share/nginx/html\n",
        encoding="utf-8",
    )
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        "CC := gcc\n\nall: build test\n\nbuild:\n\t$(CC) -o app main.c\n\n"
        "test: build\n\t./app --self-test\n\n.PHONY: all build test\n",
        encoding="utf-8",
    )
    assert is_code_file("Dockerfile") and is_code_file("Makefile")
    assert is_code_file("GNUmakefile") and is_code_file("Dockerfile.dev")
    assert extractor.detect_language(str(dockerfile)) == CodeLanguage.DOCKERFILE
    assert extractor.detect_language(str(makefile)) == CodeLanguage.MAKEFILE
    assert extractor.detect_language("CMakeLists.txt") == CodeLanguage.CMAKE

    docker_chunks = _chunks(doc_extractor, dockerfile)
    assert docker_chunks and docker_chunks[0].language == "dockerfile"
    assert _symbol_names(docker_chunks) == ["build", "nginx:alpine"]

    make_chunks = _chunks(doc_extractor, makefile)
    assert make_chunks and make_chunks[0].language == "makefile"
    assert {"build", "test"} <= set(_symbol_names(make_chunks))


def test_code_filenames_constant_matches_detection():
    assert "makefile" in CODE_FILENAMES and ".gitignore" in CODE_FILENAMES
    assert ".env.example" in CODE_FILENAMES and ".env" not in CODE_FILENAMES
    for name in CODE_FILENAMES:
        assert is_code_file(name), name
        assert CodeExtractor().detect_language(name) != CodeLanguage.UNKNOWN, name


@pytest.mark.parametrize("ext", [
    ".sh", ".ps1", ".bat", ".sql", ".lua", ".pl", ".r", ".dart", ".ex", ".erl", ".hs",
    ".clj", ".zig", ".nim", ".jl", ".m", ".fs", ".vb", ".f90", ".cbl", ".groovy", ".gradle",
    ".tf", ".proto", ".graphql", ".vue", ".svelte", ".astro", ".css", ".scss", ".yaml",
    ".toml", ".ini", ".xml", ".csproj", ".cmake", ".mk", ".dockerfile",
])
def test_new_extensions_are_code(ext):
    assert ext in SUPPORTED_CODE_EXTENSIONS
    assert is_code_file(f"thing{ext}")
    assert CodeExtractor().detect_language(f"thing{ext}") != CodeLanguage.UNKNOWN


def test_documents_stay_documents():
    for ext in (".txt", ".md", ".json", ".html", ".ipynb"):
        assert ext not in SUPPORTED_CODE_EXTENSIONS
        assert not is_code_file(f"notes{ext}")


def test_generic_languages_find_declarations(extractor):
    lua = "local function helper(a)\n  return a\nend\n\nfunction M.run()\n  helper(1)\nend\n"
    assert [s.name for s in extractor.extract_symbols(lua, CodeLanguage.LUA)] == ["helper", "M.run"]

    tf = (
        'resource "aws_s3_bucket" "logs" {\n  bucket = "logs"\n}\n\n'
        'variable "region" {\n  default = "us-east-1"\n}\n'
    )
    assert [(s.name, s.symbol_type) for s in extractor.extract_symbols(tf, CodeLanguage.TERRAFORM)] == [
        ("aws_s3_bucket.logs", "resource"), ("region", "variable"),
    ]

    proto = "syntax = \"proto3\";\n\nmessage Ping {\n  int32 id = 1;\n}\n\nservice Echo {\n  rpc Send(Ping) returns (Ping);\n}\n"
    assert [(s.name, s.symbol_type) for s in extractor.extract_symbols(proto, CodeLanguage.PROTOBUF)] == [
        ("Ping", "message"), ("Echo", "service"),
    ]

    elixir = "defmodule Greeter do\n  def hello(name) do\n    \"hi #{name}\"\n  end\nend\n"
    syms = extractor.extract_symbols(elixir, CodeLanguage.ELIXIR)
    assert [(s.name, s.symbol_type, s.line_end) for s in syms] == [("Greeter", "module", 5)]

    yaml = "version: 2\n\njobs:\n  build:\n    steps:\n      - run: make\n\nworkflows:\n  main:\n    jobs: [build]\n"
    assert [s.name for s in extractor.extract_symbols(yaml, CodeLanguage.YAML)] == ["jobs", "workflows"]

    hs = "module Main where\n\nsquare :: Int -> Int\nsquare x = x * x\n\nmain :: IO ()\nmain = print (square 3)\n"
    assert [s.name for s in extractor.extract_symbols(hs, CodeLanguage.HASKELL)] == ["square", "main"]


# ── Degenerate inputs ───────────────────────────────────────────────────


def test_tiny_file_still_yields_one_chunk(doc_extractor, tmp_path):
    path = tmp_path / "tiny.py"
    path.write_text("x = 1\ny = 2\nz=x+y\n", encoding="utf-8")   # 20 chars over 3 lines
    chunks = _chunks(doc_extractor, path)
    assert len(chunks) == 1
    assert chunks[0].text.strip() == "x = 1\ny = 2\nz=x+y"
    assert chunks[0].symbol_type in ("file", "code_block")


def test_empty_file_raises_value_error_mentioning_empty(doc_extractor, tmp_path):
    path = tmp_path / "empty.js"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        _chunks(doc_extractor, path)
    blank = tmp_path / "blank.py"
    blank.write_text("\n\n   \n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        _chunks(doc_extractor, blank)


def test_binary_file_raises_value_error_mentioning_binary(doc_extractor, tmp_path):
    path = tmp_path / "blob.c"
    path.write_bytes(b"int main() {\x00\x00\x01\x02 return 0; }")
    with pytest.raises(ValueError, match="binary"):
        _chunks(doc_extractor, path)


def test_utf16_and_bom_files_decode(extractor, tmp_path):
    utf16 = tmp_path / "wide.py"
    utf16.write_text("def hello():\n    return 'héllo'\n", encoding="utf-16")
    content, language, _ = extractor.extract_file(str(utf16))
    assert content.startswith("def hello") and language == CodeLanguage.PYTHON

    bom = tmp_path / "bom.js"
    bom.write_text("function f() {}\n", encoding="utf-8-sig")
    content, _, _ = extractor.extract_file(str(bom))
    assert content.startswith("function f")

    latin = tmp_path / "old.pas"
    latin.write_bytes(b"unit Caf\xe9;\ninterface\nimplementation\nend.\n")
    content, _, unit = extractor.extract_file(str(latin))
    assert "Caf" in content and unit == "Caf\xe9"


def test_minified_js_chunks_by_size_with_minified_metadata(doc_extractor, tmp_path):
    path = tmp_path / "bundle.js"
    one_line = "!function(e){" + "var a=e.foo(1);" * 800 + "}(window);"
    path.write_text(one_line + "\n", encoding="utf-8")
    chunks = _chunks(doc_extractor, path)
    assert len(chunks) > 1
    assert all(c.symbol_type == "generated" for c in chunks)
    assert all(c.metadata.get("minified") is True for c in chunks)
    assert all(len(c.text) <= 1500 for c in chunks)
    assert "".join(c.text for c in chunks) == one_line


def test_oversized_symbol_in_long_file_is_replaced_by_nested_symbols(extractor):
    methods = "\n".join(
        f"    def method_{i}(self):\n        return {i}\n" for i in range(150)
    )
    src = "class Huge:\n" + methods + "\n\ndef tail():\n    return 'tail'\n"
    chunks = extractor.chunk_code(src, "doc", "huge.py", CodeLanguage.PYTHON)
    names = _symbol_names(chunks)
    assert "method_0" in names and "method_149" in names and "tail" in names
    total = len(src.split("\n"))
    assert all(c.line_end - c.line_start + 1 < total * 0.6 for c in chunks)
    assert next(c for c in chunks if c.symbol_name == "method_7").parent_symbol == "Huge"


def test_symbols_never_overlap(extractor):
    src = "function a() {\n  return 1;\n\nfunction b() {\n  return 2;\n}\n"
    chunks = extractor.chunk_code(src, "doc", "x.js", CodeLanguage.JAVASCRIPT)
    spans = sorted((c.line_start, c.line_end) for c in chunks)
    for (s1, e1), (s2, _) in zip(spans, spans[1:]):
        assert e1 < s2


def test_chunk_code_never_raises_on_garbage(extractor):
    garbage = "}}}{{{ ''' \"\"\" `` /* \n" * 40 + "def (:\n\tclass\n"
    for language in CodeLanguage:
        chunks = extractor.chunk_code(garbage, "doc", "junk", language)
        assert chunks, language
        assert all(c.text.strip() for c in chunks)


# ── Name-based support ──────────────────────────────────────────────────


def test_is_supported_filename():
    assert is_supported_filename("Makefile")
    assert is_supported_filename("Component.vue")
    assert is_supported_filename("CMakeLists.txt")
    assert is_supported_filename(".env.example")
    assert is_supported_filename("report.pdf")
    assert not is_supported_filename(".env")
    assert not is_supported_filename("app.exe")
    assert not is_supported_filename("archive.zip")
    assert DocumentExtractor.is_supported_filename("Dockerfile")
    assert ".vue" in DocumentExtractor.SUPPORTED_EXTENSIONS


def test_extract_text_routes_named_code_files(doc_extractor, tmp_path):
    cmake = tmp_path / "CMakeLists.txt"
    cmake.write_text("cmake_minimum_required(VERSION 3.20)\nproject(demo)\n", encoding="utf-8")
    result = doc_extractor.extract_text(cmake)
    assert result[1].startswith("cmake_minimum_required")
    with pytest.raises(ValueError, match="Unsupported"):
        doc_extractor.extract_text(tmp_path / "secrets.env")


# ── Folder scan ─────────────────────────────────────────────────────────


def test_scan_folder_skips_junk_and_reports_skipped_unsupported(tmp_path):
    from services.upload_service import UploadService

    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "src" / "Widget.vue").write_text("<script>export default {}</script>\n", encoding="utf-8")
    (root / "Makefile").write_text("all:\n\techo ok\n", encoding="utf-8")
    (root / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1\n", encoding="utf-8")
    (root / "src" / "vendor.min.js").write_text("!function(){}()\n", encoding="utf-8")
    (root / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (root / "poetry.lock").write_text("[[package]]\n", encoding="utf-8")
    (root / "tool.exe").write_bytes(b"MZ\x00\x00")
    (root / "font.woff2").write_bytes(b"wOF2")
    (root / "data.unknownext").write_text("??\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (root / "notes.txt").write_text("plain notes\n", encoding="utf-8")

    scan = UploadService._scan_folder(root)
    names = sorted(Path(p).name for p in scan.files)
    assert names == ["Makefile", "Widget.vue", "app.py", "notes.txt"]
    # data.unknownext and .env are unsupported by name; the rest are excluded
    # by pattern and not counted.
    assert scan.skipped_unsupported == 2
    assert len(scan) == 4 and list(scan) == scan.files


def test_scan_folder_with_explicit_extensions_keeps_old_behaviour(tmp_path):
    from services.upload_service import UploadService

    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "b.weird").write_text("x = 1\n", encoding="utf-8")
    (root / "c.js").write_text("x = 1\n", encoding="utf-8")

    scan = UploadService._scan_folder(root, file_extensions=[".py", ".weird"])
    assert sorted(Path(p).name for p in scan.files) == ["a.py", "b.weird"]
    assert scan.skipped_unsupported == 0


def test_scan_folder_skips_oversized_files_and_outside_symlinks(tmp_path, monkeypatch):
    from services.upload_service import UploadService

    root = tmp_path / "repo"
    root.mkdir()
    (root / "small.py").write_text("x = 1\n", encoding="utf-8")
    (root / "big.py").write_text("x = 1\n" * 200, encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("y = 2\n", encoding="utf-8")
    try:
        (root / "link.py").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    monkeypatch.setattr(UploadService, "SCAN_MAX_FILE_BYTES", 100)
    scan = UploadService._scan_folder(root)
    assert [Path(p).name for p in scan.files] == ["small.py"]
    assert scan.skipped_unsupported == 1
