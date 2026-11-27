"""Live data sources the dashboards (and CLI) can read from.

    sim[:scenario][@speedup]   simulated LR3, every channel
    /dev/ttyUSB0 | COM3 | tcp://host:port
                               ELM327 / OBDLink adapter, generic OBD-II engine data
    replay:path/to/log.csv[@speedup]
                               replay a recorded log, e.g. a GAP IIDTool export,
                               using a column mapping (see data/gap_mapping.json)

Every source returns samples as ``{"t": seconds, channel: value, ...}``. Values
are metric (°C, km/h, mm); the dashboards convert for display.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .obd import open_connection
from .obd.pids import DEFAULT_LOG_PIDS, PIDS


@dataclass(frozen=True)
class Channel:
    name: str
    label: str
    unit: str          # metric unit; "" for text/enumerated values
    group: str         # engine | fuel | electrical | driveline | suspension


CHANNELS: dict[str, Channel] = {c.name: c for c in [
    *[Channel(p.name, p.description, p.unit, "fuel" if "trim" in p.description.lower() else
              "electrical" if p.name == "voltage" else "engine") for p in PIDS.values()],
    Channel("selector", "Gear selector", "", "driveline"),
    Channel("gear", "Current gear", "", "driveline"),
    Channel("range", "Transfer box range", "", "driveline"),
    Channel("terrain", "Terrain Response mode", "", "driveline"),
    Channel("rear_locker", "Rear diff lock engagement", "%", "driveline"),
    Channel("trans_temp", "Transmission fluid temperature", "°C", "driveline"),
    Channel("height_fl", "Ride height front left", "mm", "suspension"),
    Channel("height_fr", "Ride height front right", "mm", "suspension"),
    Channel("height_rl", "Ride height rear left", "mm", "suspension"),
    Channel("height_rr", "Ride height rear right", "mm", "suspension"),
]}


class Source:
    name = "source"
    supports_dtcs = False

    def sample(self) -> dict: ...
    def dtcs(self) -> list[str]:
        return []
    def clear_dtcs(self) -> None:
        raise NotImplementedError("This source cannot clear codes")
    def close(self) -> None:
        pass


class OBDSource(Source):
    supports_dtcs = True

    def __init__(self, target: str, pids: list[str] | None = None):
        self.elm = open_connection(target)
        self.name = f"{target} · {self.elm.protocol}"
        sup = self.elm.supported_pids()
        self.pids = [p for p in (pids or DEFAULT_LOG_PIDS) if PIDS[p].pid in sup]
        self._elapsed = getattr(self.elm.t, "elapsed", None)
        self._extended = getattr(self.elm.t, "extended", None)
        self._t0 = time.monotonic()

    def sample(self) -> dict:
        t = self._elapsed() if self._elapsed else time.monotonic() - self._t0
        row: dict = {"t": round(t, 2)}
        for p in self.pids:
            v = self.elm.read(p)
            row[p] = None if v is None else round(v, 2)
        if self._extended:
            row.update({k: (round(v, 1) if isinstance(v, float) else v) for k, v in self._extended(t).items()})
        return row

    def dtcs(self) -> list[str]:
        return self.elm.read_dtcs()

    def clear_dtcs(self) -> None:
        self.elm.clear_dtcs()

    def close(self) -> None:
        self.elm.close()


def load_mapping(path: str | Path | None = None) -> dict:
    if path:
        return json.loads(Path(path).read_text())
    user = Path.home() / ".lr3diag" / "gap_mapping.json"
    if user.exists():
        return json.loads(user.read_text())
    return json.loads(resources.files("lr3diag").joinpath("data/gap_mapping.json").read_text())


def _norm(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def map_row(raw: dict[str, str], mapping: dict) -> dict:
    """Convert one CSV row to channel values using the mapping's column rules."""
    cols = {_norm(k): v for k, v in raw.items() if k is not None}
    out: dict = {}
    for col, rule in mapping.get("columns", {}).items():
        if isinstance(rule, str):
            rule = {"channel": rule}
        v = cols.get(_norm(col))
        if v is None or str(v).strip() == "":
            continue
        v = str(v).strip()
        try:
            out[rule["channel"]] = round(float(v) * rule.get("scale", 1.0) + rule.get("offset", 0.0), 3)
        except ValueError:
            out[rule["channel"]] = v  # text value, e.g. gear "D" or terrain mode
    return out


GEAR_NAMES = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6}
SELECTOR_NAMES = {"reverse": "R", "neutral": "N", "park": "P"}


def _gap_value(rule: dict, state: str, value: str):
    """Convert one GAP reading (state text + numeric value) using a mapping rule."""
    if rule["channel"] == "gear":
        st_ = state.strip().lower()
        if st_ in GEAR_NAMES:
            return GEAR_NAMES[st_]
        if st_ in SELECTOR_NAMES:
            return SELECTOR_NAMES[st_]
        return None
    if value.strip() == "":
        return state.strip() or None
    try:
        return round(float(value) * rule.get("scale", 1.0) + rule.get("offset", 0.0), 3)
    except ValueError:
        return value.strip()


