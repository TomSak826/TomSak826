"""A simulated ELM327 connected to a simulated LR3 4.4 V8.

Lets you try out every feature and run the tests without the truck.
It answers AT commands and OBD requests the way a real adapter does on the
LR3's CAN bus, so the parsing code is exercised end to end.

Scenarios (``sim:<name>``, optionally ``@<speedup>``, e.g. ``sim:thermostat@20``):
    healthy       everything normal
    vacuum_leak   both banks lean at idle, better under load  (P0171/P0174)
    thermostat    thermostat stuck open, engine never fully warms  (P0128)
    charging      weak alternator, system voltage low
    misfire       bank 2 misfire: rough idle, erratic bank-2 trims  (P0300/P0305)
    offroad       slow rock crawl in low range, off-road ride height, axle articulation

The simulator also produces Land Rover-specific channels (ride height, gear,
transfer range, Terrain Response mode, transmission temperature). A real
ELM327 can't read these. In real use they come from a GAP IIDTool log import.
"""

from __future__ import annotations

import math
import random
import time

from .elm327 import encode_dtc
from .pids import PIDS_BY_NUM

SCENARIOS = {
    "healthy": [],
    "vacuum_leak": ["P0171", "P0174"],
    "thermostat": ["P0128"],
    "charging": [],
    "misfire": ["P0300", "P0305"],
    "offroad": [],
}

SIM_VIN = "SIMLR3000000000007"[:17]


