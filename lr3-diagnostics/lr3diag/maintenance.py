"""Maintenance schedule tracking.

Status is computed from the most recent ``service_log`` entry for each item
and the latest odometer reading. Intervals come from ``data/schedule.json``,
or ``~/.lr3diag/schedule.json`` if you've made your own copy.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from importlib import resources
from pathlib import Path

SOON_MILES = 1000
SOON_DAYS = 30


@dataclass
class Item:
    key: str
    name: str
    miles: int | None
    months: int | None
    kind: str
    notes: str = ""


@dataclass
class Status:
    item: Item
    last_date: str | None
    last_odometer: int | None
    due_miles: int | None
    due_date: str | None
    miles_left: int | None
    days_left: int | None
    state: str  # OVERDUE | DUE SOON | OK | NO RECORD


def user_schedule_path() -> Path:
    return Path.home() / ".lr3diag" / "schedule.json"


def load_schedule(path: str | Path | None = None) -> list[Item]:
    if path:
        raw = json.loads(Path(path).read_text())
    elif user_schedule_path().exists():
        raw = json.loads(user_schedule_path().read_text())
    else:
        raw = json.loads(resources.files("lr3diag").joinpath("data/schedule.json").read_text())
    return [Item(**{k: v for k, v in it.items() if k in Item.__dataclass_fields__}) for it in raw["items"]]


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError("unreachable")


def record_odometer(conn: sqlite3.Connection, miles: int, when: str | None = None) -> None:
    conn.execute("INSERT INTO odometer(date, miles) VALUES (?, ?)", (when or date.today().isoformat(), miles))
    conn.commit()


def current_odometer(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT MAX(m) FROM (SELECT MAX(miles) AS m FROM odometer UNION ALL SELECT MAX(odometer) FROM service_log)"
    ).fetchone()
    return row[0]


def log_service(conn: sqlite3.Connection, item_key: str, odometer: int, when: str | None = None,
                notes: str = "", cost: float | None = None, schedule: list[Item] | None = None) -> None:
    schedule = schedule or load_schedule()
    keys = {i.key for i in schedule}
    if item_key not in keys:
        raise KeyError(f"Unknown item '{item_key}'. Known: {', '.join(sorted(keys))}")
    conn.execute(
        "INSERT INTO service_log(item, date, odometer, notes, cost) VALUES (?,?,?,?,?)",
        (item_key, when or date.today().isoformat(), odometer, notes, cost),
    )
    conn.commit()


def history(conn: sqlite3.Connection, item_key: str | None = None) -> list[sqlite3.Row]:
    if item_key:
        return conn.execute("SELECT * FROM service_log WHERE item=? ORDER BY date DESC, odometer DESC", (item_key,)).fetchall()
    return conn.execute("SELECT * FROM service_log ORDER BY date DESC, odometer DESC").fetchall()


def status(conn: sqlite3.Connection, today: date | None = None, odometer: int | None = None,
           schedule: list[Item] | None = None) -> list[Status]:
    today = today or date.today()
    odo = odometer if odometer is not None else current_odometer(conn)
    out: list[Status] = []
    for item in schedule or load_schedule():
        last = conn.execute(
            "SELECT date, odometer FROM service_log WHERE item=? ORDER BY date DESC, odometer DESC LIMIT 1",
            (item.key,),
        ).fetchone()
        if not last:
            out.append(Status(item, None, None, None, None, None, None, "NO RECORD"))
            continue
        due_miles = last["odometer"] + item.miles if item.miles else None
        due_dt = _add_months(date.fromisoformat(last["date"]), item.months) if item.months else None
        miles_left = due_miles - odo if (due_miles is not None and odo is not None) else None
        days_left = (due_dt - today).days if due_dt else None

        if (miles_left is not None and miles_left <= 0) or (days_left is not None and days_left <= 0):
            state = "OVERDUE"
        elif (miles_left is not None and miles_left <= SOON_MILES) or (days_left is not None and days_left <= SOON_DAYS):
            state = "DUE SOON"
        else:
            state = "OK"
        out.append(Status(item, last["date"], last["odometer"], due_miles,
                          due_dt.isoformat() if due_dt else None, miles_left, days_left, state))
    order = {"OVERDUE": 0, "DUE SOON": 1, "NO RECORD": 2, "OK": 3}
    out.sort(key=lambda s: (order[s.state], s.miles_left if s.miles_left is not None else 10**9))
    return out
