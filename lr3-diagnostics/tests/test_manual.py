from lr3diag import manual


def test_index_and_search(tmp_path, conn):
    (tmp_path / "eas.md").write_text(
        "# Front Air Spring Removal\nRaise the vehicle. Depressurise the air suspension system before removing the spring.\n"
        "# Thermostat Replacement\nDrain the coolant. Remove the thermostat housing bolts.\n")
    (tmp_path / "trans.html").write_text(
        "<h2>Transmission Fluid Level Check</h2><p>Check the ZF 6HP26 fluid level at 40°C.</p><script>x()</script>")
    counts = manual.index_paths(conn, [tmp_path])
    assert sum(counts.values()) == 3

    hits = manual.search(conn, "air spring")
    assert hits[0].title == "Front Air Spring Removal"
    assert manual.search(conn, "thermo")[0].title == "Thermostat Replacement"   # prefix match
    assert manual.search(conn, '"fluid level"')[0].title.startswith("Transmission")
    assert manual.search(conn, "x()") == [] or all("x()" not in h.snippet for h in manual.search(conn, "x"))
    assert manual.search(conn, 'weird " query ) OR') is not None              # never raises

    # re-indexing replaces rather than duplicates
    manual.index_paths(conn, [tmp_path])
    assert sum(r["sections"] for r in manual.stats(conn)) == 3
