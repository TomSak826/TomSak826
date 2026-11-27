"""Analyse logged OBD data and flag likely problems on the LR3 4.4 V8.

Each check is a small, readable function. Add your own as you learn what
"normal" looks like for *your* truck. A baseline log recorded when everything
is healthy is the most useful reference you can have.
"""

from __future__ import annotations

import csv
import statistics as st
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Finding:
    severity: str          # INFO | WARN | FAULT
    title: str
    detail: str
    manual_query: str = ""  # suggested workshop-manual search
    dtcs: list[str] = field(default_factory=list)


def load_csv(path: str | Path) -> list[dict[str, float | None]]:
    rows = []
    with Path(path).open() as f:
        for r in csv.DictReader(f):
            rows.append({k: (float(v) if v not in ("", None, "None") else None) for k, v in r.items()})
    return rows


def _col(rows, name, where=None):
    return [r[name] for r in rows if r.get(name) is not None and (where is None or where(r))]


def summarize(rows) -> dict[str, dict[str, float]]:
    out = {}
    if not rows:
        return out
    keys = list(dict.fromkeys(k for r in rows for k in r))
    for k in keys:
        vals = [v for v in _col(rows, k) if isinstance(v, (int, float))]
        if vals:
            out[k] = {"min": min(vals), "max": max(vals), "mean": st.fmean(vals), "n": len(vals)}
    return out


def _has(rows, name) -> bool:
    return any(r.get(name) is not None for r in rows)


def _is_idle(r):
    if r.get("rpm") is not None:
        return (r.get("speed") or 0) < 2 and r["rpm"] > 400 and (r.get("throttle") or 0) < 20
    # No RPM logged (e.g. a GAP log without engine speed): stopped with the throttle closed.
    return (r.get("speed") or 0) < 2 and (r.get("throttle") or 0) < 20


def _is_cruise(r):
    return (r.get("speed") or 0) > 40


def _warm(r):
    return (r.get("coolant") or 0) >= 75


def _usable(rows):
    """Rows suitable for fuel-trim judgement, plus any caveat about how they were chosen."""
    if _has(rows, "coolant"):
        return [r for r in rows if _warm(r)], ""
    return ([r for r in rows if (r.get("t") or 0) >= 120],
            "Coolant temperature wasn't logged, so the first 2 minutes were skipped on the assumption that the engine was warm after that.")


# Driving-condition bands used to tell a vacuum leak (worst at idle) from a
# fuel-delivery / air-metering problem (same or worse under load).
TRIM_BANDS = [
    ("Idle", _is_idle),
    ("Light load / cruise", lambda r: (r.get("speed") or 0) >= 2 and (r.get("throttle") or 0) < 30),
    ("Moderate load", lambda r: 30 <= (r.get("throttle") or 0) < 50),
    ("Heavy load", lambda r: (r.get("throttle") or 0) >= 50),
]
MIN_BAND_SAMPLES = 20


def trim_breakdown(rows) -> list[dict]:
    """Average short-term, long-term and total fuel trim per bank for each driving condition."""
    use, _ = _usable(rows)
    if not _has(rows, "throttle"):
        bands = [("Idle", _is_idle), ("Cruise", _is_cruise)]
    else:
        bands = TRIM_BANDS
    out = []
    for name, cond in bands + [("All", lambda r: True)]:
        sel = [r for r in use if cond(r)]
        row = {"band": name, "n": len(sel)}
        for b in (1, 2):
            pts = [(r[f"stft{b}"], r[f"ltft{b}"]) for r in sel
                   if r.get(f"stft{b}") is not None and r.get(f"ltft{b}") is not None]
            if len(pts) >= MIN_BAND_SAMPLES:
                row[f"stft{b}"] = st.fmean(p[0] for p in pts)
                row[f"ltft{b}"] = st.fmean(p[1] for p in pts)
                row[f"total{b}"] = row[f"stft{b}"] + row[f"ltft{b}"]
        out.append(row)
    return out


