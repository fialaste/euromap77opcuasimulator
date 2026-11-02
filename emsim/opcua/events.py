"""Concrete event types and event emission.

All logbook and request event types of OPC 40077 are abstract, so the simulator defines
a concrete subtype of each one it sends in its own namespace. The concrete
CycleParametersEventType carries one InjectionUnitCycleParameters_<n> object per
injection unit and one MouldCycleParameters_<n> object per mould (OPC 40077, 17.3).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from asyncua import Server, ua
from asyncua.server.history import HistoryDict

from ..units import default_range, eu_information, unit_code
from .builder import Builder, NodeSpec
from .typetree import MR_MANDATORY, Decl, TypeTree

log = logging.getLogger(__name__)

# LogbookEventsEnumeration value -> abstract type name
LOGBOOK_TYPES = {
    0: "ParameterChangeLogType",
    1: "UserLogType",
    2: "RemoteAccessLogType",
    3: "SequenceChangeLogType",
    4: "MachineModeChangeLogType",
    5: "ProductionStatusChangeLogType",
    6: "ProductionDatasetChangeLogType",
    7: "ProductionDatasetFrozenLogType",
    8: "StandstillReasonLogType",
    9: "MessageLogType",
    10: "UserFeedbackLogType",
}
REQUEST_TYPES = [
    "RequestAddMaterialEventType",
    "RequestCyclicJobListEventType",
    "RequestCyclicJobWriteEventType",
    "RequestJobListEventType",
    "RequestProductionDatasetListEventType",
    "RequestProductionDatasetReadEventType",
    "RequestProductionDatasetWriteEventType",
]
# Fields of the BaseEventType that asyncua fills itself
_BASE_FIELDS = {
    "EventId",
    "EventType",
    "SourceNode",
    "SourceName",
    "Time",
    "ReceiveTime",
    "LocalTime",
    "Message",
    "Severity",
    "ConditionClassId",
    "ConditionClassName",
    "ConditionSubClassId",
    "ConditionSubClassName",
}


class LogbookHistory(HistoryDict):
    """asyncua rebuilds the event from its fields before storing it and loses the emitting node;
    take it from SourceNode instead."""

    async def save_event(self, event) -> None:
        source = getattr(event, "SourceNode", None)
        if source in self._events_periods:
            event.emitting_node = source
        elif event.emitting_node not in self._events_periods:
            return
        await super().save_event(event)


@dataclass
class EventField:
    path: str
    vtype: ua.VariantType
    is_array: bool


class EventType:
    def __init__(self, name: str, nodeid: ua.NodeId, fields: dict[str, EventField]):
        self.name = name
        self.nodeid = nodeid
        self.fields = fields
        self.generator = None  # set by Events.init


class Events:
    def __init__(self, server: Server, ns: int, tree: TypeTree, source: ua.NodeId, history_count: int = 1000):
        self.server = server
        self.ns = ns
        self.tree = tree
        self.source = source
        self.history_count = history_count
        self.types: dict[str, EventType] = {}
        self.cycle: EventType | None = None
        self.cycle_constants: dict[str, tuple[Any, ua.VariantType]] = {}
        self._lock = asyncio.Lock()
        self.clock_offset = timedelta(0)
        self.listeners: list = []

    async def _find_type(self, name: str) -> ua.NodeId:
        async def walk(node):
            for child in await node.get_children(refs=ua.ObjectIds.HasSubtype):
                if (await child.read_browse_name()).Name == name:
                    return child.nodeid
                found = await walk(child)
                if found is not None:
                    return found
            return None

        found = await walk(self.server.get_node(ua.ObjectIds.BaseEventType))
        if found is None:
            found = await walk(self.server.get_node(ua.ObjectIds.BaseObjectType))
        if found is None:
            raise KeyError(name)
        return found

    async def _fields(self, type_id: ua.NodeId, builder: Builder | None = None, root: str = "") -> dict[str, EventField]:
        fields: dict[str, EventField] = {}

        async def collect(decls: list[Decl], prefix: str):
            for d in decls:
                if d.name in _BASE_FIELDS and not prefix:
                    continue
                p = f"{prefix}{d.name}"
                if d.nodeclass == ua.NodeClass.Variable:
                    vt = await builder_vtype(d.data_type)
                    fields[p] = EventField(p, vt, d.value_rank >= 0)
                if d.nodeclass in (ua.NodeClass.Variable, ua.NodeClass.Object):
                    await collect(d.children, p + "/")

        tmp = builder or Builder(self.server, self.ns, self.tree)
        builder_vtype = tmp.vtype
        await collect(await self.tree.type_children(type_id), "")
        return fields

    async def _concrete(self, abstract_name: str, sim_name: str) -> EventType:
        parent = self.server.get_node(await self._find_type(abstract_name))
        nodeid = ua.NodeId(f"Types/{sim_name}", self.ns)
        await parent.add_object_type(nodeid, ua.QualifiedName(sim_name, self.ns))
        fields = await self._fields(nodeid)
        et = EventType(sim_name, nodeid, fields)
        self.types[abstract_name] = et
        return et

    async def init(self, injection_units: list[dict], moulds: list[dict], cycle_params: list[str], logbook: list[int],
                   with_jobs: bool, with_datasets: bool) -> None:
        for value in logbook:
            abstract = LOGBOOK_TYPES[value]
            await self._concrete(abstract, "Sim" + abstract)
        requests = []
        if with_jobs:
            requests += [
                "RequestCyclicJobListEventType",
                "RequestCyclicJobWriteEventType",
                "RequestJobListEventType",
                "RequestAddMaterialEventType",
            ]
        if with_datasets:
            requests += [
                "RequestProductionDatasetListEventType",
                "RequestProductionDatasetReadEventType",
                "RequestProductionDatasetWriteEventType",
            ]
        for abstract in requests:
            await self._concrete(abstract, "Sim" + abstract)
        await self._build_cycle_type(injection_units, moulds, cycle_params)

        source = self.server.get_node(self.source)
        await source.write_attribute(
            ua.AttributeIds.EventNotifier,
            ua.DataValue(ua.Variant(ua.EventNotifier.SubscribeToEvents.mask, ua.VariantType.Byte)),
        )
        # Server -> machine notifier hierarchy, so a client can find the event source by browsing
        ref = ua.AddReferencesItem()
        ref.SourceNodeId = ua.NodeId(ua.ObjectIds.Server)
        ref.ReferenceTypeId = ua.NodeId(ua.ObjectIds.HasNotifier)
        ref.IsForward = True
        ref.TargetNodeId = self.source
        ref.TargetNodeClass = ua.NodeClass.Object
        await self.server.iserver.isession.add_references([ref])
        for et in [*self.types.values(), self.cycle]:
            et.generator = await self.server.get_event_generator(et.nodeid, self.source)
        self.server.iserver.history_manager.set_storage(LogbookHistory())
        await self.server.historize_node_event(source, period=None, count=self.history_count)

    async def _build_cycle_type(self, injection_units: list[dict], moulds: list[dict], cycle_params: list[str]):
        b = Builder(self.server, self.ns, self.tree)
        cpe = self.server.get_node(await self._find_type("CycleParametersEventType"))
        iucp_id = await self._find_type("InjectionUnitCycleParametersType")
        mcp_id = await self._find_type("MouldCycleParametersType")
        tzcp_id = await self._find_type("TemperatureZoneCycleParametersType")
        tz_children = await self.tree.type_children(tzcp_id)
        iu_children = await self.tree.type_children(iucp_id)
        mould_children = await self.tree.type_children(mcp_id)

        evt_id = ua.NodeId("Types/SimCycleParametersEventType", self.ns)
        await cpe.add_object_type(evt_id, ua.QualifiedName("SimCycleParametersEventType", self.ns))

        def zone_decl(name: str) -> Decl:
            return Decl(
                nodeid=tzcp_id,
                nodeclass=ua.NodeClass.Object,
                browse_name=ua.QualifiedName(name, tzcp_id.NamespaceIndex),
                display_name=ua.LocalizedText(name),
                description=ua.LocalizedText(""),
                ref_type=ua.NodeId(ua.ObjectIds.HasComponent),
                type_def=tzcp_id,
                rule=MR_MANDATORY,
                children=tz_children,
            )

        def select(path: str, d: Decl) -> bool:
            # Optional cycle parameters are only part of the event when the profile lists them
            if d.nodeclass == ua.NodeClass.Variable and d.ref_type == ua.NodeId(ua.ObjectIds.HasComponent):
                return d.name in cycle_params or d.name == "Classification"
            return True

        def values(spec: NodeSpec) -> Any:
            return None

        def nothing(path: str, d: Decl):
            return []

        async def add_unit(kind: str, index: int, abstract_id: ua.NodeId, base_children: list[Decl], zones: list[dict]):
            prefix = "Barrel" if kind == "InjectionUnit" else "Mould"
            type_name = f"Sim{kind}{index}CycleParametersType"
            type_id = ua.NodeId(f"Types/{type_name}", self.ns)
            await self.server.get_node(abstract_id).add_object_type(type_id, ua.QualifiedName(type_name, self.ns))
            children = list(base_children) + [
                zone_decl(f"{prefix}TemperatureZoneCycleParameters_{j + 1}") for j in range(len(zones))
            ]
            type_decl = Decl(abstract_id, ua.NodeClass.Object, ua.QualifiedName(type_name, self.ns),
                             ua.LocalizedText(type_name), ua.LocalizedText(""), ua.NodeId(ua.ObjectIds.HasSubtype),
                             type_id, MR_MANDATORY, children=children)
            for c in children:
                if c.mandatory or select("", c):
                    await b.build(type_id, c, f"Types/{type_name}/{c.name}", select, nothing, values, as_type=True)
            obj_name = f"{kind}CycleParameters_{index}"
            obj_decl = Decl(abstract_id, ua.NodeClass.Object,
                            ua.QualifiedName(obj_name, abstract_id.NamespaceIndex), ua.LocalizedText(obj_name),
                            ua.LocalizedText(""), ua.NodeId(ua.ObjectIds.HasComponent), type_id, MR_MANDATORY,
                            children=type_decl.children)
            await b.build(evt_id, obj_decl, f"Types/SimCycleParametersEventType/{obj_name}", select, nothing, values,
                          as_type=True)

        for i, iu in enumerate(injection_units):
            await add_unit("InjectionUnit", i + 1, iucp_id, iu_children, iu.get("zones", []))
        for i, m in enumerate(moulds):
            await add_unit("Mould", i + 1, mcp_id, mould_children, m.get("zones", []))

        fields = await self._fields(evt_id, b)
        self.cycle = EventType("SimCycleParametersEventType", evt_id, fields)
        # Constant sub-properties of analog fields (EURange, EngineeringUnits) and zone identities
        for path in fields:
            name = path.rsplit("/", 1)[-1]
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            pname = parent.rsplit("/", 1)[-1]
            if name == "EngineeringUnits":
                eu = eu_information(unit_code(pname))
                if eu is not None:
                    self.cycle_constants[path] = (eu, ua.VariantType.ExtensionObject)
            elif name == "EURange":
                lo, hi = default_range(parent, pname)
                self.cycle_constants[path] = (ua.Range(Low=lo, High=hi), ua.VariantType.ExtensionObject)

    async def emit(self, et: EventType | None, fields: dict[str, Any], message: str, severity: int = 100) -> None:
        """Send one event to subscribers of the machine object and of the Server object."""
        if et is None or et.generator is None:
            return
        async with self._lock:
            # Every event is its own object: the history keeps a reference to it
            ev = copy.copy(et.generator.event)
            ev.data_types = dict(ev.data_types)
            for key, f in et.fields.items():
                if key in fields:
                    val = fields[key]
                    if isinstance(val, ua.Variant):
                        ev.add_property(key, val.Value, val.VariantType)
                    else:
                        ev.add_property(key, val, f.vtype)
                elif key not in self.cycle_constants or et is not self.cycle:
                    ev.add_property(key, None, f.vtype)
            if et is self.cycle:
                for key, (val, vt) in self.cycle_constants.items():
                    ev.add_property(key, val, vt)
            now = datetime.now(timezone.utc)
            ev.EventId = ua.Variant(uuid.uuid4().hex.encode("utf-8"), ua.VariantType.ByteString)
            ev.Time = now + self.clock_offset
            ev.ReceiveTime = now
            local = time.localtime(now.timestamp())
            ev.LocalTime = ua.TimeZoneDataType(Offset=local.tm_gmtoff // 60, DaylightSavingInOffset=local.tm_isdst > 0)
            ev.Message = ua.LocalizedText(message)
            ev.Severity = severity
            ev.SourceNode = self.source
            ev.emitting_node = self.source
            service = self.server.iserver.isession.subscription_service
            await service.trigger_event(ev)
            server_ev = copy.copy(ev)
            server_ev.emitting_node = ua.NodeId(ua.ObjectIds.Server)
            await service.trigger_event(server_ev)
            record = {
                "time": ev.Time.isoformat(),
                "type": et.name,
                "message": message,
                "severity": severity,
                "fields": {k: _jsonable(v) for k, v in fields.items()},
            }
        for listener in self.listeners:
            try:
                listener(record)
            except Exception:  # pragma: no cover - UI listener must never break the server
                log.exception("event listener failed")


def _jsonable(v: Any) -> Any:
    if isinstance(v, ua.Variant):
        v = v.Value
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, ua.LocalizedText):
        return v.Text
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)
