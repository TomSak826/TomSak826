from lr3diag.obd.elm327 import _parse_payloads, decode_dtc, encode_dtc
from lr3diag.obd.pids import PIDS

from .conftest import make_elm


def test_dtc_roundtrip():
    for code in ["P0171", "P0305", "C1A20", "U0100", "B1D01"]:
        assert decode_dtc(*encode_dtc(code)) == code
    assert decode_dtc(0, 0) is None


def test_parse_single_and_multiframe():
    assert _parse_payloads(["41 0C 1A F8"]) == [bytes.fromhex("410C1AF8")]
    assert _parse_payloads(["SEARCHING...", "410D3C"]) == [bytes.fromhex("410D3C")]
    lines = ["014", "0:49020153494D", "1:4C523330303030", "2:30303030303030"]
    (p,) = _parse_payloads(lines)
    assert p[:3] == bytes.fromhex("490201") and p[3:].startswith(b"SIMLR3")


def test_pid_decoders():
    assert PIDS["rpm"].decode(bytes([0x1A, 0xF8])) == 1726
    assert PIDS["coolant"].decode(bytes([130])) == 90
    assert PIDS["stft1"].decode(bytes([128])) == 0
    for name in ("coolant", "rpm", "voltage", "ltft2"):
        p = PIDS[name]
        assert abs(p.decode(p.encode(42.0)) - 42.0) < 1


def test_sim_end_to_end():
    elm = make_elm("vacuum_leak")
    assert "CAN" in elm.protocol
    assert PIDS["rpm"].pid in elm.supported_pids()
    assert 500 < elm.read("rpm") < 800
    assert elm.read_dtcs() == ["P0171", "P0174"]
    assert elm.read_vin() and len(elm.read_vin()) == 17
    elm.clear_dtcs()
    assert elm.read_dtcs() == []
