import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

from lr3diag import dtc, parts
from lr3diag.sources import ReplaySource, open_source, read_log
from lr3diag.web.server import LiveFeed, make_handler


def test_flat_csv_with_mapping(tmp_path):
    f = tmp_path / "other.csv"
    f.write_text("Time,Engine Coolant Temperature,Ride Height Front Left,Unrelated\n"
                 "10.0,88,12.5,x\n10.5,89,13.0,y\n")
    rows = read_log(f)
    assert rows[0] == {"coolant": 88.0, "height_fl": 12.5, "t": 0.0}
    assert rows[1]["t"] == 0.5
    assert ReplaySource(str(f)).sample()["coolant"] == 88.0


GAP_SAMPLE = (
    "VIN TESTVIN0000000000\nModel L319 - LR3 2007 \nLog test saved on the Sun\nIIDControl 4.3.0\n\n\n"
    "Live Value Name:, Times:, States:, Values:,,,Live Value Name:, Times:, States:, Values:,,,"
    "Live Value Name:, Times:, States:, Values:,,,Event name:,Speed:(mph),Time:,\n"
    '"Vehicle speed",0,"","",,,"Transmission current gear",0,"Undefined","",,,"Long term fuel trim (bank 1)",0,"","",,,\n'
    '"Vehicle speed",0.1,"","0.000000",,,"Transmission current gear",0.1,"First","1",,,"Long term fuel trim (bank 1)",0.2,"","7.812500",,,\n'
    '"Vehicle speed",1.0,"","31.068550",,,"Transmission current gear",1.1,"Reverse","2",,,"Long term fuel trim (bank 1)",1.3,"","9.375000",,,\n'
    ',,,,,,"Transmission current gear",1.6,"Third","4",,,,,,,,,\n'
)


def test_gap_native_log(tmp_path):
    from lr3diag.sources import is_gap_log, read_gap_log
    f = tmp_path / "IIDTool_Log.csv"
    f.write_text(GAP_SAMPLE)
    assert is_gap_log(f)
    rows, info = read_gap_log(f, step=0.5)
    assert info["header"][0].startswith("VIN")
    assert rows[0]["t"] == 0.0 and rows[0]["gear"] == 1 and rows[0]["ltft1"] == 7.812
    last = rows[-1]
    assert abs(last["speed"] - 50.0) < 0.01        # mph converted to km/h
    assert last["gear"] == 3 and last["ltft1"] == 9.375
    assert any(r.get("gear") == "R" for r in rows)
    assert read_log(f) == rows                      # auto-detected


def test_sim_source_has_offroad_channels():
    s = open_source("sim:offroad@100")
    time.sleep(0.05)
    r = s.sample()
    assert r["range"] == "LO" and r["terrain"] == "Rock Crawl" and "height_rr" in r


def test_dtc_lookup():
    assert dtc.lookup("p0171")["description"].startswith("System too lean")
    assert dtc.lookup("P1234")["type"] == "Manufacturer-specific"


def test_parts_links():
    ps = parts.with_links(parts.load_parts())
    oil = next(p for p in ps if p["key"] == "oil_filter")
    assert any("amazon.com" in link["url"] and "LR3" in link["url"] for link in oil["links"])


def test_web_api(tmp_path):
    feed = LiveFeed(open_source("sim:vacuum_leak@50"), hz=20)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(feed, str(tmp_path / "w.db")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    get = lambda p: json.loads(urllib.request.urlopen(base + p).read())  # noqa: E731

    def post(p, body):
        req = urllib.request.Request(base + p, json.dumps(body).encode(), {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read())

    try:
        for page in ("/", "/diagnostics", "/maintenance", "/offroad", "/manual", "/static/common.js"):
            assert urllib.request.urlopen(base + page).status == 200
        time.sleep(0.4)
        assert get("/api/info")["supports_dtcs"]
        assert len(get("/api/history")) > 2
        assert [c["code"] for c in get("/api/dtcs")] == ["P0171", "P0174"]
        post("/api/odometer", {"miles": 120000})
        post("/api/maintenance/log", {"item": "engine_oil", "odometer": 119000})
        m = get("/api/maintenance")
        assert m["odometer"] == 120000 and m["history"][0]["item"] == "engine_oil"
        assert get("/api/manual/search?q=anything")["hits"] == []
        assert "t," in urllib.request.urlopen(base + "/api/history.csv").read().decode().splitlines()[0] + ","
    finally:
        srv.shutdown()
