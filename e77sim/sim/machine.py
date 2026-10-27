"""One simulated injection moulding machine: address space, state, cycle and method logic."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import random
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from asyncua import Server, ua
from asyncua.common.callback import CallbackType

from ..catalog import option_patterns
from ..opcua.builder import Builder, NodeSpec, coerce, make_variant, match_any
from ..opcua.events import Events
from ..opcua.server import NS_DI, NS_IMM, create_server
from ..opcua.typetree import TypeTree
from ..units import default_range, eu_information, unit_code, unit_display
from . import generators as gens
from .datasets import Datasets
from .page import page_png

log = logging.getLogger("e77sim")

# Enumerations (values from the NodeSets)
MM_OTHER, MM_AUTOMATIC, MM_SEMI_AUTOMATIC, MM_MANUAL, MM_SETUP, MM_SLEEP = range(6)
PS_OTHER, PS_NO_PRODUCTION, PS_START_UP, PS_READY, PS_PRODUCTION, PS_DRY_RUN = range(6)
JS_OTHER, JS_TRANSFERRED, JS_SETUP_ACTIVE, JS_SETUP_INT, JS_SETUP_FIN, JS_STARTUP = range(6)
JS_IN_PRODUCTION, JS_INTERRUPTED, JS_FINISHED = 6, 7, 8
CQ_GOOD, CQ_BAD, CQ_TEST, CQ_FAILED = range(4)
CAV_NO_PART, CAV_GOOD, CAV_BAD, CAV_REWORK = range(4)
EO_OTHER, EO_MACHINE, EO_OPERATOR, EO_MES = range(4)
TZ_CLASS = {"OTHER": 0, "HEATING": 1, "COOLING": 2, "TEMPERATURE_CONTROL": 3, "HOT_RUNNER": 4, "MEASURING": 5}
LB_PARAMETER, LB_USER, LB_MODE, LB_STATUS, LB_DS_CHANGE, LB_DS_FROZEN, LB_STANDSTILL, LB_MESSAGE = 0, 1, 4, 5, 6, 7, 8, 9

MANDATORY_CYCLE_PARAMS = ["CushionVolume", "PlastificationVolume", "SpecificPressureMaximum", "InjectionTime", "DosingTime"]
COUNTERS = {
    "JobCycleCounter", "JobPartsCounter", "JobGoodPartsCounter", "JobBadPartsCounter", "JobTestSamplesCounter",
    "BoxCycleCounter", "BoxPartsCounter", "BoxGoodPartsCounter", "BoxBadPartsCounter", "BoxTestSamplesCounter",
    "MachineCycleCounter", "LastCycleTime", "AverageCycleTime", "LastPartId",
}
DERIVED = {"MachineMESStatus/ProductionControl/ProductionStatus", "Jobs/ActiveJobValues/JobStatus",
           "MachineMESStatus/ProductionControl/AutomaticRunEnabled"}
IDENTITY_ANALOG = {"ScrewDiameter", "MaxScrewStroke", "ScrewVolume"}
IDENTITY_NAMES = {"Index", "Id", "Name", "NodeVersion", "BarrelId", "ScrewId", "Description", "Classification",
                  "CardUid", "UserLevel", "UserRole", "Language"}


class MethodError(Exception):
    def __init__(self, code: int, text: str = ""):
        super().__init__(text or ua.StatusCode(code).name)
        self.code = code


def _lt(v: Any) -> ua.LocalizedText:
    return v if isinstance(v, ua.LocalizedText) else ua.LocalizedText(str(v or ""))


class Machine:
    def __init__(self, profile: dict, port: int, host: str, data_dir: Path, seed: int | None = None,
                 passwords: dict[str, str] | None = None, allow_anonymous: bool = True, security: bool = True):
        self.profile = profile
        ident = profile["identity"]
        self.name = f"IMM_{_clean(ident['manufacturer'])}_{_clean(ident['serial_number'])}"
        self.port = port
        self.host = host
        self.data_dir = data_dir
        self.passwords = passwords or {}
        self.allow_anonymous = allow_anonymous
        self.security = security
        self.facets = list(profile.get("facets") or [])
        self.rng = random.Random(seed)
        self.seed = seed
        self.server: Server | None = None
        self.ns = 0
        self.specs: dict[str, NodeSpec] = {}
        self.values: dict[str, Any] = {}
        self.generators: dict[str, gens.Generator] = {}
        self.events: Events | None = None
        self.datasets: Datasets | None = None
        self.running = False
        self.cycle_enabled = True
        self.event_log: deque = deque(maxlen=200)
        self.cycle_cfg: dict[str, dict] = {k: dict(v) for k, v in (profile.get("cycle", {}).get("parameters") or {}).items()
                                           if "options" not in profile or "cycle" in profile["options"]
                                           or k in MANDATORY_CYCLE_PARAMS}
        self.last_cycle: dict[str, Any] = {}
        self.cycle_times: deque = deque(maxlen=10)
        self.cycle_progress = 0.0
        self.test_sample_requested = False
        self.stop_at_cycle_end = False
        self.watchdog_ms = 0
        self.watchdog_deadline: float | None = None
        self.cyclic_job_queue: list[dict] = []
        self.job_list: list[dict] = []
        self.box_number = 1
        self.part_number = 0
        self.standstill_active = False
        self.clients = 0
        self._tasks: list[asyncio.Task] = []
        self._suppress_external = False
        self._sleep_previous_mode = MM_AUTOMATIC
        self.started_at: datetime | None = None
        self.ui_call = False  # set while the web UI calls a method, for EventOriginator

    @property
    def caller(self) -> int:
        return EO_OPERATOR if self.ui_call else EO_MES

    # ------------------------------------------------------------------ paths
    def p(self, rel: str) -> str:
        return f"{self.name}/{rel}" if rel else self.name

    def rel(self, path: str) -> str:
        return path[len(self.name) + 1:] if path.startswith(self.name + "/") else ""

    def has(self, rel: str) -> bool:
        return self.p(rel) in self.specs

    def get(self, rel: str, default: Any = None) -> Any:
        return self.values.get(self.p(rel), default)

    @property
    def endpoint(self) -> str:
        host = self.host if self.host not in ("0.0.0.0", "") else "localhost"
        return f"opc.tcp://{host}:{self.port}/"

    # ------------------------------------------------------------------ build
    async def start(self) -> None:
        self.server = await create_server(
            self.name, self.host, self.port, self.data_dir / "pki", self.facets, self.passwords,
            self.allow_anonymous, self.security,
        )
        server = self.server
        self.ns = await server.register_namespace(f"urn:e77sim:{self.name}")
        tree = TypeTree(server)
        imm = await server.get_namespace_index(NS_IMM)
        root_decl = await tree.root(ua.NodeId(1007, imm), self.name)
        di = await server.get_namespace_index(NS_DI)
        builder = Builder(server, self.ns, tree)
        root_decl.ref_type = ua.NodeId(ua.ObjectIds.HasComponent)
        await builder.build(
            ua.NodeId(5001, di), root_decl, self.name, self._select, self._expand, self._initial_value,
            bname=ua.QualifiedName(self.name, self.ns),
        )
        self.specs = builder.specs
        for path, spec in self.specs.items():
            spec.cls = self._classify(spec)
            if spec.analog:
                spec.unit = unit_display(unit_code(spec.name))
                spec.eurange = default_range(path, spec.name, self.profile.get("ranges"))
            if spec.nodeclass == ua.NodeClass.Variable:
                self.values[path] = await self._read(spec)

        self.events = Events(server, self.ns, tree, ua.NodeId(self.name, self.ns))
        self.events.listeners.append(self._on_event)
        await self.events.init(
            self.profile.get("injection_units") or [],
            self.profile.get("moulds") or [],
            self.cycle_param_names(),
            self.supported_logbook_events(),
            "jobs" in self.facets,
            "datasets" in self.facets,
        )
        if "datasets" in self.facets:
            self.datasets = Datasets(self, builder, tree)
            await self.datasets.init()
        self._bind_methods()
        server.subscribe_server_callback(CallbackType.PostWrite, self._post_write)
        self._setup_generators()
        await server.start()
        self.running = True
        self.started_at = datetime.now(timezone.utc)
        await self.update_production_status(EO_MACHINE)
        self._tasks = [
            asyncio.create_task(self._generator_loop(), name=f"{self.name}-gen"),
            asyncio.create_task(self._cycle_loop(), name=f"{self.name}-cycle"),
            asyncio.create_task(self._watchdog_loop(), name=f"{self.name}-watchdog"),
        ]
        log.info("%s (%s) běží na %s", self.name, self.profile.get("title", ""), self.endpoint)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        if self.server is not None and self.running:
            await self.server.stop()
        self.running = False

    async def restart(self) -> None:
        """Stop and start only the OPC UA endpoint, the address space and state are kept."""
        if self.running:
            await self.server.stop()
            self.running = False
            log.info("%s: OPC UA server zastaven", self.name)
        else:
            await self.server.start()
            self.running = True
            log.info("%s: OPC UA server spuštěn na %s", self.name, self.endpoint)

    def _select(self, path: str, decl) -> bool:
        rel = self.rel(path)
        top = rel.split("/", 1)[0]
        if top == "Jobs" and "jobs" not in self.facets:
            return False
        if top == "ProductionDatasetManagement" and "datasets" not in self.facets:
            return False
        if rel == "Jobs" or rel == "ProductionDatasetManagement":
            return True  # the facet requires the object
        if decl.name == "EngineeringUnits":
            return unit_code(path.rsplit("/", 2)[-2]) is not None
        if rel.startswith("InjectionUnits/") and rel.endswith("/ScrewId"):
            return bool(self._unit_cfg(rel).get("screw_id"))
        if rel.startswith("PowerUnits/") and rel.endswith("/ActualTemperature"):
            return self._power_cfg(rel).get("oil_temperature") is not None
        if "options" in self.profile:
            return match_any(path, option_patterns(self.profile["options"]) + (self.profile.get("include") or []))
        mode = self.profile.get("optional", "all")
        if mode == "all":
            return not match_any(path, self.profile.get("exclude") or [])
        return match_any(path, self.profile.get("include") or [])

    def _expand(self, path: str, decl) -> list[tuple[str, dict]]:
        rel = self.rel(path)
        prof = self.profile
        if decl.name == "InjectionUnit_<Nr>":
            return [(f"InjectionUnit_{i + 1}", u) for i, u in enumerate(prof.get("injection_units") or [])]
        if decl.name == "Mould_<Nr>":
            return [(f"Mould_{i + 1}", m) for i, m in enumerate(prof.get("moulds") or [])]
        if decl.name == "PowerUnit_<Nr>":
            return [(f"PowerUnit_{i + 1}", m) for i, m in enumerate(prof.get("power_units") or [])]
        if decl.name == "User_<Nr>":
            return [(f"User_{i + 1}", u) for i, u in enumerate(prof.get("users") or [])]
        if decl.name == "<TemperatureZone_Nr>":
            parts = rel.split("/")
            zones = []
            if parts[0] == "InjectionUnits":
                zones = self._unit_cfg(rel).get("zones") or []
            elif parts[0] == "Moulds":
                zones = self._mould_cfg(rel).get("zones") or []
            return [(f"TemperatureZone_{j + 1}", z) for j, z in enumerate(zones)]
        return []

    def _indexed(self, rel: str, key: str, prefix: str) -> tuple[int, dict]:
        parts = rel.split("/")
        if len(parts) > 1 and parts[1].startswith(prefix + "_"):
            i = int(parts[1].rsplit("_", 1)[1]) - 1
            items = self.profile.get(key) or []
            if 0 <= i < len(items):
                return i + 1, items[i]
        return 0, {}

    def _unit_cfg(self, rel: str) -> dict:
        return self._indexed(rel, "injection_units", "InjectionUnit")[1]

    def _mould_cfg(self, rel: str) -> dict:
        return self._indexed(rel, "moulds", "Mould")[1]

    def _power_cfg(self, rel: str) -> dict:
        return self._indexed(rel, "power_units", "PowerUnit")[1]

    def _zone_cfg(self, rel: str) -> tuple[int, dict]:
        parts = rel.split("/")
        owner = self._unit_cfg(rel) if parts[0] == "InjectionUnits" else self._mould_cfg(rel)
        if len(parts) > 3 and parts[3].startswith("TemperatureZone_"):
            j = int(parts[3].rsplit("_", 1)[1])
            zones = owner.get("zones") or []
            if 0 < j <= len(zones):
                return j, zones[j - 1]
        return 0, {}

    def supported_logbook_events(self) -> list[int]:
        lb = [LB_PARAMETER, LB_USER, LB_MODE, LB_STATUS, LB_STANDSTILL, LB_MESSAGE]
        if "datasets" in self.facets:
            lb += [LB_DS_CHANGE, LB_DS_FROZEN]
        return sorted(lb)

    def cycle_param_names(self) -> list[str]:
        return [k for k in self.cycle_cfg if k not in MANDATORY_CYCLE_PARAMS]

    def _initial_value(self, spec: NodeSpec) -> Any:
        rel = self.rel(spec.path)
        name = spec.name
        prof = self.profile
        ident = prof["identity"]
        conf = prof.get("configuration") or {}
        job = prof.get("job") or {}
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""

        if name == "EURange":
            pname = parent.rsplit("/", 1)[-1]
            lo, hi = default_range(spec.path.rsplit("/", 1)[0], pname, prof.get("ranges"))
            return ua.Range(Low=lo, High=hi)
        if name == "EngineeringUnits":
            return eu_information(unit_code(parent.rsplit("/", 1)[-1]))
        if name == "NodeVersion":
            return "1"

        if rel.startswith("MachineInformation/"):
            m = {
                "Manufacturer": _lt(ident.get("manufacturer")),
                "ManufacturerUri": ident.get("manufacturer_uri", ""),
                "Model": _lt(ident.get("model")),
                "SerialNumber": str(ident.get("serial_number", "")),
                "ControllerName": ident.get("controller_name", ""),
                "DeviceClass": "Injection Moulding Machine",
                "ProductCode": ident.get("product_code", ""),
                "HardwareRevision": ident.get("hardware_revision", ""),
                "SoftwareRevision": ident.get("software_revision", ""),
                "DeviceRevision": ident.get("device_revision", ""),
                "DeviceManual": ident.get("device_manual", ""),
                "AssetId": ident.get("asset_id", ""),
                "ComponentName": _lt(ident.get("component_name", conf.get("user_machine_name", ""))),
                "RevisionCounter": 0,
                "ProductInstanceUri": f"{ident.get('manufacturer_uri') or 'urn:' + _clean(ident['manufacturer'])}"
                                      f"/{ident.get('serial_number')}",
                "SupportedLogbookEvents": self.supported_logbook_events(),
            }
            return m.get(name)

        if rel.startswith("MachineConfiguration/"):
            if name == "UserMachineName":
                return conf.get("user_machine_name", "")
            if name == "LocationName":
                return conf.get("location_name", "")
            if name == "TimeZoneOffset":
                off = datetime.now().astimezone().utcoffset() or timedelta(0)
                return ua.TimeZoneDataType(Offset=int(off.total_seconds() // 60),
                                           DaylightSavingInOffset=bool(time.localtime().tm_isdst > 0))
            if name == "PageDirectory":
                return [ua.PageEntryDataType(Id="main", Title=ua.LocalizedText("Hlavní obrazovka")),
                        ua.PageEntryDataType(Id="temperatures", Title=ua.LocalizedText("Teploty"))]

        if rel.startswith("MachineMESConfiguration/"):
            if name == "MESUrl":
                return conf.get("mes_url", "")
            if name == "StandstillReasons":
                return [ua.StandstillReasonType(Id=str(r["id"]), Text=ua.LocalizedText(r.get("text", "")),
                                                LockedByMES=bool(r.get("locked_by_mes", False)))
                        for r in prof.get("standstill_reasons") or []]

        if rel.startswith("MachineMESStatus/"):
            if name == "AutomaticRunEnabled":
                return True
            if name == "ProductionOnlyWithMES":
                return bool(conf.get("production_only_with_mes", False))
            if name == "ProductionReleasedByMES":
                return bool(conf.get("production_released_by_mes", True))
            if name == "ProductionStatus":
                return PS_NO_PRODUCTION
            if rel.endswith("MESMessage/Severity") or rel.endswith("StandstillMessage/Severity"):
                return 0

        if rel.startswith("MachineStatus/"):
            if rel == "MachineStatus/IsPresent":
                return True
            if name == "MachineMode":
                return MM_AUTOMATIC
            idx, user = self._indexed(rel[len("MachineStatus/"):], "users", "User")
            if user:
                return {
                    "Id": str(user.get("id", idx)),
                    "Name": user.get("name", ""),
                    "IsPresent": bool(user.get("present", idx == 1)),
                    "CardUid": user.get("card_uid", ""),
                    "Language": user.get("language", "cs-CZ"),
                    "UserLevel": str(user.get("user_level", "")),
                    "UserRole": user.get("user_role", ""),
                }.get(name)

        if rel.startswith("InjectionUnits/") or rel.startswith("Moulds/") or rel.startswith("PowerUnits/"):
            j, zone = self._zone_cfg(rel)
            if zone:
                nominal = float(zone.get("nominal", 200))
                return {
                    "Name": zone.get("name", f"Zone {j}"),
                    "Index": j,
                    "IsPresent": True,
                    "ControlMode": 2,  # AUTOMATIC
                    "Classification": TZ_CLASS.get(str(zone.get("classification", "HEATING")).upper(), 1),
                    "NominalTemperature": nominal,
                    "ActualTemperature": nominal,
                    "StandbyTemperature": float(zone.get("standby", max(0.0, nominal - 60))),
                    "HighDeviationTemperature1": float(zone.get("high1", 10)),
                    "HighDeviationTemperature2": float(zone.get("high2", 20)),
                    "LowDeviationTemperature1": float(zone.get("low1", 10)),
                    "LowDeviationTemperature2": float(zone.get("low2", 20)),
                }.get(name)
            if rel.startswith("InjectionUnits/"):
                i, unit = self._indexed(rel, "injection_units", "InjectionUnit")
                return {
                    "Index": i, "IsPresent": True, "InProduction": False,
                    "BarrelId": unit.get("barrel_id", ""), "ScrewId": unit.get("screw_id", ""),
                    "ScrewDiameter": unit.get("screw_diameter"), "MaxScrewStroke": unit.get("max_screw_stroke"),
                    "ScrewVolume": unit.get("screw_volume"),
                }.get(name)
            if rel.startswith("Moulds/"):
                i, mould = self._indexed(rel, "moulds", "Mould")
                return {"Index": i, "IsPresent": True, "MouldStatus": 3, "Id": mould.get("id", ""),
                        "Description": mould.get("description", "")}.get(name)
            if rel.startswith("PowerUnits/"):
                i, pu = self._indexed(rel, "power_units", "PowerUnit")
                return {"Index": i, "IsPresent": True, "PowerOn": True, "Id": pu.get("id", f"PU{i}"),
                        "ActualTemperature": pu.get("oil_temperature")}.get(name)

        if rel.startswith("Jobs/ActiveJob/"):
            return _job_value(name, job)
        if rel.startswith("Jobs/ActiveJobValues/"):
            if name == "JobStatus":
                return JS_IN_PRODUCTION if job.get("autostart", True) else JS_TRANSFERRED
            if name == "CurrentLotName":
                return job.get("lot_name", "")
            if name == "BoxId":
                return self._box_id()
            if name == "MachineCycleCounter":
                return int(prof.get("machine_cycle_counter", 125000))
        if rel.startswith("Jobs/JobInPreparation/"):
            return _job_value(name, {})
        if rel.startswith("ProductionDatasetManagement/"):
            if name == "ClientProcessingTimeout":
                return 60000.0
            if name == "Information":
                return self.dataset_info(job.get("production_dataset_name", ""),
                                         job.get("production_dataset_description", "")) \
                    if "ActiveProductionDatasetStatus" in rel else ua.ProductionDatasetInformationType()
        return None

    def _box_id(self) -> str:
        return f"BOX-{self.box_number:05d}"

    def dataset_info(self, name: str, description: str = "") -> Any:
        ident = self.profile["identity"]
        conf = self.profile.get("configuration") or {}
        job = self.profile.get("job") or {}
        now = datetime.now(timezone.utc)
        return ua.ProductionDatasetInformationType(
            Name=name, Description=description, MESId="", CreationTimestamp=now, LastModificationTimestamp=now,
            LastSaveTimestamp=now, UserName=self.current_user()[1] if self.specs else "", Components=1,
            Manufacturer=str(ident.get("manufacturer", "")), SerialNumber=str(ident.get("serial_number", "")),
            Model=str(ident.get("model", "")), ControllerName=str(ident.get("controller_name", "")),
            UserMachineName=str(conf.get("user_machine_name", "")), LocationName=str(conf.get("location_name", "")),
            ProductName=(job.get("product_name") or [""])[0], MouldId=str(job.get("mould_id", "")),
            NumCavities=int(job.get("num_cavities", 1)),
        )

    def _classify(self, spec: NodeSpec) -> str:
        rel = self.rel(spec.path)
        if spec.nodeclass == ua.NodeClass.Method:
            return "M"
        if spec.nodeclass == ua.NodeClass.Object:
            return ""
        if spec.name in ("EURange", "EngineeringUnits"):
            return "I"
        if spec.name in COUNTERS or rel in DERIVED:
            return "C"
        if rel.startswith("Jobs/") or rel.startswith("ProductionDatasetManagement/"):
            return "K"
        if spec.analog:
            if spec.name.startswith("Actual"):
                return "A"
            return "I" if spec.name in IDENTITY_ANALOG else "K"
        if rel.startswith("MachineInformation/") or spec.name in IDENTITY_NAMES:
            return "I"
        if spec.enum or spec.vtype == ua.VariantType.Boolean:
            return "S"
        return "K"

    # ------------------------------------------------------------------ store
    async def _read(self, spec: NodeSpec) -> Any:
        dv = await self.server.get_node(spec.nodeid).read_data_value(raise_on_bad_status=False)
        return dv.Value.Value if dv.Value is not None else None

    async def set(self, rel_or_path: str, value: Any) -> Any:
        """Write a value to the store and the OPC UA node. Returns the old value."""
        path = rel_or_path if rel_or_path.startswith(self.name + "/") else self.p(rel_or_path)
        spec = self.specs.get(path)
        if spec is None:
            return None
        if not spec.is_array and spec.vtype is not None:
            value = coerce(spec.vtype, value)
        old = self.values.get(path)
        self.values[path] = value
        now = datetime.now(timezone.utc)
        dv = ua.DataValue(make_variant(spec, value), SourceTimestamp=now, ServerTimestamp=now)
        await self.server.write_attribute_value(spec.nodeid, dv)
        return old

    async def set_if(self, rel: str, value: Any) -> None:
        if self.has(rel):
            await self.set(rel, value)

    async def _post_write(self, event, dispatcher) -> None:
        """A client wrote values: update the store and run the reactions."""
        if not event.is_external:
            return
        user = getattr(event.user, "name", None) or "client"
        for wv, status in zip(event.request_params.NodesToWrite, event.response_params or []):
            if not status.is_good() or wv.AttributeId != ua.AttributeIds.Value:
                continue
            nid = wv.NodeId
            if nid.NamespaceIndex != self.ns or not isinstance(nid.Identifier, str):
                continue
            path = nid.Identifier
            if path not in self.specs:
                continue
            old = self.values.get(path)
            new = wv.Value.Value.Value if wv.Value.Value is not None else None
            self.values[path] = new
            log.info("%s: klient %s zapsal %s = %s", self.name, user, self.rel(path), _short(new))
            await self.on_change(self.rel(path), old, new, EO_MES)

    async def operator_set(self, rel: str, value: Any) -> None:
        """A value set from the web UI."""
        spec = self.specs.get(self.p(rel))
        if spec is None:
            raise KeyError(rel)
        if spec.cls == "C":
            raise ValueError("čítač nebo odvozená hodnota se nedá nastavit")
        gen = self.generators.get(spec.path)
        if gen is not None:
            gen.enabled = False  # a manual value pauses the generator of the node
        old = await self.set(rel, value)
        await self.on_change(rel, old, self.values[spec.path], EO_OPERATOR)

    async def on_change(self, rel: str, old: Any, new: Any, origin: int) -> None:
        if old == new:
            return
        name = rel.rsplit("/", 1)[-1]
        if rel == "MachineStatus/MachineMode":
            await self.log_event(LB_MODE, {"OldMachineMode": old, "NewMachineMode": new}, origin,
                                 f"MachineMode {self.enum_name(rel, old)} -> {self.enum_name(rel, new)}")
        elif rel == "MachineMESStatus/StandstillReasonId":
            await self.log_event(LB_STANDSTILL, {"StandstillReasonId": new}, origin, f"Důvod prostoje {new!r}")
        elif rel.endswith("Status/Frozen"):
            await self.log_event(LB_DS_FROZEN, {"OldValue": bool(old), "NewValue": bool(new)}, origin,
                                 f"Datová sada {'zmrazena' if new else 'uvolněna'}")
        elif rel.startswith("MachineStatus/Users/") and name == "IsPresent":
            uid = self.get(rel.rsplit("/", 1)[0] + "/Id")
            await self.log_event(LB_USER, {"UserChange": 0 if new else 1}, origin,
                                 f"Uživatel {uid} {'přihlášen' if new else 'odhlášen'}",
                                 user_rel=rel.rsplit("/", 1)[0])
        else:
            spec = self.specs[self.p(rel)]
            fields = {"ParameterId": rel, "OldValue": make_variant(spec, old), "NewValue": make_variant(spec, new)}
            if spec.analog:
                eu = eu_information(unit_code(spec.name))
                fields["OldValueUnit"] = eu
                fields["NewValueUnit"] = eu
            await self.log_event(LB_PARAMETER, fields, origin, f"Změna parametru {rel}")
            if self.datasets is not None and spec.cls in ("K", "A") and "Temperature" in rel and name != "ActualTemperature":
                await self.datasets.mark_modified()
        await self.update_production_status(origin)

    def enum_name(self, rel: str, value: Any) -> str:
        spec = self.specs.get(self.p(rel))
        if spec is not None and spec.enum and value in spec.enum:
            return spec.enum[value]
        return str(value)

    def current_user(self) -> tuple[str | None, str]:
        """(rel path, name) of the first logged in operator."""
        for i, _ in enumerate(self.profile.get("users") or []):
            rel = f"MachineStatus/Users/User_{i + 1}"
            if self.get(rel + "/IsPresent"):
                return rel, self.get(rel + "/Name") or ""
        return None, ""

    async def log_event(self, kind: int, fields: dict, origin: int, message: str, severity: int = 100,
                        user_rel: str | None = None) -> None:
        if self.events is None:
            return
        from ..opcua.events import LOGBOOK_TYPES

        et = self.events.types.get(LOGBOOK_TYPES[kind])
        user_rel = user_rel or self.current_user()[0]
        f = dict(fields)
        f["EventOriginator"] = origin
        if self.has("Jobs/ActiveJobValues/JobCycleCounter"):
            f["JobCycleCounter"] = self.get("Jobs/ActiveJobValues/JobCycleCounter")
        if user_rel:
            for key in ("Id", "Name", "IsPresent", "CardUid", "Language", "UserLevel", "UserRole"):
                if self.has(f"{user_rel}/{key}"):
                    f[f"User/{key}"] = self.get(f"{user_rel}/{key}")
        else:
            f["User/Id"] = ""
            f["User/Name"] = "MES" if origin == EO_MES else ""
            f["User/IsPresent"] = False
        await self.events.emit(et, f, message, severity)

    def _on_event(self, record: dict) -> None:
        self.event_log.appendleft(record)

    # ------------------------------------------------------------- production
    def can_produce(self) -> tuple[bool, str]:
        if not self.cycle_enabled:
            return False, "cyklus zastaven v UI"
        mode = self.get("MachineStatus/MachineMode")
        if mode not in (MM_AUTOMATIC, MM_SEMI_AUTOMATIC):
            return False, f"režim {self.enum_name('MachineStatus/MachineMode', mode)}"
        if self.standstill_active:
            return False, "aktivní hlášení prostoje"
        pc = "MachineMESStatus/ProductionControl/"
        if not self.get(pc + "AutomaticRunEnabled"):
            return False, "automatický běh zakázán (DisableAutomaticRun)"
        if self.get(pc + "ProductionOnlyWithMES", False) and not self.get(pc + "ProductionReleasedByMES"):
            return False, "čeká na uvolnění výroby z MES"
        if self.has("Jobs/ActiveJobValues/JobStatus"):
            js = self.get("Jobs/ActiveJobValues/JobStatus")
            if js != JS_IN_PRODUCTION:
                return False, f"zakázka {self.enum_name('Jobs/ActiveJobValues/JobStatus', js)}"
        return True, "výroba"

    async def update_production_status(self, origin: int = EO_MACHINE) -> None:
        ok, _ = self.can_produce()
        mode = self.get("MachineStatus/MachineMode")
        if ok:
            status = PS_PRODUCTION
        elif mode in (MM_AUTOMATIC, MM_SEMI_AUTOMATIC) and not self.standstill_active:
            status = PS_READY
        else:
            status = PS_NO_PRODUCTION
        rel = "MachineMESStatus/ProductionControl/ProductionStatus"
        old = self.get(rel)
        if old != status:
            await self.set(rel, status)
            await self.log_event(LB_STATUS, {"OldProductionStatus": old, "NewProductionStatus": status}, origin,
                                 f"ProductionStatus {self.enum_name(rel, old)} -> {self.enum_name(rel, status)}")
        for i, _ in enumerate(self.profile.get("injection_units") or []):
            await self.set_if(f"InjectionUnits/InjectionUnit_{i + 1}/InProduction", status == PS_PRODUCTION)

    async def _cycle_loop(self) -> None:
        cyc = self.profile.get("cycle") or {}
        while True:
            ok, _ = self.can_produce()
            if not ok:
                self.cycle_progress = 0.0
                await asyncio.sleep(0.25)
                continue
            expected = self.get("Jobs/ActiveJob/ExpectedCycleTime") or (self.profile.get("job") or {}).get(
                "expected_cycle_time", 20000)
            duration = max(500.0, float(expected) * (1 + self.rng.gauss(0, float(cyc.get("jitter", 0.03)))))
            t0 = time.monotonic()
            aborted = False
            while (elapsed := (time.monotonic() - t0) * 1000) < duration:
                self.cycle_progress = elapsed / duration
                if not self.can_produce()[0]:
                    aborted = True
                    break
                await asyncio.sleep(min(0.2, (duration - elapsed) / 1000))
            self.cycle_progress = 0.0
            if aborted:
                continue
            try:
                await self.complete_cycle(duration)
            except Exception:
                log.exception("%s: chyba při dokončení cyklu", self.name)

    def sample_cycle_parameters(self) -> tuple[dict[str, float], bool]:
        values, in_tol = {}, True
        for name, cfg in self.cycle_cfg.items():
            v = self.rng.gauss(float(cfg.get("nominal", 0)), float(cfg.get("sigma", 0)))
            v = max(0.0, v)
            tol = cfg.get("tol")
            if tol is not None and abs(v - float(cfg.get("nominal", 0))) > float(tol):
                in_tol = False
            values[name] = round(v, 3)
        return values, in_tol

    async def complete_cycle(self, duration_ms: float) -> None:
        job = self.profile.get("job") or {}
        jobs = self.has("Jobs/ActiveJobValues/JobStatus")
        cavities = int(self.get("Jobs/ActiveJob/NumCavities") or job.get("num_cavities", 1) or 1)
        av = "Jobs/ActiveJobValues/"
        params_by_unit = []
        all_in_tol = True
        for _ in self.profile.get("injection_units") or []:
            params, in_tol = self.sample_cycle_parameters()
            params_by_unit.append(params)
            all_in_tol = all_in_tol and in_tol
        if self.test_sample_requested:
            quality = CQ_TEST
            self.test_sample_requested = False
        elif not all_in_tol or self.rng.random() < float((self.profile.get("cycle") or {}).get("scrap_rate", 0)):
            quality = CQ_BAD
        else:
            quality = CQ_GOOD
        good = cavities if quality == CQ_GOOD else 0
        bad = cavities if quality == CQ_BAD else 0
        test = cavities if quality == CQ_TEST else 0

        counters = {}
        if jobs:
            for prefix in ("Job", "Box"):
                for key, inc in (("CycleCounter", 1), ("PartsCounter", cavities), ("GoodPartsCounter", good),
                                 ("BadPartsCounter", bad), ("TestSamplesCounter", test)):
                    rel = f"{av}{prefix}{key}"
                    if self.has(rel):
                        counters[prefix + key] = int(self.get(rel) or 0) + inc
                        await self.set(rel, counters[prefix + key])
            if self.has(av + "MachineCycleCounter"):
                counters["MachineCycleCounter"] = int(self.get(av + "MachineCycleCounter") or 0) + 1
                await self.set(av + "MachineCycleCounter", counters["MachineCycleCounter"])
        self.cycle_times.append(duration_ms)
        avg = sum(self.cycle_times) / len(self.cycle_times)
        job_name = self.get("Jobs/ActiveJob/JobName") or job.get("job_name", "")
        part_ids = []
        for c in range(cavities):
            self.part_number += 1
            part_ids.append(f"{job_name}-{self.part_number:07d}")
        await self.set_if(av + "LastCycleTime", duration_ms)
        await self.set_if(av + "AverageCycleTime", avg)
        await self.set_if(av + "LastPartId", part_ids)

        cav_q = {CQ_GOOD: CAV_GOOD, CQ_BAD: CAV_BAD, CQ_TEST: CAV_GOOD}.get(quality, CAV_NO_PART)
        fields: dict[str, Any] = {
            "JobName": job_name,
            "CurrentLotName": self.get(av + "CurrentLotName") or job.get("lot_name", ""),
            "CycleTime": duration_ms,
            "CycleQuality": quality,
            "CavityCycleQuality": [cav_q] * cavities,
            "PartId": part_ids,
            "AverageCycleTime": avg,
            "JobStatus": self.get(av + "JobStatus", JS_IN_PRODUCTION),
            "BoxId": self.get(av + "BoxId", ""),
        }
        for key in ("JobCycleCounter", "JobPartsCounter", "JobGoodPartsCounter", "JobBadPartsCounter",
                    "JobTestSamplesCounter"):
            fields[key] = counters.get(key, self.last_cycle.get("fields", {}).get(key, 0) + (
                {"JobCycleCounter": 1, "JobPartsCounter": cavities, "JobGoodPartsCounter": good,
                 "JobBadPartsCounter": bad, "JobTestSamplesCounter": test}[key]))
        for key in ("BoxCycleCounter", "BoxPartsCounter", "BoxGoodPartsCounter", "BoxBadPartsCounter",
                    "BoxTestSamplesCounter", "MachineCycleCounter"):
            if key in counters:
                fields[key] = counters[key]
        for i, params in enumerate(params_by_unit):
            pre = f"InjectionUnitCycleParameters_{i + 1}/"
            fields[pre + "Index"] = i + 1
            for k, v in params.items():
                fields[pre + k] = v
            fields.update(self._zone_fields(pre + "BarrelTemperatureZoneCycleParameters_",
                                            f"InjectionUnits/InjectionUnit_{i + 1}"))
        for i, _ in enumerate(self.profile.get("moulds") or []):
            pre = f"MouldCycleParameters_{i + 1}/"
            fields[pre + "Index"] = i + 1
            fields.update(self._zone_fields(pre + "MouldTemperatureZoneCycleParameters_", f"Moulds/Mould_{i + 1}"))
        await self.events.emit(self.events.cycle, fields, f"Cyklus {fields['JobCycleCounter']} dokončen")
        self.last_cycle = {"time": datetime.now(timezone.utc).isoformat(), "fields": fields,
                           "params": params_by_unit, "quality": quality}

        if jobs:
            nominal_box = int(self.get("Jobs/ActiveJob/NominalBoxParts") or 0)
            if nominal_box and self.has(av + "BoxPartsCounter") and int(self.get(av + "BoxGoodPartsCounter") or 0) >= nominal_box:
                await self.new_box()
            nominal = int(self.get("Jobs/ActiveJob/NominalParts") or 0)
            if nominal and int(self.get(av + "JobGoodPartsCounter") or 0) >= nominal and not self.get(
                    "Jobs/ActiveJob/ContinueAtJobEnd"):
                await self.set_job_status(JS_FINISHED, EO_MACHINE)
                log.info("%s: zakázka %s dokončena (%s dílů)", self.name, job_name, nominal)
            if self.stop_at_cycle_end:
                self.stop_at_cycle_end = False
                await self.set_job_status(JS_INTERRUPTED, EO_MACHINE)
        elif self.stop_at_cycle_end:
            self.stop_at_cycle_end = False
            self.cycle_enabled = False
            await self.update_production_status()

    def _zone_fields(self, prefix: str, owner_rel: str) -> dict[str, Any]:
        out = {}
        j = 1
        while self.has(f"{owner_rel}/TemperatureZones/TemperatureZone_{j}"):
            z = f"{owner_rel}/TemperatureZones/TemperatureZone_{j}/"
            out[f"{prefix}{j}/Index"] = j
            out[f"{prefix}{j}/Name"] = self.get(z + "Name")
            out[f"{prefix}{j}/Classification"] = self.get(z + "Classification", 1)
            out[f"{prefix}{j}/ActualTemperature"] = round(float(self.get(z + "ActualTemperature") or 0), 2)
            j += 1
        return out

    async def new_box(self) -> None:
        av = "Jobs/ActiveJobValues/"
        self.box_number += 1
        for key in ("BoxCycleCounter", "BoxPartsCounter", "BoxGoodPartsCounter", "BoxBadPartsCounter",
                    "BoxTestSamplesCounter"):
            await self.set_if(av + key, 0)
        await self.set_if(av + "BoxId", self._box_id())

    async def set_job_status(self, status: int, origin: int) -> None:
        rel = "Jobs/ActiveJobValues/JobStatus"
        if not self.has(rel):
            return
        old = self.get(rel)
        if old != status:
            await self.set(rel, status)
            log.info("%s: JobStatus %s -> %s", self.name, self.enum_name(rel, old), self.enum_name(rel, status))
        await self.update_production_status(origin)

    async def reset_job_counters(self) -> None:
        av = "Jobs/ActiveJobValues/"
        for key in ("JobCycleCounter", "JobPartsCounter", "JobGoodPartsCounter", "JobBadPartsCounter",
                    "JobTestSamplesCounter"):
            await self.set_if(av + key, 0)

    # ------------------------------------------------------------- generators
    def _setup_generators(self) -> None:
        for g in self.profile.get("generators") or []:
            for path, spec in self.specs.items():
                if spec.cls in ("A", "S") and fnmatch.fnmatchcase(self.rel(path), g["path"]):
                    seed = None if self.seed is None else hash((self.seed, path)) & 0xFFFFFFFF
                    params = {k: v for k, v in g.items() if k not in ("path", "enabled")}
                    self.generators[path] = gens.make(params, seed=seed, enabled=g.get("enabled", True))

    def set_generator(self, rel: str, params: dict | None, enabled: bool = True) -> None:
        """`params` None removes the generator of the node."""
        spec = self.specs[self.p(rel)]
        if spec.cls not in ("A", "S"):
            raise ValueError("generátor jde nastavit jen u analogových (A) a stavových (S) uzlů")
        if params is None:
            self.generators.pop(spec.path, None)
            return
        params = dict(params)
        if spec.cls == "S":
            choices = self.state_choices(spec)
            nominal = gens._typed(params.get("nominal"))
            if nominal is not None and nominal not in choices:
                raise ValueError("nominální stav není ve výčtu uzlu")
            dev = float(params.get("deviation") or 0)
            if not 0 <= dev <= 1:
                raise ValueError("u stavu je odchylka pravděpodobnost 0 až 1")
        seed = None if self.seed is None else hash((self.seed, spec.path)) & 0xFFFFFFFF
        self.generators[spec.path] = gens.make(params, seed=seed, enabled=enabled)

    @staticmethod
    def state_choices(spec: NodeSpec) -> list:
        if spec.enum:
            return list(spec.enum)
        return [True, False] if spec.vtype == ua.VariantType.Boolean else []

    def randomize_all(self) -> None:
        for path, spec in self.specs.items():
            if spec.cls == "A" and path not in self.generators and spec.eurange:
                lo, hi = spec.eurange
                self.generators[path] = gens.make({"nominal": None, "deviation": (hi - lo) * 0.002, "tau": 10})
        for g in self.generators.values():
            g.enabled = True

    def freeze_all(self) -> None:
        for g in self.generators.values():
            g.enabled = False

    async def _generator_loop(self) -> None:
        while True:
            now = time.monotonic()
            for path, g in list(self.generators.items()):
                if not g.due(now):
                    continue
                spec = self.specs[path]
                parent = path.rsplit("/", 1)[0]
                siblings = {s.name: self.values.get(p) for p, s in self.specs.items()
                            if p.rsplit("/", 1)[0] == parent} if isinstance(g.nominal, str) else {}
                lo, hi = spec.eurange or (float("-inf"), float("inf"))
                choices = self.state_choices(spec) if spec.cls == "S" else None
                try:
                    new = g.step(self.values.get(path), lo, hi, siblings, now, choices)
                except Exception as e:  # bad parameters from the UI
                    g.enabled = False
                    log.warning("%s: generátor %s vypnut: %s", self.name, self.rel(path), e)
                    continue
                if spec.vtype in (ua.VariantType.Double, ua.VariantType.Float) and new is not None:
                    new = round(float(new), 3)
                old = self.values.get(path)
                if new != old:
                    await self.set(path, new)
                    if spec.cls == "S":
                        await self.on_change(self.rel(path), old, self.values[path], EO_MACHINE)
            await asyncio.sleep(0.1)

    # --------------------------------------------------------------- watchdog
    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(0.2)
            if self.watchdog_deadline is not None and time.monotonic() > self.watchdog_deadline:
                self.watchdog_deadline = None
                log.warning("%s: watchdog vypršel, výroba už není uvolněna z MES", self.name)
                rel = "MachineMESStatus/ProductionControl/ProductionReleasedByMES"
                old = await self.set(rel, False)
                await self.raise_message("WATCHDOG", "Watchdog MES vypršel", 600, 114, False, EO_MACHINE)
                await self.on_change(rel, old, False, EO_MACHINE)

    # --------------------------------------------------------------- messages
    async def raise_message(self, msg_id: str, text: str, severity: int, classification: int, standstill: bool,
                            origin: int = EO_OPERATOR) -> None:
        await self.log_event(LB_MESSAGE, {"Classification": classification, "Id": msg_id,
                                          "IsStandstillMessage": standstill}, origin, text, severity)
        if standstill:
            sm = "MachineMESStatus/StandstillMessage/"
            await self.set(sm + "Id", msg_id)
            await self.set(sm + "Message", ua.LocalizedText(text))
            await self.set(sm + "Severity", severity)
            await self.set(sm + "Classification", classification)
            self.standstill_active = True
            await self.update_production_status(origin)

    async def clear_standstill(self) -> None:
        sm = "MachineMESStatus/StandstillMessage/"
        await self.set(sm + "Id", "")
        await self.set(sm + "Message", ua.LocalizedText(""))
        await self.set(sm + "Severity", 0)
        await self.set(sm + "Classification", 0)
        self.standstill_active = False
        await self.update_production_status(EO_OPERATOR)

    async def send_request_event(self, abstract: str, fields: dict, message: str) -> None:
        et = self.events.types.get(abstract)
        if et is None:
            raise ValueError(f"{abstract} není v profilu")
        await self.events.emit(et, fields, message)

    # ---------------------------------------------------------------- methods
    def _bind_methods(self) -> None:
        handlers = {
            "MachineConfiguration/SetMachineTime": self.m_set_machine_time,
            "MachineConfiguration/GetPage": self.m_get_page,
            "MachineConfiguration/GetCurrentPage": self.m_get_current_page,
            "MachineMESStatus/SetMESMessage": self.m_set_mes_message,
            "MachineMESStatus/ClearMESMessage": self.m_clear_mes_message,
            "MachineMESStatus/ProductionControl/EnableAutomaticRun": self.m_enable_automatic_run,
            "MachineMESStatus/ProductionControl/DisableAutomaticRun": self.m_disable_automatic_run,
            "MachineMESStatus/ProductionControl/RequestTestSample": self.m_request_test_sample,
            "MachineMESStatus/ProductionControl/ResetWatchDog": self.m_reset_watchdog,
            "MachineMESStatus/ProductionControl/SetWatchDogTime": self.m_set_watchdog_time,
            "MachineStatus/ActivateSleepMode": self.m_activate_sleep,
            "MachineStatus/DeactivateSleepMode": self.m_deactivate_sleep,
            "Jobs/SendCyclicJobList": self.m_send_cyclic_job_list,
            "Jobs/SendJobList": self.m_send_job_list,
            "Jobs/ActiveJob/SetCyclicJobData": self.m_set_cyclic_job_data,
            "Jobs/JobInPreparation/SetCyclicJobData": self.m_set_cyclic_job_data,
            "Jobs/ActiveJobValues/StartJob": self.m_start_job,
            "Jobs/ActiveJobValues/InterruptJob": self.m_interrupt_job,
            "Jobs/ActiveJobValues/FinishJob": self.m_finish_job,
            "Jobs/ActiveJobValues/StopAtCycleEnd": self.m_stop_at_cycle_end,
            "Jobs/ActiveJobValues/ResetJobCounters": self.m_reset_job_counters,
            "Jobs/ActiveJobValues/ResetBoxCounters": self.m_reset_box_counters,
            "Jobs/ActiveJobValues/ResetAverageCycleTime": self.m_reset_average_cycle_time,
        }
        if self.datasets is not None:
            handlers.update(self.datasets.handlers())
        for rel, fn in handlers.items():
            spec = self.specs.get(self.p(rel))
            if spec is not None:
                self.bind(spec.nodeid, rel, fn)

    def bind(self, nodeid: ua.NodeId, rel: str, fn, out_types: list[ua.VariantType] | None = None) -> None:
        async def callback(parent, *args):
            values = [a.Value if isinstance(a, ua.Variant) else a for a in args]
            try:
                result = await fn(rel, *values)
            except MethodError as e:
                log.info("%s: %s() -> %s", self.name, rel.rsplit("/", 1)[-1], ua.StatusCode(e.code).name)
                return ua.StatusCode(e.code)
            except TypeError as e:
                log.info("%s: %s() špatné argumenty: %s", self.name, rel, e)
                return ua.StatusCode(ua.StatusCodes.BadArgumentsMissing)
            log.info("%s: volání %s(%s)", self.name, rel, ", ".join(_short(v) for v in values))
            return result or []

        self.server.link_method(self.server.get_node(nodeid), callback)

    async def m_set_machine_time(self, rel, dt, tz=None):
        if not isinstance(dt, datetime):
            raise MethodError(ua.StatusCodes.BadInvalidArgument)
        aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        self.events.clock_offset = aware - datetime.now(timezone.utc)
        if tz is not None:
            await self.set("MachineConfiguration/TimeZoneOffset", tz)
        return []

    async def m_get_page(self, rel, page_id):
        ids = [p.Id for p in self.get("MachineConfiguration/PageDirectory") or []]
        if page_id not in ids:
            raise MethodError(ua.StatusCodes.BadInvalidArgument)
        return [ua.Variant(page_png(self, page_id), ua.VariantType.ByteString)]

    async def m_get_current_page(self, rel, unit):
        return [ua.Variant(page_png(self, "main"), ua.VariantType.ByteString)]

    async def m_set_mes_message(self, rel, msg_id, message, severity):
        await self.set("MachineMESStatus/MESMessage/Id", msg_id or "")
        await self.set("MachineMESStatus/MESMessage/Message", message or "")
        await self.set("MachineMESStatus/MESMessage/Severity", int(severity or 0))
        return []

    async def m_clear_mes_message(self, rel):
        return await self.m_set_mes_message(rel, "", "", 0)

    async def _set_auto_run(self, value: bool):
        rel = "MachineMESStatus/ProductionControl/AutomaticRunEnabled"
        old = await self.set(rel, value)
        if old != value:
            await self.log_event(LB_PARAMETER, {"ParameterId": rel, "OldValue": ua.Variant(bool(old)),
                                                "NewValue": ua.Variant(value)}, self.caller, f"AutomaticRunEnabled = {value}")
        await self.update_production_status(self.caller)
        return []

    async def m_enable_automatic_run(self, rel):
        return await self._set_auto_run(True)

    async def m_disable_automatic_run(self, rel):
        return await self._set_auto_run(False)

    async def m_request_test_sample(self, rel):
        self.test_sample_requested = True
        return []

    async def m_reset_watchdog(self, rel):
        if self.watchdog_ms > 0:
            self.watchdog_deadline = time.monotonic() + self.watchdog_ms / 1000
        return []

    async def m_set_watchdog_time(self, rel, ms):
        if ms is None or int(ms) < 0:
            raise MethodError(ua.StatusCodes.BadInvalidArgument)
        self.watchdog_ms = int(ms)
        self.watchdog_deadline = time.monotonic() + self.watchdog_ms / 1000 if self.watchdog_ms else None
        return []

    async def m_activate_sleep(self, rel):
        mode = self.get("MachineStatus/MachineMode")
        if mode == MM_SLEEP:
            return []
        self._sleep_previous_mode = mode
        old = await self.set("MachineStatus/MachineMode", MM_SLEEP)
        await self.on_change("MachineStatus/MachineMode", old, MM_SLEEP, self.caller)
        return []

    async def m_deactivate_sleep(self, rel):
        if self.get("MachineStatus/MachineMode") != MM_SLEEP:
            return []
        new = self._sleep_previous_mode
        old = await self.set("MachineStatus/MachineMode", new)
        await self.on_change("MachineStatus/MachineMode", old, new, self.caller)
        return []

    async def m_send_cyclic_job_list(self, rel, job_list):
        self.cyclic_job_queue = [_struct_dict(j) for j in (job_list or [])]
        log.info("%s: přijat seznam %d cyklických zakázek", self.name, len(self.cyclic_job_queue))
        if self.has("Jobs/JobInPreparation/JobName") and not self.get("Jobs/JobInPreparation/JobName"):
            await self.prepare_next_job()
        return []

    async def m_send_job_list(self, rel, job_list):
        self.job_list = [_struct_dict(j) for j in (job_list or [])]
        log.info("%s: přijat seznam %d zakázek", self.name, len(self.job_list))
        return []

    async def prepare_next_job(self) -> bool:
        if not self.cyclic_job_queue or not self.has("Jobs/JobInPreparation/JobName"):
            return False
        j = self.cyclic_job_queue.pop(0)
        await self.write_job("Jobs/JobInPreparation", j)
        return True

    async def write_job(self, obj: str, j: dict) -> None:
        mapping = {
            "JobName": "JobName", "JobDescription": "JobDescription", "CustomerName": "CustomerName",
            "ProductionDatasetName": "ProductionDatasetName",
            "ProductionDatasetDescription": "ProductionDatasetDescription", "Material": "Material",
            "ProductName": "ProductName", "ProductDescription": "ProductDescription",
            "ContinueAtJobEnd": "ContinueAtJobEnd", "NominalParts": "NominalParts",
            "NominalBoxParts": "NominalBoxParts", "ExpectedCycleTime": "ExpectedCycleTime", "MouldId": "MouldId",
            "NumCavities": "NumCavities",
        }
        for key, prop in mapping.items():
            if key in j and self.has(f"{obj}/{prop}"):
                v = j[key]
                if isinstance(v, str) and self.specs[self.p(f"{obj}/{prop}")].is_array:
                    v = [v] if v else []
                await self.set(f"{obj}/{prop}", v if v is not None else _job_value(prop, {}))

    async def m_set_cyclic_job_data(self, rel, job_name, job_description, customer_name, ds_name, ds_desc,
                                    material, product_name, product_description, continue_at_end, nominal_parts,
                                    nominal_box_parts, expected_cycle_time, mould_id, num_cavities):
        obj = rel.rsplit("/", 1)[0]
        await self.write_job(obj, {
            "JobName": job_name, "JobDescription": job_description, "CustomerName": customer_name,
            "ProductionDatasetName": ds_name, "ProductionDatasetDescription": ds_desc, "Material": material or [],
            "ProductName": product_name or [], "ProductDescription": product_description or [],
            "ContinueAtJobEnd": bool(continue_at_end), "NominalParts": nominal_parts or 0,
            "NominalBoxParts": nominal_box_parts or 0, "ExpectedCycleTime": expected_cycle_time or 0.0,
            "MouldId": mould_id or "", "NumCavities": num_cavities or 1,
        })
        if obj == "Jobs/ActiveJob" and self.get("Jobs/ActiveJobValues/JobStatus") in (JS_FINISHED, JS_OTHER):
            await self.set_job_status(JS_TRANSFERRED, self.caller)
        return []

    async def m_start_job(self, rel):
        status = self.get("Jobs/ActiveJobValues/JobStatus")
        if status == JS_IN_PRODUCTION:
            raise MethodError(ua.StatusCodes.BadInvalidState)
        if status != JS_INTERRUPTED:
            prepared = self.has("Jobs/JobInPreparation/JobName") and self.get("Jobs/JobInPreparation/JobName")
            if prepared and status in (JS_FINISHED, JS_OTHER, JS_SETUP_FIN, JS_TRANSFERRED):
                job = {k: self.get(f"Jobs/JobInPreparation/{k}") for k in (
                    "JobName", "JobDescription", "CustomerName", "ProductionDatasetName",
                    "ProductionDatasetDescription", "Material", "ProductName", "ProductDescription",
                    "ContinueAtJobEnd", "NominalParts", "NominalBoxParts", "ExpectedCycleTime", "MouldId",
                    "NumCavities") if self.has(f"Jobs/JobInPreparation/{k}")}
                await self.write_job("Jobs/ActiveJob", job)
                await self.write_job("Jobs/JobInPreparation", {k: _job_value(k, {}) for k in job})
                await self.prepare_next_job()
            await self.reset_job_counters()
            self.cycle_times.clear()
            await self.new_box()
        await self.set_job_status(JS_IN_PRODUCTION, self.caller)
        return []

    async def m_interrupt_job(self, rel):
        if self.get("Jobs/ActiveJobValues/JobStatus") != JS_IN_PRODUCTION:
            raise MethodError(ua.StatusCodes.BadInvalidState)
        await self.set_job_status(JS_INTERRUPTED, self.caller)
        return []

    async def m_finish_job(self, rel):
        await self.set_job_status(JS_FINISHED, self.caller)
        return []

    async def m_stop_at_cycle_end(self, rel):
        self.stop_at_cycle_end = True
        return []

    async def m_reset_job_counters(self, rel):
        await self.reset_job_counters()
        return []

    async def m_reset_box_counters(self, rel):
        await self.new_box()
        return []

    async def m_reset_average_cycle_time(self, rel):
        self.cycle_times.clear()
        await self.set_if("Jobs/ActiveJobValues/AverageCycleTime", 0.0)
        return []


def _job_value(name: str, job: dict) -> Any:
    keys = {
        "JobName": ("job_name", ""), "JobDescription": ("job_description", ""),
        "CustomerName": ("customer_name", ""), "ProductionDatasetName": ("production_dataset_name", ""),
        "ProductionDatasetDescription": ("production_dataset_description", ""), "Material": ("material", []),
        "ProductName": ("product_name", []), "ProductDescription": ("product_description", []),
        "ContinueAtJobEnd": ("continue_at_job_end", False), "NominalParts": ("nominal_parts", 0),
        "NominalBoxParts": ("nominal_box_parts", 0), "ExpectedCycleTime": ("expected_cycle_time", 0.0),
        "MouldId": ("mould_id", ""), "NumCavities": ("num_cavities", 1 if job else 0),
    }
    if name not in keys:
        return None
    key, default = keys[name]
    return job.get(key, default)


def _struct_dict(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    fields = getattr(obj, "__dataclass_fields__", None)
    if fields:
        return {k: getattr(obj, k) for k in fields}
    return {}


def _clean(s: Any) -> str:
    return "".join(c if c.isalnum() else "_" for c in str(s)).upper()


def _short(v: Any) -> str:
    s = v.Text if isinstance(v, ua.LocalizedText) else str(v)
    return s if len(s) < 60 else s[:57] + "..."
