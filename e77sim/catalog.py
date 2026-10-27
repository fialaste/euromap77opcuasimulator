"""Catalog of machine types for the "Add machine" wizard and the groups of optional Euromap 77 nodes."""

from __future__ import annotations

import copy
from typing import Any

# Optional nodes of OPC 40077 in groups a user can understand. Patterns are globs over the full node path.
# "needs" names the facet the group belongs to; "cycle" has no nodes, it adds the optional cycle parameters.
GROUPS: list[dict[str, Any]] = [
    {"id": "identity", "title": "Rozšířená identita stroje",
     "text": "Revize hardwaru a softwaru, kód produktu, číslo majetku, odkaz na manuál.",
     "patterns": ["*/MachineInformation/*"]},
    {"id": "screw", "title": "Šnek a válec",
     "text": "Průměr šneku, maximální zdvih a objem šneku.",
     "patterns": ["*/InjectionUnits/*/ScrewDiameter*", "*/InjectionUnits/*/MaxScrewStroke*",
                  "*/InjectionUnits/*/ScrewVolume*"]},
    {"id": "zones", "title": "Tolerance teplotních zón",
     "text": "Meze odchylek, pohotovostní teplota a druh každé zóny.",
     "patterns": ["*/TemperatureZone_*/HighDeviationTemperature*", "*/TemperatureZone_*/LowDeviationTemperature*",
                  "*/TemperatureZone_*/StandbyTemperature*", "*/TemperatureZone_*/Classification"]},
    {"id": "cycle", "title": "Podrobné cyklové parametry",
     "text": "Rychlosti a tlaky vstřiku, otáčky šneku a tlak v dutině formy v události po každém cyklu.",
     "patterns": []},
    {"id": "users", "title": "Podrobnosti o obsluze",
     "text": "Jazyk, role, úroveň oprávnění a číslo karty přihlášené obsluhy.",
     "patterns": ["*/Users/User_*/CardUid", "*/Users/User_*/Language", "*/Users/User_*/UserLevel",
                  "*/Users/User_*/UserRole"]},
    {"id": "mes", "title": "Řízení z MES",
     "text": "Výroba jen s MES, hlídání spojení (watchdog), požadavek na testovací díl, adresa MES.",
     "patterns": ["*/MachineMESConfiguration/MESUrl", "*/ProductionControl/ProductionOnlyWithMES",
                  "*/ProductionControl/RequestTestSample", "*/ProductionControl/ResetWatchDog",
                  "*/ProductionControl/SetWatchDogTime"]},
    {"id": "sleep", "title": "Úsporný režim",
     "text": "Uspání a probuzení stroje z MES.",
     "patterns": ["*/MachineStatus/ActivateSleepMode", "*/MachineStatus/DeactivateSleepMode"]},
    {"id": "page", "title": "Snímek obrazovky",
     "text": "Klient si může vyžádat obrázek obrazovky řízení stroje.",
     "patterns": ["*/MachineConfiguration/GetCurrentPage", "*/MachineConfiguration/GetPage",
                  "*/MachineConfiguration/PageDirectory"]},
    {"id": "jobs_ext", "title": "Rozšířené zakázky", "needs": "jobs",
     "text": "Příprava zakázky, seznamy zakázek z MES, počítání po krabicích, průměrný čas cyklu.",
     "patterns": ["*/Jobs/*"]},
    {"id": "datasets_ext", "title": "Rozšířené datové sady", "needs": "datasets",
     "text": "Stav aktivní sady a sady v přípravě, seznamy sad, informace o sadě.",
     "patterns": ["*/ProductionDatasetManagement/*"]},
]
GROUP_IDS = [g["id"] for g in GROUPS]
_PATTERNS = {g["id"]: g["patterns"] for g in GROUPS}

DRIVES = {"elektrický": None, "hydraulický": 45.0, "hybridní": 45.0}


def option_patterns(options: list[str]) -> list[str]:
    out: list[str] = []
    for o in options or []:
        out += _PATTERNS.get(o, [])
    return out


