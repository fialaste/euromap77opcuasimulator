import asyncio
import logging
import socket

import pytest
import pytest_asyncio

from e77sim.config import load_profile
from e77sim.sim.machine import Machine

logging.getLogger("asyncua").setLevel(logging.ERROR)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start_machine(profile_name: str, tmp_path, cycle_ms: int = 600, **kwargs) -> Machine:
    prof = load_profile(profile_name)
    prof["job"]["expected_cycle_time"] = cycle_ms
    m = Machine(prof, free_port(), "127.0.0.1", tmp_path, seed=7, **kwargs)
    await m.start()
    return m


@pytest_asyncio.fixture
async def arburg(tmp_path):
    m = await start_machine("generic", tmp_path)
    yield m
    await m.stop()


@pytest_asyncio.fixture
async def zhafir(tmp_path):
    m = await start_machine("zhafir_22", tmp_path)
    yield m
    await m.stop()


async def wait_for(cond, timeout=5.0):
    t = 0.0
    while not cond():
        await asyncio.sleep(0.05)
        t += 0.05
        if t > timeout:
            raise TimeoutError
