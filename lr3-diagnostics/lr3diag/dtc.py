"""Diagnostic trouble code lookup with LR3-specific notes."""

from __future__ import annotations

import json
import re
from importlib import resources

_SYSTEM = {"P": "Powertrain", "C": "Chassis", "B": "Body", "U": "Network"}
_SUBSYS = {
    "P": {"0": "Fuel/air metering & aux emissions", "1": "Fuel/air metering", "2": "Fuel/air metering (injector circuit)",
          "3": "Ignition system / misfire", "4": "Auxiliary emission controls", "5": "Vehicle speed / idle control",
          "6": "Computer & output circuits", "7": "Transmission", "8": "Transmission", "9": "Transmission",
          "A": "Hybrid propulsion"},
}


def _db() -> dict:
    return json.loads(resources.files("lr3diag").joinpath("data/dtc.json").read_text())


def lookup(code: str) -> dict:
    code = code.strip().upper()
    if not re.fullmatch(r"[PCBU][0-3][0-9A-F]{3}", code):
        raise ValueError(f"'{code}' is not a valid DTC (expected e.g. P0171)")
    entry = _db().get(code, {})
    generic = code[1] in "02"   # 0/2 = SAE-defined, 1/3 = manufacturer
    return {
        "code": code,
        "system": _SYSTEM[code[0]],
        "subsystem": _SUBSYS.get(code[0], {}).get(code[2], ""),
        "type": "Generic (SAE)" if generic else "Manufacturer-specific",
        "description": entry.get("description", "Not in local database. Look it up in the workshop manual."),
        "lr3_notes": entry.get("lr3_notes", ""),
        "manual_query": entry.get("manual_query", code),
    }
