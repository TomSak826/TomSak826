"""Minimal ELM327 driver.

Connection strings:
    /dev/ttyUSB0, COM3, /dev/rfcomm0   serial / USB / Bluetooth-SPP (needs pyserial)
    tcp://192.168.0.10:35000           WiFi adapters
    sim  or  sim:<scenario>            built-in simulated LR3 (no hardware)
"""

from __future__ import annotations

import re
import socket
import time
from typing import Protocol

from .pids import PIDS, PID


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...
    def read_until_prompt(self, timeout: float) -> str: ...
    def close(self) -> None: ...


class SerialTransport:
    def __init__(self, port: str, baud: int = 38400):
        try:
            import serial  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Serial adapters need pyserial:  pip install pyserial") from exc
        self.ser = serial.Serial(port, baudrate=baud, timeout=0.1)

    def write(self, data: bytes) -> None:
        self.ser.write(data)

    def read_until_prompt(self, timeout: float) -> str:
        buf, end = b"", time.monotonic() + timeout
        while time.monotonic() < end:
            chunk = self.ser.read(256)
            if chunk:
                buf += chunk
                if b">" in chunk:
                    break
        return buf.decode(errors="replace")

    def close(self) -> None:
        self.ser.close()


class TCPTransport:
    def __init__(self, host: str, port: int):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(0.1)

    def write(self, data: bytes) -> None:
        self.sock.sendall(data)

    def read_until_prompt(self, timeout: float) -> str:
        buf, end = b"", time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                chunk = self.sock.recv(256)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf += chunk
            if b">" in chunk:
                break
        return buf.decode(errors="replace")

    def close(self) -> None:
        self.sock.close()


class OBDError(RuntimeError):
    pass


_NOISE = ("SEARCHING", "BUS INIT", "OK", "STOPPED")
_ERRORS = ("UNABLE TO CONNECT", "CAN ERROR", "BUS ERROR", "NO DATA", "?", "ERROR")


class ELM327:
    def __init__(self, transport: Transport, timeout: float = 2.0):
        self.t = transport
        self.timeout = timeout
        self.version = ""
        self.protocol = ""
        self._supported: set[int] | None = None

    # -- low level -------------------------------------------------------
    def command(self, cmd: str, timeout: float | None = None) -> list[str]:
        self.t.write((cmd + "\r").encode())
        raw = self.t.read_until_prompt(timeout or self.timeout)
        lines = [ln.strip() for ln in raw.replace(">", "").replace("\n", "\r").split("\r")]
        return [ln for ln in lines if ln and ln.upper() != cmd.upper()]

    def initialize(self) -> None:
        self.command("ATZ", timeout=3)            # reset
        self.command("ATE0")                      # echo off
        self.command("ATL0")                      # linefeeds off
        self.command("ATS0")                      # spaces off
        self.command("ATH0")                      # headers off
        self.command("ATSP0")                     # auto protocol
        ver = self.command("ATI")
        self.version = ver[0] if ver else "unknown"
        # Force protocol detection with a real request.
        self.request(0x01, 0x00, timeout=8)
        dp = self.command("ATDP")
        self.protocol = dp[0] if dp else "unknown"

    def request(self, mode: int, pid: int | None = None, timeout: float | None = None) -> list[bytes]:
        """Send an OBD request and return one payload per responding ECU.

        Each payload starts with the response mode byte (mode + 0x40).
        """
        cmd = f"{mode:02X}" + (f"{pid:02X}" if pid is not None else "")
        lines = self.command(cmd, timeout)
        if any(e in ln.upper() for ln in lines for e in _ERRORS if e != "?") or lines == ["?"]:
            if any("NO DATA" in ln.upper() for ln in lines):
                return []
            raise OBDError(f"{cmd}: {' | '.join(lines)}")
        return _parse_payloads(lines)

    # -- Mode 01 ---------------------------------------------------------
    def supported_pids(self) -> set[int]:
        if self._supported is None:
            sup: set[int] = set()
            base = 0x00
            while True:
                payloads = self.request(0x01, base)
                bits = next((p[2:6] for p in payloads if len(p) >= 6 and p[1] == base), None)
                if not bits:
                    break
                val = int.from_bytes(bits, "big")
                for i in range(32):
                    if val & (1 << (31 - i)):
                        sup.add(base + i + 1)
                if (base + 0x20) not in sup or base >= 0xC0:
                    break
                base += 0x20
            self._supported = sup
        return self._supported

    def read(self, pid: PID | str) -> float | None:
        p = PIDS[pid] if isinstance(pid, str) else pid
        for payload in self.request(0x01, p.pid):
            if len(payload) >= 2 + p.nbytes and payload[0] == 0x41 and payload[1] == p.pid:
                return p.decode(payload[2:2 + p.nbytes])
        return None

    # -- DTCs ------------------------------------------------------------
    def read_dtcs(self, pending: bool = False) -> list[str]:
        mode = 0x07 if pending else 0x03
        codes: list[str] = []
        for payload in self.request(mode, timeout=5):
            data = payload[1:]
            if len(data) % 2 == 1:       # CAN responses carry a count byte
                data = data[1:]
            for i in range(0, len(data) - 1, 2):
                code = decode_dtc(data[i], data[i + 1])
                if code and code not in codes:
                    codes.append(code)
        return codes

    def clear_dtcs(self) -> None:
        self.request(0x04, timeout=5)

    def read_vin(self) -> str | None:
        payloads = self.request(0x09, 0x02, timeout=5)
        for p in payloads:
            text = bytes(b for b in p[3:] if 0x20 < b < 0x7F).decode(errors="ignore")
            if len(text) >= 17:
                return text[-17:]
        return None

    def close(self) -> None:
        self.t.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


