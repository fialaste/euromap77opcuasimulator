"""The running machines: start, add and remove at run time, saved to stroje.yaml in the data folder."""

from __future__ import annotations

import asyncio
import copy
import logging
from pathlib import Path
from typing import Any

import yaml

from .config import DEFAULT_MACHINES, MAX_MACHINES, load_profile, validate
from .sim.machine import Machine

log = logging.getLogger("e77sim")

BASE_PORT = 4840
SAVE_FILE = "stroje.yaml"
SAVE_HEADER = ("# Stroje simulátoru, uložené z webového UI.\n"
               "# Smazáním tohoto souboru (nebo tlačítkem Obnovit výchozí stroje) se vrátí výchozí stroje.\n")


class FleetError(Exception):
    pass


class Fleet:
    def __init__(self, data_dir: Path, host: str = "0.0.0.0", seed: int | None = None,
                 passwords: dict[str, str] | None = None, allow_anonymous: bool = True, security: bool = True,
                 persist: bool = True):
        self.data_dir = data_dir
        self.host = host
        self.seed = seed
        self.passwords = passwords or {}
        self.allow_anonymous = allow_anonymous
        self.security = security
        self.persist = persist
        self.machines: list[Machine] = []
        self._started = 0
        self._lock = asyncio.Lock()  # one add, remove or reset at a time

    @property
    def save_path(self) -> Path:
        return self.data_dir / SAVE_FILE

    # ------------------------------------------------------------- specs
    def default_specs(self) -> list[tuple[dict, int | None]]:
        return [(load_profile(name), BASE_PORT + i) for i, name in enumerate(DEFAULT_MACHINES)]

    def saved_specs(self) -> list[tuple[dict, int | None]] | None:
        """Machines from stroje.yaml, None when there is no file or it cannot be used."""
        if not self.save_path.is_file():
            return None
        try:
            with open(self.save_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            out = []
            for item in data.get("machines") or []:
                prof = item["profile"]
                validate(prof, SAVE_FILE)
                out.append((prof, item.get("port")))
            return out[:MAX_MACHINES]
        except Exception as e:
            log.warning("%s nejde načíst (%s), spouštím výchozí stroje", self.save_path, e)
            return None

    def save(self) -> None:
        if not self.persist:
            return
        data = {"machines": [{"port": m.port, "profile": m.profile} for m in self.machines]}
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.save_path, "w", encoding="utf-8") as f:
            f.write(SAVE_HEADER)
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

    # ------------------------------------------------------------ machines
    def free_port(self, wanted: int | None = None) -> int:
        used = {m.port for m in self.machines}
        if wanted and wanted not in used:
            return wanted
        port = BASE_PORT
        while port in used:
            port += 1
        return port

    def unique(self, prof: dict) -> dict:
        """The same type twice needs a different serial number, so the machines are distinguishable."""
        prof = copy.deepcopy(prof)
        ident = prof["identity"]
        names = {(m.profile["identity"]["manufacturer"], str(m.profile["identity"]["serial_number"]))
                 for m in self.machines}
        sn, n = str(ident["serial_number"]), 2
        key = (ident["manufacturer"], sn)
        while key in names:
            key = (ident["manufacturer"], f"{sn}-{n}")
            n += 1
        if key[1] != sn:
            ident["serial_number"] = key[1]
            conf = prof.setdefault("configuration", {})
            conf["user_machine_name"] = f"{conf.get('user_machine_name', 'Lis')} ({n - 1})"
        return prof

    async def add(self, prof: dict, port: int | None = None, save: bool = True) -> Machine:
        async with self._lock:
            return await self._add(prof, port, save)

    async def _add(self, prof: dict, port: int | None, save: bool) -> Machine:
        if len(self.machines) >= MAX_MACHINES:
            raise FleetError(f"Simulátor pojme nejvýše {MAX_MACHINES} stroje. Nejdřív některý odeberte.")
        validate(prof, "stroj")
        prof = self.unique(prof)
        port = self.free_port(port)
        seed = None if self.seed is None else self.seed + self._started
        m = Machine(prof, port, self.host, self.data_dir, seed=seed, passwords=self.passwords,
                    allow_anonymous=self.allow_anonymous, security=self.security)
        try:
            await m.start()
        except OSError as e:
            await m.stop()
            raise FleetError(f"Port {port} pro {m.name} nelze otevřít: {e}") from e
        self._started += 1
        self.machines.append(m)
        log.info("Přidán stroj %s na %s", m.name, m.endpoint)
        if save:
            self.save()
        return m

    def find(self, name: str) -> Machine | None:
        return next((m for m in self.machines if m.name == name), None)

    async def remove(self, name: str) -> None:
        async with self._lock:
            m = self.find(name)
            if m is None:
                raise FleetError(f"stroj {name} neběží")
            self.machines.remove(m)
            await m.stop()
            log.info("Odebrán stroj %s", m.name)
            self.save()

    async def start(self, specs: list[tuple[dict, int | None]]) -> None:
        async with self._lock:
            for prof, port in specs[:MAX_MACHINES]:
                await self._add(prof, port, save=False)

    async def reset(self) -> None:
        """Back to the default machines; the saved file is deleted (kept when nothing is saved, i.e. with -m)."""
        async with self._lock:
            await self._stop_all()
            if self.persist and self.save_path.is_file():
                self.save_path.unlink()
            for prof, port in self.default_specs():
                await self._add(prof, port, save=False)

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_all()

    async def _stop_all(self) -> None:
        while self.machines:
            await self.machines.pop().stop()


def profile_yaml(prof: dict[str, Any]) -> str:
    return yaml.safe_dump(prof, allow_unicode=True, sort_keys=False)