def check_fuel_trims(rows) -> list[Finding]:
    if not (_has(rows, "stft1") or _has(rows, "stft2")):
        return []
    _, caveat = _usable(rows)
    caveat = (" " + caveat) if caveat else ""
    bands = {b["band"]: b for b in trim_breakdown(rows)}
    tot = lambda band, bank: bands.get(band, {}).get(f"total{bank}")  # noqa: E731
    out: list[Finding] = []

    all1, all2 = tot("All", 1), tot("All", 2)
    idle1, idle2 = tot("Idle", 1), tot("Idle", 2)
    load_band = next((n for n in ("Heavy load", "Moderate load", "Cruise", "Light load / cruise")
                      if tot(n, 1) is not None and tot(n, 2) is not None), None)
    load1, load2 = (tot(load_band, 1), tot(load_band, 2)) if load_band else (None, None)
    worst = max((abs(v) for b in bands.values() for k, v in b.items() if k.startswith("total")), default=0)

    def sev(v):
        return "FAULT" if abs(v) > 20 else "WARN" if abs(v) > 10 else "INFO"

    table = "; ".join(f"{b['band']}: B1 {b['total1']:+.1f}%, B2 {b['total2']:+.1f}%"
                      for b in bands.values() if "total1" in b and "total2" in b and b["band"] != "All")

    # 1. Classic vacuum leak: lean at idle, much better under load.
    if (idle1 is not None and idle2 is not None and idle1 > 10 and idle2 > 10
            and load1 is not None and load1 < idle1 - 5 and load2 < idle2 - 5):
        out.append(Finding(
            "WARN" if max(idle1, idle2) <= 20 else "FAULT",
            "Both banks lean at idle, improving under load: likely vacuum leak",
            f"Total trim by condition: {table}. Unmetered air matters most at idle. On the 4.4 V8, check "
            "the PCV/breather hoses, the intake manifold gaskets and the brake booster hose. A smoke test "
            "finds leaks quickly." + caveat,
            manual_query="intake manifold vacuum leak PCV", dtcs=["P0171", "P0174"]))
        return out

    # 2. Both banks lean (or rich) together across conditions: a shared cause.
    if all1 is not None and all2 is not None and abs(all1 - all2) < 4 and min(all1, all2) > 7.5:
        worse_under_load = (idle1 is not None and load1 is not None
                            and load1 >= idle1 + 2 and load2 >= idle2 + 2)
        if worse_under_load:
            title = "Both banks lean everywhere, worst under load: suspect fuel delivery or MAF"
            why = ("Because the lean condition is no better at idle than under load, a vacuum leak is "
                   "unlikely. When both banks need more fuel as demand rises, the usual suspects are "
                   "fuel delivery (weak pump, restricted filter or low fuel pressure) or a MAF sensor "
                   "under-reading airflow.")
        else:
            title = "Both banks lean in every condition: shared cause"
            why = ("Both banks add a similar amount of fuel at idle and under load. Common causes are a "
                   "dirty or failing MAF sensor, low fuel pressure, or air entering after the MAF.")
        out.append(Finding(
            sev(worst), title,
            f"Average total trim (STFT + LTFT): B1 {all1:+.1f}%, B2 {all2:+.1f}%. By condition: {table}. "
            f"{why} Normal long-term trim is within about ±5%." + caveat,
            manual_query="fuel pressure test mass air flow sensor", dtcs=["P0171", "P0174"]))
        return out
    if all1 is not None and all2 is not None and abs(all1 - all2) < 4 and max(all1, all2) < -7.5:
        out.append(Finding(
            sev(worst), "Both banks rich in every condition: shared cause",
            f"Average total trim: B1 {all1:+.1f}%, B2 {all2:+.1f}%. By condition: {table}. Suspect high "
            "fuel pressure, leaking injectors, a MAF sensor over-reading airflow, or fuel-diluted oil "
            "feeding vapour through the PCV system." + caveat,
            manual_query="fuel pressure regulator injector leak", dtcs=["P0172", "P0175"]))
        return out

    # 3. One bank on its own.
    for bank in (1, 2):
        for band, b in bands.items():
            v = b.get(f"total{bank}")
            other = b.get(f"total{3 - bank}")
            if band == "All" or v is None or abs(v) <= 10:
                continue
            one_bank = other is not None and abs(other) < 8
            direction = "lean (adding fuel)" if v > 0 else "rich (removing fuel)"
            hint = ("Only one bank is affected, which points to that bank: an injector, an exhaust leak ahead of "
                    "the O2 sensor, the O2 sensor itself, or an intake gasket on that side."
                    if one_bank else
                    "Both banks are affected, which points to a shared cause: MAF sensor, fuel pressure/pump, "
                    "or a large vacuum leak.")
            out.append(Finding(sev(v), f"Bank {bank} running {direction} at {band.lower()}",
                               f"Total trim {v:+.1f}% (STFT + LTFT). {hint}" + caveat,
                               manual_query="fuel trim lean MAF fuel pressure" if v > 0 else "fuel trim rich injector",
                               dtcs=(["P0171"] if bank == 1 else ["P0174"]) if v > 0
                               else (["P0172"] if bank == 1 else ["P0175"])))
    return out


