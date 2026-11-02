"""Machine profiles (YAML): the built-in catalog and profile files."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from .catalog import GROUP_IDS
from .resources import profile_dir

MAX_MACHINES = 3
# Order of the catalog in the UI: the generic machine first, then the pilot machines
CATALOG = ["generic", "battenfeld_1", "arburg_31", "arburg_37", "zhafir_22", "zhafir_28", "engel_26", "engel_27"]
DEFAULT_MACHINES = ["arburg_31", "battenfeld_1"]


def builtin_profiles() -> list[str]:
    return list(CATALOG)


def load_profile(name_or_path: str) -> dict[str, Any]:
    """A built-in profile by name (see CATALOG) or a YAML file."""
    if name_or_path in CATALOG:
        path = profile_dir() / f"{name_or_path}.yaml"
    else:
        path = Path(name_or_path)
        if not path.is_file():
            raise FileNotFoundError(f"profil {name_or_path!r} neexistuje (vestavěné: {', '.join(CATALOG)})")
    with open(path, encoding="utf-8") as f:
        prof = yaml.safe_load(f) or {}
    prof = resolve(prof)
    if name_or_path in CATALOG:
        prof["preset"] = name_or_path
    validate(prof, str(path))
    return prof


def resolve(prof: dict[str, Any]) -> dict[str, Any]:
    """Apply `extends: <built-in profile>`: dictionaries are merged, everything else is replaced."""
    base_name = prof.pop("extends", None)
    if not base_name:
        return prof
    if base_name not in CATALOG:
        raise ValueError(f"extends: neznámý vestavěný profil {base_name!r}")
    with open(profile_dir() / f"{base_name}.yaml", encoding="utf-8") as f:
        base = resolve(yaml.safe_load(f) or {})
    return merge(base, prof)


def merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def validate(prof: dict[str, Any], source: str = "profil") -> None:
    ident = prof.get("identity") or {}
    for key in ("manufacturer", "serial_number", "model", "controller_name"):
        if not ident.get(key):
            raise ValueError(f"{source}: chybí identity.{key}")
    ident["serial_number"] = str(ident["serial_number"])
    for f in prof.get("facets") or []:
        if f not in ("jobs", "datasets"):
            raise ValueError(f"{source}: neznámý facet {f!r} (jobs, datasets)")
    if prof.get("optional", "all") not in ("all", "none"):
        raise ValueError(f"{source}: optional musí být all nebo none")
    for o in prof.get("options") or []:
        if o not in GROUP_IDS:
            raise ValueError(f"{source}: neznámá skupina volitelných uzlů {o!r} ({', '.join(GROUP_IDS)})")
    if not prof.get("injection_units"):
        raise ValueError(f"{source}: stroj potřebuje aspoň jednu vstřikovací jednotku")
    prof.setdefault("title", f"{ident['manufacturer']} {ident['model']}")
    prof.setdefault("job", {})
    prof.setdefault("cycle", {}).setdefault("parameters", {})
