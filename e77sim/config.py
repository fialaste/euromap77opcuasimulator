"""Machine profiles (YAML)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .resources import profile_dir

BUILTIN = {"arburg_like": "arburg_like.yaml", "zhafir_like": "zhafir_like.yaml"}
MAX_MACHINES = 3


def builtin_profiles() -> list[str]:
    return sorted(BUILTIN)


def load_profile(name_or_path: str) -> dict[str, Any]:
    """A built-in profile by name (arburg_like, zhafir_like) or a YAML file."""
    path = Path(name_or_path)
    if name_or_path in BUILTIN:
        path = profile_dir() / BUILTIN[name_or_path]
    elif not path.is_file():
        raise FileNotFoundError(f"profil {name_or_path!r} neexistuje (vestavěné: {', '.join(builtin_profiles())})")
    with open(path, encoding="utf-8") as f:
        prof = yaml.safe_load(f) or {}
    validate(prof, str(path))
    return prof


def validate(prof: dict[str, Any], source: str = "profil") -> None:
    ident = prof.get("identity") or {}
    for key in ("manufacturer", "serial_number", "model", "controller_name"):
        if not ident.get(key):
            raise ValueError(f"{source}: chybí identity.{key}")
    for f in prof.get("facets") or []:
        if f not in ("jobs", "datasets"):
            raise ValueError(f"{source}: neznámý facet {f!r} (jobs, datasets)")
    if prof.get("optional", "all") not in ("all", "none"):
        raise ValueError(f"{source}: optional musí být all nebo none")
    if not prof.get("injection_units"):
        raise ValueError(f"{source}: stroj potřebuje aspoň jednu vstřikovací jednotku")
    prof.setdefault("job", {})
    prof.setdefault("cycle", {}).setdefault("parameters", {})
