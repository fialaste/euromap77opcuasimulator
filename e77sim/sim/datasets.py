"""ProductionDatasetManagement facet: dataset storage and TemporaryFileTransfer (OPC 10000-5, Annex C.4)."""

from __future__ import annotations

import asyncio
import fnmatch
import itertools
import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from asyncua import ua

if TYPE_CHECKING:
    from ..opcua.builder import Builder
    from ..opcua.typetree import TypeTree
    from .machine import Machine

log = logging.getLogger("e77sim")

ST_PRODUCTION, ST_PREPARATION, ST_FILE_SYSTEM = 1, 2, 4
PDM = "ProductionDatasetManagement"
ACTIVE = f"{PDM}/ActiveProductionDatasetStatus"
PREP = f"{PDM}/ProductionDatasetInPreparationStatus"
TRANSFER = f"{PDM}/ProductionDatasetTransfer"
FILE_MODE_READ, FILE_MODE_WRITE, FILE_MODE_ERASE, FILE_MODE_APPEND = 1, 2, 4, 8


@dataclass
class Dataset:
    info: Any
    content: bytes


@dataclass
class Transfer:
    handle: int
    write: bool
    storage: int
    name: str
    path: str
    nodeid: ua.NodeId
    data: bytearray = field(default_factory=bytearray)
    position: int = 0
    info: Any = None
    timeout: asyncio.Task | None = None
    handles: dict[int, int] = field(default_factory=dict)  # FileHandle -> position for extra Open() calls


