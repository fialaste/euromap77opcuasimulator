"""Integration tests: an asyncua client against a running simulated machine."""

import asyncio
import json

import pytest
from asyncua import Client, ua
from asyncua.common.events import get_filter_from_event_type

from conftest import wait_for

IMM = "http://opcfoundation.org/UA/PlasticsRubber/IMM2MES/"


def nid(m, rel):
    return ua.NodeId(m.p(rel), m.ns)


async def test_namespaces_profiles_identity(arburg):
    async with Client(arburg.endpoint) as c:
        ns = await c.get_namespace_array()
        assert IMM in ns
        assert "http://opcfoundation.org/UA/PlasticsRubber/GeneralTypes/" in ns
        assert "http://opcfoundation.org/UA/DI/" in ns
        profiles = await c.get_node(ua.ObjectIds.Server_ServerCapabilities_ServerProfileArray).read_value()
        assert "http://opcfoundation.org/UA-Profile/PlasticsRubber/IMM2MES/Server/Basic" in profiles
        assert "http://opcfoundation.org/UA-Profile/PlasticsRubber/IMM2MES/Server/Jobs" in profiles
        assert (
            "http://opcfoundation.org/UA-Profile/PlasticsRubber/IMM2MES/Server/ProductionDatasetManagement"
            in profiles
        )
        assert await c.get_node(nid(arburg, "MachineInformation/DeviceClass")).read_value() == (
            "Injection Moulding Machine"
        )
        # String NodeIds are the BrowsePath from the machine root
        ns_idx = c.get_node(nid(arburg, "MachineStatus/MachineMode")).nodeid.NamespaceIndex
        node = c.get_node(f"ns={ns_idx};s={arburg.name}/MachineStatus/MachineMode")
        assert await node.read_value() == 1  # AUTOMATIC


async def test_machine_is_under_device_set_with_type(arburg):
    async with Client(arburg.endpoint) as c:
        di = await c.get_namespace_index("http://opcfoundation.org/UA/DI/")
        imm = await c.get_namespace_index(IMM)
        devset = c.get_node(ua.NodeId(5001, di))
        names = [(await n.read_browse_name()).Name for n in await devset.get_children()]
        assert arburg.name in names
        machine = c.get_node(nid(arburg, ""))
        assert await machine.read_type_definition() == ua.NodeId(1007, imm)
        zone = c.get_node(nid(arburg, "InjectionUnits/InjectionUnit_1/TemperatureZones/TemperatureZone_1/ActualTemperature"))
        rng = await c.get_node(ua.NodeId(zone.nodeid.Identifier + "/EURange", zone.nodeid.NamespaceIndex)).read_value()
        assert rng.High == 450.0
        eu = await c.get_node(
            ua.NodeId(zone.nodeid.Identifier + "/EngineeringUnits", zone.nodeid.NamespaceIndex)).read_value()
        assert eu.UnitId == 4408652


async def test_mandatory_nodes_of_basic_profile_exist(zhafir):
    """Every mandatory instance declaration of IMM_MES_InterfaceType is present (minimal profile)."""
    from e77sim.opcua.typetree import TypeTree

    tree = TypeTree(zhafir.server)
    imm = await zhafir.server.get_namespace_index(IMM)
    root = await tree.root(ua.NodeId(1007, imm), zhafir.name)
    missing = []

    def walk(decl, path):
        for c in decl.children:
            if c.mandatory and c.name not in ("Lock",):
                p = f"{path}/{c.name}"
                if p not in zhafir.specs:
                    missing.append(p)
                elif c.nodeclass != ua.NodeClass.Method:
                    walk(c, p)

    walk(root, zhafir.name)
    assert not missing
    assert not zhafir.has("ProductionDatasetManagement")
    assert zhafir.has("Jobs/ActiveJobValues/StartJob")