def form_of(prof: dict[str, Any]) -> dict[str, Any]:
    """The wizard fields of a profile."""
    ident = prof["identity"]
    units = prof.get("injection_units") or []
    power = prof.get("power_units") or []
    oil = any(p.get("oil_temperature") is not None for p in power)
    return {
        "manufacturer": ident.get("manufacturer", ""),
        "model": ident.get("model", ""),
        "serial_number": str(ident.get("serial_number", "")),
        "controller_name": ident.get("controller_name", ""),
        "machine_name": (prof.get("configuration") or {}).get("user_machine_name", ""),
        "drive": prof.get("drive") or ("hydraulický" if oil else "elektrický"),
        "injection_units": len(units),
        "zones": len(_heating(units[0].get("zones") or [])) if units else 0,
        "moulds": len(prof.get("moulds") or []),
        "facets": list(prof.get("facets") or []),
        "options": list(prof["options"]) if "options" in prof else list(GROUP_IDS),
    }


def _heating(zones: list[dict]) -> list[dict]:
    return [z for z in zones if z.get("classification", "HEATING") == "HEATING"]


def _resize_zones(zones: list[dict], n: int) -> list[dict]:
    """Keep the first n heating zones (adding new ones below the last setpoint) and all other zones."""
    heating, other = _heating(zones), [z for z in zones if z not in _heating(zones)]
    heating = [dict(z) for z in heating[:n]]
    while len(heating) < n:
        last = heating[-1]["nominal"] if heating else 230.0
        heating.append({"name": f"Zone {len(heating)}", "nominal": max(40.0, float(last) - 5)})
    return heating + [dict(z) for z in other]


def _resize(items: list[dict], n: int, idkey: str) -> list[dict]:
    out = [copy.deepcopy(x) for x in items[:n]]
    while items and len(out) < n:
        x = copy.deepcopy(items[0])
        if x.get(idkey):
            x[idkey] = f"{x[idkey]}-{len(out) + 1}"
        out.append(x)
    return out


def compose(base: dict[str, Any], form: dict[str, Any]) -> dict[str, Any]:
    """A profile from a catalog entry and the wizard fields."""
    prof = copy.deepcopy(base)
    ident = prof["identity"]
    for key in ("manufacturer", "model", "serial_number", "controller_name"):
        if str(form.get(key) or "").strip():
            ident[key] = str(form[key]).strip()
    if str(form.get("machine_name") or "").strip():
        prof.setdefault("configuration", {})["user_machine_name"] = str(form["machine_name"]).strip()
        ident["component_name"] = str(form["machine_name"]).strip()

    n_units = _clamp(form.get("injection_units"), 1, 4, len(prof.get("injection_units") or []) or 1)
    units = _resize(prof.get("injection_units") or [{"zones": []}], n_units, "barrel_id")
    n_zones = _clamp(form.get("zones"), 1, 12, None)
    if n_zones is not None:
        for u in units:
            u["zones"] = _resize_zones(u.get("zones") or [], n_zones)
    prof["injection_units"] = units

    n_moulds = _clamp(form.get("moulds"), 1, 2, len(prof.get("moulds") or []) or 1)
    prof["moulds"] = _resize(prof.get("moulds") or [{"id": "MOULD-1"}], n_moulds, "id")
    if prof["moulds"] and prof.get("job"):
        prof["job"]["mould_id"] = prof["moulds"][0].get("id")

    drive = form.get("drive") or prof.get("drive") or "elektrický"
    if drive not in DRIVES:
        raise ValueError(f"neznámý pohon {drive!r}")
    prof["drive"] = drive
    oil = DRIVES[drive]
    prof["power_units"] = [{"id": "Hydraulic unit" if oil is not None else "Servo drive", "oil_temperature": oil}]

    facets = [f for f in (form.get("facets") if form.get("facets") is not None else prof.get("facets") or [])
              if f in ("jobs", "datasets")]
    prof["facets"] = facets
    options = form.get("options") if form.get("options") is not None else prof.get("options", GROUP_IDS)
    needs = {g["id"]: g.get("needs") for g in GROUPS}
    prof["options"] = [o for o in GROUP_IDS if o in options and (needs[o] is None or needs[o] in facets)]
    prof.pop("optional", None)
    prof.pop("include", None)
    prof.pop("exclude", None)
    return prof


def _clamp(v: Any, lo: int, hi: int, default: int | None) -> int | None:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default