_HEX = re.compile(r"^[0-9A-Fa-f]+$")


def _parse_payloads(lines: list[str]) -> list[bytes]:
    """Turn ELM327 response lines (headers off) into byte payloads.

    Handles single-frame replies (``410C1AF8``) and CAN multi-frame replies,
    which look like::

        014
        0:490201314A34
        1:47413233...
    """
    payloads: list[bytes] = []
    multi: list[str] = []
    for ln in lines:
        s = ln.replace(" ", "")
        if any(s.upper().startswith(n) for n in _NOISE):
            continue
        m = re.match(r"^([0-9A-Fa-f]):(.*)$", s)
        if m:
            multi.append(m.group(2))
            continue
        if not _HEX.match(s):
            continue
        if len(s) <= 3:   # multi-frame byte-count header line
            if multi:
                payloads.append(bytes.fromhex("".join(multi)))
                multi = []
            continue
        if len(s) % 2:
            continue
        payloads.append(bytes.fromhex(s))
    if multi:
        joined = "".join(multi)
        payloads.append(bytes.fromhex(joined[: len(joined) // 2 * 2]))
    return payloads


def decode_dtc(a: int, b: int) -> str | None:
    if a == 0 and b == 0:
        return None
    letter = "PCBU"[a >> 6]
    return f"{letter}{(a >> 4) & 0x3}{a & 0xF:X}{b:02X}"


def encode_dtc(code: str) -> bytes:
    letter = "PCBU".index(code[0].upper())
    first = (letter << 6) | (int(code[1]) << 4) | int(code[2], 16)
    return bytes([first, int(code[3:5], 16)])


def open_connection(target: str, init: bool = True) -> ELM327:
    if target.startswith("sim"):
        from .simulator import SimulatedLR3
        scenario = target.split(":", 1)[1] if ":" in target else "healthy"
        transport: Transport = SimulatedLR3(scenario)
    elif target.startswith("tcp://"):
        host, port = target[6:].rsplit(":", 1)
        transport = TCPTransport(host, int(port))
    else:
        transport = SerialTransport(target)
    elm = ELM327(transport)
    if init:
        elm.initialize()
    return elm
