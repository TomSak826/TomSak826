from lr3diag import analysis

from .conftest import drive


def titles(scenario):
    return [f.title for f in analysis.analyze(drive(scenario))]


def test_healthy_has_no_warnings():
    assert not [f for f in analysis.analyze(drive("healthy")) if f.severity != "INFO"]


def test_vacuum_leak():
    assert any("vacuum leak" in t for t in titles("vacuum_leak"))


def test_thermostat():
    assert any("operating temperature" in t for t in titles("thermostat"))


def test_charging():
    assert any("charging voltage" in t.lower() for t in titles("charging"))


def test_misfire_points_at_bank2():
    f = [f for f in analysis.analyze(drive("misfire")) if "idle" in f.title.lower()]
    assert f and "bank-2" in f[0].detail
