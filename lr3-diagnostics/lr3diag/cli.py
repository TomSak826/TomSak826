"""Command-line interface:  lr3 <command> ...   (or  python -m lr3diag ...)"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from . import analysis, db, dtc, maintenance, manual, parts


def _conn(args):
    return db.connect(args.db)


# -- manual -------------------------------------------------------------------
def cmd_manual_index(args):
    counts = manual.index_paths(_conn(args), args.paths)
    if not counts:
        print("No supported files found (.pdf .html .htm .txt .md).")
    for src, n in counts.items():
        print(f"{n:6d} sections  {src}")


def cmd_manual_search(args):
    hits = manual.search(_conn(args), " ".join(args.query), args.limit)
    if not hits:
        print("No matches. Have you indexed the manual?  lr3 manual index <path>")
    for i, h in enumerate(hits, 1):
        print(f"{i:2d}. {h.title}  [{h.location}]  {h.source}")
        print(f"    {' '.join(h.snippet.split())}\n")


def cmd_manual_show(args):
    row = manual.get_section(_conn(args), args.source, args.location)
    if not row:
        sys.exit("Not found.")
    print(f"{row['title']}  [{row['location']}]  {row['source']}\n")
    print(row["body"])


def cmd_manual_stats(args):
    rows = manual.stats(_conn(args))
    if not rows:
        print("Nothing indexed yet.")
    for r in rows:
        print(f"{r['sections']:6d}  {r['source']}")


# -- maintenance ----------------------------------------------------------------
def cmd_maint_status(args):
    conn = _conn(args)
    odo = args.odometer if args.odometer is not None else maintenance.current_odometer(conn)
    print(f"Odometer: {odo:,} mi\n" if odo else "Odometer: unknown (lr3 maint odo <miles>)\n")
    for s in maintenance.status(conn, odometer=odo):
        due = []
        if s.miles_left is not None:
            due.append(f"{s.miles_left:+,} mi")
        if s.days_left is not None:
            due.append(f"{s.days_left:+} days")
        last = f"last {s.last_date} @ {s.last_odometer:,} mi" if s.last_date else "never logged"
        print(f"  {s.state:<9}  {s.item.name:<42} {'  '.join(due):<22} {last}")


def cmd_maint_log(args):
    maintenance.log_service(_conn(args), args.item, args.odometer, args.date, args.notes or "", args.cost)
    print("Logged.")


def cmd_maint_odo(args):
    maintenance.record_odometer(_conn(args), args.miles, args.date)
    print(f"Odometer set to {args.miles:,} mi.")


def cmd_maint_items(args):
    for i in maintenance.load_schedule():
        iv = " / ".join(x for x in [f"{i.miles:,} mi" if i.miles else "", f"{i.months} mo" if i.months else ""] if x)
        print(f"  {i.key:<18} {i.name:<42} {iv}")


def cmd_maint_history(args):
    for r in maintenance.history(_conn(args), args.item):
        cost = f"${r['cost']:.2f}" if r["cost"] is not None else ""
        print(f"  {r['date']}  {r['odometer']:>8,} mi  {r['item']:<18} {cost:>9}  {r['notes']}")


def cmd_parts(args):
    for p in parts.with_links(parts.load_parts()):
        print(f"  {p['name']:<36} {p['part_number'] or '(part # not set)'}")
        if args.links:
            for link in p["links"]:
                print(f"      {link['name']}: {link['url']}")


# -- OBD ------------------------------------------------------------------------
def _elm(args):
    from .obd import open_connection
    return open_connection(args.port)


def cmd_obd_scan(args):
    with _elm(args) as elm:
        print(f"Adapter: {elm.version}   Protocol: {elm.protocol}")
        vin = elm.read_vin()
        if vin:
            print(f"VIN: {vin}")
        for label, pending in (("Stored", False), ("Pending", True)):
            codes = elm.read_dtcs(pending=pending)
            print(f"\n{label} codes: {len(codes)}")
            for c in codes:
                info = dtc.lookup(c)
                print(f"  {c}  {info['description']}")
                if info["lr3_notes"]:
                    print(f"         {info['lr3_notes']}")


def cmd_obd_clear(args):
    if not args.yes:
        ans = input("Clearing codes also resets the emissions readiness monitors. Continue? [y/N] ")
        if ans.strip().lower() != "y":
            return
    with _elm(args) as elm:
        elm.clear_dtcs()
    print("Codes cleared.")


def cmd_obd_live(args):
    from .obd.logger import sample_stream
    from .obd.pids import PIDS
    pids = args.pids.split(",") if args.pids else ["rpm", "speed", "coolant", "stft1", "ltft1", "stft2", "ltft2", "voltage"]
    with _elm(args) as elm:
        try:
            for row in sample_stream(elm, pids, interval=args.interval):
                parts_ = [f"{k}={v:g}{PIDS[k].unit}" for k, v in row.items() if k != "t" and v is not None]
                print(f"\r{row['t']:7.1f}s  " + "  ".join(parts_) + "   ", end="", flush=True)
        except KeyboardInterrupt:
            print()


def cmd_obd_log(args):
    from .obd.logger import log_to_csv
    from .obd.pids import DEFAULT_LOG_PIDS
    out = args.out or datetime.now().strftime("logs/lr3_%Y%m%d_%H%M%S.csv")
    pids = args.pids.split(",") if args.pids else DEFAULT_LOG_PIDS
    with _elm(args) as elm:
        print(f"Logging {len(pids)} PIDs to {out}. Ctrl+C to stop.")
        t0 = time.monotonic()
        try:
            n = log_to_csv(elm, out, pids, interval=args.interval, duration=args.duration)
        except KeyboardInterrupt:
            n = None
    print(f"\nSaved {out}" + (f" ({n} samples, {time.monotonic() - t0:.0f}s)" if n else ""))


def cmd_obd_pids(args):
    from .obd.pids import PIDS
    for p in PIDS.values():
        print(f"  {p.name:<9} 0x{p.pid:02X}  {p.description} ({p.unit})")


# -- analysis / dtc / dashboard ---------------------------------------------------
def cmd_analyze(args):
    import json
    from .sources import is_gap_log, read_gap_log, read_log
    mapping = json.load(open(args.mapping)) if args.mapping else None
    if is_gap_log(args.file):
        rows, info = read_gap_log(args.file, mapping)
        print("GAP IIDTool log: " + " | ".join(info["header"]))
        if info["unmapped"]:
            print("Channels not mapped (add them to gap_mapping.json): " + ", ".join(info["unmapped"]))
    else:
        rows = read_log(args.file, mapping)
    if not rows:
        sys.exit("No rows in file.")
    print(f"{len(rows)} samples, {rows[-1].get('t', 0) / 60:.1f} min\n")
    print(f"  {'channel':<12}{'min':>10}{'mean':>10}{'max':>10}")
    for k, s in analysis.summarize(rows).items():
        if k != "t":
            print(f"  {k:<12}{s['min']:>10.2f}{s['mean']:>10.2f}{s['max']:>10.2f}")
    missing = [c for c in ("rpm", "coolant", "maf", "load", "voltage") if not any(r.get(c) is not None for r in rows)]
    if missing:
        print(f"\n  Not in this log: {', '.join(missing)}. Some checks are skipped or less certain.")

    if any(r.get("stft1") is not None for r in rows):
        print("\nFuel trim by driving condition (average %, total = short + long term)")
        print(f"  {'condition':<22}{'samples':>8}   {'B1 short':>8}{'long':>7}{'total':>7}   {'B2 short':>8}{'long':>7}{'total':>7}")
        for b in analysis.trim_breakdown(rows):
            def cell(k):
                return f"{b[k]:+.1f}" if k in b else "—"
            print(f"  {b['band']:<22}{b['n']:>8}   {cell('stft1'):>8}{cell('ltft1'):>7}{cell('total1'):>7}"
                  f"   {cell('stft2'):>8}{cell('ltft2'):>7}{cell('total2'):>7}")

    findings = analysis.analyze(rows)
    print(f"\nFindings: {len(findings) or 'none'}")
    for f in findings:
        print(f"\n  [{f.severity}] {f.title}\n    {f.detail}")
        if f.manual_query:
            print(f"    Manual: lr3 manual search {f.manual_query}")


def cmd_dtc(args):
    for code in args.codes:
        info = dtc.lookup(code)
        print(f"{info['code']}  {info['description']}")
        print(f"  {info['type']} · {info['system']}" + (f" · {info['subsystem']}" if info["subsystem"] else ""))
        if info["lr3_notes"]:
            print(f"  LR3: {info['lr3_notes']}")
        print(f"  Manual: lr3 manual search {info['manual_query']}\n")


def cmd_dash(args):
    from .web.server import serve
    serve(args.source if args.source != "none" else None, args.host, args.port, args.db, args.hz, args.mapping)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lr3", description="2007 Land Rover LR3 diagnostics toolkit")
    p.add_argument("--db", help="database file (default ~/.lr3diag/lr3.db or $LR3_DB)")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("manual", help="index and search the workshop manual").add_subparsers(dest="sub", required=True)
    x = m.add_parser("index", help="index PDF/HTML/text files or folders"); x.add_argument("paths", nargs="+"); x.set_defaults(fn=cmd_manual_index)
    x = m.add_parser("search", help="full-text search"); x.add_argument("query", nargs="+"); x.add_argument("-n", "--limit", type=int, default=10); x.set_defaults(fn=cmd_manual_search)
    x = m.add_parser("show", help="print one section"); x.add_argument("source", help="part of the file name"); x.add_argument("location", help='e.g. "page 12"'); x.set_defaults(fn=cmd_manual_show)
    x = m.add_parser("stats", help="what's indexed"); x.set_defaults(fn=cmd_manual_stats)

    m = sub.add_parser("maint", help="maintenance tracking").add_subparsers(dest="sub", required=True)
    x = m.add_parser("status", help="what's due"); x.add_argument("--odometer", type=int); x.set_defaults(fn=cmd_maint_status)
    x = m.add_parser("log", help="record a service"); x.add_argument("item"); x.add_argument("odometer", type=int)
    x.add_argument("--date"); x.add_argument("--notes"); x.add_argument("--cost", type=float); x.set_defaults(fn=cmd_maint_log)
    x = m.add_parser("odo", help="record current odometer"); x.add_argument("miles", type=int); x.add_argument("--date"); x.set_defaults(fn=cmd_maint_odo)
    x = m.add_parser("items", help="list schedule items"); x.set_defaults(fn=cmd_maint_items)
    x = m.add_parser("history", help="service history"); x.add_argument("item", nargs="?"); x.set_defaults(fn=cmd_maint_history)

    x = sub.add_parser("parts", help="maintenance parts & quick links"); x.add_argument("--links", action="store_true"); x.set_defaults(fn=cmd_parts)

    o = sub.add_parser("obd", help="talk to the truck through an ELM327 adapter").add_subparsers(dest="sub", required=True)
    for name, fn, hlp in (("scan", cmd_obd_scan, "read VIN and trouble codes"), ("clear", cmd_obd_clear, "clear trouble codes"),
                          ("live", cmd_obd_live, "show live data"), ("log", cmd_obd_log, "record live data to CSV"),
                          ("pids", cmd_obd_pids, "list known PIDs")):
        x = o.add_parser(name, help=hlp)
        x.add_argument("--port", default="sim", help="/dev/ttyUSB0, COM3, tcp://192.168.0.10:35000, or sim[:scenario]")
        x.set_defaults(fn=fn)
        if name in ("live", "log"):
            x.add_argument("--pids", help="comma-separated, e.g. rpm,coolant,stft1")
            x.add_argument("--interval", type=float, default=0.0, help="seconds between samples")
        if name == "log":
            x.add_argument("--out"); x.add_argument("--duration", type=float, help="seconds")
        if name == "clear":
            x.add_argument("-y", "--yes", action="store_true")

    x = sub.add_parser("analyze", help="analyse a recorded CSV log"); x.add_argument("file"); x.add_argument("--mapping", help="column mapping JSON for third-party logs"); x.set_defaults(fn=cmd_analyze)
    x = sub.add_parser("dtc", help="look up trouble codes"); x.add_argument("codes", nargs="+"); x.set_defaults(fn=cmd_dtc)

    x = sub.add_parser("dash", help="start the web dashboards")
    x.add_argument("--source", default="sim", help="sim[:scenario], adapter port, tcp://host:port, replay:log.csv[@speed], or none")
    x.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to allow phones on the same network")
    x.add_argument("--port", type=int, default=8333)
    x.add_argument("--hz", type=float, default=4.0, help="samples per second")
    x.add_argument("--mapping", help="column mapping JSON for replay logs")
    x.set_defaults(fn=cmd_dash)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except (KeyError, ValueError, RuntimeError) as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()