async def test_cycle_event_with_nested_parameters(arburg):
    async with Client(arburg.endpoint) as c:
        got = []

        class Handler:
            def event_notification(self, event):
                got.append(event)

        sub = await c.create_subscription(50, Handler())
        etype = c.get_node(arburg.events.cycle.nodeid)
        await sub.subscribe_events(c.get_node(nid(arburg, "")), etype)
        await sub.subscribe_events(c.nodes.server, etype)
        await wait_for(lambda: len(got) >= 2, 5)
        fields = got[0].get_event_props_as_fields_dict()
        assert fields["JobName"].Value == "J-2026-0417"
        assert fields["JobCycleCounter"].Value >= 1
        assert fields["CycleQuality"].Value in (0, 1)
        assert 2 < fields["InjectionUnitCycleParameters_1/CushionVolume"].Value < 7
        assert fields["InjectionUnitCycleParameters_1/CushionVolume/EngineeringUnits"].Value.UnitId == 4410705
        t = fields["InjectionUnitCycleParameters_1/BarrelTemperatureZoneCycleParameters_1/ActualTemperature"].Value
        assert 200 < t < 300
        assert fields["MouldCycleParameters_1/MouldTemperatureZoneCycleParameters_3/Name"].Value == "Hot runner 1"


async def test_job_methods_and_counters(arburg):
    async with Client(arburg.endpoint) as c:
        av = c.get_node(nid(arburg, "Jobs/ActiveJobValues"))
        await wait_for(lambda: (arburg.get("Jobs/ActiveJobValues/JobCycleCounter") or 0) >= 1, 5)
        await av.call_method(nid(arburg, "Jobs/ActiveJobValues/InterruptJob"))
        assert await c.get_node(nid(arburg, "Jobs/ActiveJobValues/JobStatus")).read_value() == 7
        assert await c.get_node(nid(arburg, "MachineMESStatus/ProductionControl/ProductionStatus")).read_value() == 3
        with pytest.raises(ua.UaStatusCodeError):
            await av.call_method(nid(arburg, "Jobs/ActiveJobValues/InterruptJob"))

        # Prepare a new job from a cyclic job list and start it
        job = ua.CyclicJobListElementType(
            JobName="J-NEW", JobDescription="d", CustomerName="c", ProductionDatasetName="DS", Material=["PA6"],
            ProductName=["P"], ProductDescription=["PD"], NominalParts=8, NominalBoxParts=4,
            ExpectedCycleTime=500.0, MouldId="M1", NumCavities=4,
        )
        jobs = c.get_node(nid(arburg, "Jobs"))
        await jobs.call_method(nid(arburg, "Jobs/SendCyclicJobList"), ua.Variant([job], ua.VariantType.ExtensionObject))
        assert await c.get_node(nid(arburg, "Jobs/JobInPreparation/JobName")).read_value() == "J-NEW"
        await av.call_method(nid(arburg, "Jobs/ActiveJobValues/FinishJob"))
        await av.call_method(nid(arburg, "Jobs/ActiveJobValues/StartJob"))
        assert await c.get_node(nid(arburg, "Jobs/ActiveJob/JobName")).read_value() == "J-NEW"
        assert await c.get_node(nid(arburg, "Jobs/ActiveJobValues/JobStatus")).read_value() == 6
        # 8 parts with 4 cavities = 2 good cycles, then the job finishes by itself
        await wait_for(lambda: arburg.get("Jobs/ActiveJobValues/JobStatus") == 8, 10)
        assert arburg.get("Jobs/ActiveJobValues/JobGoodPartsCounter") >= 8


