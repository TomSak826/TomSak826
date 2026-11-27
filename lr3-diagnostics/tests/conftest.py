import itertools

import pytest

from lr3diag import db
from lr3diag.obd.elm327 import ELM327
from lr3diag.obd.simulator import SimulatedLR3


@pytest.fixture
def conn():
    return db.connect(":memory:")


def make_elm(scenario="healthy", step=0.5):
    """ELM327 on a simulated truck whose clock advances `step` seconds per read."""
    ticks = itertools.count()
    sim = SimulatedLR3(scenario, clock=lambda: next(ticks) * step)
    elm = ELM327(sim)
    elm.initialize()
    return elm


def drive(scenario, seconds=1200, step=0.25):
    from lr3diag.obd.logger import sample_stream
    from lr3diag.obd.pids import DEFAULT_LOG_PIDS
    elm = make_elm(scenario, step)
    return list(sample_stream(elm, DEFAULT_LOG_PIDS, duration=seconds))
