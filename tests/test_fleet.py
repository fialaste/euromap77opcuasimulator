import pytest
import yaml

from e77sim import catalog
from e77sim.config import CATALOG, DEFAULT_MACHINES, load_profile
from e77sim.fleet import SAVE_FILE, Fleet, FleetError


def fleet(tmp_path, **kw):
    return Fleet(tmp_path, "127.0.0.1", seed=1, security=False, **kw)


def ports(f):
    return [m.port for m in f.machines]


@pytest.mark.parametrize("name", CATALOG)
def test_catalog_profiles_load(name):
    prof = load_profile(name)
    assert prof["title"] and prof["preset"] == name
    form = catalog.form_of(prof)
    assert form["injection_units"] >= 1 and form["zones"] >= 1


def test_compose_applies_the_wizard_fields():
    form = {"manufacturer": "ACME", "model": "X 200", "serial_number": "42", "machine_name": "Lis X",
            "drive": "hydraulický", "injection_units": 2, "zones": 3, "moulds": 2,
            "facets": ["jobs"], "options": ["screw", "datasets_ext", "cycle"]}
    prof = catalog.compose(load_profile("arburg_31"), form)
    assert prof["identity"]["manufacturer"] == "ACME" and prof["identity"]["serial_number"] == "42"
    assert len(prof["injection_units"]) == 2 and len(prof["moulds"]) == 2
    heating = [z for z in prof["injection_units"][1]["zones"] if z.get("classification", "HEATING") == "HEATING"]
    assert len(heating) == 3
    assert prof["power_units"][0]["oil_temperature"] is not None
    assert prof["options"] == ["screw", "cycle"]  # datasets_ext needs the datasets facet
    assert prof["job"]["mould_id"] == prof["moulds"][0]["id"]


async def test_options_select_optional_nodes(tmp_path):
    f = fleet(tmp_path, persist=False)
    prof = catalog.compose(load_profile("generic"), {"facets": ["jobs"], "options": ["screw"]})
    m = await f.add(prof)
    try:
        assert m.has("InjectionUnits/InjectionUnit_1/ScrewDiameter")
        assert not m.has("MachineInformation/SoftwareRevision")
        assert not m.has("Jobs/SendJobList") and m.has("Jobs/ActiveJobValues/StartJob")
        assert not m.has("ProductionDatasetManagement")
        assert set(m.cycle_cfg) == {"CushionVolume", "PlastificationVolume", "SpecificPressureMaximum",
                                    "InjectionTime", "DosingTime"}
    finally:
        await f.stop()


async def test_add_remove_save_and_reset(tmp_path):
    f = fleet(tmp_path)
    await f.start(f.default_specs())
    try:
        assert [m.profile["preset"] for m in f.machines] == DEFAULT_MACHINES
        assert not (tmp_path / SAVE_FILE).exists()  # nothing changed yet
        await f.add(load_profile("engel_26"))
        with pytest.raises(FleetError):
            await f.add(load_profile("generic"))
        saved = yaml.safe_load((tmp_path / SAVE_FILE).read_text(encoding="utf-8"))
        assert [x["profile"]["preset"] for x in saved["machines"]] == DEFAULT_MACHINES + ["engel_26"]

        await f.remove(0)
        await f.add(load_profile("engel_26"))  # same type twice gets its own serial number
        names = [m.name for m in f.machines]
        assert len(set(names)) == 3 and len(set(ports(f))) == 3

        g = fleet(tmp_path)
        specs = g.saved_specs()
        assert [p["identity"]["serial_number"] for p, _ in specs] == \
            [m.profile["identity"]["serial_number"] for m in f.machines]

        await f.reset()
        assert [m.profile["preset"] for m in f.machines] == DEFAULT_MACHINES
        assert not (tmp_path / SAVE_FILE).exists()
    finally:
        await f.stop()


def test_broken_save_file_falls_back_to_defaults(tmp_path):
    (tmp_path / SAVE_FILE).write_text("machines: [{profile: {identity: {}}}]", encoding="utf-8")
    assert fleet(tmp_path).saved_specs() is None
