from e77sim.sim import generators as gens


def run(g, start, n=200, lo=0.0, hi=500.0, siblings=None, choices=None):
    v, t = start, 0.0
    out = []
    for _ in range(n):
        t += gens.PERIOD
        v = g.step(v, lo, hi, siblings or {}, t, choices)
        out.append(v)
    return out


def test_seed_makes_runs_repeatable():
    a = run(gens.make({"nominal": 10, "deviation": 2, "tau": 0}, seed=1), 0)
    b = run(gens.make({"nominal": 10, "deviation": 2, "tau": 0}, seed=1), 0)
    assert a == b


def test_values_stay_in_eurange():
    vals = run(gens.make({"nominal": 440, "deviation": 50, "tau": 0}, seed=3), 0, lo=0, hi=450)
    assert min(vals) >= 0 and max(vals) <= 450


def test_analog_follows_sibling_setpoint():
    g = gens.make({"nominal": "NominalTemperature", "deviation": 0, "tau": 5}, seed=1)
    vals = run(g, 25.0, n=60, siblings={"NominalTemperature": 230.0})
    assert abs(vals[-1] - 230.0) < 1.0


def test_analog_without_nominal_holds_its_start_value():
    vals = run(gens.make({"nominal": None, "deviation": 0.1, "tau": 10}, seed=2), 120.0, n=300)
    assert abs(sum(vals) / len(vals) - 120.0) < 1.0


def test_state_stays_nominal_without_deviation():
    g = gens.make({"nominal": "1", "deviation": 0, "tau": 5}, seed=1)
    assert set(run(g, 1, n=50, choices=[0, 1, 2, 3])) == {1}


def test_state_leaves_and_returns_to_nominal():
    g = gens.make({"nominal": 1, "deviation": 0.2, "tau": 3}, seed=4)
    vals = run(g, 1, n=400, choices=[0, 1, 4])
    assert 1 in vals and (0 in vals or 4 in vals)
    assert vals.count(1) > len(vals) / 2
    assert vals[-1] in (0, 1, 4)


def test_state_uses_listed_states_only():
    g = gens.make({"nominal": 1, "deviation": 0.5, "tau": 1, "states": [4]}, seed=4)
    assert set(run(g, 1, n=200, choices=[0, 1, 2, 3, 4])) == {1, 4}


def test_old_generator_kinds_are_translated():
    g = gens.make({"kind": "follow", "params": {"target": "NominalTemperature", "tau": 60, "sigma": 0.3}})
    assert (g.nominal, g.deviation, g.tau) == ("NominalTemperature", 0.3, 60)
