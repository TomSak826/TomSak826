import math

from lr3diag import analysis


def _rows(bad_bank=None, seconds=120, step=0.1):
    rows = []
    for i in range(int(seconds / step)):
        t = i * step
        lam = 1.0 + 0.02 * math.sin(2 * math.pi * 1.2 * t)        # upstream dither ~1.2 Hz
        tracking = 0.45 - (lam - 1.0) * 20                          # downstream copying upstream
        r = {"t": t, "throttle": 25, "lambda1": lam, "lambda2": lam,
             "o2_b1s2": 0.72 + 0.02 * math.sin(t / 5), "o2_b2s2": 0.70 + 0.02 * math.sin(t / 4)}
        if bad_bank:
            r[f"o2_b{bad_bank}s2"] = tracking
        rows.append(r)
    return rows


def test_failed_bank1_converter_flagged():
    f = analysis.check_catalyst(_rows(bad_bank=1))
    assert f and f[0].severity == "WARN" and f[0].title.startswith("Bank 1") and "P0420" in f[0].dtcs


def test_healthy_converters():
    f = analysis.check_catalyst(_rows())
    assert len(f) == 1 and f[0].severity == "INFO"


def test_fuel_cut_excluded():
    rows = _rows(bad_bank=None)
    for r in rows[300:340]:                                    # 4 s of decel fuel cut
        r.update(lambda1=1.29, lambda2=1.29, o2_b1s2=0.05, o2_b2s2=0.05)
    assert analysis.check_catalyst(rows)[0].severity == "INFO"
