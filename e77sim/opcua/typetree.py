"""Resolve the instance declarations of an OPC UA type from the imported NodeSets.

The result is a tree of `Decl` objects. It merges the declarations of a type with
those of its supertypes, and the children declared directly below an instance
declaration override the ones of its TypeDefinition (OPC 10000-3, 6.3.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from asyncua import Server, ua

MR_MANDATORY = 78
MR_OPTIONAL = 80
MR_OPTIONAL_PLACEHOLDER = 11508
MR_MANDATORY_PLACEHOLDER = 11510
_RULES = {MR_MANDATORY, MR_OPTIONAL, MR_OPTIONAL_PLACEHOLDER, MR_MANDATORY_PLACEHOLDER}


@dataclass
class Decl:
    nodeid: ua.NodeId
    nodeclass: ua.NodeClass
    browse_name: ua.QualifiedName
    display_name: ua.LocalizedText
    description: ua.LocalizedText
    ref_type: ua.NodeId
    type_def: ua.NodeId | None
    rule: int
    children: list[Decl] = field(default_factory=list)
    # Variable attributes
    data_type: ua.NodeId | None = None
    value_rank: int = -1
    array_dimensions: list[int] | None = None
    access_level: int = 1
    value: ua.Variant | None = None

    @property
    def name(self) -> str:
        return self.browse_name.Name

    @property
    def mandatory(self) -> bool:
        return self.rule == MR_MANDATORY

    @property
    def placeholder(self) -> bool:
        return self.rule in (MR_OPTIONAL_PLACEHOLDER, MR_MANDATORY_PLACEHOLDER)

    def child(self, name: str) -> Decl | None:
        return next((c for c in self.children if c.name == name), None)


class TypeTree:
    def __init__(self, server: Server):
        self.server = server
        self._type_cache: dict[ua.NodeId, list[Decl]] = {}
        self._supertype_cache: dict[ua.NodeId, ua.NodeId | None] = {}

    def _node(self, nodeid: ua.NodeId):
        return self.server.get_node(nodeid)

    async def supertype(self, nodeid: ua.NodeId) -> ua.NodeId | None:
        if nodeid not in self._supertype_cache:
            refs = await self._node(nodeid).get_references(
                refs=ua.ObjectIds.HasSubtype, direction=ua.BrowseDirection.Inverse
            )
            self._supertype_cache[nodeid] = refs[0].NodeId if refs else None
        return self._supertype_cache[nodeid]

    async def is_subtype(self, nodeid: ua.NodeId, base: ua.NodeId) -> bool:
        cur: ua.NodeId | None = nodeid
        while cur is not None:
            if cur == base:
                return True
            cur = await self.supertype(cur)
        return False

    async def _modelling_rule(self, nodeid: ua.NodeId) -> int | None:
        refs = await self._node(nodeid).get_references(
            refs=ua.ObjectIds.HasModellingRule, direction=ua.BrowseDirection.Forward
        )
        for r in refs:
            if r.NodeId.NamespaceIndex == 0 and r.NodeId.Identifier in _RULES:
                return r.NodeId.Identifier
        return None

    async def _direct_children(self, nodeid: ua.NodeId) -> list[Decl]:
        """Instance declarations directly below a type or instance-declaration node (not recursive)."""
        refs = await self._node(nodeid).get_references(
            refs=ua.ObjectIds.Aggregates, direction=ua.BrowseDirection.Forward, includesubtypes=True
        )
        out = []
        for r in refs:
            if r.NodeClass not in (ua.NodeClass.Object, ua.NodeClass.Variable, ua.NodeClass.Method):
                continue
            rule = await self._modelling_rule(r.NodeId)
            if rule is None:
                continue
            node = self._node(r.NodeId)
            d = Decl(
                nodeid=r.NodeId,
                nodeclass=r.NodeClass,
                browse_name=r.BrowseName,
                display_name=r.DisplayName,
                description=await node.read_description(),
                ref_type=r.ReferenceTypeId,
                type_def=r.TypeDefinition if not r.TypeDefinition.is_null() else None,
                rule=rule,
            )
            if r.NodeClass == ua.NodeClass.Variable:
                d.data_type = await node.read_data_type()
                d.value_rank = await node.read_value_rank()
                d.array_dimensions = await node.read_array_dimensions()
                d.access_level = (await node.read_attribute(ua.AttributeIds.AccessLevel)).Value.Value
                d.value = (await node.read_data_value(raise_on_bad_status=False)).Value
            out.append(d)
        return out

    async def type_children(self, type_id: ua.NodeId) -> list[Decl]:
        """All instance declarations of a type, including inherited ones, resolved recursively."""
        if type_id in self._type_cache:
            return self._type_cache[type_id]
        merged: dict[str, Decl] = {}
        chain = []
        cur: ua.NodeId | None = type_id
        while cur is not None:
            chain.append(cur)
            cur = await self.supertype(cur)
        # Most derived type wins, so walk from the base upwards and overwrite.
        for t in reversed(chain):
            for d in await self._direct_children(t):
                merged[d.name] = d
        result = list(merged.values())
        self._type_cache[type_id] = result
        for d in result:
            await self._resolve(d)
        return result

    async def _resolve(self, d: Decl) -> None:
        if d.nodeclass == ua.NodeClass.Method:
            return
        own = {c.name: c for c in await self._direct_children(d.nodeid)}
        inherited = await self.type_children(d.type_def) if d.type_def is not None else []
        children: dict[str, Decl] = {c.name: c for c in inherited}
        for name, c in own.items():
            await self._resolve(c)
            children[name] = c
        d.children = list(children.values())

    async def root(self, type_id: ua.NodeId, name: str) -> Decl:
        """A synthetic declaration for an instance of `type_id`."""
        node = self._node(type_id)
        d = Decl(
            nodeid=type_id,
            nodeclass=ua.NodeClass.Object,
            browse_name=ua.QualifiedName(name, 0),
            display_name=ua.LocalizedText(name),
            description=await node.read_description(),
            ref_type=ua.NodeId(ua.ObjectIds.Organizes),
            type_def=type_id,
            rule=MR_MANDATORY,
        )
        d.children = await self.type_children(type_id)
        return d
