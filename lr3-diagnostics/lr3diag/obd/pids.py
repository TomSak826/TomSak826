"""Standard SAE J1979 Mode 01 PIDs relevant to the LR3 4.4 V8.

Each PID decodes its data bytes (A, B, ...) into engineering units using the
SAE formulas. The 4.4 V8 has two banks, so fuel trims and O2 sensors are
reported for Bank 1 and Bank 2 separately.

Land Rover-specific modules (air suspension, transfer case, parking brake,
body control) are *not* reachable through generic OBD-II Mode 01. They need
manufacturer protocols; see the README.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class PID:
    pid: int
    name: str          # short column name used in logs
    description: str
    unit: str
    nbytes: int
    decode: Callable[[bytes], float]
    encode: Callable[[float], bytes] | None = None  # used by the simulator


def _u8(f):
    return lambda d: f(d[0])


def _u16(f):
    return lambda d: f(d[0] * 256 + d[1])


def _clip8(x):
    return bytes([max(0, min(255, round(x)))])


def _clip16(x):
    v = max(0, min(65535, round(x)))
    return bytes([v >> 8, v & 0xFF])


_trim_dec = _u8(lambda a: (a - 128) * 100 / 128)
_trim_enc = lambda v: _clip8(v * 128 / 100 + 128)  # noqa: E731

_LIST = [
    PID(0x04, "load", "Calculated engine load", "%", 1, _u8(lambda a: a * 100 / 255), lambda v: _clip8(v * 255 / 100)),
    PID(0x05, "coolant", "Engine coolant temperature", "°C", 1, _u8(lambda a: a - 40), lambda v: _clip8(v + 40)),
    PID(0x06, "stft1", "Short-term fuel trim, bank 1", "%", 1, _trim_dec, _trim_enc),
    PID(0x07, "ltft1", "Long-term fuel trim, bank 1", "%", 1, _trim_dec, _trim_enc),
    PID(0x08, "stft2", "Short-term fuel trim, bank 2", "%", 1, _trim_dec, _trim_enc),
    PID(0x09, "ltft2", "Long-term fuel trim, bank 2", "%", 1, _trim_dec, _trim_enc),
    PID(0x0B, "map", "Intake manifold absolute pressure", "kPa", 1, _u8(float), _clip8),
    PID(0x0C, "rpm", "Engine speed", "rpm", 2, _u16(lambda x: x / 4), lambda v: _clip16(v * 4)),
    PID(0x0D, "speed", "Vehicle speed", "km/h", 1, _u8(float), _clip8),
    PID(0x0E, "timing", "Ignition timing advance", "°", 1, _u8(lambda a: a / 2 - 64), lambda v: _clip8((v + 64) * 2)),
    PID(0x0F, "iat", "Intake air temperature", "°C", 1, _u8(lambda a: a - 40), lambda v: _clip8(v + 40)),
    PID(0x10, "maf", "Mass air flow", "g/s", 2, _u16(lambda x: x / 100), lambda v: _clip16(v * 100)),
    PID(0x11, "throttle", "Throttle position", "%", 1, _u8(lambda a: a * 100 / 255), lambda v: _clip8(v * 255 / 100)),
    PID(0x1F, "runtime", "Run time since engine start", "s", 2, _u16(float), _clip16),
    PID(0x2F, "fuel", "Fuel tank level", "%", 1, _u8(lambda a: a * 100 / 255), lambda v: _clip8(v * 255 / 100)),
    PID(0x33, "baro", "Barometric pressure", "kPa", 1, _u8(float), _clip8),
    PID(0x42, "voltage", "Control module voltage", "V", 2, _u16(lambda x: x / 1000), lambda v: _clip16(v * 1000)),
    PID(0x46, "ambient", "Ambient air temperature", "°C", 1, _u8(lambda a: a - 40), lambda v: _clip8(v + 40)),
]

PIDS: dict[str, PID] = {p.name: p for p in _LIST}
PIDS_BY_NUM: dict[int, PID] = {p.pid: p for p in _LIST}

# A good default set for diagnosing the 4.4 V8. More PIDs means a slower
# sample rate, because each PID is a separate request over the bus.
DEFAULT_LOG_PIDS = ["rpm", "speed", "coolant", "iat", "load", "throttle", "maf",
                    "stft1", "ltft1", "stft2", "ltft2", "voltage"]

KMH_TO_MPH = 0.621371


def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32