class SimulatedLR3:
    def __init__(self, scenario: str = "healthy", seed: int = 7, clock=time.monotonic):
        speedup = 1.0
        if "@" in scenario:
            scenario, s = scenario.split("@", 1)
            speedup = float(s)
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown scenario '{scenario}'. Choose from: {', '.join(SCENARIOS)}")
        self.scenario = scenario
        self.speedup = speedup
        self.dtcs = list(SCENARIOS[scenario])
        self.rng = random.Random(seed)
        self._clock = clock
        self._t0 = clock()
        self._pending = ""
        self._out = ""
        self.headers = False
        self.spaces = True

    # Transport interface -------------------------------------------------
    def write(self, data: bytes) -> None:
        self._pending += data.decode()
        while "\r" in self._pending:
            cmd, self._pending = self._pending.split("\r", 1)
            self._out += self._handle(cmd.strip().upper().replace(" ", "")) + "\r\r>"

    def read_until_prompt(self, timeout: float) -> str:
        out, self._out = self._out, ""
        return out

    def close(self) -> None:
        pass

    def elapsed(self) -> float:
        """Simulated seconds since engine start."""
        return (self._clock() - self._t0) * self.speedup

    # Vehicle model ---------------------------------------------------------
    def state(self, t: float | None = None) -> dict[str, float]:
        t = self.elapsed() if t is None else t
        n = self.rng.gauss
        ambient = 15.0

        # Drive cycle: 2 min idle, then repeating 3-min loop of
        # accelerate / cruise ~60 mph / slow / stop.
        if t < 120:
            speed, throttle = 0.0, 0.0
        else:
            c = (t - 120) % 180
            if c < 20:
                speed, throttle = c / 20 * 97, 35
            elif c < 140:
                speed, throttle = 97 + 3 * math.sin(c / 9), 18
            elif c < 165:
                speed, throttle = 97 * (165 - c) / 25, 0
            else:
                speed, throttle = 0.0, 0.0
        if self.scenario == "offroad" and t >= 60:
            speed = max(0.0, 5 + 4 * math.sin(t / 7))
            throttle = 12 + 8 * max(0.0, math.sin(t / 5))
        idle = speed < 1 and throttle < 1

        rpm = 650 if idle else 900 + speed * 18 + throttle * 12
        if self.scenario == "misfire" and idle:
            rpm += n(0, 90)
        else:
            rpm += n(0, 12)

        load = 22 if idle else 25 + throttle * 1.2
        maf = 5.0 if idle else 6 + rpm / 1000 * 7 * (0.4 + throttle / 60)

        # Coolant warm-up toward the thermostat set point.
        target = 92.0
        if self.scenario == "thermostat":
            target = 72.0 if not idle else 80.0
        if self.scenario == "offroad":
            target = 99.0 + 4 * math.sin(t / 90)
        coolant = target - (target - ambient) * math.exp(-t / 260)

        stft1, stft2 = n(0, 2.5), n(0, 2.5)
        ltft1, ltft2 = 1.5 + n(0, 0.3), 0.8 + n(0, 0.3)
        if self.scenario == "vacuum_leak":
            lean = 16 if idle else 5
            ltft1 += lean
            ltft2 += lean
        if self.scenario == "misfire":
            stft2 = n(6, 9)

        voltage = 14.2 + n(0, 0.05)
        if self.scenario == "charging":
            voltage = max(11.8, 13.0 - t / 3000) + n(0, 0.08)

        return {
            "load": load, "coolant": coolant, "stft1": stft1, "ltft1": ltft1,
            "stft2": stft2, "ltft2": ltft2, "map": 32 if idle else 35 + throttle * 1.4,
            "rpm": rpm, "speed": speed, "timing": 12 if idle else 22 + n(0, 1),
            "iat": ambient + 10 + n(0, 0.5), "maf": maf, "throttle": throttle * 0.8 + 11,
            "runtime": t, "fuel": 62.0, "baro": 100.0, "voltage": voltage, "ambient": ambient,
        }

    def extended(self, t: float | None = None) -> dict:
        """Land Rover module data (EAS, TCM, transfer box, Terrain Response)."""
        t = self.elapsed() if t is None else t
        base = self.state(t)
        n = self.rng.gauss
        speed = base["speed"]
        off = self.scenario == "offroad"
        # Ride height in mm relative to standard height. Off-road height is
        # about +55 mm; in this model, articulation moves diagonal corners
        # in opposite directions.
        h = 55.0 if off else 0.0
        art = 60 * math.sin(t / 6) if off and t >= 60 else 0.0
        heights = {
            "height_fl": h + art + n(0, 1.5), "height_fr": h - art + n(0, 1.5),
            "height_rl": h - art * 0.8 + n(0, 1.5), "height_rr": h + art * 0.8 + n(0, 1.5),
        }
        if speed < 1 and base["throttle"] < 12 and t < 120 and not off:
            selector, gear = "P" if t < 20 else "D", 0
        else:
            selector = "D"
            ratio = speed / (8 if off else 16)
            gear = max(1, min(6, 1 + int(ratio)))
        trans_temp = 30 + (85 if not off else 98) * (1 - math.exp(-t / 500)) + n(0, 0.3)
        return {
            **heights,
            "selector": selector,
            "gear": gear,
            "range": "LO" if off else "HI",
            "terrain": "Rock Crawl" if off else "General",
            "rear_locker": round(min(100, abs(art) * 1.5)) if off else 0,
            "trans_temp": trans_temp,
        }

    # ELM327 command handling -----------------------------------------------
    def _fmt(self, data: bytes) -> str:
        h = data.hex().upper()
        return " ".join(h[i:i + 2] for i in range(0, len(h), 2)) if self.spaces else h

    def _handle(self, cmd: str) -> str:
        if cmd.startswith("AT"):
            at = cmd[2:]
            if at == "Z":
                return "ELM327 v1.5 (simulated)"
            if at == "I":
                return "ELM327 v1.5 (simulated)"
            if at == "DP":
                return "ISO 15765-4 (CAN 11/500)"
            if at == "S0":
                self.spaces = False
            if at == "S1":
                self.spaces = True
            return "OK"

        try:
            mode = int(cmd[:2], 16)
        except ValueError:
            return "?"

        if mode == 0x01:
            pid = int(cmd[2:4], 16)
            if pid in (0x00, 0x20, 0x40):
                sup = 0
                for p in list(PIDS_BY_NUM) + [0x20, 0x40]:
                    if pid < p <= pid + 32:
                        sup |= 1 << (32 - (p - pid))
                if pid == 0x40:
                    sup &= ~1  # no 0x60 range
                return self._fmt(bytes([0x41, pid]) + sup.to_bytes(4, "big"))
            spec = PIDS_BY_NUM.get(pid)
            if not spec or not spec.encode:
                return "NO DATA"
            return self._fmt(bytes([0x41, pid]) + spec.encode(self.state()[spec.name]))

        if mode in (0x03, 0x07):
            codes = self.dtcs if mode == 0x03 else []
            body = bytes([0x40 + mode, len(codes)]) + b"".join(encode_dtc(c) for c in codes)
            return self._multiframe(body)

        if mode == 0x04:
            self.dtcs = []
            return "44"

        if mode == 0x09 and cmd[2:4] == "02":
            return self._multiframe(bytes([0x49, 0x02, 0x01]) + SIM_VIN.encode())

        return "NO DATA"

    def _multiframe(self, body: bytes) -> str:
        if len(body) <= 7:
            return self._fmt(body)
        lines = [f"{len(body):03X}"]
        first, rest = body[:6], body[6:]
        lines.append("0:" + self._fmt(first))
        i = 1
        while rest:
            lines.append(f"{i % 16:X}:" + self._fmt(rest[:7]))
            rest = rest[7:]
            i += 1
        return "\r".join(lines)