def check_coolant(rows) -> list[Finding]:
    out: list[Finding] = []
    temps = _col(rows, "coolant")
    if not temps:
        return out
    hottest = max(temps)
    if hottest >= 110:
        out.append(Finding("FAULT", "Engine overheating",
                           f"Coolant reached {hottest:.0f}°C. Stop driving. Check the coolant level, look for leaks "
                           "at the crossover/valley pipe and the radiator, and check the fan and water pump.",
                           manual_query="cooling system overheating coolant leak"))
    elif hottest >= 105:
        out.append(Finding("WARN", "Coolant running hot", f"Peak {hottest:.0f}°C. Normal is around 88–100°C.",
                           manual_query="cooling system thermostat fan"))

    warm_window = [r for r in rows if (r.get("t") or 0) >= 900 and r.get("coolant") is not None]
    if warm_window:
        late_max = max(r["coolant"] for r in warm_window)
        if late_max < 80:
            out.append(Finding("WARN", "Engine not reaching operating temperature",
                               f"After 15 minutes the coolant only reached {late_max:.0f}°C. The thermostat is "
                               "probably stuck open. Fuel economy suffers and you may see P0128.",
                               manual_query="thermostat replacement", dtcs=["P0128"]))
    return out


def check_voltage(rows) -> list[Finding]:
    out: list[Finding] = []
    running = _col(rows, "voltage", lambda r: (r.get("rpm") or 0) > 500 and (r.get("t") or 0) > 30)
    if not running:
        return out
    avg, low, high = st.fmean(running), min(running), max(running)
    if avg < 13.2:
        out.append(Finding("FAULT" if avg < 12.6 else "WARN", "Low charging voltage",
                           f"Average {avg:.2f} V with the engine running (lowest {low:.2f} V); expected 13.5–14.7 V. "
                           "Test the alternator and battery. LR3 modules throw spurious suspension, parking-brake "
                           "and HDC faults when voltage is low, so fix this before chasing other faults.",
                           manual_query="generator alternator charging system battery"))
    elif high > 15.0:
        out.append(Finding("WARN", "Over-voltage", f"Peak {high:.2f} V. Suspect the voltage regulator.",
                           manual_query="generator alternator regulator"))
    return out


def check_idle(rows) -> list[Finding]:
    rpm = _col(rows, "rpm", lambda r: _is_idle(r) and _warm(r))
    if len(rpm) < 10:
        return []
    sd, mean = st.pstdev(rpm), st.fmean(rpm)
    out: list[Finding] = []
    if sd > 40:
        stft2 = _col(rows, "stft2", _is_idle)
        stft1 = _col(rows, "stft1", _is_idle)
        bank_hint = ""
        if stft1 and stft2:
            s1, s2 = st.pstdev(stft1), st.pstdev(stft2)
            if s2 > 2 * s1:
                bank_hint = " Bank-2 short-term trim is much more erratic than bank 1, so start with the bank-2 coils and plugs."
            elif s1 > 2 * s2:
                bank_hint = " Bank-1 short-term trim is much more erratic than bank 2, so start with the bank-1 coils and plugs."
        out.append(Finding("WARN", "Rough / unstable idle",
                           f"Idle RPM varies ±{sd:.0f} rpm around {mean:.0f}. This suggests a misfire, a vacuum leak "
                           f"or a dirty throttle body.{bank_hint}",
                           manual_query="misfire ignition coil spark plug", dtcs=["P0300"]))
    if mean > 900:
        out.append(Finding("INFO", "High warm idle", f"Warm idle averages {mean:.0f} rpm.",
                           manual_query="idle speed throttle body"))
    return out


CHECKS = [check_fuel_trims, check_coolant, check_voltage, check_idle]


def analyze(rows) -> list[Finding]:
    findings: list[Finding] = []
    for chk in CHECKS:
        findings.extend(chk(rows))
    order = {"FAULT": 0, "WARN": 1, "INFO": 2}
    return sorted(findings, key=lambda f: order[f.severity])
