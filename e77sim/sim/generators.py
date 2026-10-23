"""Random value generators for analog (A) and state (S) nodes."""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Any

ANALOG_KINDS = {
    "constant": {"value": 0.0},
    "uniform": {"min": 0.0, "max": 100.0},
    "gauss": {"mean": 50.0, "sigma": 1.0},
    "walk": {"step": 0.5, "min": 0.0, "max": 100.0},
    "follow": {"target": "NominalTemperature", "tau": 60.0, "sigma": 0.3},
    "sine": {"offset": 50.0, "amplitude": 10.0, "period": 60.0},
}
STATE_KINDS = {
    "weighted": {"weights": {}, "dwell_min": 10.0, "dwell_max": 60.0},
    "markov": {"transitions": {}, "dwell_min": 10.0, "dwell_max": 60.0},
    "sequence": {"steps": []},
}
COMMON = {"period": 1.0, "spike_prob": 0.0, "spike_size": 0.0}


@dataclass
class Generator:
    """One generator bound to one node. `params` are kind specific, see ANALOG_KINDS/STATE_KINDS."""

    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    seed: int | None = None
    _rng: random.Random = field(default_factory=random.Random, repr=False)
    _next: float = 0.0
    _t0: float = field(default_factory=time.monotonic)
    _dwell_until: float = 0.0
    _seq_index: int = 0

    def __post_init__(self):
        if self.kind not in ANALOG_KINDS and self.kind not in STATE_KINDS:
            raise ValueError(f"unknown generator kind {self.kind!r}")
        if self.seed is not None:
            self._rng.seed(self.seed)

    @property
    def is_state(self) -> bool:
        return self.kind in STATE_KINDS

    @property
    def period(self) -> float:
        return max(0.1, float(self.params.get("period", 1.0)))

    def due(self, now: float) -> bool:
        if not self.enabled or now < self._next:
            return False
        self._next = now + self.period
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "params": self.params, "enabled": self.enabled, "seed": self.seed}

    def step(self, current: Any, lo: float, hi: float, siblings: dict[str, Any], now: float) -> Any:
        if self.is_state:
            return self._step_state(current, now)
        value = self._step_analog(float(current or 0.0), siblings, now)
        p = self.params
        if p.get("spike_prob") and self._rng.random() < float(p["spike_prob"]):
            value += self._rng.choice((-1, 1)) * float(p.get("spike_size", 0.0))
            return value  # a spike may leave the EURange on purpose
        return min(hi, max(lo, value))

    def _step_analog(self, current: float, siblings: dict[str, Any], now: float) -> float:
        p, r = self.params, self._rng
        k = self.kind
        if k == "constant":
            return float(p.get("value", current))
        if k == "uniform":
            return r.uniform(float(p.get("min", 0)), float(p.get("max", 1)))
        if k == "gauss":
            return r.gauss(float(p.get("mean", current)), float(p.get("sigma", 1)))
        if k == "walk":
            v = current + r.gauss(0, float(p.get("step", 0.5)))
            return min(float(p.get("max", v)), max(float(p.get("min", v)), v))
        if k == "follow":
            target = p.get("target", 0.0)
            if isinstance(target, str):
                target = siblings.get(target, current)
            tau = max(0.1, float(p.get("tau", 60)))
            alpha = 1 - math.exp(-self.period / tau)
            return current + (float(target) - current) * alpha + r.gauss(0, float(p.get("sigma", 0)))
        if k == "sine":
            t = now - self._t0
            period = max(0.1, float(p.get("period", 60)))
            return float(p.get("offset", 0)) + float(p.get("amplitude", 1)) * math.sin(2 * math.pi * t / period)
        return current

    def _step_state(self, current: Any, now: float) -> Any:
        p, r = self.params, self._rng
        if self.kind == "sequence":
            steps = p.get("steps") or []
            if not steps:
                return current
            if now < self._dwell_until:
                return current
            state, duration = steps[self._seq_index % len(steps)]
            self._seq_index += 1
            self._dwell_until = now + float(duration)
            return state
        if now < self._dwell_until:
            return current
        self._dwell_until = now + r.uniform(float(p.get("dwell_min", 10)), float(p.get("dwell_max", 60)))
        if self.kind == "weighted":
            weights = p.get("weights") or {}
        else:
            transitions = p.get("transitions") or {}
            weights = transitions.get(str(current)) or transitions.get(current) or {}
        if not weights:
            return current
        states = list(weights)
        choice = r.choices(states, weights=[float(weights[s]) for s in states])[0]
        return _typed(choice)


def _typed(v: Any) -> Any:
    """Keys from JSON/YAML come as strings; turn numeric and boolean ones back."""
    if isinstance(v, str):
        low = v.lower()
        if low in ("true", "false"):
            return low == "true"
        try:
            return int(v)
        except ValueError:
            return v
    return v


def make(kind: str, params: dict[str, Any] | None = None, seed: int | None = None, enabled: bool = True) -> Generator:
    base = dict(COMMON)
    base.update(ANALOG_KINDS.get(kind) or STATE_KINDS.get(kind) or {})
    base.update(params or {})
    return Generator(kind=kind, params=base, seed=seed, enabled=enabled)
