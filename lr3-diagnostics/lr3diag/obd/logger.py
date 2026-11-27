"""Record live OBD data to CSV for later analysis."""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Callable, Iterator

from .elm327 import ELM327
from .pids import PIDS


def sample_stream(elm: ELM327, pids: list[str], interval: float = 0.0,
                  duration: float | None = None, samples: int | None = None) -> Iterator[dict]:
    """Yield one dict per sample: {'t': seconds, pid: value, ...}."""
    supported = elm.supported_pids()
    usable = [p for p in pids if PIDS[p].pid in supported]
    skipped = sorted(set(pids) - set(usable))
    if skipped:
        print(f"Not supported by this vehicle/adapter, skipping: {', '.join(skipped)}")
    elapsed: Callable[[], float] = getattr(elm.t, "elapsed", None) or (lambda s=time.monotonic(): time.monotonic() - s)
    n = 0
    while True:
        t = elapsed()
        if (duration is not None and t >= duration) or (samples is not None and n >= samples):
            return
        row = {"t": round(t, 2)}
        for p in usable:
            v = elm.read(p)
            row[p] = None if v is None else round(v, 3)
        yield row
        n += 1
        if interval:
            time.sleep(interval)


def log_to_csv(elm: ELM327, out: str | Path, pids: list[str], **kw) -> int:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", newline="") as f:
        writer = None
        for row in sample_stream(elm, pids, **kw):
            if writer is None:
                writer = csv.DictWriter(f, fieldnames=list(row))
                writer.writeheader()
            writer.writerow(row)
            n += 1
            if n % 20 == 0:
                f.flush()
    return n
