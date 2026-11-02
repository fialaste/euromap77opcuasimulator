"""Paths to bundled files, also inside a PyInstaller one-file executable."""

from __future__ import annotations

import sys
from pathlib import Path


def package_dir() -> Path:
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen) / "emsim"
    return Path(__file__).resolve().parent


def nodeset_dir() -> Path:
    return package_dir() / "nodesets"


def profile_dir() -> Path:
    return package_dir() / "profiles"


def static_dir() -> Path:
    return package_dir() / "ui" / "static"


def data_dir() -> Path:
    """Writable folder next to the executable (or the current folder when run from source).

    The folder of version 1.1 and older (e77sim-data) is renamed, so saved machines are kept.
    """
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    path, old = base / "emsim-data", base / "e77sim-data"
    if not path.exists() and old.is_dir():
        try:
            old.rename(path)
        except OSError:
            return old
    return path