def read_gap_log(path: str | Path, mapping: dict | None = None, step: float = 0.5) -> tuple[list[dict], dict]:
    """Read a GAP IIDTool live-data CSV export.

    GAP logs put each channel in its own block of columns
    (``name, time, state, value, , ,``), and every channel has its own
    timestamps. The channels are merged onto a common time grid of ``step``
    seconds, holding each channel's most recent value.
    Returns (rows, info) where info has the VIN/model/date header lines.
    """
    import bisect

    mapping = mapping or load_mapping()
    rules = {}
    for col, rule in mapping.get("columns", {}).items():
        rules[_norm(col)] = {"channel": rule} if isinstance(rule, str) else rule

    lines = Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines()
    hdr = next(i for i, ln in enumerate(lines) if ln.startswith("Live Value Name"))
    info = {"header": [ln.strip() for ln in lines[:hdr] if ln.strip()], "unmapped": set(), "channels": {}}
    series: dict[str, list[tuple[float, object]]] = {}
    for row in csv.reader(lines[hdr + 1:]):
        for i in range(0, len(row) - 3, 6):
            name, t, state, value = row[i:i + 4]
            if not name or not t:
                continue
            rule = rules.get(_norm(name))
            if not rule:
                info["unmapped"].add(name)
                continue
            v = _gap_value(rule, state, value)
            if v is None:
                continue
            series.setdefault(rule["channel"], []).append((float(t), v))
            info["channels"][rule["channel"]] = name
    if not series:
        return [], info
    for s in series.values():
        s.sort(key=lambda x: x[0])
    times = {ch: [x[0] for x in s] for ch, s in series.items()}
    start = max(s[0][0] for s in series.values())   # wait until every channel has a value
    end = max(s[-1][0] for s in series.values())
    rows = []
    t = start
    while t < end + step:
        row: dict = {"t": round(t - start, 2)}
        for ch, s in series.items():
            j = bisect.bisect_right(times[ch], t) - 1
            if j >= 0:
                row[ch] = s[j][1]
        rows.append(row)
        t += step
    info["unmapped"] = sorted(info["unmapped"])
    return rows, info


def is_gap_log(path: str | Path) -> bool:
    with Path(path).open(encoding="utf-8-sig", errors="replace") as f:
        head = [next(f, "") for _ in range(20)]
    return any(ln.startswith("Live Value Name") for ln in head)


def read_log(path: str | Path, mapping: dict | None = None) -> list[dict]:
    """Read a CSV log into samples. Native lr3diag logs and GAP exports are detected automatically."""
    if is_gap_log(path):
        return read_gap_log(path, mapping)[0]
    mapping = mapping or load_mapping()
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        native = "t" in fields and any(c in CHANNELS for c in fields)
        tcol = _norm(mapping.get("time_column", "time"))
        tscale = float(mapping.get("time_scale", 1.0))
        rows = []
        for i, raw in enumerate(reader):
            if native:
                row = {}
                for k, v in raw.items():
                    if v in ("", None, "None"):
                        continue
                    try:
                        row[k] = float(v)
                    except ValueError:
                        row[k] = v
            else:
                row = map_row(raw, mapping)
                tv = {_norm(k): v for k, v in raw.items() if k}.get(tcol)
                try:
                    row["t"] = float(tv) * tscale
                except (TypeError, ValueError):
                    row["t"] = float(i)
            rows.append(row)
    if rows:
        t0 = rows[0].get("t", 0.0)
        for r in rows:
            r["t"] = round(r.get("t", 0.0) - t0, 2)
    return rows


class ReplaySource(Source):
    """Plays back a log in real time (or faster), looping at the end."""

    def __init__(self, path: str, speed: float = 1.0, mapping: dict | None = None):
        self.rows = read_log(path, mapping)
        if not self.rows:
            raise ValueError(f"No rows in {path}")
        self.name = f"replay · {Path(path).name}"
        self.speed = speed
        self._t0 = time.monotonic()
        self._i = 0

    def sample(self) -> dict:
        span = self.rows[-1]["t"] or 1.0
        target = ((time.monotonic() - self._t0) * self.speed) % (span + 0.001)
        if target < self.rows[self._i]["t"]:
            self._i = 0
        while self._i + 1 < len(self.rows) and self.rows[self._i + 1]["t"] <= target:
            self._i += 1
        return dict(self.rows[self._i])


def open_source(target: str, mapping_path: str | None = None) -> Source:
    if target.startswith("replay:"):
        spec = target[len("replay:"):]
        speed = 1.0
        if "@" in spec:
            spec, s = spec.rsplit("@", 1)
            speed = float(s)
        return ReplaySource(spec, speed, load_mapping(mapping_path))
    return OBDSource(target)
