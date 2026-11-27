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


def test_rpm_only_log_bands():
    """A log with engine speed but no road speed/throttle must not call everything idle."""
    rows = []
    for i in range(2000):
        t = i * 0.25
        rpm = 680 if (i // 200) % 2 == 0 else 1600
        trim = 8.0 if rpm < 900 else 14.0
        rows.append({"t": t, "rpm": rpm, "stft1": 0.0, "ltft1": trim, "stft2": 0.0, "ltft2": trim})
    bands = {b["band"]: b for b in analysis.trim_breakdown(rows)}
    assert abs(bands["Idle"]["total1"] - 8.0) < 0.01
    assert abs(bands["900-2000 rpm"]["total1"] - 14.0) < 0.01
