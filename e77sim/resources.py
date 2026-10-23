"""Paths to bundled files."""

from __future__ import annotations

from pathlib import Path


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def nodeset_dir() -> Path:
    return package_dir() / "nodesets"


def profile_dir() -> Path:
    return package_dir() / "profiles"


def static_dir() -> Path:
    return package_dir() / "ui" / "static"


def data_dir() -> Path:
    """Writable folder for certificates, in the current folder."""
    return Path.cwd() / "e77sim-data"
