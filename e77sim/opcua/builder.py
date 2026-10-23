"""Builds the address space of one machine explicitly, node by node.

Every instance node gets a deterministic string NodeId equal to its BrowsePath from
the machine root, e.g. ``ns=5;s=IMM_ARBURG_0123456/MachineStatus/MachineMode``.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from asyncua import Server, ua
from asyncua.common.ua_utils import data_type_to_variant_type

from .typetree import MR_MANDATORY, Decl, TypeTree

ANALOG_ITEM_TYPE = ua.NodeId(ua.ObjectIds.AnalogItemType)

# Subtrees that the simulator does not provide (DI functional groups, locking, generic parameter sets)
SKIP_NAMES = {
    "Lock",
    "Identification",
    "MethodSet",
    "ParameterSet",
    "<GroupIdentifier>",
    "<ParameterIdentifier>",
    "<TransferState>",
    "UIElement",
}
# Optional properties of AnalogItemType that are left out; EURange is mandatory, EngineeringUnits is always added
SKIP_ANALOG_PROPS = {"Definition", "ValuePrecision", "InstrumentRange", "InstrumentNumberRange", "EUNumberRange"}


@dataclass
class NodeSpec:
    path: str
    nodeid: ua.NodeId
    nodeclass: ua.NodeClass
    name: str
    decl: Decl
    vtype: ua.VariantType | None = None
    data_type: ua.NodeId | None = None
    data_type_name: str = ""
    is_array: bool = False
    writable: bool = False
    analog: bool = False
    enum: dict[int, str] | None = None
    cls: str = ""
    unit: str = ""
    eurange: tuple[float, float] | None = None
    duration: bool = False
    children: list[str] = field(default_factory=list)


Selector = Callable[[str, Decl], bool]
Expander = Callable[[str, Decl], list[tuple[str, dict[str, Any]]]]


class Builder:
    def __init__(self, server: Server, ns: int, tree: TypeTree):
        self.server = server
        self.ns = ns
        self.tree = tree
        self.specs: dict[str, NodeSpec] = {}
        self._vtype_cache: dict[ua.NodeId, ua.VariantType] = {}
        self._dtname_cache: dict[ua.NodeId, str] = {}
        self._enum_cache: dict[ua.NodeId, dict[int, str] | None] = {}

    # ---- data type helpers -------------------------------------------------
    async def vtype(self, dt: ua.NodeId) -> ua.VariantType:
        if dt not in self._vtype_cache:
            try:
                self._vtype_cache[dt] = await data_type_to_variant_type(self.server.get_node(dt))
            except Exception:
                self._vtype_cache[dt] = ua.VariantType.Variant
        return self._vtype_cache[dt]

    async def dt_name(self, dt: ua.NodeId) -> str:
        if dt not in self._dtname_cache:
            try:
                self._dtname_cache[dt] = (await self.server.get_node(dt).read_browse_name()).Name
            except Exception:
                self._dtname_cache[dt] = "BaseDataType"
        return self._dtname_cache[dt]

    async def enum_values(self, dt: ua.NodeId) -> dict[int, str] | None:
        if dt in self._enum_cache:
            return self._enum_cache[dt]
        result = None
        if dt != ua.NodeId(ua.ObjectIds.Enumeration) and await self.tree.is_subtype(
            dt, ua.NodeId(ua.ObjectIds.Enumeration)
        ):
            node = self.server.get_node(dt)
            for child in await node.get_children(refs=ua.ObjectIds.HasProperty):
                bname = (await child.read_browse_name()).Name
                val = await child.read_value()
                if bname == "EnumStrings":
                    result = {i: lt.Text for i, lt in enumerate(val)}
                elif bname == "EnumValues":
                    result = {ev.Value: ev.DisplayName.Text for ev in val}
        self._enum_cache[dt] = result
        return result

    async def default_value(self, spec: NodeSpec) -> Any:
        if spec.is_array:
            return []
        vt = spec.vtype
        if vt == ua.VariantType.Boolean:
            return False
        if vt in (ua.VariantType.String,):
            return ""
        if vt == ua.VariantType.LocalizedText:
            return ua.LocalizedText("")
        if vt in (ua.VariantType.Double, ua.VariantType.Float):
            return 0.0
        if vt in (
            ua.VariantType.Int16,
            ua.VariantType.Int32,
            ua.VariantType.Int64,
            ua.VariantType.UInt16,
            ua.VariantType.UInt32,
            ua.VariantType.UInt64,
            ua.VariantType.Byte,
            ua.VariantType.SByte,
        ):
            if spec.enum:
                return min(spec.enum)
            return 0
        if vt == ua.VariantType.ExtensionObject and spec.data_type is not None:
            cls = ua.extension_objects_by_datatype.get(spec.data_type)
            if cls is not None:
                return cls()
        if vt == ua.VariantType.NodeId:
            return ua.NodeId()
        if vt == ua.VariantType.DateTime:
            return None
        return None

    # ---- node creation -----------------------------------------------------
    async def add_node(
        self,
        parent: ua.NodeId,
        decl: Decl,
        nodeid: ua.NodeId,
        bname: ua.QualifiedName,
        display: ua.LocalizedText | None = None,
        modelling_rule: int | None = None,
        value: Any = None,
        ref_type: ua.NodeId | None = None,
    ) -> None:
        item = ua.AddNodesItem()
        item.RequestedNewNodeId = nodeid
        item.BrowseName = bname
        item.NodeClass = decl.nodeclass
        item.ParentNodeId = parent
        item.ReferenceTypeId = ref_type or decl.ref_type
        display = display or (decl.display_name if bname.Name == decl.name else ua.LocalizedText(bname.Name))
        if decl.nodeclass == ua.NodeClass.Object:
            attrs = ua.ObjectAttributes()
            attrs.EventNotifier = 0
            item.TypeDefinition = decl.type_def or ua.NodeId(ua.ObjectIds.BaseObjectType)
        elif decl.nodeclass == ua.NodeClass.Variable:
            attrs = ua.VariableAttributes()
            attrs.DataType = decl.data_type
            attrs.ValueRank = decl.value_rank
            attrs.ArrayDimensions = decl.array_dimensions
            attrs.AccessLevel = decl.access_level
            attrs.UserAccessLevel = decl.access_level
            attrs.Value = value if value is not None else ua.Variant()
            item.TypeDefinition = decl.type_def or ua.NodeId(ua.ObjectIds.BaseDataVariableType)
        else:
            attrs = ua.MethodAttributes()
            attrs.Executable = True
            attrs.UserExecutable = True
        attrs.DisplayName = display
        attrs.Description = decl.description
        item.NodeAttributes = attrs
        res = (await self.server.iserver.isession.add_nodes([item]))[0]
        res.StatusCode.check()
        if modelling_rule is not None:
            ref = ua.AddReferencesItem()
            ref.SourceNodeId = nodeid
            ref.ReferenceTypeId = ua.NodeId(ua.ObjectIds.HasModellingRule)
            ref.IsForward = True
            ref.TargetNodeId = ua.NodeId(modelling_rule)
            ref.TargetNodeClass = ua.NodeClass.Object
            (await self.server.iserver.isession.add_references([ref]))[0].check()

    async def add_method_args(self, decl: Decl, method_id: ua.NodeId, prefix: str, modelling_rule: int | None) -> None:
        """Copy InputArguments/OutputArguments from the method declaration."""
        node = self.server.get_node(decl.nodeid)
        for child in await node.get_children(refs=ua.ObjectIds.HasProperty):
            bname = await child.read_browse_name()
            if bname.Name not in ("InputArguments", "OutputArguments"):
                continue
            item = ua.AddNodesItem()
            item.RequestedNewNodeId = ua.NodeId(f"{prefix}/{bname.Name}", self.ns)
            item.BrowseName = bname
            item.NodeClass = ua.NodeClass.Variable
            item.ParentNodeId = method_id
            item.ReferenceTypeId = ua.NodeId(ua.ObjectIds.HasProperty)
            item.TypeDefinition = ua.NodeId(ua.ObjectIds.PropertyType)
            attrs = ua.VariableAttributes()
            attrs.DisplayName = ua.LocalizedText(bname.Name)
            attrs.DataType = ua.NodeId(ua.ObjectIds.Argument)
            attrs.ValueRank = 1
            attrs.ArrayDimensions = [0]
            attrs.Value = ua.Variant(await child.read_value(), ua.VariantType.ExtensionObject)
            item.NodeAttributes = attrs
            (await self.server.iserver.isession.add_nodes([item]))[0].StatusCode.check()
            if modelling_rule is not None:
                ref = ua.AddReferencesItem()
                ref.SourceNodeId = item.RequestedNewNodeId
                ref.ReferenceTypeId = ua.NodeId(ua.ObjectIds.HasModellingRule)
                ref.IsForward = True
                ref.TargetNodeId = ua.NodeId(MR_MANDATORY)
                ref.TargetNodeClass = ua.NodeClass.Object
                (await self.server.iserver.isession.add_references([ref]))[0].check()

    # ---- tree --------------------------------------------------------------
    async def build(
        self,
        parent: ua.NodeId,
        decl: Decl,
        path: str,
        select: Selector,
        expand: Expander,
        values: Callable[[NodeSpec], Any],
        as_type: bool = False,
        bname: ua.QualifiedName | None = None,
    ) -> NodeSpec:
        """Create `decl` (and its selected children) below `parent` at `path`."""
        nodeid = ua.NodeId(path, self.ns)
        bname = bname or decl.browse_name
        spec = NodeSpec(path=path, nodeid=nodeid, nodeclass=decl.nodeclass, name=bname.Name, decl=decl)
        rule = MR_MANDATORY if as_type else None
        value = None
        if decl.nodeclass == ua.NodeClass.Variable:
            spec.data_type = decl.data_type
            spec.vtype = await self.vtype(decl.data_type)
            spec.data_type_name = await self.dt_name(decl.data_type)
            spec.is_array = decl.value_rank >= 0
            spec.writable = bool(decl.access_level & ua.AccessLevel.CurrentWrite.mask)
            spec.analog = decl.type_def == ANALOG_ITEM_TYPE
            spec.enum = await self.enum_values(decl.data_type)
            spec.duration = spec.data_type_name == "Duration"
            v = values(spec)
            if v is None:
                v = await self.default_value(spec)
            value = make_variant(spec, v)
        await self.add_node(parent, decl, nodeid, bname, modelling_rule=rule, value=value)
        self.specs[path] = spec
        if decl.nodeclass == ua.NodeClass.Method:
            await self.add_method_args(decl, nodeid, path, rule)
            return spec
        for child in sorted(decl.children, key=_child_order):
            if child.name in SKIP_NAMES:
                continue
            if spec.analog and child.name in SKIP_ANALOG_PROPS:
                continue
            if child.placeholder:
                for name, _ctx in expand(path, child):
                    qn = ua.QualifiedName(name, child.browse_name.NamespaceIndex)
                    c = await self.build(nodeid, child, f"{path}/{name}", select, expand, values, as_type, qn)
                    spec.children.append(c.path)
                continue
            child_path = f"{path}/{child.name}"
            if not child.mandatory and not select(child_path, child):
                continue
            c = await self.build(nodeid, child, child_path, select, expand, values, as_type)
            spec.children.append(c.path)
        return spec


def _child_order(d: Decl) -> tuple[int, str]:
    order = {ua.NodeClass.Variable: 0, ua.NodeClass.Object: 1, ua.NodeClass.Method: 2}
    return order.get(d.nodeclass, 3), d.name


def make_variant(spec: NodeSpec, v: Any) -> ua.Variant:
    vt = spec.vtype or ua.VariantType.Variant
    if vt == ua.VariantType.Variant:
        return ua.Variant(v)
    if spec.is_array:
        return ua.Variant(list(v or []), vt, is_array=True)
    return ua.Variant(coerce(vt, v), vt)


def coerce(vt: ua.VariantType, v: Any) -> Any:
    if v is None:
        return None
    if vt in (ua.VariantType.Double, ua.VariantType.Float):
        return float(v)
    if vt in (
        ua.VariantType.Int16,
        ua.VariantType.Int32,
        ua.VariantType.Int64,
        ua.VariantType.UInt16,
        ua.VariantType.UInt32,
        ua.VariantType.UInt64,
        ua.VariantType.Byte,
        ua.VariantType.SByte,
    ):
        return int(v)
    if vt == ua.VariantType.Boolean:
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "ano", "on")
        return bool(v)
    if vt == ua.VariantType.String:
        return str(v)
    if vt == ua.VariantType.LocalizedText and not isinstance(v, ua.LocalizedText):
        return ua.LocalizedText(str(v))
    return v


def match_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)
