from datetime import date

import pytest

from lr3diag import maintenance as m


def by_key(rows):
    return {s.item.key: s for s in rows}


def test_status_states(conn):
    m.log_service(conn, "engine_oil", 100_000, "2026-01-10")
    m.log_service(conn, "cabin_filter", 99_500, "2025-11-01")
    m.log_service(conn, "spark_plugs", 50_000, "2020-01-01")
    m.record_odometer(conn, 107_000, "2026-11-20")
    s = by_key(m.status(conn, today=date(2026, 11, 27)))
    assert s["engine_oil"].state == "DUE SOON"        # 500 mi left
    assert s["cabin_filter"].state == "OVERDUE"        # past 12 months
    assert s["spark_plugs"].state == "OK"              # mileage only, 43k left
    assert s["coolant"].state == "NO RECORD"
    assert s["engine_oil"].due_miles == 107_500


def test_unknown_item_rejected(conn):
    with pytest.raises(KeyError):
        m.log_service(conn, "flux_capacitor", 1)


def test_add_months_end_of_month():
    assert m._add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
