"""Maintenance-part quick links."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from urllib.parse import quote_plus


def load_parts(path: str | Path | None = None) -> dict:
    user = Path.home() / ".lr3diag" / "parts.json"
    if path:
        raw = Path(path).read_text()
    elif user.exists():
        raw = user.read_text()
    else:
        raw = resources.files("lr3diag").joinpath("data/parts.json").read_text()
    return json.loads(raw)


def with_links(data: dict) -> list[dict]:
    """Return parts with generated vendor search links added to their own links."""
    out = []
    for p in data["parts"]:
        term = p["part_number"] or f'{data.get("vehicle_query", "")} {p["query"]}'.strip()
        if p["part_number"]:
            term = f'{p["part_number"]} {data.get("vehicle_query", "")}'.strip()
        links = list(p.get("links", []))
        links += [{"name": v["name"], "url": v["url"].replace("{q}", quote_plus(term))} for v in data.get("vendors", [])]
        out.append({**p, "links": links, "search_term": term})
    return out
