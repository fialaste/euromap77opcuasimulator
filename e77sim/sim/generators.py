"""The one value generator for analog (A) and state (S) nodes: a nominal value and a random deviation."""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("e77sim")

PERIOD = 1.0  # seconds between two steps of a generator

# Keys of the generators of version 1.0, translated when an old profile is loaded
_LEGACY_NOMINAL = ("target", "mean", "value", "offset")
_LEGACY_DEVIATION = ("sigma", "step")


@dataclass
class Generator:
    """Nominal value, deviation and time constant.

    Analog node: the value follows `nominal` with the time constant `tau` (s) and gets Gaussian noise
    with the standard deviation `deviation`. `nominal` is a number, the name of a sibling node
    (e.g. NominalTemperature), or None for the value the node had when the generator started.

    State node: the value stays at `nominal`. Every step it leaves it with the probability `deviation`
    (0…1) for a random other state from `states` and comes back after about `tau` seconds.
    """

    nominal: Any = None
    deviation: float = 0.0
    tau: float = 10.0
    states: list | None = None
    enabled: bool = True
    seed: int | None = None
    _rng: random.Random = field(default_factory=random.Random, repr=False)
    _next: float = 0.0
    _back_at: float | None = None

    def __post_init__(self):
        self.deviation = float(self.deviation or 0.0)
        self.tau = max(0.0, float(self.tau or 0.0))
        if self.seed is not None:
            self._rng.seed(self.seed)

    def due(self, now: float) -> bool:
        if not self.enabled or now < self._next:
            return False
        self._next = now + PERIOD
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"nominal": self.nominal, "deviation": self.deviation, "tau": self.tau, "states": self.states,
                "enabled": self.enabled}

    def step(self, current: Any, lo: float, hi: float, siblings: dict[str, Any], now: float,
             choices: list | None = None) -> Any:
        if choices is not None:
            return self._step_state(current, now, choices)
        current = float(current or 0.0)
        target = self.nominal
        if target is None:
            target = self.nominal = current
        elif isinstance(target, str):
            try:
                target = float(target)
            except ValueError:
                target = siblings.get(target, current)
        target = float(target if target is not None else current)
        alpha = 1.0 if self.tau <= 0 else 1 - math.exp(-PERIOD / self.tau)
        value = current + (target - current) * alpha + self._rng.gauss(0, self.deviation)
        return min(hi, max(lo, value))

    def _step_state(self, current: Any, now: float, choices: list) -> Any:
        nominal = _typed(self.nominal) if self.nominal is not None else current
        if self.nominal is None:
            self.nominal = current
        if self._back_at is not None:
            if now < self._back_at:
                return current
            self._back_at = None
            return nominal
        if current != nominal:
            return nominal
        others = [s for s in ([_typed(s) for s in self.states] if self.states else choices) if s != nominal]
        if others and self._rng.random() < self.deviation:
            self._back_at = now + self.tau * self._rng.uniform(0.5, 1.5)
            return self._rng.choice(others)
        return current


def _typed(v: Any) -> Any:
    """Keys from JSON/YAML/the UI come as strings; turn numeric and boolean ones back."""
    if isinstance(v, str):
        low = v.lower()
        if low in ("true", "false"):
            return low == "true"
        try:
            return int(v)
        except ValueError:
            return v
    return v


def make(params: dict[str, Any] | None = None, seed: int | None = None, enabled: bool = True) -> Generator:
    """A generator from profile or UI parameters. Parameters of the old generator kinds are translated."""
    p = dict(params or {})
    if "kind" in p or "params" in p:
        old = dict(p.pop("params", None) or {})
        kind = p.pop("kind", None)
        log.warning("generátor %s: druhy generátorů už nejsou, převádím na nominál a odchylku", kind)
        if "nominal" not in p:
            p["nominal"] = next((old[k] for k in _LEGACY_NOMINAL if k in old), None)
        if "deviation" not in p:
            p["deviation"] = next((old[k] for k in _LEGACY_DEVIATION if k in old), 0.0)
        p.setdefault("tau", old.get("tau", 10.0))
    nominal = p.get("nominal")
    if isinstance(nominal, str) and nominal.strip() == "":
        nominal = None
    return Generator(nominal=nominal, deviation=p.get("deviation", 0.0), tau=p.get("tau", 10.0),
                     states=p.get("states") or None, seed=seed, enabled=enabled)