class Datasets:
    def __init__(self, machine: Machine, builder: Builder, tree: TypeTree):
        self.m = machine
        self.builder = builder
        self.tree = tree
        self.library: dict[str, Dataset] = {}
        self.active: Dataset | None = None
        self.preparation: Dataset | None = None
        self.mes_list: list[dict] = []
        self.transfers: dict[int, Transfer] = {}
        self._handles = itertools.count(1)
        self._files = itertools.count(1)
        self._file_decl = None

    async def init(self) -> None:
        job = self.m.profile.get("job") or {}
        name = job.get("production_dataset_name") or "DEFAULT"
        info = self.m.dataset_info(name, job.get("production_dataset_description", ""))
        self.active = Dataset(info, self.snapshot(name))
        self.library[name] = Dataset(replace(info), self.active.content)
        await self.publish(ACTIVE, self.active)
        self._file_decl = await self.tree.root(ua.NodeId(ua.ObjectIds.FileType), "File")
        self._file_decl.ref_type = ua.NodeId(ua.ObjectIds.HasComponent)

    def handlers(self) -> dict:
        return {
            f"{TRANSFER}/GenerateFileForRead": self.m_generate_for_read,
            f"{TRANSFER}/GenerateFileForWrite": self.m_generate_for_write,
            f"{TRANSFER}/CloseAndCommit": self.m_close_and_commit,
            f"{PDM}/GetProductionDatasetInformation": self.m_get_information,
            f"{PDM}/SendProductionDatasetInformation": self.m_send_information,
            f"{PDM}/ProductionDatasetLists/GetProductionDatasetList": self.m_get_list,
            f"{PDM}/ProductionDatasetLists/SendProductionDatasetList": self.m_send_list,
            f"{ACTIVE}/Load": self.m_load,
            f"{ACTIVE}/Save": self.m_save,
            f"{PREP}/Load": self.m_load,
            f"{PREP}/Save": self.m_save,
        }

    # ---- content -----------------------------------------------------------
    def snapshot(self, name: str) -> bytes:
        """Dataset content: setpoints of the machine and the cycle parameter settings, as JSON."""
        setpoints = {
            self.m.rel(p): v
            for p, v in self.m.values.items()
            if p.endswith("/NominalTemperature") or p.endswith("/StandbyTemperature")
        }
        doc = {"format": "e77sim-dataset/1", "name": name, "machine": self.m.name, "setpoints": setpoints,
               "cycle": self.m.cycle_cfg}
        return json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")

    async def apply(self, ds: Dataset) -> None:
        try:
            doc = json.loads(ds.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.info("%s: datová sada %s není ve formátu simulátoru, nastavení se nemění", self.m.name, ds.info.Name)
            return
        for rel, v in (doc.get("setpoints") or {}).items():
            await self.m.set_if(rel, v)
        if isinstance(doc.get("cycle"), dict):
            self.m.cycle_cfg.update(doc["cycle"])

    async def publish(self, status_rel: str, ds: Dataset | None) -> None:
        info = ds.info if ds is not None else ua.ProductionDatasetInformationType()
        await self.m.set_if(f"{status_rel}/Information", info)
        await self.m.set_if(f"{status_rel}/Modified", False)

    async def mark_modified(self) -> None:
        if self.m.has(f"{ACTIVE}/Modified") and not self.m.get(f"{ACTIVE}/Modified"):
            await self.m.set(f"{ACTIVE}/Modified", True)

    async def store(self, storage: int, name: str, ds: Dataset, origin: int) -> None:
        from .machine import LB_DS_CHANGE

        if self.m.get(f"{ACTIVE}/Frozen") and storage == ST_PRODUCTION:
            raise _bad(ua.StatusCodes.BadInvalidState)
        ds.info = replace(ds.info, Name=name, LastSaveTimestamp=datetime.now(timezone.utc))
        if storage == ST_PRODUCTION:
            old = self.active.info.Name if self.active else ""
            self.active = ds
            await self.apply(ds)
            await self.publish(ACTIVE, ds)
            await self.m.log_event(LB_DS_CHANGE, {"OldProductionDatasetName": old, "NewProductionDatasetName": name},
                                   origin, f"Aktivní datová sada {old} -> {name}")
        elif storage == ST_PREPARATION:
            if not self.m.has(PREP):
                raise _bad(ua.StatusCodes.BadNotSupported)
            self.preparation = ds
            await self.publish(PREP, ds)
        elif storage == ST_FILE_SYSTEM:
            self.library[name] = ds
        else:
            raise _bad(ua.StatusCodes.BadInvalidArgument)
        log.info("%s: datová sada %s uložena (storage %s)", self.m.name, name, storage)

    def fetch(self, storage: int, name: str) -> Dataset:
        if storage == ST_PRODUCTION and self.active is not None:
            ds = Dataset(self.active.info, self.snapshot(self.active.info.Name))
            return ds
        if storage == ST_PREPARATION and self.preparation is not None:
            return self.preparation
        if storage == ST_FILE_SYSTEM and name in self.library:
            return self.library[name]
        raise _bad(ua.StatusCodes.BadNotFound)

    # ---- temporary files ---------------------------------------------------
    async def _create_file(self, write: bool, storage: int, name: str, data: bytes, info: Any) -> Transfer:
        n = next(self._files)
        tpath = self.m.p(TRANSFER)
        path = f"{tpath}/File_{n}"
        handle = next(self._handles)

        def values(spec):
            return {"Size": len(data), "Writable": write, "UserWritable": write, "OpenCount": 1}.get(spec.name)

        await self.builder.build(ua.NodeId(tpath, self.m.ns), self._file_decl, path, lambda p, d: False,
                                 lambda p, d: [], values, bname=ua.QualifiedName(f"File_{n}", self.m.ns))
        t = Transfer(handle, write, storage, name, path, ua.NodeId(path, self.m.ns), bytearray(data), info=info)
        t.handles[handle] = 0
        self.transfers[handle] = t
        for meth, fn in (("Open", self.f_open), ("Close", self.f_close), ("Read", self.f_read),
                         ("Write", self.f_write), ("GetPosition", self.f_get_position),
                         ("SetPosition", self.f_set_position)):
            nid = ua.NodeId(f"{path}/{meth}", self.m.ns)
            self.m.bind(nid, self.m.rel(f"{path}/{meth}"), _with_transfer(fn, t))
        timeout_ms = float(self.m.get(f"{TRANSFER}/ClientProcessingTimeout") or 60000)
        t.timeout = asyncio.create_task(self._expire(t, timeout_ms / 1000))
        return t

    async def _expire(self, t: Transfer, seconds: float) -> None:
        await asyncio.sleep(seconds)
        log.info("%s: přenos %s vypršel (ClientProcessingTimeout)", self.m.name, t.path.rsplit("/", 1)[-1])
        await self._drop(t, cancel_timer=False)

    async def _drop(self, t: Transfer, cancel_timer: bool = True) -> None:
        if cancel_timer and t.timeout is not None:
            t.timeout.cancel()
        for h in list(t.handles):
            self.transfers.pop(h, None)
        self.transfers.pop(t.handle, None)
        try:
            await self.m.server.delete_nodes([self.m.server.get_node(t.nodeid)], recursive=True)
        except Exception:
            pass
        for p in [p for p in self.m.specs if p == t.path or p.startswith(t.path + "/")]:
            self.m.specs.pop(p, None)
            self.m.values.pop(p, None)
        self.builder.specs = self.m.specs

    async def m_generate_for_read(self, rel, options):
        storage, name = int(options.Storage), options.Name or ""
        ds = self.fetch(storage, name)
        t = await self._create_file(False, storage, ds.info.Name or name, ds.content, ds.info)
        return [ua.Variant(t.nodeid, ua.VariantType.NodeId), ua.Variant(t.handle, ua.VariantType.UInt32),
                ua.Variant(ua.NodeId(), ua.VariantType.NodeId)]

    async def m_generate_for_write(self, rel, options):
        storage, name = int(options.Storage), options.Name or ""
        if storage not in (ST_PRODUCTION, ST_PREPARATION, ST_FILE_SYSTEM) or not name:
            raise _bad(ua.StatusCodes.BadInvalidArgument)
        if storage == ST_PRODUCTION and self.m.get(f"{ACTIVE}/Frozen"):
            raise _bad(ua.StatusCodes.BadInvalidState)
        t = await self._create_file(True, storage, name, b"", self.m.dataset_info(name))
        return [ua.Variant(t.nodeid, ua.VariantType.NodeId), ua.Variant(t.handle, ua.VariantType.UInt32)]

    async def m_close_and_commit(self, rel, handle):
        t = self.transfers.get(int(handle or 0))
        if t is None:
            raise _bad(ua.StatusCodes.BadInvalidArgument)
        try:
            if t.write:
                await self.store(t.storage, t.name, Dataset(t.info, bytes(t.data)), self.m.caller)
        finally:
            await self._drop(t)
        return [ua.Variant(ua.NodeId(), ua.VariantType.NodeId)]

    async def m_get_information(self, rel, handle):
        t = self.transfers.get(int(handle or 0))
        if t is None:
            raise _bad(ua.StatusCodes.BadInvalidArgument)
        return [ua.Variant(t.info, ua.VariantType.ExtensionObject)]

    async def m_send_information(self, rel, handle, info):
        t = self.transfers.get(int(handle or 0))
        if t is None or not t.write:
            raise _bad(ua.StatusCodes.BadInvalidArgument)
        t.info = info
        return []

    async def m_get_list(self, rel, name_filter, mould_id):
        out = []
        for name, ds in sorted(self.library.items()):
            if name_filter and not fnmatch.fnmatchcase(name, name_filter):
                continue
            if mould_id and ds.info.MouldId != mould_id:
                continue
            out.append(ds.info)
        return [ua.Variant(out, ua.VariantType.ExtensionObject, is_array=True)]

    async def m_send_list(self, rel, items):
        self.mes_list = [
            {"Name": i.Name, "Description": i.Description, "MouldId": i.MouldId, "MESId": i.MESId}
            for i in items or []
        ]
        log.info("%s: MES poslal seznam %d datových sad", self.m.name, len(self.mes_list))
        return []

    async def m_load(self, rel, name, components=None):
        target = ST_PRODUCTION if rel.startswith(ACTIVE) else ST_PREPARATION
        src = self.library.get(name or "")
        if src is None:
            raise _bad(ua.StatusCodes.BadNotFound)
        await self.store(target, name, Dataset(replace(src.info), src.content), self.m.caller)
        return []

    async def m_save(self, rel, name):
        source = ST_PRODUCTION if rel.startswith(ACTIVE) else ST_PREPARATION
        ds = self.fetch(source, "")
        if not name:
            raise _bad(ua.StatusCodes.BadInvalidArgument)
        self.library[name] = Dataset(replace(ds.info, Name=name, LastSaveTimestamp=datetime.now(timezone.utc)),
                                     ds.content)
        if source == ST_PRODUCTION:
            await self.m.set(f"{ACTIVE}/Modified", False)
        return []

    # ---- FileType methods --------------------------------------------------
    async def f_open(self, t: Transfer, rel, mode):
        mode = int(mode or 0)
        if (mode & FILE_MODE_WRITE) and not t.write:
            raise _bad(ua.StatusCodes.BadNotWritable)
        h = next(self._handles)
        t.handles[h] = 0
        self.transfers[h] = t
        return [ua.Variant(h, ua.VariantType.UInt32)]

    async def f_close(self, t: Transfer, rel, handle):
        handle = int(handle or 0)
        if handle not in t.handles:
            raise _bad(ua.StatusCodes.BadInvalidArgument)
        t.handles.pop(handle)
        self.transfers.pop(handle, None)
        if handle == t.handle:
            await self._drop(t)  # Close instead of CloseAndCommit aborts the transfer
        return []

    async def f_read(self, t: Transfer, rel, handle, length):
        handle = int(handle or 0)
        if handle not in t.handles:
            raise _bad(ua.StatusCodes.BadInvalidArgument)
        pos = t.handles[handle]
        chunk = bytes(t.data[pos:pos + max(0, int(length or 0))])
        t.handles[handle] = pos + len(chunk)
        return [ua.Variant(chunk, ua.VariantType.ByteString)]

    async def f_write(self, t: Transfer, rel, handle, data):
        handle = int(handle or 0)
        if handle not in t.handles or not t.write:
            raise _bad(ua.StatusCodes.BadInvalidArgument)
        data = bytes(data or b"")
        pos = t.handles[handle]
        t.data[pos:pos + len(data)] = data
        t.handles[handle] = pos + len(data)
        await self.m.set_if(self.m.rel(f"{t.path}/Size"), len(t.data))
        return []

    async def f_get_position(self, t: Transfer, rel, handle):
        handle = int(handle or 0)
        if handle not in t.handles:
            raise _bad(ua.StatusCodes.BadInvalidArgument)
        return [ua.Variant(t.handles[handle], ua.VariantType.UInt64)]

    async def f_set_position(self, t: Transfer, rel, handle, position):
        handle = int(handle or 0)
        if handle not in t.handles:
            raise _bad(ua.StatusCodes.BadInvalidArgument)
        t.handles[handle] = min(len(t.data), int(position or 0))
        return []


def _with_transfer(fn, t: Transfer):
    async def call(rel, *args):
        return await fn(t, rel, *args)

    return call


def _bad(code: int):
    from .machine import MethodError

    return MethodError(code)
