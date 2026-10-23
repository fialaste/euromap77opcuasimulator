from e77sim.sim import generators as gens


def run(g, start, n=200, lo=0.0, hi=500.0, siblings=None):
    v, t = start, 0.0
    out = []
    for _ in range(n):
        t += g.period
        v = g.step(v, lo, hi, siblings or {}, t)
        out.append(v)
    return out


def test_seed_makes_runs_repeatable():
    a = run(gens.make("gauss", {"mean": 10, "sigma": 2}, seed=1), 0)
    b = run(gens.make("gauss", {"mean": 10, "sigma": 2}, seed=1), 0)
    assert a == b


def test_values_stay_in_eurange():
    vals = run(gens.make("uniform", {"min": -50, "max": 900}, seed=3), 0, lo=0, hi=450)
    assert min(vals) >= 0 and max(vals) <= 450


def test_follow_approaches_sibling_setpoint():
    g = gens.make("follow", {"target": "NominalTemperature", "tau": 5, "sigma": 0}, seed=1)
    vals = run(g, 25.0, n=60, siblings={"NominalTemperature": 230.0})
    assert abs(vals[-1] - 230.0) < 1.0


def test_weighted_state_respects_weights_and_dwell():
    g = gens.make("weighted", {"weights": {"1": 1, "4": 0}, "dwell_min": 0, "dwell_max": 0}, seed=1)
    assert set(run(g, 0, n=20)) == {1}


def test_markov_follows_transitions():
    g = gens.make("markov", {"transitions": {"1": {"3": 1}, "3": {"1": 1}}, "dwell_min": 0, "dwell_max": 0}, seed=1)
    assert run(g, 1, n=4) == [3, 1, 3, 1]


def test_sequence_cycles_through_steps():
    g = gens.make("sequence", {"steps": [[1, 1], [4, 1]], "period": 1})
    assert run(g, 0, n=4) == [1, 4, 1, 4]
