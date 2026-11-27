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
    for k in rows[0]:
        vals = _col(rows, k)
        if vals:
            out[k] = {"min": min(vals), "max": max(vals), "mean": st.fmean(vals), "n": len(vals)}
    return out


def _is_idle(r):
    return (r.get("speed") or 0) < 2 and (r.get("rpm") or 0) > 400 and (r.get("throttle") or 0) < 20


def _is_cruise(r):
    return (r.get("speed") or 0) > 40


def _warm(r):
    return (r.get("coolant") or 0) >= 75


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def check_fuel_trims(rows) -> list[Finding]:
    out: list[Finding] = []
    total = {}
    for bank in (1, 2):
        s, l = f"stft{bank}", f"ltft{bank}"
        if not _col(rows, s) or not _col(rows, l):
            continue
        idle = [r[s] + r[l] for r in rows if _is_idle(r) and _warm(r) and r.get(s) is not None and r.get(l) is not None]
        cruise = [r[s] + r[l] for r in rows if _is_cruise(r) and _warm(r) and r.get(s) is not None and r.get(l) is not None]
        total[bank] = (st.fmean(idle) if idle else None, st.fmean(cruise) if cruise else None)

    def sev(v):
        return "FAULT" if abs(v) > 20 else "WARN" if abs(v) > 10 else None

    if not total:
        return out
    b1i, b1c = total.get(1, (None, None))
    b2i, b2c = total.get(2, (None, None))

    both_lean_idle = b1i is not None and b2i is not None and b1i > 10 and b2i > 10
    if both_lean_idle and b1c is not None and b2c is not None and b1c < b1i - 5 and b2c < b2i - 5:
        out.append(Finding(
            "WARN" if max(b1i, b2i) <= 20 else "FAULT",
            "Both banks lean at idle, improving under load: likely vacuum leak",
            f"Total trim at idle: B1 {b1i:+.1f}%, B2 {b2i:+.1f}%. At cruise: B1 {b1c:+.1f}%, B2 {b2c:+.1f}%. "
            "Unmetered air matters most at idle. On the 4.4 V8, check the PCV/breather hoses, the intake "
            "manifold gaskets and the brake booster hose. A smoke test finds leaks quickly.",
            manual_query="intake manifold vacuum leak PCV",
            dtcs=["P0171", "P0174"],
        ))
        return out

    for bank, (i, c) in total.items():
        for label, v in (("idle", i), ("cruise", c)):
            if v is None or not sev(v):
                continue
            other = total.get(3 - bank, (None, None))[0 if label == "idle" else 1]
            one_bank = other is not None and abs(other) < 8
            direction = "lean (adding fuel)" if v > 0 else "rich (removing fuel)"
            hint = ("Only one bank is affected, which points to that bank: an injector, an exhaust leak ahead of the "
                    "O2 sensor, the O2 sensor itself, or an intake gasket on that side."
                    if one_bank else
                    "Both banks are affected, which points to a shared cause: MAF sensor, fuel pressure/pump, or a "
                    "large vacuum leak.")
            out.append(Finding(sev(v), f"Bank {bank} running {direction} at {label}",
                               f"Total trim {v:+.1f}% (STFT + LTFT). {hint}",
                               manual_query="fuel trim lean MAF fuel pressure" if v > 0 else "fuel trim rich injector",
                               dtcs=(["P0171"] if bank == 1 else ["P0174"]) if v > 0 else (["P0172"] if bank == 1 else ["P0175"])))
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
