"""Code extractor for source files across multiple programming languages.

This module provides intelligent code-aware text extraction and chunking
for codebases, preserving symbol boundaries and extracting metadata
useful for RAG-based code understanding.

Parsing is deliberately lenient: a file with syntax errors, unbalanced
braces, unterminated strings, mixed tabs and spaces, an odd encoding or a
minified single line must still index usefully. Wrong splits are
acceptable; a file that fails to index, or that collapses into one giant
symbol, is not.

Supported languages:
- Curly-brace: JavaScript, TypeScript, C#, Java, Go, Rust, C, C++, PHP,
  Swift, Kotlin, Scala, Dart, Objective-C, Groovy/Gradle
- Indentation-based: Python, Ruby, Nim, Haskell, F#, YAML, Makefile
- Legacy: Pascal, Delphi, Modula-2, Assembly, Fortran, COBOL, Visual Basic
- Scripts and data: shell, PowerShell, batch, SQL, Lua, Perl, R, Elixir,
  Erlang, Clojure, Zig, Julia, Terraform, Protobuf, GraphQL, CSS, TOML,
  INI, XML, Dockerfile, CMake, Vue/Svelte/Astro components
"""

import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class CodeLanguage(Enum):
    """Supported code languages."""
    # Modern languages
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    CSHARP = "csharp"
    JAVA = "java"
    GO = "go"
    RUST = "rust"
    C = "c"
    CPP = "cpp"
    PHP = "php"
    RUBY = "ruby"
    SWIFT = "swift"
    KOTLIN = "kotlin"
    SCALA = "scala"
    DART = "dart"
    OBJC = "objc"
    GROOVY = "groovy"
    # Legacy languages
    PASCAL = "pascal"
    DELPHI = "delphi"
    MODULA2 = "modula2"
    ASSEMBLY = "assembly"
    FORTRAN = "fortran"
    COBOL = "cobol"
    VISUALBASIC = "visualbasic"
    # Scripting
    SHELL = "shell"
    POWERSHELL = "powershell"
    BATCH = "batch"
    SQL = "sql"
    LUA = "lua"
    PERL = "perl"
    R = "r"
    ELIXIR = "elixir"
    ERLANG = "erlang"
    HASKELL = "haskell"
    CLOJURE = "clojure"
    ZIG = "zig"
    NIM = "nim"
    JULIA = "julia"
    FSHARP = "fsharp"
    # Infrastructure / schema
    TERRAFORM = "terraform"
    PROTOBUF = "protobuf"
    GRAPHQL = "graphql"
    # Single-file components
    VUE = "vue"
    SVELTE = "svelte"
    ASTRO = "astro"
    # Styles, config and build files treated as code
    CSS = "css"
    YAML = "yaml"
    TOML = "toml"
    INI = "ini"
    XML = "xml"
    MAKEFILE = "makefile"
    DOCKERFILE = "dockerfile"
    CMAKE = "cmake"
    # Fallback: known code-like file with no dedicated patterns
    GENERIC = "generic"
    UNKNOWN = "unknown"


@dataclass
class CodeSymbol:
    """Represents a code symbol (procedure, function, class, etc.)."""
    name: str
    symbol_type: str  # procedure, function, class, record, unit, module, label, etc.
    line_start: int
    line_end: int
    text: str
    unit_name: Optional[str] = None
    parent_symbol: Optional[str] = None  # For nested symbols
    parameters: Optional[str] = None
    return_type: Optional[str] = None
    visibility: Optional[str] = None  # public, private, protected
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CodeChunk:
    """A chunk of code with metadata for indexing."""
    chunk_id: str
    document_id: str
    filename: str
    text: str
    line_start: int
    line_end: int
    language: str
    unit_name: Optional[str] = None
    symbol_name: Optional[str] = None
    symbol_type: Optional[str] = None
    parent_symbol: Optional[str] = None
    chunk_index: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


# Well-known extension-less (or oddly-suffixed) files recognised by basename.
# Lower-case. `.env` itself is deliberately absent: it holds secrets.
CODE_FILENAME_LANGUAGES: Dict[str, CodeLanguage] = {
    'makefile': CodeLanguage.MAKEFILE,
    'gnumakefile': CodeLanguage.MAKEFILE,
    'justfile': CodeLanguage.MAKEFILE,
    'dockerfile': CodeLanguage.DOCKERFILE,
    'containerfile': CodeLanguage.DOCKERFILE,
    'jenkinsfile': CodeLanguage.GROOVY,
    'vagrantfile': CodeLanguage.RUBY,
    'rakefile': CodeLanguage.RUBY,
    'gemfile': CodeLanguage.RUBY,
    'brewfile': CodeLanguage.RUBY,
    'procfile': CodeLanguage.GENERIC,
    'cmakelists.txt': CodeLanguage.CMAKE,
    'pipfile': CodeLanguage.TOML,
    '.gitignore': CodeLanguage.GENERIC,
    '.dockerignore': CodeLanguage.GENERIC,
    '.editorconfig': CodeLanguage.INI,
    '.bashrc': CodeLanguage.SHELL,
    '.zshrc': CodeLanguage.SHELL,
    '.profile': CodeLanguage.SHELL,
    '.env.example': CodeLanguage.GENERIC,
}

# Exported for modules that classify files by name (services/file_kinds.py).
CODE_FILENAMES = frozenset(CODE_FILENAME_LANGUAGES)

# `Dockerfile.dev`, `Makefile.inc` and friends.
_CODE_FILENAME_PREFIXES: Tuple[Tuple[str, CodeLanguage], ...] = (
    ('dockerfile.', CodeLanguage.DOCKERFILE),
    ('containerfile.', CodeLanguage.DOCKERFILE),
    ('makefile.', CodeLanguage.MAKEFILE),
)

# Languages where '#' starts a line comment.
HASH_COMMENT_LANGUAGES = frozenset({
    CodeLanguage.SHELL, CodeLanguage.PYTHON, CodeLanguage.RUBY, CodeLanguage.PERL,
    CodeLanguage.R, CodeLanguage.YAML, CodeLanguage.TOML, CodeLanguage.NIM,
    CodeLanguage.JULIA, CodeLanguage.POWERSHELL, CodeLanguage.ELIXIR,
    CodeLanguage.MAKEFILE, CodeLanguage.DOCKERFILE, CodeLanguage.CMAKE,
    CodeLanguage.INI, CodeLanguage.GRAPHQL, CodeLanguage.GENERIC,
})
# Languages where '--' starts a line comment.
DASH_COMMENT_LANGUAGES = frozenset({
    CodeLanguage.SQL, CodeLanguage.LUA, CodeLanguage.HASKELL,
})
# Languages where '//' does NOT start a comment (so we must not treat it as one).
_NO_SLASH_COMMENT = HASH_COMMENT_LANGUAGES | DASH_COMMENT_LANGUAGES | {
    CodeLanguage.ERLANG, CodeLanguage.CLOJURE, CodeLanguage.VISUALBASIC,
    CodeLanguage.FORTRAN, CodeLanguage.COBOL, CodeLanguage.BATCH,
}
# Hash-comment languages that also take '//' (and `/* */`).
_ALSO_SLASH_COMMENT = frozenset({CodeLanguage.TERRAFORM, CodeLanguage.PHP})

# Lines that look like the start of a new declaration. Used only to recover
# from unbalanced blocks: a declaration at the same indentation as the one we
# are closing, preceded by a blank line, almost certainly starts a sibling.
_DECL_LINE_RE = re.compile(
    r'^(?:(?:export|default|declare|pub(?:\([^)]*\))?|public|private|protected|internal|'
    r'static|async|abstract|final|override|virtual|unsafe|extern|inline|local|friend|'
    r'shared|partial|sealed|open|data|unsafe|module)\s+)*'
    r'(?:'
    r'(?:function|func|fn|def|defp|defmodule|defmacro|class|struct|enum|interface|trait|'
    r'impl|type|module|namespace|sub|proc|package|resource|data|message|service|rpc|'
    r'create|import|from|use|using|require|include|template|typedef|union|object|'
    r'macro|iterator|subroutine|program|property|end|select|insert|update|alter|drop|'
    r'with|describe|it|test|contract|library|@\w+|#\[|#include|#define|#pragma|#if)\b'
    r'|(?:const|let|var|val)\s+\w+\s*=\s*(?:async\s+)?(?:function\b|\(|\w+\s*=>)'
    r'|[\w.:-]+\s*\(\s*\)\s*\{?\s*$'
    r'|[-+]\s*\('
    r')',
    re.IGNORECASE,
)

# Rust char literal: 'x', '\n', '\u{1F600}'. Anything else after a quote is a lifetime.
_RUST_CHAR_LITERAL = re.compile(r"'(?:\\u\{[0-9a-fA-F]+\}|\\.|[^'\\])'")

_SFC_SCRIPT_RE = re.compile(r'<script\b[^>]*>(.*?)</script\s*>', re.IGNORECASE | re.DOTALL)

# Hard caps for block scanning.
_BLOCK_SCAN_CAP = 2000     # lines a balanced block may span before we give up
_UNBALANCED_CAP = 200      # lines an unbalanced block may claim


def _indent_of(line: str) -> int:
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