async def test_mes_release_and_watchdog(arburg):
    async with Client(arburg.endpoint) as c:
        pc = c.get_node(nid(arburg, "MachineMESStatus/ProductionControl"))
        await c.get_node(nid(arburg, "MachineMESStatus/ProductionControl/ProductionOnlyWithMES")).write_value(True)
        await pc.call_method(nid(arburg, "MachineMESStatus/ProductionControl/SetWatchDogTime"),
                             ua.Variant(300, ua.VariantType.Int32))
        await wait_for(lambda: arburg.get("MachineMESStatus/ProductionControl/ProductionReleasedByMES") is False, 3)
        await wait_for(lambda: arburg.get("MachineMESStatus/ProductionControl/ProductionStatus") == 3, 2)
        await c.get_node(nid(arburg, "MachineMESStatus/ProductionControl/ProductionReleasedByMES")).write_value(True)
        await pc.call_method(nid(arburg, "MachineMESStatus/ProductionControl/SetWatchDogTime"),
                             ua.Variant(0, ua.VariantType.Int32))
        await wait_for(lambda: arburg.get("MachineMESStatus/ProductionControl/ProductionStatus") == 4, 2)


async def test_logbook_history(arburg):
    async with Client(arburg.endpoint) as c:
        status = c.get_node(nid(arburg, "MachineStatus"))
        await status.call_method(nid(arburg, "MachineStatus/ActivateSleepMode"))
        await status.call_method(nid(arburg, "MachineStatus/DeactivateSleepMode"))
        await asyncio.sleep(0.3)
        machine = c.get_node(nid(arburg, ""))
        etype = c.get_node(arburg.events.types["MachineModeChangeLogType"].nodeid)
        hist = await machine.read_event_history(evtypes=[etype])
        modes = [(e.OldMachineMode, e.NewMachineMode) for e in hist if hasattr(e, "NewMachineMode")]
        assert (1, 5) in modes and (5, 1) in modes


async def test_read_only_user_cannot_write(tmp_path):
    from conftest import start_machine

    m = await start_machine("zhafir_22", tmp_path)
    try:
        c = Client(m.endpoint)
        c.set_user("OPC40077_read_only")
        c.set_password("")
        async with c:
            node = c.get_node(nid(m, "MachineConfiguration/LocationName"))
            assert await node.read_value() == m.get("MachineConfiguration/LocationName")
            with pytest.raises(ua.UaStatusCodeError):
                await node.write_value("X")
        c = Client(m.endpoint)
        c.set_user("OPC40077")
        c.set_password("")
        async with c:
            node = c.get_node(nid(m, "MachineConfiguration/LocationName"))
            await node.write_value("Hala B")
            assert m.get("MachineConfiguration/LocationName") == "Hala B"
    finally:
        await m.stop()


async def test_dataset_transfer_roundtrip(arburg):
    async with Client(arburg.endpoint) as c:
        tr = c.get_node(nid(arburg, "ProductionDatasetManagement/ProductionDatasetTransfer"))
        opts = ua.ProductionDatasetReadOptionsType(Storage=1, Name="")
        file_id, handle, _ = await tr.call_method(
            nid(arburg, "ProductionDatasetManagement/ProductionDatasetTransfer/GenerateFileForRead"),
            ua.Variant(opts, ua.VariantType.ExtensionObject))
        f = c.get_node(file_id)
        data = await f.call_method(ua.NodeId(file_id.Identifier + "/Read", file_id.NamespaceIndex),
                                   ua.Variant(handle, ua.VariantType.UInt32), ua.Variant(100000, ua.VariantType.Int32))
        doc = json.loads(data.decode())
        assert doc["name"] == "KRYT_A_4x"
        await tr.call_method(nid(arburg, "ProductionDatasetManagement/ProductionDatasetTransfer/CloseAndCommit"),
                             ua.Variant(handle, ua.VariantType.UInt32))

        # Write a changed dataset into production
        zone = "InjectionUnits/InjectionUnit_1/TemperatureZones/TemperatureZone_1/NominalTemperature"
        doc["setpoints"][zone] = 250.0
        wopts = ua.ProductionDatasetWriteOptionsType(Storage=1, Name="KRYT_B", Components=1)
        file_id, handle = await tr.call_method(
            nid(arburg, "ProductionDatasetManagement/ProductionDatasetTransfer/GenerateFileForWrite"),
            ua.Variant(wopts, ua.VariantType.ExtensionObject))
        f = c.get_node(file_id)
        await f.call_method(ua.NodeId(file_id.Identifier + "/Write", file_id.NamespaceIndex),
                            ua.Variant(handle, ua.VariantType.UInt32),
                            ua.Variant(json.dumps(doc).encode(), ua.VariantType.ByteString))
        await tr.call_method(nid(arburg, "ProductionDatasetManagement/ProductionDatasetTransfer/CloseAndCommit"),
                             ua.Variant(handle, ua.VariantType.UInt32))
        assert arburg.get(zone) == 250.0
        info = await c.get_node(nid(arburg, "ProductionDatasetManagement/ActiveProductionDatasetStatus/Information")).read_value()
        assert info.Name == "KRYT_B"
        # temporary file node is gone
        with pytest.raises(ua.UaStatusCodeError):
            await f.read_browse_name()

        lists = c.get_node(nid(arburg, "ProductionDatasetManagement/ProductionDatasetLists"))
        save = c.get_node(nid(arburg, "ProductionDatasetManagement/ActiveProductionDatasetStatus"))
        await save.call_method(nid(arburg, "ProductionDatasetManagement/ActiveProductionDatasetStatus/Save"),
                               ua.Variant("KRYT_B", ua.VariantType.String))
        result = await lists.call_method(
            nid(arburg, "ProductionDatasetManagement/ProductionDatasetLists/GetProductionDatasetList"),
            ua.Variant("KRYT*", ua.VariantType.String), ua.Variant("", ua.VariantType.String))
        assert sorted(i.Name for i in result) == ["KRYT_A_4x", "KRYT_B"]


