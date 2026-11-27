# LR3 Diagnostics

A personal diagnostics toolkit for a **2007 Land Rover LR3** (4.4 V8). It covers:

- **Workshop manual search**: index your own PDF/HTML/text copy of the manual and run full-text search on it.
- **Live data & analysis**: read engine data through an ELM327/OBDLink adapter, log it, and run automatic checks for vacuum leaks, a stuck thermostat, charging faults and misfires.
- **Maintenance tracking**: a schedule, a service log, what's due or overdue, and quick links for parts.
- **Dashboards**: Diagnostics, Maintenance, Off-road and Manual pages in your browser. They run on a laptop or phone in the truck, with no internet needed.

It needs no third-party Python packages except `pypdf` (for PDF manuals) and `pyserial` (for USB/Bluetooth adapters). A built-in **simulator** lets you try everything without the truck.

## Quick start

```bash
cd lr3-diagnostics
pip install -e ".[all]"          # or just run with: python -m lr3diag ...

lr3 dash --source sim             # open http://localhost:8333
lr3 dash --source sim:offroad     # simulated rock crawl: ride height, low range, etc.
```

Simulator scenarios: `healthy`, `vacuum_leak`, `thermostat`, `charging`, `misfire`, `offroad`.
Add `@N` to run N× faster, e.g. `sim:thermostat@20`.

## Dashboards

| Page | What it shows |
|---|---|
| **Diagnostics** | Live tiles and 2-minute trend charts (RPM, coolant, bank 1/2 fuel trim, voltage, MAF, load); automatic checks with links into the manual; read/clear trouble codes; CSV download |
| **Maintenance** | Odometer, overdue/due-soon counts, full schedule with remaining miles and days, service log form, history, parts quick links |
| **Off-road** | Gear, hi/lo range, Terrain Response mode, speed, coolant and transmission temperature, rear locker, a top-down ride-height diagram with articulation, a phone-based pitch/roll meter, 10-minute temperature trends |
| **Manual** | Search the indexed workshop manual and read sections |

To use it from your phone, run `lr3 dash --host 0.0.0.0` on a laptop and open `http://<laptop-ip>:8333` on the phone. The tilt meter needs the page served over HTTPS or opened on `localhost`, because that's a browser requirement for motion sensors.

## Where the data comes from

| Source | `--source` | Engine data | Ride height, gear, range, Terrain Response, trans temp |
|---|---|---|---|
| ELM327 / OBDLink (USB, Bluetooth, WiFi) | `/dev/ttyUSB0`, `/dev/rfcomm0`, `COM3`, `tcp://192.168.0.10:35000` | ✅ live | ❌ (not in generic OBD-II) |
| GAP IIDTool log export | `replay:mylog.csv` (`@N` for speed) | ✅ | ✅ if the log contains them |
| Simulator | `sim[:scenario]` | ✅ | ✅ (simulated) |

**About the GAP IIDTool:** it talks to the Land Rover modules (air suspension, transmission, transfer box) that generic OBD-II can't reach. GAP doesn't publish a live-streaming API, so this tool reads **live-data logs exported from the IIDControl app**. The GAP format is detected automatically: each channel has its own timestamps, and they're merged onto a common 0.5 s timeline.

```bash
lr3 analyze IIDTool_Log_fuel_trim_reset.csv        # fuel-trim breakdown + automatic checks
lr3 dash --source replay:IIDTool_Log_fuel_trim_reset.csv@5
```

Channel names confirmed from a real IIDControl 4.3 export (throttle, gear, short/long fuel trims, vehicle speed) are already mapped. If a log has channels the tool doesn't know yet, `lr3 analyze` lists them under "not mapped". Add them to your own copy of `gap_mapping.json` (`~/.lr3diag/gap_mapping.json`), with `scale`/`offset` if the units aren't metric.

**Tip:** for the most useful fuel-trim logs, also record engine speed, coolant temperature, MAF and calculated load in the GAP app. Then the tool can confirm the engine was warm and compare airflow with expected values.

For live engine data while driving, an ELM327 or OBDLink adapter alongside the GAP tool works well.

## Command line

```bash
# Workshop manual
lr3 manual index ~/Documents/LR3_Workshop_Manual/      # PDFs, HTML, txt, md
lr3 manual search front air spring removal
lr3 manual search '"torque converter"' thermostat
lr3 manual show Workshop_Manual.pdf "page 412"

# Maintenance
lr3 maint odo 142300
lr3 maint log engine_oil 142300 --cost 89.50 --notes "OEM filter"
lr3 maint status
lr3 maint items          # item keys
lr3 parts --links

# OBD
lr3 obd scan --port /dev/rfcomm0          # VIN + stored/pending codes
lr3 obd live --port /dev/rfcomm0
lr3 obd log  --port /dev/rfcomm0 --duration 900
lr3 analyze logs/lr3_20261127_101500.csv
lr3 dtc P0171 P0174
```

All data is stored in `~/.lr3diag/lr3.db`; set `LR3_DB` to use another file. Back that file up.

## Customising

Copy any of these from `lr3diag/data/` to `~/.lr3diag/` and edit the copy:

- `schedule.json`: maintenance items and intervals. The defaults are conservative community values, so **check them against your owner's handbook**.
- `parts.json`: parts and vendor links. Add **confirmed** part numbers and your favourite shops.
- `gap_mapping.json`: column mapping for GAP log imports.

The automatic checks live in `lr3diag/analysis.py`. Each check is a short function you can add to or tune. Record a log while the truck is healthy and use it as your baseline.

## Tests

```bash
python -m pytest
```

The tests run every simulator scenario through the full adapter → parser → analysis path, plus the manual indexing, maintenance and web API code.

## Layout

```
lr3diag/
  manual.py        workshop-manual indexing (SQLite FTS5) and search
  maintenance.py   schedule, service log, due/overdue status
  parts.py         part quick links
  analysis.py      automatic checks on logged data
  dtc.py           trouble-code lookup (data/dtc.json)
  sources.py       live data sources: adapter, simulator, log replay
  obd/             ELM327 driver, PID decoders, logger, simulator
  web/             dashboard server + pages (standard library only)
  cli.py           `lr3` command
```