class CodeExtractor:
    """Extracts and chunks code from various programming language files."""

    # File extension to language mapping
    EXTENSION_MAP = {
        # Python
        '.py': CodeLanguage.PYTHON,
        '.pyw': CodeLanguage.PYTHON,
        '.pyi': CodeLanguage.PYTHON,  # Type stubs
        # JavaScript/TypeScript
        '.js': CodeLanguage.JAVASCRIPT,
        '.jsx': CodeLanguage.JAVASCRIPT,
        '.mjs': CodeLanguage.JAVASCRIPT,
        '.cjs': CodeLanguage.JAVASCRIPT,
        '.ts': CodeLanguage.TYPESCRIPT,
        '.tsx': CodeLanguage.TYPESCRIPT,
        '.mts': CodeLanguage.TYPESCRIPT,
        '.cts': CodeLanguage.TYPESCRIPT,
        # C#
        '.cs': CodeLanguage.CSHARP,
        # Java
        '.java': CodeLanguage.JAVA,
        # Go
        '.go': CodeLanguage.GO,
        # Rust
        '.rs': CodeLanguage.RUST,
        # C/C++
        '.c': CodeLanguage.C,
        '.h': CodeLanguage.C,
        '.cpp': CodeLanguage.CPP,
        '.cxx': CodeLanguage.CPP,
        '.cc': CodeLanguage.CPP,
        '.hpp': CodeLanguage.CPP,
        '.hxx': CodeLanguage.CPP,
        '.hh': CodeLanguage.CPP,
        # PHP
        '.php': CodeLanguage.PHP,
        '.phtml': CodeLanguage.PHP,
        # Ruby
        '.rb': CodeLanguage.RUBY,
        '.rake': CodeLanguage.RUBY,
        '.gemspec': CodeLanguage.RUBY,
        # Swift
        '.swift': CodeLanguage.SWIFT,
        # Kotlin
        '.kt': CodeLanguage.KOTLIN,
        '.kts': CodeLanguage.KOTLIN,
        # Scala
        '.scala': CodeLanguage.SCALA,
        '.sc': CodeLanguage.SCALA,
        # Dart
        '.dart': CodeLanguage.DART,
        # Objective-C
        '.m': CodeLanguage.OBJC,
        '.mm': CodeLanguage.OBJC,
        # Groovy / Gradle
        '.groovy': CodeLanguage.GROOVY,
        '.gradle': CodeLanguage.GROOVY,
        # Pascal/Delphi
        '.pas': CodeLanguage.DELPHI,
        '.dpr': CodeLanguage.DELPHI,  # Delphi project
        '.dpk': CodeLanguage.DELPHI,  # Delphi package
        '.pp': CodeLanguage.PASCAL,   # Free Pascal
        '.inc': CodeLanguage.DELPHI,  # Include files
        '.dfm': CodeLanguage.DELPHI,  # Delphi form (treat as text)
        # Modula-2
        '.mod': CodeLanguage.MODULA2,
        '.def': CodeLanguage.MODULA2,  # Definition module
        '.mi': CodeLanguage.MODULA2,   # Implementation module
        # Assembly
        '.asm': CodeLanguage.ASSEMBLY,
        '.s': CodeLanguage.ASSEMBLY,
        # Fortran
        '.f': CodeLanguage.FORTRAN,
        '.f90': CodeLanguage.FORTRAN,
        '.f95': CodeLanguage.FORTRAN,
        '.for': CodeLanguage.FORTRAN,
        # COBOL
        '.cbl': CodeLanguage.COBOL,
        '.cob': CodeLanguage.COBOL,
        '.cpy': CodeLanguage.COBOL,
        # Visual Basic
        '.vb': CodeLanguage.VISUALBASIC,
        '.vbs': CodeLanguage.VISUALBASIC,
        '.bas': CodeLanguage.VISUALBASIC,
        # Shell
        '.sh': CodeLanguage.SHELL,
        '.bash': CodeLanguage.SHELL,
        '.zsh': CodeLanguage.SHELL,
        '.fish': CodeLanguage.SHELL,
        '.ksh': CodeLanguage.SHELL,
        # PowerShell
        '.ps1': CodeLanguage.POWERSHELL,
        '.psm1': CodeLanguage.POWERSHELL,
        '.psd1': CodeLanguage.POWERSHELL,
        # Batch
        '.bat': CodeLanguage.BATCH,
        '.cmd': CodeLanguage.BATCH,
        # SQL
        '.sql': CodeLanguage.SQL,
        '.psql': CodeLanguage.SQL,
        '.ddl': CodeLanguage.SQL,
        # Lua
        '.lua': CodeLanguage.LUA,
        # Perl
        '.pl': CodeLanguage.PERL,
        '.pm': CodeLanguage.PERL,
        '.t': CodeLanguage.PERL,
        # R
        '.r': CodeLanguage.R,
        '.rmd': CodeLanguage.R,
        # Elixir / Erlang
        '.ex': CodeLanguage.ELIXIR,
        '.exs': CodeLanguage.ELIXIR,
        '.erl': CodeLanguage.ERLANG,
        '.hrl': CodeLanguage.ERLANG,
        # Haskell
        '.hs': CodeLanguage.HASKELL,
        # Clojure
        '.clj': CodeLanguage.CLOJURE,
        '.cljs': CodeLanguage.CLOJURE,
        '.cljc': CodeLanguage.CLOJURE,
        '.edn': CodeLanguage.CLOJURE,
        # Zig / Nim / Julia
        '.zig': CodeLanguage.ZIG,
        '.nim': CodeLanguage.NIM,
        '.jl': CodeLanguage.JULIA,
        # F#
        '.fs': CodeLanguage.FSHARP,
        '.fsx': CodeLanguage.FSHARP,
        # Terraform / HCL
        '.tf': CodeLanguage.TERRAFORM,
        '.tfvars': CodeLanguage.TERRAFORM,
        '.hcl': CodeLanguage.TERRAFORM,
        # Schemas
        '.proto': CodeLanguage.PROTOBUF,
        '.graphql': CodeLanguage.GRAPHQL,
        '.gql': CodeLanguage.GRAPHQL,
        # Single-file components
        '.vue': CodeLanguage.VUE,
        '.svelte': CodeLanguage.SVELTE,
        '.astro': CodeLanguage.ASTRO,
        # Styles
        '.css': CodeLanguage.CSS,
        '.scss': CodeLanguage.CSS,
        '.sass': CodeLanguage.CSS,
        '.less': CodeLanguage.CSS,
        '.styl': CodeLanguage.CSS,
        # Config / markup treated as code
        '.yaml': CodeLanguage.YAML,
        '.yml': CodeLanguage.YAML,
        '.toml': CodeLanguage.TOML,
        '.ini': CodeLanguage.INI,
        '.cfg': CodeLanguage.INI,
        '.conf': CodeLanguage.INI,
        '.properties': CodeLanguage.INI,
        '.xml': CodeLanguage.XML,
        '.xsd': CodeLanguage.XML,
        '.xsl': CodeLanguage.XML,
        '.plist': CodeLanguage.XML,
        '.csproj': CodeLanguage.XML,
        '.vbproj': CodeLanguage.XML,
        '.sln': CodeLanguage.GENERIC,
        '.cmake': CodeLanguage.CMAKE,
        # Build / scripts
        '.mk': CodeLanguage.MAKEFILE,
        '.make': CodeLanguage.MAKEFILE,
        '.ninja': CodeLanguage.GENERIC,
        '.dockerfile': CodeLanguage.DOCKERFILE,
    }

    # Patterns for Pascal/Delphi
    DELPHI_PATTERNS = {
        'unit': re.compile(
            r'^\s*unit\s+(\w+)\s*;',
            re.IGNORECASE | re.MULTILINE
        ),
        'program': re.compile(
            r'^\s*program\s+(\w+)\s*;',
            re.IGNORECASE | re.MULTILINE
        ),
        'library': re.compile(
            r'^\s*library\s+(\w+)\s*;',
            re.IGNORECASE | re.MULTILINE
        ),
        'interface': re.compile(
            r'^\s*interface\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'implementation': re.compile(
            r'^\s*implementation\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'procedure': re.compile(
            r'^\s*(class\s+)?(procedure|constructor|destructor)\s+(\w+(?:\.\w+)?)\s*(\([^)]*\))?\s*;',
            re.IGNORECASE | re.MULTILINE
        ),
        'function': re.compile(
            r'^\s*(class\s+)?function\s+(\w+(?:\.\w+)?)\s*(\([^)]*\))?\s*:\s*(\w+)\s*;',
            re.IGNORECASE | re.MULTILINE
        ),
        'class': re.compile(
            r'^\s*(\w+)\s*=\s*class\s*(\([^)]*\))?\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'record': re.compile(
            r'^\s*(\w+)\s*=\s*(packed\s+)?record\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'type_section': re.compile(
            r'^\s*type\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'var_section': re.compile(
            r'^\s*var\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'const_section': re.compile(
            r'^\s*const\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'uses': re.compile(
            r'^\s*uses\s+([^;]+);',
            re.IGNORECASE | re.MULTILINE | re.DOTALL
        ),
        'end_block': re.compile(
            r'^\s*end\s*[;.]',
            re.IGNORECASE | re.MULTILINE
        ),
    }

    # Patterns for Modula-2
    MODULA2_PATTERNS = {
        'module': re.compile(
            r'^\s*(DEFINITION|IMPLEMENTATION)?\s*MODULE\s+(\w+)\s*;',
            re.IGNORECASE | re.MULTILINE
        ),
        'procedure': re.compile(
            r'^\s*PROCEDURE\s+(\w+)\s*(\([^)]*\))?\s*(:\s*\w+)?\s*;',
            re.IGNORECASE | re.MULTILINE
        ),
        'type': re.compile(
            r'^\s*TYPE\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'var': re.compile(
            r'^\s*VAR\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'const': re.compile(
            r'^\s*CONST\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'import': re.compile(
            r'^\s*(FROM\s+\w+\s+)?IMPORT\s+([^;]+);',
            re.IGNORECASE | re.MULTILINE
        ),
        'export': re.compile(
            r'^\s*EXPORT\s+([^;]+);',
            re.IGNORECASE | re.MULTILINE
        ),
        'end_module': re.compile(
            r'^\s*END\s+\w+\s*[;.]',
            re.IGNORECASE | re.MULTILINE
        ),
    }

    # Patterns for Assembly
    ASM_PATTERNS = {
        'label': re.compile(
            r'^(\w+)\s*:',
            re.MULTILINE
        ),
        'proc': re.compile(
            r'^\s*(\w+)\s+(PROC|proc)\s*(FAR|NEAR|far|near)?\s*$',
            re.MULTILINE
        ),
        'endp': re.compile(
            r'^\s*(\w+)\s+(ENDP|endp)\s*$',
            re.MULTILINE
        ),
        'macro': re.compile(
            r'^\s*(\w+)\s+(MACRO|macro)\s*',
            re.MULTILINE
        ),
        'endm': re.compile(
            r'^\s*(ENDM|endm)\s*$',
            re.MULTILINE
        ),
        'segment': re.compile(
            r'^\s*(\w+)\s+(SEGMENT|segment)\s*',
            re.MULTILINE
        ),
        'ends': re.compile(
            r'^\s*(\w+)\s+(ENDS|ends)\s*$',
            re.MULTILINE
        ),
        'comment_block': re.compile(
            r'^\s*;[-=]+\s*$',  # Comment separator lines
            re.MULTILINE
        ),
    }

    # Patterns for Python. A signature may span several lines, so only the
    # head of the declaration is matched; the block finder locates the colon.
    PYTHON_PATTERNS = {
        'class': re.compile(
            r'^(\s*)class\s+(\w+)\b',
            re.MULTILINE
        ),
        'function': re.compile(
            r'^(\s*)(?:async\s+)?def\s+(\w+)\s*\(',
            re.MULTILINE
        ),
        'method': re.compile(
            r'^(\s+)(?:async\s+)?def\s+(\w+)\s*\(',
            re.MULTILINE
        ),
    }

    # Patterns for curly-brace languages (JS/TS/C#/Java/Go/Rust/C/C++/PHP/Swift/Kotlin/Scala/...)
    # The opening brace is optional on the declaration line: Allman-style
    # code puts it on the next line, and the block finder tolerates that.
    CURLY_BRACE_PATTERNS = {
        # JavaScript/TypeScript
        'js_function': re.compile(
            r'^(\s*)(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*(?:<[^>]*>)?\s*\(',
            re.MULTILINE
        ),
        'js_arrow': re.compile(
            r'^(\s*)(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::\s*[^=]+)?=\s*(?:async\s+)?(?:\([^)]*\)|[^=]+)\s*=>',
            re.MULTILINE
        ),
        'js_class': re.compile(
            r'^(\s*)(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)(?:<[^>]*>)?(?:\s+extends\s+[\w.<>, ]+)?(?:\s+implements\s+[^{]+)?\s*\{?',
            re.MULTILINE
        ),
        'js_method': re.compile(
            r'^(\s+)(?:public\s+|private\s+|protected\s+)?(?:static\s+)?(?:async\s+)?(?:get\s+|set\s+)?(?!if\b|for\b|while\b|switch\b|catch\b|return\b)(\w+)\s*(?:<[^>]*>)?\s*\([^)]*\)\s*(?::\s*[^{]+)?\s*\{',
            re.MULTILINE
        ),
        # TypeScript interface/type
        'ts_interface': re.compile(
            r'^(\s*)(?:export\s+)?(?:declare\s+)?interface\s+(\w+)(?:<[^>]*>)?(?:\s+extends\s+[^{]+)?\s*\{?',
            re.MULTILINE
        ),
        'ts_type': re.compile(
            r'^(\s*)(?:export\s+)?(?:declare\s+)?type\s+(\w+)(?:<[^>]*>)?\s*=',
            re.MULTILINE
        ),
        'ts_enum': re.compile(
            r'^(\s*)(?:export\s+)?(?:declare\s+)?(?:const\s+)?enum\s+(\w+)\s*\{?',
            re.MULTILINE
        ),
        # C#/Java
        'csharp_class': re.compile(
            r'^(\s*)(?:(?:public|private|protected|internal|static|abstract|sealed|partial|final|export)\s+)*(?:class|struct|record|enum)\s+(\w+)(?:<[^>]*>)?(?:\s*:\s*[^{]+)?(?:\s+extends\s+[\w.<>, ]+)?(?:\s+implements\s+[^{]+)?\s*\{?\s*$',
            re.MULTILINE
        ),
        'csharp_interface': re.compile(
            r'^(\s*)(?:(?:public|private|protected|internal)\s+)*interface\s+(\w+)(?:<[^>]*>)?(?:\s*:\s*[^{]+)?(?:\s+extends\s+[^{]+)?\s*\{?\s*$',
            re.MULTILINE
        ),
        'csharp_method': re.compile(
            r'^(\s*)(?!\s*(?:else|return|new|throw|case|if|for|foreach|while|switch|catch|using|yield|await|delete|typedef)\b)'
            r'(?:(?:public|private|protected|internal|static|virtual|override|abstract|async|final|synchronized|native|unsafe|extern|inline|constexpr|explicit|friend|export)\s+)*'
            r'(?:[\w.<>\[\]\*&:,?]+\s+)+\*?(\w+)\s*(?:<[^>]*>)?\s*\([^;{]*\)\s*(?:const\s*)?(?:override\s*|final\s*|noexcept\s*)*(?:throws\s+[\w.,\s]+)?(?:where\s+[^{]+)?(?:=>)?\s*(?:\{.*)?$',
            re.MULTILINE
        ),
        'csharp_property': re.compile(
            r'^(\s*)(?:(?:public|private|protected|internal|static|virtual|override|abstract)\s+)*(?:\w+(?:<[^>]*>)?(?:\[\])?\??)\s+(\w+)\s*\{\s*(?:get|set|init)',
            re.MULTILINE
        ),
        # Constructors and C functions without a return type: `Foo(int x) {`
        'bare_function': re.compile(
            r'^(\s*)(?!\s*(?:else|return|new|throw|case|if|for|foreach|while|switch|catch|using|sizeof|typedef)\b)(?:[A-Za-z_]\w*::)?(?:~)?([A-Za-z_]\w*)\s*\([^;{]*\)\s*(?:const\s*)?(?::\s*[^{]+)?\{?\s*$',
            re.MULTILINE
        ),
        # Go
        'go_func': re.compile(
            r'^func\s+(?:\([^)]+\)\s+)?(\w+)\s*(?:\[[^\]]*\])?\s*\(',
            re.MULTILINE
        ),
        'go_type': re.compile(
            r'^type\s+(\w+)\s+(?:struct|interface)\s*\{',
            re.MULTILINE
        ),
        # Rust
        'rust_fn': re.compile(
            r'^(\s*)(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+"[^"]*"\s+)?fn\s+(\w+)(?:<[^>]*>)?\s*\(',
            re.MULTILINE
        ),
        'rust_struct': re.compile(
            r'^(\s*)(?:pub(?:\([^)]*\))?\s+)?struct\s+(\w+)(?:<[^>]*>)?',
            re.MULTILINE
        ),
        'rust_impl': re.compile(
            r'^(\s*)(?:unsafe\s+)?impl(?:<[^>]*>)?\s+(?:[\w:]+(?:<[^>]*>)?\s+for\s+)?(\w+)(?:<[^>]*>)?(?:\s+where\s+[^{]+)?\s*\{?',
            re.MULTILINE
        ),
        'rust_trait': re.compile(
            r'^(\s*)(?:pub(?:\([^)]*\))?\s+)?(?:unsafe\s+)?trait\s+(\w+)(?:<[^>]*>)?(?:\s*:\s*[^{]+)?\s*\{?',
            re.MULTILINE
        ),
        'rust_enum': re.compile(
            r'^(\s*)(?:pub(?:\([^)]*\))?\s+)?enum\s+(\w+)(?:<[^>]*>)?\s*\{?',
            re.MULTILINE
        ),
        'rust_mod': re.compile(
            r'^(\s*)(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*\{',
            re.MULTILINE
        ),
        # PHP
        'php_function': re.compile(
            r'^(\s*)(?:(?:public|private|protected|static|abstract|final)\s+)*function\s+&?(\w+)\s*\(',
            re.MULTILINE
        ),
        'php_class': re.compile(
            r'^(\s*)(?:(?:abstract|final|readonly)\s+)*(?:class|interface|trait|enum)\s+(\w+)(?:\s+extends\s+[\w\\]+)?(?:\s+implements\s+[^{]+)?\s*\{?',
            re.MULTILINE
        ),
        # Ruby
        'ruby_class': re.compile(
            r'^(\s*)class\s+(\w+)(?:\s*<\s*\w+)?',
            re.MULTILINE
        ),
        'ruby_module': re.compile(
            r'^(\s*)module\s+(\w+)',
            re.MULTILINE
        ),
        'ruby_def': re.compile(
            r'^(\s*)def\s+(?:self\.)?(\w+[?!=]?)',
            re.MULTILINE
        ),
        # Swift
        'swift_func': re.compile(
            r'^(\s*)(?:(?:public|private|internal|open|fileprivate|static|class|override|mutating|final|@objc)\s+)*func\s+(\w+)(?:<[^>]*>)?\s*\(',
            re.MULTILINE
        ),
        'swift_class': re.compile(
            r'^(\s*)(?:(?:public|private|internal|open|fileprivate|final)\s+)*(?:class|actor|protocol|extension|enum)\s+(\w+)(?:<[^>]*>)?(?:\s*:\s*[^{]+)?\s*\{?',
            re.MULTILINE
        ),
        'swift_struct': re.compile(
            r'^(\s*)(?:(?:public|private|internal|fileprivate)\s+)*struct\s+(\w+)(?:<[^>]*>)?(?:\s*:\s*[^{]+)?\s*\{?',
            re.MULTILINE
        ),
        # Kotlin
        'kotlin_fun': re.compile(
            r'^(\s*)(?:(?:public|private|protected|internal|inline|suspend|override|open|operator|infix)\s+)*fun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?(\w+)\s*\(',
            re.MULTILINE
        ),
        'kotlin_class': re.compile(
            r'^(\s*)(?:(?:public|private|protected|internal|open|abstract|sealed|data|enum|inner|annotation|value)\s+)*(?:class|object|interface)\s+(\w+)(?:<[^>]*>)?',
            re.MULTILINE
        ),
        # Scala
        'scala_def': re.compile(
            r'^(\s*)(?:(?:override|private|protected|final|implicit)\s+)*def\s+(\w+)(?:\[[^\]]*\])?\s*(?:\([^)]*\))*',
            re.MULTILINE
        ),
        'scala_class': re.compile(
            r'^(\s*)(?:abstract\s+|sealed\s+|final\s+)?(?:case\s+)?class\s+(\w+)(?:\[[^\]]*\])?',
            re.MULTILINE
        ),
        'scala_object': re.compile(
            r'^(\s*)(?:case\s+)?object\s+(\w+)',
            re.MULTILINE
        ),
        'scala_trait': re.compile(
            r'^(\s*)(?:sealed\s+)?trait\s+(\w+)(?:\[[^\]]*\])?',
            re.MULTILINE
        ),
        # Dart
        'dart_class': re.compile(
            r'^(\s*)(?:abstract\s+|base\s+|final\s+|sealed\s+)*(?:class|mixin|enum|extension)\s+(\w+)',
            re.MULTILINE
        ),
        # Objective-C
        'objc_interface': re.compile(
            r'^(\s*)@(?:interface|implementation|protocol)\s+(\w+)',
            re.MULTILINE
        ),
        'objc_method': re.compile(
            r'^(\s*)[-+]\s*\([^)]*\)\s*(\w+)',
            re.MULTILINE
        ),
        # Groovy / Gradle
        'groovy_def': re.compile(
            r'^(\s*)(?:(?:public|private|protected|static|final|synchronized)\s+)*def\s+(\w+)\s*\(',
            re.MULTILINE
        ),
        'gradle_task': re.compile(
            r'^(\s*)(?:tasks\.register|task)\s*\(?\s*[\'"]?(\w+)',
            re.MULTILINE
        ),
        'gradle_block': re.compile(
            r'^()(\w+)\s*\{\s*$',
            re.MULTILINE
        ),
    }

    # Generic line-wise declaration finders. Each entry is
    # (symbol_type, pattern): `name` is the symbol name; `kind`, when present
    # and symbol_type is 'kind', becomes the symbol type. Anchored at line
    # start, cheap, and happy to be wrong occasionally.
    _I = re.IGNORECASE
    GENERIC_PATTERNS: Dict[CodeLanguage, List[Tuple[str, "re.Pattern"]]] = {
        CodeLanguage.SHELL: [
            ('function', re.compile(r'^\s*function\s+(?P<name>[\w.:-]+)')),
            ('function', re.compile(r'^\s*(?P<name>[\w.:-]+)\s*\(\s*\)')),
        ],
        CodeLanguage.POWERSHELL: [
            ('kind', re.compile(r'^\s*(?P<kind>function|filter|workflow|class|enum|configuration)\s+(?P<name>[\w:.-]+)', _I)),
        ],
        CodeLanguage.BATCH: [
            ('label', re.compile(r'^\s*:(?!:)(?P<name>[\w.-]+)\s*$')),
        ],
        CodeLanguage.SQL: [
            ('kind', re.compile(
                r'^\s*CREATE\s+(?:OR\s+(?:REPLACE|ALTER)\s+)?'
                r'(?:(?:GLOBAL|LOCAL|TEMP|TEMPORARY|UNIQUE|MATERIALIZED|CLUSTERED|NONCLUSTERED|'
                r'DEFINER\s*=\s*\S+|ALGORITHM\s*=\s*\S+|SQL\s+SECURITY\s+\w+|FULLTEXT|SPATIAL)\s+)*'
                r'(?P<kind>TABLE|VIEW|FUNCTION|PROCEDURE|PROC|TRIGGER|INDEX|TYPE|SCHEMA|DATABASE|'
                r'SEQUENCE|PACKAGE\s+BODY|PACKAGE|EXTENSION|POLICY|ROLE|DOMAIN|AGGREGATE)\s+'
                r'(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[\w.$"`\[\]]+)', _I)),
        ],
        CodeLanguage.LUA: [
            ('function', re.compile(r'^\s*(?:local\s+)?function\s+(?P<name>[\w.:]+)')),
            ('function', re.compile(r'^\s*(?:local\s+)?(?P<name>[\w.]+)\s*=\s*function\b')),
        ],
        CodeLanguage.PERL: [
            ('package', re.compile(r'^\s*package\s+(?P<name>[\w:]+)')),
            ('sub', re.compile(r'^\s*sub\s+(?P<name>\w+)')),
        ],
        CodeLanguage.R: [
            ('function', re.compile(r'^\s*(?P<name>[\w.]+)\s*(?:<-|=)\s*function\s*\(')),
        ],
        CodeLanguage.ELIXIR: [
            ('kind', re.compile(r'^\s*(?P<kind>defmodule|defprotocol|defimpl)\s+(?P<name>[\w.]+)')),
            ('kind', re.compile(r'^\s*(?P<kind>defp?|defmacrop?|defguardp?|defdelegate)\s+(?P<name>[\w?!]+)')),
        ],
        CodeLanguage.ERLANG: [
            ('module', re.compile(r'^-module\(\s*(?P<name>\w+)')),
            ('function', re.compile(r'^(?P<name>[a-z][\w@]*)\s*\(')),
        ],
        CodeLanguage.HASKELL: [
            ('module', re.compile(r'^\s*module\s+(?P<name>[\w.]+)')),
            ('kind', re.compile(r"^\s*(?P<kind>data|newtype|type|class|instance)\s+(?:\([^)]*\)\s*=>\s*)?(?P<name>[\w.']+)")),
            ('function', re.compile(r"^\s*(?P<name>[a-z_][\w']*)\s*::")),
        ],
        CodeLanguage.CLOJURE: [
            ('kind', re.compile(r'^\s*\((?P<kind>ns|defn-?|defmacro|defmulti|defmethod|defprotocol|defrecord|deftype|defonce|definterface|defstruct|def)\s+(?:\^\S+\s+)?(?P<name>[^\s()\[\]{}"]+)')),
        ],
        CodeLanguage.ZIG: [
            ('function', re.compile(r'^\s*(?:pub\s+)?(?:export\s+)?(?:extern\s+)?(?:inline\s+)?fn\s+(?P<name>\w+)')),
            ('type', re.compile(r'^\s*(?:pub\s+)?const\s+(?P<name>\w+)\s*=\s*(?:packed\s+|extern\s+)?(?:struct|enum|union)\b')),
        ],
        CodeLanguage.NIM: [
            ('kind', re.compile(r'^\s*(?P<kind>proc|func|method|iterator|template|macro|converter)\s+(?P<name>[\w`]+)')),
            ('type', re.compile(r'^\s+(?P<name>\w+)\*?\s*=\s*(?:ref\s+|ptr\s+)?(?:object|enum|tuple|distinct)\b')),
        ],
        CodeLanguage.JULIA: [
            ('function', re.compile(r'^\s*function\s+(?P<name>[\w.!]+)')),
            ('struct', re.compile(r'^\s*(?:mutable\s+)?struct\s+(?P<name>\w+)')),
            ('module', re.compile(r'^\s*(?:bare)?module\s+(?P<name>\w+)')),
            ('macro', re.compile(r'^\s*macro\s+(?P<name>\w+)')),
            ('type', re.compile(r'^\s*(?:abstract|primitive)\s+type\s+(?P<name>\w+)')),
        ],
        CodeLanguage.FSHARP: [
            ('kind', re.compile(r"^\s*(?P<kind>type|module|namespace)\s+(?:rec\s+)?(?:private\s+|internal\s+|public\s+)?(?P<name>[\w.']+)")),
            ('function', re.compile(r"^\s*(?:let|and)\s+(?:rec\s+)?(?:inline\s+)?(?:private\s+|internal\s+)?(?:mutable\s+)?(?P<name>[\w']+)")),
            ('member', re.compile(r"^\s*(?:(?:static|abstract|override|default)\s+)*member\s+(?:\w+\.)?(?P<name>[\w']+)")),
        ],
        CodeLanguage.VISUALBASIC: [
            ('kind', re.compile(
                r'^\s*(?:(?:Public|Private|Protected|Friend|Shared|Overrides|Overridable|Overloads|'
                r'MustOverride|NotOverridable|Static|Partial|MustInherit|NotInheritable|ReadOnly|'
                r'WriteOnly|Default|Async|Iterator)\s+)*'
                r'(?P<kind>Sub|Function|Property|Class|Module|Structure|Interface|Enum|Namespace)\s+(?P<name>\w+)', _I)),
        ],
        CodeLanguage.FORTRAN: [
            ('kind', re.compile(
                r'^\s*(?:(?:recursive|pure|elemental|impure|non_recursive)\s+)*'
                r'(?:(?:integer|real|logical|character|complex|double\s+precision|type\s*\([^)]*\)|class\s*\([^)]*\))(?:\s*\([^)]*\))?\s+)?'
                r'(?:module\s+(?=function|subroutine))?'
                r'(?P<kind>subroutine|function|program|module(?!\s+procedure)|submodule)\s+(?P<name>\w+)', _I)),
        ],
        CodeLanguage.COBOL: [
            ('kind', re.compile(r'^(?:\d{6})?\s*(?P<name>\w[\w-]*)\s+(?P<kind>DIVISION|SECTION)\s*\.', _I)),
            ('paragraph', re.compile(r'^(?:\d{6})?\s{0,11}(?P<name>[A-Za-z][\w-]*)\s*\.\s*$')),
        ],
        CodeLanguage.TERRAFORM: [
            ('kind', re.compile(r'^\s*(?P<kind>resource|data)\s+"(?P<name>[^"]+)"\s+"(?P<name2>[^"]+)"')),
            ('kind', re.compile(r'^\s*(?P<kind>module|variable|output|provider|locals|terraform|backend)\b(?:\s+"(?P<name>[^"]+)")?')),
        ],
        CodeLanguage.PROTOBUF: [
            ('kind', re.compile(r'^\s*(?P<kind>message|service|enum|extend|oneof)\s+(?P<name>\w+)')),
            ('rpc', re.compile(r'^\s*rpc\s+(?P<name>\w+)')),
        ],
        CodeLanguage.GRAPHQL: [
            ('kind', re.compile(r'^\s*(?:extend\s+)?(?P<kind>type|input|enum|interface|union|scalar|directive|schema|fragment|query|mutation|subscription)\b\s*@?(?P<name>\w+)?')),
        ],
        CodeLanguage.CSS: [
            ('rule', re.compile(r'^(?P<name>[^\s{}/@;][^{};]{0,200}?)\s*\{')),
            ('at_rule', re.compile(r'^(?P<name>@[\w-]+[^{;]{0,200}?)\s*\{')),
        ],
        CodeLanguage.YAML: [
            ('key', re.compile(r'^(?P<name>[A-Za-z0-9_$][\w.\-/ $]{0,80}|"[^"]+"|\'[^\']+\')\s*:(?:\s|$)')),
        ],
        CodeLanguage.TOML: [
            ('section', re.compile(r'^\s*\[\[?\s*(?P<name>[^\]]+?)\s*\]?\]')),
        ],
        CodeLanguage.INI: [
            ('section', re.compile(r'^\s*\[\s*(?P<name>[^\]]+?)\s*\]')),
        ],
        CodeLanguage.MAKEFILE: [
            ('define', re.compile(r'^define\s+(?P<name>\S+)')),
            ('rule', re.compile(r'^(?P<name>[^\s:#=][^:=#]{0,120}?)\s*::?(?!=)')),
        ],
        CodeLanguage.DOCKERFILE: [
            ('stage', re.compile(r'^\s*FROM\s+(?:--platform=\S+\s+)?(?P<name>\S+)(?:\s+AS\s+(?P<alias>[\w.-]+))?', _I)),
        ],
        CodeLanguage.CMAKE: [
            ('kind', re.compile(r'^\s*(?P<kind>function|macro)\s*\(\s*(?P<name>\w+)', _I)),
        ],
    }

    # How a generic declaration's block ends.
    _BLOCK_STYLE: Dict[CodeLanguage, str] = {
        CodeLanguage.SHELL: 'brace',
        CodeLanguage.POWERSHELL: 'brace',
        CodeLanguage.PERL: 'brace',
        CodeLanguage.R: 'brace',
        CodeLanguage.ZIG: 'brace',
        CodeLanguage.TERRAFORM: 'brace',
        CodeLanguage.PROTOBUF: 'brace',
        CodeLanguage.GRAPHQL: 'brace',
        CodeLanguage.CSS: 'brace',
        CodeLanguage.LUA: 'end',
        CodeLanguage.JULIA: 'end',
        CodeLanguage.ELIXIR: 'end',
        CodeLanguage.VISUALBASIC: 'end_kind',
        CodeLanguage.FORTRAN: 'end_kind',
        CodeLanguage.CMAKE: 'end_kind',
        CodeLanguage.HASKELL: 'indent',
        CodeLanguage.NIM: 'indent',
        CodeLanguage.FSHARP: 'indent',
        CodeLanguage.YAML: 'indent',
        CodeLanguage.MAKEFILE: 'indent',
        CodeLanguage.BATCH: 'next_decl',
        CodeLanguage.COBOL: 'next_decl',
        CodeLanguage.TOML: 'next_decl',
        CodeLanguage.INI: 'next_decl',
        CodeLanguage.DOCKERFILE: 'next_decl',
        CodeLanguage.SQL: 'sql',
        CodeLanguage.ERLANG: 'dot',
        CodeLanguage.CLOJURE: 'paren',
    }

    _KIND_ALIASES = {
        'defp': 'function', 'def': 'function', 'defmacro': 'macro', 'defmacrop': 'macro',
        'defguard': 'guard', 'defguardp': 'guard', 'defdelegate': 'function',
        'defmodule': 'module', 'defprotocol': 'protocol', 'defimpl': 'impl',
        'defn': 'function', 'defn-': 'function', 'defmulti': 'function',
        'defmethod': 'function', 'defrecord': 'record', 'deftype': 'type',
        'defonce': 'def', 'definterface': 'interface', 'defstruct': 'struct',
        'ns': 'namespace', 'proc': 'procedure', 'sub': 'procedure',
        'package body': 'package',
    }

    _END_KEYWORD_OPENERS: Dict[CodeLanguage, frozenset] = {
        CodeLanguage.LUA: frozenset({'function', 'if', 'for', 'while', 'do', 'repeat'}),
        CodeLanguage.JULIA: frozenset({
            'function', 'if', 'for', 'while', 'begin', 'let', 'struct', 'mutable',
            'module', 'baremodule', 'do', 'try', 'quote', 'macro', 'abstract', 'primitive',
        }),
        CodeLanguage.ELIXIR: frozenset(),  # `do` at line end and `fn` open blocks
    }

    CURLY_BRACE_LANGUAGES = frozenset({
        CodeLanguage.JAVASCRIPT, CodeLanguage.TYPESCRIPT, CodeLanguage.CSHARP,
        CodeLanguage.JAVA, CodeLanguage.GO, CodeLanguage.RUST, CodeLanguage.C,
        CodeLanguage.CPP, CodeLanguage.PHP, CodeLanguage.SWIFT, CodeLanguage.KOTLIN,
        CodeLanguage.SCALA, CodeLanguage.DART, CodeLanguage.OBJC, CodeLanguage.GROOVY,
    })
    SFC_LANGUAGES = frozenset({CodeLanguage.VUE, CodeLanguage.SVELTE, CodeLanguage.ASTRO})

    # Minified / generated detection thresholds
    MINIFIED_MAX_LINE = 5000
    MINIFIED_AVG_LINE = 500

    def __init__(self, chunk_size: int = 1500, chunk_overlap: int = 200):
        """Initialize the code extractor.

        Args:
            chunk_size: Maximum characters per chunk (larger for code)
            chunk_overlap: Overlap between chunks for context
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def detect_language(self, file_path: str) -> CodeLanguage:
        """Detect the programming language from file extension or well-known basename."""
        name = Path(file_path).name.lower()
        if name in CODE_FILENAME_LANGUAGES:
            return CODE_FILENAME_LANGUAGES[name]
        for prefix, language in _CODE_FILENAME_PREFIXES:
            if name.startswith(prefix):
                return language
        ext = Path(file_path).suffix.lower()
        return self.EXTENSION_MAP.get(ext, CodeLanguage.UNKNOWN)

    def extract_file(self, file_path: str, encoding: str = 'utf-8') -> Tuple[str, CodeLanguage, Optional[str]]:
        """Read a code file and detect its language.

        Returns:
            Tuple of (content, language, unit_name)

        Raises:
            ValueError: if the file looks binary (NUL bytes in its head)
        """
        path = Path(file_path)
        data = path.read_bytes()
        content = self.decode_bytes(data, path.name, encoding=encoding)

        language = self.detect_language(file_path)
        unit_name = self._extract_unit_name(content, language)

        return content, language, unit_name

    @staticmethod
    def decode_bytes(data: bytes, name: str = "", encoding: str = 'utf-8') -> str:
        """Decode source bytes leniently, refusing binary content.

        UTF-16 is detected by its BOM (a UTF-16 file is full of NUL bytes, so
        the binary check must come after). `utf-8-sig` strips a UTF-8 BOM.
        `latin-1` never fails, so the chain always produces text.
        """
        if data.startswith((b'\xff\xfe', b'\xfe\xff')):
            try:
                return data.decode('utf-16')
            except UnicodeDecodeError:
                pass
        if b'\x00' in data[:8192]:
            raise ValueError(f"{name or 'file'} looks like a binary file")

        encodings = ['utf-8-sig', encoding, 'latin-1', 'cp1252', 'iso-8859-1']
        for enc in encodings:
            try:
                return data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode('utf-8', errors='replace')

    def _extract_unit_name(self, content: str, language: CodeLanguage) -> Optional[str]:
        """Extract the main unit/module name from code."""
        if language in (CodeLanguage.PASCAL, CodeLanguage.DELPHI):
            # Try unit first
            match = self.DELPHI_PATTERNS['unit'].search(content)
            if match:
                return match.group(1)
            # Try program
            match = self.DELPHI_PATTERNS['program'].search(content)
            if match:
                return match.group(1)
            # Try library
            match = self.DELPHI_PATTERNS['library'].search(content)
            if match:
                return match.group(1)

        elif language == CodeLanguage.MODULA2:
            match = self.MODULA2_PATTERNS['module'].search(content)
            if match:
                return match.group(2)

        elif language == CodeLanguage.ASSEMBLY:
            # Use filename as unit name for assembly
            return None

        return None

    def extract_symbols(self, content: str, language: CodeLanguage) -> List[CodeSymbol]:
        """Extract code symbols (procedures, functions, classes, etc.) from content."""
        if language in (CodeLanguage.PASCAL, CodeLanguage.DELPHI):
            return self._extract_delphi_symbols(content)
        elif language == CodeLanguage.MODULA2:
            return self._extract_modula2_symbols(content)
        elif language == CodeLanguage.ASSEMBLY:
            return self._extract_asm_symbols(content)
        elif language == CodeLanguage.PYTHON:
            return self._extract_python_symbols(content)
        elif language in (CodeLanguage.RUBY,):
            return self._extract_ruby_symbols(content)
        elif language in self.CURLY_BRACE_LANGUAGES:
            return self._extract_curly_brace_symbols(content, language)
        elif language in self.SFC_LANGUAGES:
            return self._extract_sfc_symbols(content, language)
        elif language in self.GENERIC_PATTERNS:
            return self._extract_generic_symbols(content, language)
        return []

    def _extract_delphi_symbols(self, content: str) -> List[CodeSymbol]:
        """Extract symbols from Pascal/Delphi code."""
        symbols = []
        lines = content.split('\n')
        unit_name = None
        current_section = None  # interface, implementation

        # First pass: find unit name
        match = self.DELPHI_PATTERNS['unit'].search(content)
        if match:
            unit_name = match.group(1)

        i = 0
        while i < len(lines):
            line = lines[i]
            line_num = i + 1  # 1-indexed

            # Check for section markers
            if self.DELPHI_PATTERNS['interface'].match(line):
                current_section = 'interface'
                i += 1
                continue
            elif self.DELPHI_PATTERNS['implementation'].match(line):
                current_section = 'implementation'
                i += 1
                continue

            # Check for procedures
            match = self.DELPHI_PATTERNS['procedure'].match(line)
            if match:
                is_class_method = bool(match.group(1))
                symbol_type = match.group(2).lower()
                name = match.group(3)
                params = match.group(4) or ''

                # Find the end of the procedure
                end_line = self._find_procedure_end(lines, i, language=CodeLanguage.DELPHI)
                proc_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type=f"class_{symbol_type}" if is_class_method else symbol_type,
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=proc_text,
                    unit_name=unit_name,
                    parameters=params.strip('()'),
                    visibility='public' if current_section == 'interface' else 'private',
                ))
                i = end_line + 1
                continue

            # Check for functions
            match = self.DELPHI_PATTERNS['function'].match(line)
            if match:
                is_class_method = bool(match.group(1))
                name = match.group(2)
                params = match.group(3) or ''
                return_type = match.group(4)

                end_line = self._find_procedure_end(lines, i, language=CodeLanguage.DELPHI)
                func_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="class_function" if is_class_method else "function",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=func_text,
                    unit_name=unit_name,
                    parameters=params.strip('()'),
                    return_type=return_type,
                    visibility='public' if current_section == 'interface' else 'private',
                ))
                i = end_line + 1
                continue

            # Check for class declarations
            match = self.DELPHI_PATTERNS['class'].match(line)
            if match:
                name = match.group(1)
                parent = match.group(2)

                end_line = self._find_block_end(lines, i)
                class_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="class",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=class_text,
                    unit_name=unit_name,
                    parent_symbol=parent.strip('()') if parent else None,
                ))
                i = end_line + 1
                continue

            # Check for record declarations
            match = self.DELPHI_PATTERNS['record'].match(line)
            if match:
                name = match.group(1)

                end_line = self._find_block_end(lines, i)
                record_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="record",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=record_text,
                    unit_name=unit_name,
                ))
                i = end_line + 1
                continue

            i += 1

        return symbols

    def _extract_modula2_symbols(self, content: str) -> List[CodeSymbol]:
        """Extract symbols from Modula-2 code."""
        symbols = []
        lines = content.split('\n')
        module_name = None
        module_type = None  # DEFINITION or IMPLEMENTATION

        # Find module name
        match = self.MODULA2_PATTERNS['module'].search(content)
        if match:
            module_type = match.group(1) or 'PROGRAM'
            module_name = match.group(2)

        i = 0
        while i < len(lines):
            line = lines[i]
            line_num = i + 1

            # Check for procedures
            match = self.MODULA2_PATTERNS['procedure'].match(line)
            if match:
                name = match.group(1)
                params = match.group(2) or ''
                return_type = match.group(3) or ''

                end_line = self._find_procedure_end(lines, i, language=CodeLanguage.MODULA2)
                proc_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="procedure",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=proc_text,
                    unit_name=module_name,
                    parameters=params.strip('()'),
                    return_type=return_type.strip(': ') if return_type else None,
                ))
                i = end_line + 1
                continue

            i += 1

        return symbols

    def _extract_asm_symbols(self, content: str) -> List[CodeSymbol]:
        """Extract symbols from Assembly code."""
        symbols = []
        lines = content.split('\n')
        current_segment = None

        i = 0
        while i < len(lines):
            line = lines[i]
            line_num = i + 1

            # Check for segment
            match = self.ASM_PATTERNS['segment'].match(line)
            if match:
                current_segment = match.group(1)
                i += 1
                continue

            # Check for PROC
            match = self.ASM_PATTERNS['proc'].match(line)
            if match:
                name = match.group(1)
                proc_type = match.group(3) or 'NEAR'

                # Find ENDP
                end_line = i
                for j in range(i + 1, len(lines)):
                    endp_match = self.ASM_PATTERNS['endp'].match(lines[j])
                    if endp_match and endp_match.group(1).upper() == name.upper():
                        end_line = j
                        break

                proc_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="procedure",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=proc_text,
                    metadata={'segment': current_segment, 'type': proc_type},
                ))
                i = end_line + 1
                continue

            # Check for MACRO
            match = self.ASM_PATTERNS['macro'].match(line)
            if match:
                name = match.group(1)

                # Find ENDM
                end_line = i
                for j in range(i + 1, len(lines)):
                    if self.ASM_PATTERNS['endm'].match(lines[j]):
                        end_line = j
                        break

                macro_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="macro",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=macro_text,
                    metadata={'segment': current_segment},
                ))
                i = end_line + 1
                continue

            # Check for standalone labels (potential entry points)
            match = self.ASM_PATTERNS['label'].match(line)
            if match:
                name = match.group(1)
                # Skip common assembler directives
                if name.upper() not in ('DB', 'DW', 'DD', 'DQ', 'ASSUME', 'ORG', 'OFFSET'):
                    # Find the extent of this label's code block
                    end_line = self._find_asm_label_end(lines, i)
                    label_text = '\n'.join(lines[i:end_line + 1])

                    # Only add if it has meaningful content
                    if len(label_text.strip()) > len(line):
                        symbols.append(CodeSymbol(
                            name=name,
                            symbol_type="label",
                            line_start=line_num,
                            line_end=end_line + 1,
                            text=label_text,
                            metadata={'segment': current_segment},
                        ))

            i += 1

        return symbols

    def _find_procedure_end(self, lines: List[str], start: int, language: CodeLanguage) -> int:
        """Find the end line of a procedure/function."""
        depth = 0
        in_body = False

        for i in range(start, len(lines)):
            line = lines[i].strip().lower()

            # Skip empty lines and comments
            if not line or line.startswith('//') or line.startswith('{') or line.startswith('(*'):
                continue

            # Track begin/end pairs
            if 'begin' in line:
                depth += 1
                in_body = True

            # Check for end
            if language in (CodeLanguage.PASCAL, CodeLanguage.DELPHI):
                if line.startswith('end') and (line == 'end;' or line == 'end' or line.startswith('end;')):
                    if depth <= 1:
                        return i
                    depth -= 1
            elif language == CodeLanguage.MODULA2:
                if line.startswith('end') and ';' in line:
                    if depth <= 1:
                        return i
                    depth -= 1

        # If no end found, return a reasonable block
        return min(start + 50, len(lines) - 1)

    def _find_block_end(self, lines: List[str], start: int) -> int:
        """Find the end of a class/record block."""
        for i in range(start + 1, len(lines)):
            line = lines[i].strip().lower()
            if line.startswith('end;') or line == 'end;':
                return i
        return min(start + 30, len(lines) - 1)

    def _find_asm_label_end(self, lines: List[str], start: int) -> int:
        """Find the end of an assembly label's code block."""
        # Look for next label, PROC, or significant separator
        for i in range(start + 1, min(start + 100, len(lines))):
            line = lines[i]

            # Check for next label
            if self.ASM_PATTERNS['label'].match(line):
                return i - 1

            # Check for next PROC
            if self.ASM_PATTERNS['proc'].match(line):
                return i - 1

            # Check for RET (likely end of subroutine)
            if re.match(r'^\s*(RET|RETN|RETF|ret|retn|retf)\s*', line):
                return i

        return min(start + 30, len(lines) - 1)

    def _extract_python_symbols(self, content: str) -> List[CodeSymbol]:
        """Extract symbols from Python code."""
        symbols = []
        lines = content.split('\n')

        i = 0
        while i < len(lines):
            line = lines[i]
            line_num = i + 1

            # Check for class
            match = self.PYTHON_PATTERNS['class'].match(line)
            if match:
                indent = len(match.group(1).expandtabs(4))
                name = match.group(2)

                end_line = self._find_python_block_end(lines, i, indent)
                class_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="class",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=class_text,
                ))
                i = end_line + 1
                continue

            # Check for function/method
            match = self.PYTHON_PATTERNS['function'].match(line)
            if match:
                indent = len(match.group(1).expandtabs(4))
                name = match.group(2)
                is_method = indent > 0

                end_line = self._find_python_block_end(lines, i, indent)
                func_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="method" if is_method else "function",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=func_text,
                ))
                i = end_line + 1
                continue

            i += 1

        return symbols

    @staticmethod
    def _triple_quote_toggles(line: str) -> bool:
        """True when a line opens or closes a triple-quoted string (odd count)."""
        return (line.count('"""') + line.count("'''")) % 2 == 1

    def _find_python_block_end(self, lines: List[str], start: int, base_indent: int) -> int:
        """Find the end of a Python indented block.

        Tabs count as four spaces, lines inside triple-quoted strings never
        end a block, and a signature that spans several lines (closing
        `):` at the declaration's own indentation) is part of the header.
        """
        n = len(lines)
        # Skip the header: everything up to the line that ends with ':'.
        body_start = start + 1
        in_triple = False
        for h in range(start, min(start + 30, n)):
            stripped = lines[h].split('#', 1)[0].rstrip()
            if self._triple_quote_toggles(lines[h]):
                in_triple = not in_triple
            if not in_triple and stripped.endswith(':'):
                body_start = h + 1
                break

        in_triple = False
        for i in range(body_start, n):
            line = lines[i].expandtabs(4)
            stripped = line.strip()

            if in_triple:
                if self._triple_quote_toggles(line):
                    in_triple = False
                continue

            # Skip empty lines and comments
            if not stripped or stripped.startswith('#'):
                continue

            current_indent = len(line) - len(line.lstrip())

            # If we hit a line with same or less indent, block ended on previous line
            if current_indent <= base_indent and not stripped.startswith((')', ']', '}')):
                return self._trim_trailing_blank(lines, start, i - 1)

            if self._triple_quote_toggles(line):
                in_triple = True

        return self._trim_trailing_blank(lines, start, n - 1)

    @staticmethod
    def _trim_trailing_blank(lines: List[str], start: int, end: int) -> int:
        """Pull a block end back over trailing blank lines."""
        while end > start and not lines[end].strip():
            end -= 1
        return end

    def _extract_ruby_symbols(self, content: str) -> List[CodeSymbol]:
        """Extract symbols from Ruby code."""
        symbols = []
        lines = content.split('\n')

        i = 0
        while i < len(lines):
            line = lines[i]
            line_num = i + 1

            # Check for class
            match = self.CURLY_BRACE_PATTERNS['ruby_class'].match(line)
            if match:
                indent = len(match.group(1))
                name = match.group(2)

                end_line = self._find_ruby_block_end(lines, i, indent)
                class_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="class",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=class_text,
                ))
                i = end_line + 1
                continue

            # Check for module
            match = self.CURLY_BRACE_PATTERNS['ruby_module'].match(line)
            if match:
                indent = len(match.group(1))
                name = match.group(2)

                end_line = self._find_ruby_block_end(lines, i, indent)
                mod_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="module",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=mod_text,
                ))
                i = end_line + 1
                continue

            # Check for def
            match = self.CURLY_BRACE_PATTERNS['ruby_def'].match(line)
            if match:
                indent = len(match.group(1))
                name = match.group(2)

                end_line = self._find_ruby_block_end(lines, i, indent)
                def_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="method" if indent > 0 else "function",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=def_text,
                ))
                i = end_line + 1
                continue

            i += 1

        return symbols

    def _find_ruby_block_end(self, lines: List[str], start: int, base_indent: int) -> int:
        """Find the end of a Ruby block (looking for matching 'end')."""
        depth = 1
        block_keywords = {'class', 'module', 'def', 'if', 'unless', 'case', 'while',
                          'until', 'for', 'do', 'begin'}

        for i in range(start + 1, len(lines)):
            line = lines[i].strip()

            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue

            # Check for block openers
            words = line.split()
            if words:
                first_word = words[0].rstrip(':')
                if first_word in block_keywords:
                    depth += 1
                elif first_word == 'end':
                    depth -= 1
                    if depth == 0:
                        return i

        return min(start + 50, len(lines) - 1)

    def _curly_patterns_for(self, language: CodeLanguage) -> List[Tuple[str, str]]:
        """Which CURLY_BRACE_PATTERNS apply to a language, in priority order."""
        if language in (CodeLanguage.JAVASCRIPT, CodeLanguage.TYPESCRIPT):
            return [
                ('js_class', 'class'),
                ('ts_interface', 'interface'),
                ('ts_enum', 'enum'),
                ('ts_type', 'type'),
                ('js_function', 'function'),
                ('js_arrow', 'function'),
                ('js_method', 'method'),
            ]
        if language in (CodeLanguage.CSHARP, CodeLanguage.JAVA):
            return [
                ('csharp_class', 'class'),
                ('csharp_interface', 'interface'),
                ('csharp_method', 'method'),
                ('csharp_property', 'property'),
                ('bare_function', 'constructor'),
            ]
        if language == CodeLanguage.GO:
            return [
                ('go_type', 'type'),
                ('go_func', 'function'),
            ]
        if language == CodeLanguage.RUST:
            return [
                ('rust_impl', 'impl'),
                ('rust_trait', 'trait'),
                ('rust_struct', 'struct'),
                ('rust_enum', 'enum'),
                ('rust_mod', 'module'),
                ('rust_fn', 'function'),
            ]
        if language in (CodeLanguage.C, CodeLanguage.CPP):
            # C/C++ use a simpler approach - just find functions
            return [
                ('csharp_class', 'class'),  # C++ classes
                ('csharp_method', 'function'),  # Functions look similar
                ('bare_function', 'function'),
            ]
        if language == CodeLanguage.PHP:
            return [
                ('php_class', 'class'),
                ('php_function', 'function'),
            ]
        if language == CodeLanguage.SWIFT:
            return [
                ('swift_class', 'class'),
                ('swift_struct', 'struct'),
                ('swift_func', 'function'),
            ]
        if language == CodeLanguage.KOTLIN:
            return [
                ('kotlin_class', 'class'),
                ('kotlin_fun', 'function'),
            ]
        if language == CodeLanguage.SCALA:
            return [
                ('scala_class', 'class'),
                ('scala_object', 'object'),
                ('scala_trait', 'trait'),
                ('scala_def', 'function'),
            ]
        if language == CodeLanguage.DART:
            return [
                ('dart_class', 'class'),
                ('csharp_method', 'function'),
                ('bare_function', 'function'),
            ]
        if language == CodeLanguage.OBJC:
            return [
                ('objc_interface', 'interface'),
                ('objc_method', 'method'),
                ('csharp_method', 'function'),
            ]
        if language == CodeLanguage.GROOVY:
            return [
                ('csharp_class', 'class'),
                ('csharp_interface', 'interface'),
                ('groovy_def', 'function'),
                ('gradle_task', 'task'),
                ('csharp_method', 'method'),
                ('gradle_block', 'block'),
            ]
        return []

    def _extract_curly_brace_symbols(self, content: str, language: CodeLanguage) -> List[CodeSymbol]:
        """Extract symbols from curly-brace languages (JS/TS/C#/Java/Go/Rust/C/C++/PHP/Swift/Kotlin/Scala/...)."""
        symbols = []
        lines = content.split('\n')
        patterns_to_check = self._curly_patterns_for(language)

        i = 0
        while i < len(lines):
            line = lines[i]
            line_num = i + 1
            matched = False

            for pattern_name, symbol_type in patterns_to_check:
                pattern = self.CURLY_BRACE_PATTERNS.get(pattern_name)
                if not pattern:
                    continue

                match = pattern.match(line)
                if match:
                    # Extract name - position varies by pattern
                    groups = match.groups()
                    name = None
                    for g in groups:
                        if g and not g.isspace() and len(g) > 0:
                            # Skip indentation groups (all whitespace)
                            if not g.strip():
                                continue
                            name = g.strip()
                            break

                    if not name:
                        continue

                    # Find the end of the block
                    if language == CodeLanguage.OBJC and pattern_name == 'objc_interface':
                        end_line = self._find_line_matching(lines, i, re.compile(r'^\s*@end\b'))
                    else:
                        end_line = self._find_curly_brace_end(lines, i, language)
                    block_text = '\n'.join(lines[i:end_line + 1])

                    symbols.append(CodeSymbol(
                        name=name,
                        symbol_type=symbol_type,
                        line_start=line_num,
                        line_end=end_line + 1,
                        text=block_text,
                    ))
                    i = end_line + 1
                    matched = True
                    break

            if not matched:
                i += 1

        return symbols

    @staticmethod
    def _find_line_matching(lines: List[str], start: int, pattern: "re.Pattern") -> int:
        """Index of the first line after `start` matching `pattern`, capped."""
        for i in range(start + 1, min(len(lines), start + _BLOCK_SCAN_CAP)):
            if pattern.match(lines[i]):
                return i
        return min(start + _UNBALANCED_CAP, len(lines) - 1)

    def _find_curly_brace_end(self, lines: List[str], start: int,
                              language: Optional[CodeLanguage] = None) -> int:
        """Find the end of a curly-brace block, leniently.

        * `"` and `'` strings end at the end of their line (an unterminated
          string must not swallow the file); backtick template strings span lines.
        * In Rust, `'` is a string only for a char literal (`'x'`, `'\\n'`);
          lifetimes (`'a`) are ignored.
        * `#`, `--` and `//` line comments are honoured per language.
        * A declaration that opens no brace within three lines is a
          prototype or one-liner: the block is that single line.
        * When braces never balance, the block ends before the next
          blank-preceded declaration at the declaration's own indentation,
          or after 200 lines, whichever comes first.
        """
        n = len(lines)
        hash_c = language in HASH_COMMENT_LANGUAGES
        dash_c = language in DASH_COMMENT_LANGUAGES
        slash_c = language is None or language not in _NO_SLASH_COMMENT or language in _ALSO_SLASH_COMMENT
        block_c = slash_c or language in (CodeLanguage.SQL, CodeLanguage.CSS)
        is_rust = language == CodeLanguage.RUST

        depth = 0
        opened = False
        in_string = False
        string_char = None
        in_block_comment = False
        decl_indent = _indent_of(lines[start])

        for i in range(start, min(n, start + _BLOCK_SCAN_CAP)):
            line = lines[i]

            # Unbalanced so far: a sibling declaration at our indentation,
            # after a blank line, means our block ended before it.
            if i > start and depth > 0 and not in_block_comment:
                stripped = line.strip()
                if (stripped and not lines[i - 1].strip() and _indent_of(line) <= decl_indent
                        and not stripped.startswith('}') and _DECL_LINE_RE.match(stripped)):
                    return i - 1

            j = 0
            length = len(line)
            while j < length:
                char = line[j]

                # Inside a string: look only for its end
                if in_string:
                    if char == '\\':
                        j += 2
                        continue
                    if char == string_char:
                        in_string = False
                    j += 1
                    continue

                # Handle multiline comments
                if in_block_comment:
                    if line.startswith('*/', j):
                        in_block_comment = False
                        j += 2
                        continue
                    j += 1
                    continue

                if block_c and line.startswith('/*', j):
                    in_block_comment = True
                    j += 2
                    continue

                # Line comments
                if slash_c and line.startswith('//', j):
                    break
                if hash_c and char == '#':
                    break
                if dash_c and line.startswith('--', j):
                    break

                # Strings
                if char in ('"', '`'):
                    in_string = True
                    string_char = char
                    j += 1
                    continue
                if char == "'":
                    if is_rust:
                        m = _RUST_CHAR_LITERAL.match(line, j)
                        j = m.end() if m else j + 1   # lifetime: just skip the quote
                        continue
                    in_string = True
                    string_char = char
                    j += 1
                    continue

                # Count braces
                if char == '{':
                    depth += 1
                    opened = True
                elif char == '}':
                    depth -= 1
                    if opened and depth <= 0:
                        return i

                j += 1

            # An unterminated quote string ends with its line.
            if in_string and string_char != '`':
                in_string = False

            # No brace within three lines: prototype or one-liner.
            if not opened and i - start >= 2:
                return start

        if not opened:
            return start

        return self._find_lenient_block_end(lines, start, language)

    def _find_lenient_block_end(self, lines: List[str], start: int,
                                language: Optional[CodeLanguage] = None) -> int:
        """Where an unbalanced block most plausibly ends.

        The next line at the declaration's indentation (or less) that starts
        a new declaration or follows a blank line, capped at 200 lines.
        """
        n = len(lines)
        decl_indent = _indent_of(lines[start])
        limit = min(n, start + _UNBALANCED_CAP + 1)
        generic = self.GENERIC_PATTERNS.get(language, []) if language else []
        for i in range(start + 1, limit):
            line = lines[i]
            stripped = line.strip()
            if not stripped:
                continue
            if _indent_of(line) > decl_indent:
                continue
            if stripped.startswith('}'):
                return i
            if not lines[i - 1].strip() or _DECL_LINE_RE.match(stripped) \
                    or any(p.match(line) for _, p in generic):
                return i - 1
        return limit - 1

    # ── Generic (line-wise) languages ────────────────────────────────────

    def _extract_sfc_symbols(self, content: str, language: CodeLanguage) -> List[CodeSymbol]:
        """Vue/Svelte/Astro: the <script> body (and Astro frontmatter) is TS;
        everything else is left for the uncovered-code chunker."""
        symbols: List[CodeSymbol] = []
        regions: List[Tuple[int, str]] = []  # (line offset, body)

        for m in _SFC_SCRIPT_RE.finditer(content):
            offset = content.count('\n', 0, m.start(1))
            regions.append((offset, m.group(1)))

        if language == CodeLanguage.ASTRO and content.startswith('---'):
            lines = content.split('\n')
            for k in range(1, min(len(lines), 2000)):
                if lines[k].strip() == '---':
                    regions.append((1, '\n'.join(lines[1:k])))
                    break

        for offset, body in regions:
            for sym in self._extract_curly_brace_symbols(body, CodeLanguage.TYPESCRIPT):
                sym.line_start += offset
                sym.line_end += offset
                symbols.append(sym)
        symbols.sort(key=lambda s: s.line_start)
        return symbols

    def _extract_generic_symbols(self, content: str, language: CodeLanguage) -> List[CodeSymbol]:
        """Line-wise declaration finder for languages with a GENERIC_PATTERNS table."""
        symbols: List[CodeSymbol] = []
        lines = content.split('\n')
        patterns = self.GENERIC_PATTERNS.get(language, [])
        style = self._BLOCK_STYLE.get(language, 'indent')

        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            matched = False
            for symbol_type, pattern in patterns:
                m = pattern.match(line)
                if not m:
                    continue
                gd = m.groupdict()
                kind = (gd.get('kind') or '').strip().lower()
                kind = re.sub(r'\s+', ' ', kind)
                name = (gd.get('name') or '').strip().strip('"`\'[]')
                if gd.get('name2'):
                    name = f"{name}.{gd['name2']}"
                if gd.get('alias'):
                    name = gd['alias']
                if not name:
                    name = kind or symbol_type
                if symbol_type == 'kind':
                    symbol_type = self._KIND_ALIASES.get(kind, kind) or 'block'
                if len(name) > 120:
                    name = name[:117] + '...'

                end = self._find_generic_block_end(lines, i, language, style, kind, name)
                while end > i and not lines[end].strip():
                    end -= 1
                if end == i and len(line.strip()) < 30:
                    # `version: 2`, `scalar Date`: context, not a symbol
                    i += 1
                    matched = True
                    break
                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type=symbol_type,
                    line_start=i + 1,
                    line_end=end + 1,
                    text='\n'.join(lines[i:end + 1]),
                ))
                i = end + 1
                matched = True
                break
            if not matched:
                i += 1
        return symbols

    def _find_generic_block_end(self, lines: List[str], start: int, language: CodeLanguage,
                                style: str, kind: str, name: str) -> int:
        if style == 'brace':
            return self._find_curly_brace_end(lines, start, language)
        if style == 'end':
            return self._find_keyword_block_end(lines, start, language)
        if style == 'end_kind':
            return self._find_end_kind_block_end(lines, start, language, kind)
        if style == 'next_decl':
            return self._find_next_decl_end(lines, start, language)
        if style == 'sql':
            return self._find_sql_end(lines, start, language)
        if style == 'dot':
            return self._find_terminator_end(lines, start, language, '.', '%')
        if style == 'paren':
            return self._find_paren_block_end(lines, start, language)
        return self._find_indent_block_end(lines, start, language, name)

    @staticmethod
    def _strip_line_comment(line: str, language: CodeLanguage) -> str:
        if language in HASH_COMMENT_LANGUAGES:
            return line.split('#', 1)[0]
        if language in DASH_COMMENT_LANGUAGES:
            return line.split('--', 1)[0]
        return line

    def _is_sibling_declaration(self, lines: List[str], i: int, decl_indent: int,
                                language: CodeLanguage) -> bool:
        """A blank-preceded declaration at our indentation: our block is over."""
        line = lines[i]
        stripped = line.strip()
        if not stripped or lines[i - 1].strip() or _indent_of(line) > decl_indent:
            return False
        return any(p.match(line) for _, p in self.GENERIC_PATTERNS.get(language, []))

    def _find_keyword_block_end(self, lines: List[str], start: int, language: CodeLanguage) -> int:
        """Blocks closed by `end` (Lua, Julia, Elixir)."""
        n = len(lines)
        openers = self._END_KEYWORD_OPENERS.get(language, frozenset())
        decl_indent = _indent_of(lines[start])
        depth = 0
        opened = False
        for i in range(start, min(n, start + _BLOCK_SCAN_CAP)):
            if i > start and depth > 0 and self._is_sibling_declaration(lines, i, decl_indent, language):
                return i - 1
            s = self._strip_line_comment(lines[i], language).strip()
            if not s:
                continue
            opens = 0
            first = re.match(r'[A-Za-z_]\w*', s)
            if first and first.group() in openers:
                opens += 1
            if language == CodeLanguage.LUA:
                opens += len(re.findall(r'\bfunction\b', s)) - (1 if first and first.group() == 'function' else 0)
                if first and first.group() == 'local' and re.search(r'\bfunction\b', s):
                    pass  # counted by the findall above
            elif language == CodeLanguage.ELIXIR:
                opens += len(re.findall(r'\bdo\s*$', s)) + len(re.findall(r'\bfn\b', s))
            elif language == CodeLanguage.JULIA:
                if re.search(r'\bdo\s*$', s) and not (first and first.group() == 'do'):
                    opens += 1
            if i == start and opens == 0:
                opens = 1
            closes = len(re.findall(r'(?:^|[\s)}\]])end\b', s))
            if language == CodeLanguage.LUA and first and first.group() == 'until':
                closes += 1
            if opens:
                opened = True
            depth += opens - closes
            if opened and depth <= 0:
                return i
        if not opened:
            return start
        return self._find_lenient_block_end(lines, start, language)

    def _find_end_kind_block_end(self, lines: List[str], start: int, language: CodeLanguage, kind: str) -> int:
        """Blocks closed by `End <kind>` (VB, Fortran, CMake)."""
        n = len(lines)
        if not kind:
            return self._find_indent_block_end(lines, start, language, '')
        if language == CodeLanguage.CMAKE:
            closer = re.compile(rf'^\s*end{re.escape(kind)}\s*\(', re.IGNORECASE)
        else:
            closer = re.compile(rf'^\s*end\s*{re.escape(kind)}\b', re.IGNORECASE)
        plain_end = re.compile(r'^\s*end\s*$', re.IGNORECASE) if language == CodeLanguage.FORTRAN else None
        decl_indent = _indent_of(lines[start])
        patterns = self.GENERIC_PATTERNS.get(language, [])
        depth = 1
        for i in range(start + 1, min(n, start + _BLOCK_SCAN_CAP)):
            line = lines[i]
            if closer.match(line) or (plain_end and depth == 1 and plain_end.match(line)):
                depth -= 1
                if depth == 0:
                    return i
                continue
            if self._is_sibling_declaration(lines, i, decl_indent, language):
                return i - 1
            for _, p in patterns:
                m = p.match(line)
                if m and (m.groupdict().get('kind') or '').strip().lower() == kind:
                    depth += 1
                    break
        return self._find_lenient_block_end(lines, start, language)

    def _find_next_decl_end(self, lines: List[str], start: int, language: CodeLanguage) -> int:
        """Block runs until the next declaration of the same language."""
        n = len(lines)
        patterns = self.GENERIC_PATTERNS.get(language, [])
        limit = min(n, start + _UNBALANCED_CAP + 1)
        for i in range(start + 1, limit):
            line = lines[i]
            if any(p.match(line) for _, p in patterns):
                return i - 1
            if language == CodeLanguage.BATCH and re.match(r'^\s*(?:goto\s+:eof|exit\s*/b)\b', line, re.IGNORECASE):
                return i
        return limit - 1

    def _find_sql_end(self, lines: List[str], start: int, language: CodeLanguage) -> int:
        """A SQL statement ends at `;` outside a $$-quoted body, at `GO`, or
        before the next CREATE."""
        n = len(lines)
        patterns = self.GENERIC_PATTERNS.get(language, [])
        in_dollar = False
        for i in range(start, min(n, start + _BLOCK_SCAN_CAP)):
            raw = lines[i]
            s = re.sub(r'--.*$', '', raw).rstrip()
            if len(re.findall(r'\$\w*\$', s)) % 2 == 1:
                in_dollar = not in_dollar
                if not in_dollar and s.endswith(';'):
                    return i
                continue
            if in_dollar:
                continue
            if i > start and any(p.match(raw) for _, p in patterns):
                return i - 1
            if s.strip().upper() == 'GO':
                return i - 1
            if s.endswith(';'):
                return i
        return self._find_lenient_block_end(lines, start, language)

    def _find_terminator_end(self, lines: List[str], start: int, language: CodeLanguage,
                             terminator: str, comment: str) -> int:
        """Block ends at the first line ending with `terminator` (Erlang's `.`)."""
        n = len(lines)
        for i in range(start, min(n, start + _UNBALANCED_CAP + 1)):
            s = lines[i].split(comment, 1)[0].rstrip()
            if s.endswith(terminator):
                return i
        return self._find_lenient_block_end(lines, start, language)

    def _find_paren_block_end(self, lines: List[str], start: int, language: CodeLanguage) -> int:
        """Lisp-style forms: balance ( [ { with strings and `;` comments."""
        n = len(lines)
        depth = 0
        opened = False
        in_string = False
        for i in range(start, min(n, start + _BLOCK_SCAN_CAP)):
            line = lines[i]
            j = 0
            while j < len(line):
                ch = line[j]
                if in_string:
                    if ch == '\\':
                        j += 2
                        continue
                    if ch == '"':
                        in_string = False
                    j += 1
                    continue
                if ch == ';':
                    break
                if ch == '\\':   # char literal
                    j += 2
                    continue
                if ch == '"':
                    in_string = True
                elif ch in '([{':
                    depth += 1
                    opened = True
                elif ch in ')]}':
                    depth -= 1
                    if opened and depth <= 0:
                        return i
                j += 1
        if not opened:
            return start
        return self._find_lenient_block_end(lines, start, language)

    def _find_indent_block_end(self, lines: List[str], start: int, language: CodeLanguage, name: str) -> int:
        """Block ends before the next non-blank line at the declaration's indentation."""
        n = len(lines)
        base = _indent_of(lines[start])
        limit = min(n, start + _BLOCK_SCAN_CAP)
        name_re = re.compile(rf'{re.escape(name)}\b') if name else None
        for i in range(start + 1, limit):
            line = lines[i]
            s = line.strip()
            if not s:
                continue
            if language in HASH_COMMENT_LANGUAGES and s.startswith('#'):
                continue
            if language in DASH_COMMENT_LANGUAGES and s.startswith('--'):
                continue
            if _indent_of(line) <= base:
                if language == CodeLanguage.HASKELL and (
                        (name_re and name_re.match(s)) or s.startswith(('|', 'where', 'deriving', '=', '{', '}', ','))):
                    continue
                if language == CodeLanguage.YAML and s.startswith('- '):
                    continue
                return i - 1
        return limit - 1

    # ── Chunking ─────────────────────────────────────────────────────────

    def looks_minified(self, content: str) -> bool:
        """Minified or generated: one enormous line, or very long lines on average."""
        if not content:
            return False
        lines = content.split('\n')
        longest = max(len(line) for line in lines)
        if longest > self.MINIFIED_MAX_LINE:
            return True
        non_blank = [line for line in lines if line.strip()]
        if not non_blank:
            return False
        return sum(len(line) for line in non_blank) / len(non_blank) > self.MINIFIED_AVG_LINE

    def chunk_code(
        self,
        content: str,
        document_id: str,
        filename: str,
        language: CodeLanguage,
        unit_name: Optional[str] = None,
    ) -> List[CodeChunk]:
        """Chunk code intelligently, preserving symbol boundaries.

        This method extracts symbols and creates chunks that respect
        procedure/function/class boundaries as much as possible. It never
        returns nothing for non-empty content: when no symbol is found the
        whole file is chunked by size.
        """
        if not content or not content.strip():
            return []

        lines = content.split('\n')

        # Minified / generated: no symbol extraction, plain size chunks.
        if self.looks_minified(content):
            pieces = self._split_lines_by_size(lines, 1)
            return self._chunks_from_pieces(
                pieces, document_id, filename, language, unit_name,
                symbol_type="generated", metadata={'minified': True}, start_index=0,
            )

        chunks: List[CodeChunk] = []
        chunk_index = 0

        # Extract all symbols; a parser bug must never fail the file.
        try:
            symbols = self.extract_symbols(content, language)
            symbols = self._normalize_symbols(symbols, lines, language)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"Symbol extraction failed for {filename} ({language.value}): {e}")
            symbols = []

        if symbols:
            # Create chunks from symbols
            for symbol in symbols:
                # If symbol is too large, split it
                if len(symbol.text) > self.chunk_size:
                    sub_chunks = self._split_large_symbol(symbol, document_id, filename, language, unit_name)
                    for sub_chunk in sub_chunks:
                        sub_chunk.chunk_id = f"{document_id}_{chunk_index}"
                        sub_chunk.chunk_index = chunk_index
                        chunks.append(sub_chunk)
                        chunk_index += 1
                else:
                    chunks.append(CodeChunk(
                        chunk_id=f"{document_id}_{chunk_index}",
                        document_id=document_id,
                        filename=filename,
                        text=symbol.text,
                        line_start=symbol.line_start,
                        line_end=symbol.line_end,
                        language=language.value,
                        unit_name=unit_name or symbol.unit_name,
                        symbol_name=symbol.name,
                        symbol_type=symbol.symbol_type,
                        parent_symbol=symbol.parent_symbol,
                        chunk_index=chunk_index,
                        metadata={
                            'parameters': symbol.parameters,
                            'return_type': symbol.return_type,
                            'visibility': symbol.visibility,
                            **symbol.metadata,
                        }
                    ))
                    chunk_index += 1

        # Also chunk any code not covered by symbols (global code, declarations, etc.)
        uncovered_chunks = self._chunk_uncovered_code(
            content, symbols, document_id, filename, language, unit_name, chunk_index
        )
        chunks.extend(uncovered_chunks)

        # Degenerate output: tiny file, or every fragment under the threshold.
        if not chunks:
            pieces = self._split_lines_by_size(lines, 1)
            chunks = self._chunks_from_pieces(
                pieces, document_id, filename, language, unit_name,
                symbol_type="file", metadata={}, start_index=0,
            )

        return chunks

    def _normalize_symbols(self, symbols: List[CodeSymbol], lines: List[str],
                           language: CodeLanguage, depth: int = 0) -> List[CodeSymbol]:
        """Make symbol spans sane: in order, non-overlapping, within the
        file, and none swallowing most of a long file.

        A symbol covering more than 60% of a file longer than 400 lines is
        almost certainly a block-matching failure. Its interior is
        re-scanned for nested declarations (methods of a class, the
        functions after a missing brace); those replace it, or it is
        dropped and the uncovered-code chunker takes those lines.
        """
        n = len(lines)
        if not symbols or n == 0:
            return []

        oversized_limit = int(n * 0.6) if n > 400 else None
        result: List[CodeSymbol] = []
        for sym in symbols:
            sym.line_start = max(1, min(sym.line_start, n))
            sym.line_end = max(sym.line_start, min(sym.line_end, n))
            span = sym.line_end - sym.line_start + 1
            if oversized_limit is not None and span > oversized_limit:
                if depth == 0 and span > 1:
                    body = '\n'.join(lines[sym.line_start:sym.line_end])
                    nested = self.extract_symbols(body, language)
                    for inner in nested:
                        inner.line_start += sym.line_start
                        inner.line_end += sym.line_start
                        inner.parent_symbol = inner.parent_symbol or sym.name
                    nested = self._normalize_symbols(nested, lines, language, depth=1)
                    if len(nested) >= 2:
                        result.extend(nested)
                        continue
                logger.debug(f"Dropping oversized symbol {sym.name} ({span}/{n} lines)")
                continue
            result.append(sym)

        result.sort(key=lambda s: (s.line_start, s.line_end))

        # Clamp overlaps: each symbol ends before the next one starts.
        clamped: List[CodeSymbol] = []
        for idx, sym in enumerate(result):
            if idx + 1 < len(result):
                next_start = result[idx + 1].line_start
                if sym.line_end >= next_start:
                    sym.line_end = next_start - 1
            if sym.line_end < sym.line_start:
                continue
            sym.text = '\n'.join(lines[sym.line_start - 1:sym.line_end])
            if len(sym.text.strip()) < 30 and sym.line_end == sym.line_start:
                continue  # a lone prototype; the uncovered chunker keeps it in context
            clamped.append(sym)
        return clamped

    def _split_lines_by_size(self, lines: List[str], first_line_no: int) -> List[Tuple[str, int, int]]:
        """Split lines into (text, line_start, line_end) pieces of at most
        chunk_size characters.

        Over-long lines are broken at punctuation. Once a piece is past 60%
        of chunk_size, a blank line or a return to column 0 after indented
        code is taken as a boundary, so chunks tend to end where blocks end.
        Hard cuts carry a couple of lines of overlap.
        """
        segs: List[Tuple[int, str]] = []
        for k, line in enumerate(lines):
            ln = first_line_no + k
            if len(line) <= self.chunk_size:
                segs.append((ln, line))
            else:
                for part in self._break_long_line(line):
                    segs.append((ln, part))

        pieces: List[Tuple[str, int, int]] = []
        cur: List[Tuple[int, str]] = []
        cur_len = 0
        soft = int(self.chunk_size * 0.6)
        overlap_lines = max(1, self.chunk_overlap // 80)

        def flush(keep_overlap: bool) -> None:
            nonlocal cur, cur_len
            if not cur:
                return
            body = list(cur)
            while body and not body[-1][1].strip():
                body.pop()
            if body:
                pieces.append(('\n'.join(s for _, s in body), body[0][0], body[-1][0]))
            if keep_overlap and overlap_lines < len(cur):
                cur = cur[-overlap_lines:]
            else:
                cur = []
            cur_len = sum(len(s) + 1 for _, s in cur)

        for ln, seg in segs:
            seg_len = len(seg) + 1
            if cur and cur_len + seg_len > self.chunk_size:
                flush(keep_overlap=True)
            elif cur and cur_len >= soft:
                if not seg.strip():
                    cur.append((ln, seg))
                    flush(keep_overlap=False)
                    continue
                prev = cur[-1][1]
                if seg[0] not in ' \t' and prev.strip() and prev[0] in ' \t':
                    flush(keep_overlap=False)
            cur.append((ln, seg))
            cur_len += seg_len
        flush(keep_overlap=False)
        return pieces

    def _break_long_line(self, line: str) -> List[str]:
        """Cut a line longer than chunk_size at the last punctuation in each window."""
        parts: List[str] = []
        size = self.chunk_size
        pos = 0
        while pos < len(line):
            window = line[pos:pos + size]
            cut = len(window)
            if pos + size < len(line):
                tail_start = int(size * 0.7)
                best = -1
                for ch in (';', '}', ')', ',', '>', ' '):
                    idx = window.rfind(ch, tail_start)
                    if idx > best:
                        best = idx
                if best > 0:
                    cut = best + 1
            parts.append(window[:cut])
            pos += cut
        return parts or [line]

    def _chunks_from_pieces(
        self,
        pieces: List[Tuple[str, int, int]],
        document_id: str,
        filename: str,
        language: CodeLanguage,
        unit_name: Optional[str],
        symbol_type: str,
        metadata: Dict[str, Any],
        start_index: int,
        symbol_name: Optional[str] = None,
        parent_symbol: Optional[str] = None,
    ) -> List[CodeChunk]:
        chunks: List[CodeChunk] = []
        chunk_index = start_index
        for text, line_start, line_end in pieces:
            chunks.append(CodeChunk(
                chunk_id=f"{document_id}_{chunk_index}",
                document_id=document_id,
                filename=filename,
                text=text,
                line_start=line_start,
                line_end=line_end,
                language=language.value,
                unit_name=unit_name,
                symbol_name=symbol_name,
                symbol_type=symbol_type,
                parent_symbol=parent_symbol,
                chunk_index=chunk_index,
                metadata=dict(metadata, is_partial=True) if len(pieces) > 1 else dict(metadata),
            ))
            chunk_index += 1
        return chunks

    def _split_large_symbol(
        self,
        symbol: CodeSymbol,
        document_id: str,
        filename: str,
        language: CodeLanguage,
        unit_name: Optional[str],
    ) -> List[CodeChunk]:
        """Split a large symbol into smaller chunks with overlap."""
        pieces = self._split_lines_by_size(symbol.text.split('\n'), symbol.line_start)
        metadata = {
            'parameters': symbol.parameters,
            'return_type': symbol.return_type,
            'visibility': symbol.visibility,
            'is_partial': True,
            **symbol.metadata,
        }
        chunks = self._chunks_from_pieces(
            pieces, document_id, filename, language, unit_name or symbol.unit_name,
            symbol_type=symbol.symbol_type, metadata=metadata, start_index=0,
            symbol_name=symbol.name, parent_symbol=symbol.parent_symbol,
        )
        for chunk in chunks:
            chunk.chunk_id = ""  # set by caller
        return chunks

    def _chunk_uncovered_code(
        self,
        content: str,
        symbols: List[CodeSymbol],
        document_id: str,
        filename: str,
        language: CodeLanguage,
        unit_name: Optional[str],
        start_index: int,
    ) -> List[CodeChunk]:
        """Chunk code that isn't part of any extracted symbol."""
        chunks = []
        lines = content.split('\n')

        # Find line ranges covered by symbols
        covered_ranges = [(s.line_start - 1, s.line_end) for s in symbols]  # Convert to 0-indexed
        covered_ranges.sort()

        # Merge overlapping ranges
        merged_ranges = []
        for start, end in covered_ranges:
            if merged_ranges and start <= merged_ranges[-1][1]:
                merged_ranges[-1] = (merged_ranges[-1][0], max(end, merged_ranges[-1][1]))
            else:
                merged_ranges.append((start, end))

        # Find uncovered ranges
        uncovered_ranges = []
        prev_end = 0
        for start, end in merged_ranges:
            if prev_end < start:
                uncovered_ranges.append((prev_end, start))
            prev_end = end
        if prev_end < len(lines):
            uncovered_ranges.append((prev_end, len(lines)))

        # Create chunks from uncovered ranges
        chunk_index = start_index
        for start, end in uncovered_ranges:
            block = lines[start:end]
            # Trim blank edges but keep line numbers honest
            while block and not block[0].strip():
                block.pop(0)
                start += 1
            while block and not block[-1].strip():
                block.pop()
            text = '\n'.join(block)
            if not text.strip():
                continue
            if len(text.strip()) < 50 and symbols:  # Skip tiny fragments between symbols
                continue

            pieces = self._split_lines_by_size(block, start + 1)
            sub_chunks = self._chunks_from_pieces(
                pieces, document_id, filename, language, unit_name,
                symbol_type="code_block", metadata={}, start_index=chunk_index,
            )
            chunks.extend(sub_chunks)
            chunk_index += len(sub_chunks)

        return chunks

    def _split_text_chunk(
        self,
        text: str,
        line_start: int,
        document_id: str,
        filename: str,
        language: CodeLanguage,
        unit_name: Optional[str],
    ) -> List[CodeChunk]:
        """Split a large text block into smaller chunks."""
        pieces = self._split_lines_by_size(text.split('\n'), line_start)
        chunks = self._chunks_from_pieces(
            pieces, document_id, filename, language, unit_name,
            symbol_type="code_block", metadata={}, start_index=0,
        )
        for chunk in chunks:
            chunk.chunk_id = ""  # set by caller
        return chunks


# Supported file extensions for easy checking (every key of EXTENSION_MAP).
SUPPORTED_CODE_EXTENSIONS = frozenset(CodeExtractor.EXTENSION_MAP)


def is_code_file(filename: str) -> bool:
    """Check if a file is a supported code file, by suffix or well-known basename."""
    path = Path(str(filename))
    if path.suffix.lower() in SUPPORTED_CODE_EXTENSIONS:
        return True
    name = path.name.lower()
    if name in CODE_FILENAMES:
        return True
    return any(name.startswith(prefix) for prefix, _ in _CODE_FILENAME_PREFIXES)
