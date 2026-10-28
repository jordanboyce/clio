"""Native file picker service using tkinter for desktop app integration."""

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

_native_picker_available: Optional[bool] = None


def is_native_picker_available() -> bool:
    """Check if native OS file picker is available (fails in headless/Docker environments)."""
    global _native_picker_available
    if _native_picker_available is not None:
        return _native_picker_available
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        _native_picker_available = True
    except Exception:
        _native_picker_available = False
    return _native_picker_available

# Supported file types for the file picker dialog
SUPPORTED_FILETYPES = [
    ("All supported files", "*.pdf *.txt *.docx *.pptx *.csv *.xlsx *.xls *.md *.json *.jsonl *.py *.js *.ts *.jsx *.tsx *.cs *.java *.go *.rs *.c *.h *.cpp *.hpp *.php *.rb *.swift *.kt *.scala *.pas *.dpr *.asm"),
    ("Documents", "*.pdf *.txt *.docx *.pptx *.csv *.xlsx *.xls *.md *.json *.jsonl"),
    ("Spreadsheets", "*.csv *.xlsx *.xls"),
    ("Python", "*.py *.pyw *.pyi"),
    ("JavaScript/TypeScript", "*.js *.jsx *.mjs *.ts *.tsx *.mts"),
    ("C#/Java", "*.cs *.java"),
    ("Go/Rust", "*.go *.rs"),
    ("C/C++", "*.c *.h *.cpp *.hpp *.cc *.cxx"),
    ("PHP/Ruby", "*.php *.rb *.rake"),
    ("Swift/Kotlin/Scala", "*.swift *.kt *.kts *.scala"),
    ("Pascal/Delphi", "*.pas *.dpr *.dpk *.pp *.inc *.dfm"),
    ("Modula-2", "*.mod *.def *.mi"),
    ("Assembly", "*.asm *.s"),
    ("PDF files", "*.pdf"),
    ("Text files", "*.txt *.md"),
    ("All files", "*.*"),
]


def supported_filetypes() -> List[tuple]:
    """The dialog's file-type filters, with the admin-registered custom types added.

    Without this the "All supported files" filter hides a registered ``.acme``
    file even though the server would index it.
    """
    types = list(SUPPORTED_FILETYPES)
    try:
        from services.custom_file_types import registry
        globs = " ".join(f"*{ext}" for ext in sorted(registry.extensions()))
    except Exception:  # the picker must open even if the registry cannot be read
        globs = ""
    if globs:
        label, existing = types[0]
        types[0] = (label, f"{existing} {globs}")
        types.insert(1, ("Custom file types", globs))
    return types


def open_file_dialog(multiple: bool = True, title: str = "Select files to index") -> List[str]:
    """
    Open a native file picker dialog.

    Args:
        multiple: If True, allow selecting multiple files
        title: Dialog window title

    Returns:
        List of selected file paths (empty list if cancelled)
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        logger.error("tkinter not available - cannot open file picker")
        return []

    # Create a hidden root window
    root = tk.Tk()
    root.withdraw()

    # Bring dialog to front (important on Windows)
    root.attributes('-topmost', True)
    root.update()

    try:
        if multiple:
            paths = filedialog.askopenfilenames(
                title=title,
                filetypes=supported_filetypes()
            )
            result = list(paths) if paths else []
        else:
            path = filedialog.askopenfilename(
                title=title,
                filetypes=supported_filetypes()
            )
            result = [path] if path else []
    except Exception as e:
        logger.error(f"Error opening file dialog: {e}")
        result = []
    finally:
        root.destroy()

    logger.info(f"File picker selected {len(result)} file(s)")
    return result


def open_folder_dialog(title: str = "Select folder to index") -> Optional[str]:
    """
    Open a native folder picker dialog.

    Args:
        title: Dialog window title

    Returns:
        Selected folder path, or None if cancelled
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        logger.error("tkinter not available - cannot open folder picker")
        return None

    # Create a hidden root window
    root = tk.Tk()
    root.withdraw()

    # Bring dialog to front
    root.attributes('-topmost', True)
    root.update()

    try:
        path = filedialog.askdirectory(title=title)
        result = path if path else None
    except Exception as e:
        logger.error(f"Error opening folder dialog: {e}")
        result = None
    finally:
        root.destroy()

    if result:
        logger.info(f"Folder picker selected: {result}")
    return result