async def test_get_page_returns_png(arburg):
    async with Client(arburg.endpoint) as c:
        mc = c.get_node(nid(arburg, "MachineConfiguration"))
        png = await mc.call_method(nid(arburg, "MachineConfiguration/GetPage"), ua.Variant("main", ua.VariantType.String))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


async def test_parameter_change_logbook_from_client(arburg):
    async with Client(arburg.endpoint) as c:
        got = []

        class Handler:
            def event_notification(self, event):
                got.append(event)

        sub = await c.create_subscription(50, Handler())
        etype = c.get_node(arburg.events.types["ParameterChangeLogType"].nodeid)
        await sub.subscribe_events(c.nodes.server, etype)
        await asyncio.sleep(0.2)
        await c.get_node(nid(arburg, "MachineConfiguration/UserMachineName")).write_value("Lis 99")
        await wait_for(lambda: len(got) >= 1, 3)
        f = got[0].get_event_props_as_fields_dict()
        assert f["ParameterId"].Value == "MachineConfiguration/UserMachineName"
        assert f["NewValue"].Value == "Lis 99"
        assert f["EventOriginator"].Value == 3  # MES


@pytest.mark.parametrize("policy", ["Basic256Sha256", "Basic256"])
async def test_secure_endpoints(tmp_path, policy):
    """OPC 40077 chapter 6 requires at least Basic256 with Sign; Basic256Sha256 is offered as well."""
    from asyncua.crypto.cert_gen import setup_self_signed_certificate
    from cryptography.x509.oid import ExtendedKeyUsageOID

    from conftest import start_machine

    m = await start_machine("zhafir_22", tmp_path / "srv")
    try:
        key, cert = tmp_path / "client.pem", tmp_path / "client.der"
        await setup_self_signed_certificate(key, cert, "urn:e77sim:test-client", "localhost",
                                            [ExtendedKeyUsageOID.CLIENT_AUTH], {"commonName": "test"})
        c = Client(m.endpoint)
        c.application_uri = "urn:e77sim:test-client"
        await c.set_security_string(f"{policy},Sign,{cert},{key}")
        c.set_user("OPC40077")
        c.set_password("")
        async with c:
            assert await c.get_node(nid(m, "MachineInformation/DeviceClass")).read_value() == (
                "Injection Moulding Machine")
    finally:
        await m.stop()
