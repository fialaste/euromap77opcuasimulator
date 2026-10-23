"""Paths to bundled files, also inside a PyInstaller one-file executable."""

from __future__ import annotations

import sys
from pathlib import Path


def package_dir() -> Path:
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen) / "e77sim"
    return Path(__file__).resolve().parent


def nodeset_dir() -> Path:
    return package_dir() / "nodesets"


def profile_dir() -> Path:
    return package_dir() / "profiles"


def static_dir() -> Path:
    return package_dir() / "ui" / "static"


def data_dir() -> Path:
    """Writable folder next to the executable (or the current folder when run from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "e77sim-data"
    return Path.cwd() / "e77sim-data"
