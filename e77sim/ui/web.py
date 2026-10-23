"""Local web UI (aiohttp). The JSON routes below exist only for the UI page itself."""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any

from aiohttp import web
from asyncua import ua

from ..opcua.builder import Builder
from ..resources import static_dir
from ..sim import generators as gens
from ..sim.machine import EO_OPERATOR, Machine

log = logging.getLogger("e77sim")


def to_json(v: Any) -> Any:
    if isinstance(v, ua.Variant):
        v = v.Value
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Enum):
        return int(v.value)
    if isinstance(v, ua.LocalizedText):
        return v.Text or ""
    if isinstance(v, ua.QualifiedName):
        return v.Name
    if isinstance(v, ua.NodeId):
        return v.to_string()
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} B>"
    if isinstance(v, (list, tuple)):
        return [to_json(x) for x in v]
    if dataclasses.is_dataclass(v):
        return {f.name: to_json(getattr(v, f.name)) for f in dataclasses.fields(v)}
    return str(v)


def from_json(value: Any, vtype: ua.VariantType | None, data_type: ua.NodeId | None, is_array: bool) -> Any:
    """Convert a value typed in the UI to the Python value for a node or argument."""
    if is_array:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                value = json.loads(value)
            else:
                value = [x.strip() for x in value.split(",") if x.strip()] if value else []
        return [from_json(x, vtype, data_type, False) for x in value or []]
    if vtype == ua.VariantType.ExtensionObject:
        if isinstance(value, str):
            value = json.loads(value) if value.strip() else {}
        cls = ua.extension_objects_by_datatype.get(data_type) if data_type is not None else None
        if cls is None:
            raise ValueError("neznámá struktura")
        return build_struct(cls, value or {})
    if vtype == ua.VariantType.LocalizedText:
        return ua.LocalizedText(str(value))
    if vtype == ua.VariantType.DateTime:
        return datetime.fromisoformat(str(value))
    if vtype in (ua.VariantType.Double, ua.VariantType.Float):
        return float(value)
    if vtype in (ua.VariantType.Int16, ua.VariantType.Int32, ua.VariantType.Int64, ua.VariantType.UInt16,
                 ua.VariantType.UInt32, ua.VariantType.UInt64, ua.VariantType.Byte, ua.VariantType.SByte):
        return int(float(value))
    if vtype == ua.VariantType.Boolean:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "ano", "yes", "on")
        return bool(value)
    if vtype == ua.VariantType.ByteString:
        return str(value).encode("utf-8")
    return value if vtype is None else str(value)


def build_struct(cls, data: dict) -> Any:
    obj = cls()
    hints = {f.name: f for f in dataclasses.fields(cls)}
    for key, val in data.items():
        if key not in hints:
            continue
        cur = getattr(obj, key)
        if isinstance(cur, ua.LocalizedText):
            val = ua.LocalizedText(str(val))
        elif isinstance(cur, Enum):
            val = type(cur)(int(val))
        elif isinstance(cur, bool):
            val = bool(val)
        elif isinstance(cur, int):
            val = int(val)
        elif isinstance(cur, float):
            val = float(val)
        elif isinstance(cur, datetime) or (cur is None and isinstance(val, str) and "T" in val):
            try:
                val = datetime.fromisoformat(val)
            except ValueError:
                pass
        elif dataclasses.is_dataclass(cur) and isinstance(val, dict):
            val = build_struct(type(cur), val)
        setattr(obj, key, val)
    return obj


