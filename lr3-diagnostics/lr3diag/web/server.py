"""Local dashboard server (Python standard library only; no internet needed).

    lr3 dash --source sim               # try it with the simulator
    lr3 dash --source /dev/rfcomm0      # Bluetooth ELM327 / OBDLink
    lr3 dash --source replay:gap_log.csv
    lr3 dash --host 0.0.0.0             # reach it from a phone on the same WiFi

Pages:  /  /diagnostics  /maintenance  /offroad  /manual
"""

from __future__ import annotations

import csv
import io
import json
import threading
import time
from collections import deque
from dataclasses import asdict
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from .. import analysis, db, dtc, maintenance, manual, parts
from ..sources import CHANNELS, Source, open_source

PAGES = {"/": "index.html", "/diagnostics": "diagnostics.html", "/maintenance": "maintenance.html",
         "/offroad": "offroad.html", "/manual": "manual.html"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript"}


class LiveFeed:
    """Polls the source on a background thread and keeps a rolling history."""

    def __init__(self, source: Source | None, hz: float = 4.0, history: int = 1200):
        self.source = source
        self.period = 1.0 / hz
        self.history: deque[dict] = deque(maxlen=history)
        self.cond = threading.Condition()
        self.error = ""
        self.seq = 0
        self.lock = threading.Lock()  # serialises access to the adapter
        if source:
            threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            start = time.monotonic()
            try:
                with self.lock:
                    s = self.source.sample()
                s["wall"] = time.time()
                with self.cond:
                    self.history.append(s)
                    self.seq += 1
                    self.error = ""
                    self.cond.notify_all()
            except Exception as exc:  # keep running through adapter hiccups
                self.error = str(exc)
                time.sleep(1)
            time.sleep(max(0.0, self.period - (time.monotonic() - start)))


def make_handler(feed: LiveFeed, db_path: str | None):
    static = resources.files("lr3diag").joinpath("web/static")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter console
            pass

        # -- helpers ----------------------------------------------------
        def _send(self, body: bytes, ctype: str, status: int = 200, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200):
            self._send(json.dumps(obj, default=str).encode(), "application/json", status)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        # -- routes -----------------------------------------------------
        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            path = url.path.rstrip("/") or "/"
            try:
                if path in PAGES:
                    return self._send(static.joinpath(PAGES[path]).read_bytes(), TYPES[".html"])
                if path.startswith("/static/"):
                    name = path[len("/static/"):]
                    if "/" in name or ".." in name:
                        return self._json({"error": "not found"}, 404)
                    f = static.joinpath(name)
                    ext = "." + name.rsplit(".", 1)[-1]
                    return self._send(f.read_bytes(), TYPES.get(ext, "application/octet-stream"))
                if path == "/api/info":
                    return self._json({
                        "source": feed.source.name if feed.source else None,
                        "supports_dtcs": bool(feed.source and feed.source.supports_dtcs),
                        "error": feed.error,
                        "channels": {k: asdict(c) for k, c in CHANNELS.items()},
                    })
                if path == "/api/stream":
                    return self._stream()
                if path == "/api/history":
                    return self._json(list(feed.history))
                if path == "/api/history.csv":
                    return self._csv()
                if path == "/api/findings":
                    rows = list(feed.history)
                    return self._json([asdict(f) for f in analysis.analyze(rows)])
                if path == "/api/dtcs":
                    if not (feed.source and feed.source.supports_dtcs):
                        return self._json({"error": "This source can't read codes"}, 400)
                    with feed.lock:
                        codes = feed.source.dtcs()
                    return self._json([dtc.lookup(c) for c in codes])
                if path.startswith("/api/dtc/"):
                    return self._json(dtc.lookup(path.rsplit("/", 1)[1]))
                if path == "/api/maintenance":
                    conn = db.connect(db_path)
                    st = maintenance.status(conn)
                    return self._json({
                        "odometer": maintenance.current_odometer(conn),
                        "status": [{**asdict(s), "item": asdict(s.item)} for s in st],
                        "history": [dict(r) for r in maintenance.history(conn)][:50],
                    })
                if path == "/api/parts":
                    return self._json(parts.with_links(parts.load_parts()))
                if path == "/api/manual/search":
                    conn = db.connect(db_path)
                    hits = manual.search(conn, q.get("q", ""), int(q.get("limit", 20)))
                    return self._json({"indexed": [dict(r) for r in manual.stats(conn)],
                                       "hits": [asdict(h) for h in hits]})
                if path == "/api/manual/section":
                    conn = db.connect(db_path)
                    row = manual.get_section(conn, q.get("source", ""), q.get("location", ""))
                    return self._json(dict(row) if row else {"error": "not found"}, 200 if row else 404)
                return self._json({"error": "not found"}, 404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)

        def do_POST(self):
            path = urlparse(self.path).path.rstrip("/")
            try:
                body = self._body()
                if path == "/api/maintenance/log":
                    conn = db.connect(db_path)
                    maintenance.log_service(conn, body["item"], int(body["odometer"]), body.get("date") or None,
                                            body.get("notes", ""), float(body["cost"]) if body.get("cost") else None)
                    return self._json({"ok": True})
                if path == "/api/odometer":
                    conn = db.connect(db_path)
                    maintenance.record_odometer(conn, int(body["miles"]), body.get("date") or date.today().isoformat())
                    return self._json({"ok": True})
                if path == "/api/dtcs/clear":
                    if not body.get("confirm"):
                        return self._json({"error": "confirmation required"}, 400)
                    with feed.lock:
                        feed.source.clear_dtcs()
                    return self._json({"ok": True})
                return self._json({"error": "not found"}, 404)
            except KeyError as exc:
                return self._json({"error": str(exc)}, 400)
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seen = feed.seq
            snapshot = list(feed.history)[-240:]
            self.wfile.write(f"event: history\ndata: {json.dumps(snapshot)}\n\n".encode())
            self.wfile.flush()
            try:
                while True:
                    with feed.cond:
                        feed.cond.wait_for(lambda: feed.seq != seen, timeout=5)
                        new = feed.seq - seen
                        seen = feed.seq
                        rows = list(feed.history)[-new:] if new else []
                    if not rows:
                        msg = f"event: status\ndata: {json.dumps({'error': feed.error})}\n\n"
                        self.wfile.write(msg.encode())
                    for r in rows:
                        self.wfile.write(f"data: {json.dumps(r)}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

        def _csv(self):
            rows = list(feed.history)
            buf = io.StringIO()
            if rows:
                keys = ["t"] + sorted({k for r in rows for k in r} - {"t", "wall"})
                w = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            name = time.strftime("lr3_%Y%m%d_%H%M%S.csv")
            self._send(buf.getvalue().encode(), "text/csv",
                       extra={"Content-Disposition": f'attachment; filename="{name}"'})

    return Handler


def serve(source_target: str | None = "sim", host: str = "127.0.0.1", port: int = 8333,
          db_path: str | None = None, hz: float = 4.0, mapping: str | None = None) -> None:
    source = open_source(source_target, mapping) if source_target else None
    feed = LiveFeed(source, hz=hz)
    httpd = ThreadingHTTPServer((host, port), make_handler(feed, db_path))
    httpd.daemon_threads = True
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"LR3 dashboards on http://{shown}:{port}/   (source: {source.name if source else 'none'})")
    print("Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        if source:
            source.close()