class WebUI:
    def __init__(self, machines: list[Machine], host: str, port: int):
        self.machines = machines
        self.host = host
        self.port = port
        self.runner: web.AppRunner | None = None
        self._method_info: dict[tuple[int, str], list[dict]] = {}

    def url(self) -> str:
        host = "localhost" if self.host in ("0.0.0.0", "") else self.host
        return f"http://{host}:{self.port}/"

    async def start(self) -> None:
        app = web.Application()
        app.add_routes([
            web.get("/", self.index),
            web.get("/ui/machines", self.machines_overview),
            web.get("/ui/m/{i}/tree", self.tree),
            web.get("/ui/m/{i}/state", self.state),
            web.get("/ui/m/{i}/method", self.method_info),
            web.post("/ui/m/{i}/set", self.set_value),
            web.post("/ui/m/{i}/generator", self.set_generator),
            web.post("/ui/m/{i}/call", self.call),
            web.post("/ui/m/{i}/action", self.action),
            web.static("/static", static_dir()),
        ])
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, self.host, self.port).start()

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()

    def m(self, request) -> Machine:
        i = int(request.match_info["i"])
        if not 0 <= i < len(self.machines):
            raise web.HTTPNotFound()
        return self.machines[i]

    async def index(self, request):
        return web.FileResponse(static_dir() / "index.html", headers={"Cache-Control": "no-cache"})

    def overview(self, m: Machine) -> dict:
        ok, reason = m.can_produce()
        av = "Jobs/ActiveJobValues/"
        return {
            "name": m.name,
            "profile": m.profile.get("profile", ""),
            "model": f"{m.profile['identity'].get('manufacturer')} {m.profile['identity'].get('model')}",
            "endpoint": m.endpoint,
            "running": m.running,
            "clients": len(m.server.bserver.clients) if m.running and m.server.bserver else 0,
            "cycle_enabled": m.cycle_enabled,
            "producing": ok,
            "reason": reason,
            "progress": round(m.cycle_progress, 3),
            "machine_mode": m.enum_name("MachineStatus/MachineMode", m.get("MachineStatus/MachineMode")),
            "production_status": m.enum_name("MachineMESStatus/ProductionControl/ProductionStatus",
                                              m.get("MachineMESStatus/ProductionControl/ProductionStatus")),
            "job": m.get("Jobs/ActiveJob/JobName") or (m.profile.get("job") or {}).get("job_name", ""),
            "job_status": m.enum_name(av + "JobStatus", m.get(av + "JobStatus")) if m.has(av + "JobStatus") else "",
            "last_cycle_time": m.get(av + "LastCycleTime"),
            "average_cycle_time": m.get(av + "AverageCycleTime"),
            "good": m.get(av + "JobGoodPartsCounter"),
            "bad": m.get(av + "JobBadPartsCounter"),
            "cycles": m.get(av + "JobCycleCounter", m.last_cycle.get("fields", {}).get("JobCycleCounter")),
            "nominal": m.get("Jobs/ActiveJob/NominalParts"),
            "facets": m.facets,
            "standstill": m.standstill_active,
            "watchdog_ms": m.watchdog_ms,
        }

    async def machines_overview(self, request):
        return web.json_response([self.overview(m) for m in self.machines])

    async def tree(self, request):
        m = self.m(request)
        nodes = []
        for path, s in m.specs.items():
            if "/ProductionDatasetTransfer/File_" in path:
                continue
            nodes.append({
                "path": m.rel(path),
                "name": s.name,
                "nodeclass": s.nodeclass.name,
                "cls": s.cls,
                "type": s.data_type_name,
                "array": s.is_array,
                "writable": s.writable,
                "unit": s.unit,
                "range": s.eurange,
                "enum": {str(k): v for k, v in s.enum.items()} if s.enum else None,
                "children": [m.rel(c) for c in s.children if "/File_" not in c],
                "description": s.decl.description.Text if s.decl.description else "",
                "nodeid": f"ns={m.ns};s={path}",
            })
        return web.json_response({
            "nodes": nodes,
            "analog_kinds": gens.ANALOG_KINDS,
            "state_kinds": gens.STATE_KINDS,
            "common": gens.COMMON,
            "cycle_params": list(m.cycle_cfg),
            "requests": sorted(k for k in m.events.types if k.startswith("Request")),
            "users": [f"MachineStatus/Users/User_{i + 1}" for i, _ in enumerate(m.profile.get("users") or [])],
            "classifications": {str(e.value): e.name for e in getattr(ua, "IMMMessageClassificationEnumeration", [])},
        })

    async def state(self, request):
        m = self.m(request)
        values = {m.rel(p): to_json(v) for p, v in m.values.items() if "/File_" not in p}
        generators = {m.rel(p): g.to_dict() for p, g in m.generators.items()}
        jobs = {"queue": to_json(m.cyclic_job_queue), "list": to_json(m.job_list)}
        datasets = None
        if m.datasets is not None:
            datasets = {
                "library": [to_json(d.info) for _, d in sorted(m.datasets.library.items())],
                "active": to_json(m.datasets.active.info) if m.datasets.active else None,
                "mes_list": m.datasets.mes_list,
                "transfers": len({id(t) for t in m.datasets.transfers.values()}),
            }
        return web.json_response({
            "overview": self.overview(m),
            "values": values,
            "generators": generators,
            "events": list(m.event_log)[:60],
            "cycle": {"cfg": m.cycle_cfg, "last": to_json(m.last_cycle.get("params")), "quality": m.last_cycle.get("quality")},
            "jobs": jobs,
            "datasets": datasets,
        }, dumps=lambda o: json.dumps(o, default=str))

    async def method_info(self, request):
        m = self.m(request)
        rel = request.query["path"]
        key = (id(m), rel)
        if key not in self._method_info:
            info = []
            try:
                args = await m.server.get_node(ua.NodeId(m.p(rel) + "/InputArguments", m.ns)).read_value()
            except Exception:
                args = []
            b = Builder(m.server, m.ns, None)
            for a in args or []:
                vt = await b.vtype(a.DataType)
                template = None
                cls = ua.extension_objects_by_datatype.get(a.DataType)
                if cls is not None:
                    template = to_json(cls())
                    if a.ValueRank >= 0:
                        template = [template]
                info.append({"name": a.Name, "type": await b.dt_name(a.DataType), "array": a.ValueRank >= 0,
                             "template": template, "description": a.Description.Text if a.Description else ""})
            self._method_info[key] = info
        return web.json_response(self._method_info[key])

    async def set_value(self, request):
        m = self.m(request)
        body = await request.json()
        rel = body["path"]
        spec = m.specs.get(m.p(rel))
        if spec is None:
            raise web.HTTPNotFound()
        try:
            value = from_json(body.get("value"), spec.vtype, spec.data_type, spec.is_array)
            if spec.enum and not spec.is_array and value not in spec.enum:
                raise ValueError("hodnota mimo výčet")
            if rel.startswith("MachineStatus/Users/") and spec.name == "IsPresent":
                old = await m.set(rel, value)
                await m.on_change(rel, old, value, EO_OPERATOR)
            else:
                await m.operator_set(rel, value)
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True})

    async def set_generator(self, request):
        m = self.m(request)
        body = await request.json()
        try:
            params = body.get("params") or {}
            if isinstance(params, str):
                params = json.loads(params) if params.strip() else {}
            seed = body.get("seed")
            m.set_generator(body["path"], body.get("kind"), params, bool(body.get("enabled", True)),
                            int(seed) if seed not in (None, "") else None)
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True})

    async def call(self, request):
        m = self.m(request)
        body = await request.json()
        rel = body["path"]
        try:
            args = await m.server.get_node(ua.NodeId(m.p(rel) + "/InputArguments", m.ns)).read_value()
        except Exception:
            args = []
        b = Builder(m.server, m.ns, None)
        variants = []
        try:
            for a, raw in zip(args or [], body.get("args") or []):
                vt = await b.vtype(a.DataType)
                val = from_json(raw, vt, a.DataType, a.ValueRank >= 0)
                variants.append(ua.Variant(val, vt, is_array=a.ValueRank >= 0))
            parent = m.server.get_node(ua.NodeId(m.p(rel).rsplit("/", 1)[0], m.ns))
            m.ui_call = True
            try:
                result = await parent.call_method(ua.NodeId(m.p(rel), m.ns), *variants)
            finally:
                m.ui_call = False
        except ua.UaStatusCodeError as e:
            return web.json_response({"error": f"{type(e).__name__}"}, status=400)
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True, "result": to_json(result)})

    async def action(self, request):
        m = self.m(request)
        body = await request.json()
        a = body.get("action")
        try:
            if a == "toggle_server":
                await m.restart()
            elif a == "toggle_cycle":
                m.cycle_enabled = not m.cycle_enabled
                await m.update_production_status(EO_OPERATOR)
            elif a == "randomize":
                m.randomize_all()
            elif a == "freeze":
                m.freeze_all()
            elif a == "message":
                await m.raise_message(str(body.get("id") or "OP-1"), str(body.get("text") or ""),
                                      int(body.get("severity") or 500), int(body.get("classification") or 0),
                                      bool(body.get("standstill")))
            elif a == "clear_standstill":
                await m.clear_standstill()
            elif a == "request":
                fields = body.get("fields") or {}
                if "Components" in fields and isinstance(fields["Components"], str):
                    fields["Components"] = [int(x) for x in fields["Components"].split(",") if x.strip()]
                for key in ("Storage",):
                    if key in fields:
                        fields[key] = int(fields[key])
                await m.send_request_event(body["type"], fields, body.get("message") or body["type"])
            elif a == "cycle_param":
                cfg = m.cycle_cfg.setdefault(body["name"], {})
                for k in ("nominal", "sigma", "tol"):
                    if body.get(k) not in (None, ""):
                        cfg[k] = float(body[k])
                    elif k == "tol":
                        cfg.pop("tol", None)
            elif a == "prepare_job":
                job = m.job_list[int(body["index"])]
                await m.write_job("Jobs/JobInPreparation", job)
            elif a == "prepare_next":
                if not await m.prepare_next_job():
                    raise ValueError("fronta cyklických zakázek je prázdná")
            elif a == "test_sample":
                m.test_sample_requested = True
            elif a == "new_box":
                await m.new_box()
            else:
                raise ValueError(f"neznámá akce {a}")
        except (ValueError, KeyError, IndexError, TypeError) as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True})
