"""Workshop-manual indexing and full-text search.

You supply your own copy of the LR3 workshop manual (PDF, HTML or text).
Each PDF page / HTML or Markdown section becomes one searchable record, so
search results point you to the exact page to open.

Supported inputs:
  * ``.pdf``          - one record per page (needs ``pip install pypdf``)
  * ``.html``/``.htm`` - split on <h1>-<h3> headings
  * ``.txt``/``.md``   - split on Markdown headings (``#``) or ALL-CAPS lines
"""

from __future__ import annotations

import html
import re
import sqlite3
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Iterator

SUPPORTED = {".pdf", ".html", ".htm", ".txt", ".md"}


@dataclass
class Section:
    location: str
    title: str
    body: str


@dataclass
class SearchHit:
    source: str
    location: str
    title: str
    snippet: str
    score: float


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------

def _parse_pdf(path: Path) -> Iterator[Section]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError("PDF support needs pypdf:  pip install pypdf") from exc
    reader = PdfReader(str(path))
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        yield Section(location=f"page {i}", title=first_line[:120], body=text)


_HEADING_MD = re.compile(r"^#{1,4}\s+(.*)$")


def _looks_like_caps_heading(line: str) -> bool:
    s = line.strip()
    letters = [c for c in s if c.isalpha()]
    return 4 <= len(s) <= 80 and len(letters) >= 4 and all(c.isupper() for c in letters)


def _parse_text(text: str) -> Iterator[Section]:
    title, buf, n = "Introduction", [], 0
    for line in text.splitlines():
        m = _HEADING_MD.match(line)
        heading = m.group(1).strip() if m else (line.strip() if _looks_like_caps_heading(line) else None)
        if heading:
            if "".join(buf).strip():
                n += 1
                yield Section(location=f"section {n}", title=title, body="\n".join(buf).strip())
            title, buf = heading, []
        else:
            buf.append(line)
    if "".join(buf).strip():
        n += 1
        yield Section(location=f"section {n}", title=title, body="\n".join(buf).strip())


class _HTMLSplitter(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sections: list[tuple[str, list[str]]] = [("Introduction", [])]
        self._in_heading = False
        self._heading: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("h1", "h2", "h3"):
            self._in_heading, self._heading = True, []
        elif tag in ("p", "br", "li", "tr", "div"):
            self.sections[-1][1].append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("h1", "h2", "h3") and self._in_heading:
            self._in_heading = False
            self.sections.append(("".join(self._heading).strip() or "Untitled", []))

    def handle_data(self, data):
        if self._skip:
            return
        (self._heading if self._in_heading else self.sections[-1][1]).append(data)


def _parse_html(text: str) -> Iterator[Section]:
    p = _HTMLSplitter()
    p.feed(text)
    n = 0
    for title, parts in p.sections:
        body = re.sub(r"\n\s*\n+", "\n\n", html.unescape("".join(parts))).strip()
        if body:
            n += 1
            yield Section(location=f"section {n}", title=title, body=body)


def parse_file(path: Path) -> Iterator[Section]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        yield from _parse_pdf(path)
    elif ext in (".html", ".htm"):
        yield from _parse_html(path.read_text(errors="replace"))
    elif ext in (".txt", ".md"):
        yield from _parse_text(path.read_text(errors="replace"))
    else:
        raise ValueError(f"Unsupported file type: {path}")


# --------------------------------------------------------------------------
# Index + search
# --------------------------------------------------------------------------

def _iter_files(paths: Iterable[Path]) -> Iterator[Path]:
    for p in paths:
        if p.is_dir():
            yield from sorted(f for f in p.rglob("*") if f.suffix.lower() in SUPPORTED)
        elif p.suffix.lower() in SUPPORTED:
            yield p


def index_paths(conn: sqlite3.Connection, paths: Iterable[str | Path]) -> dict[str, int]:
    """Index files/folders. Re-indexing a file replaces its old records."""
    counts: dict[str, int] = {}
    for f in _iter_files(Path(p) for p in paths):
        src = str(f.resolve())
        conn.execute("DELETE FROM manual_docs WHERE source = ?", (src,))
        n = 0
        for sec in parse_file(f):
            conn.execute(
                "INSERT INTO manual_docs(source, location, title, body) VALUES (?,?,?,?)",
                (src, sec.location, sec.title, sec.body),
            )
            n += 1
        counts[src] = n
    conn.commit()
    return counts


def _to_fts_query(q: str) -> str:
    """Turn free text into a safe FTS5 query.

    Quoted phrases are kept as phrases; other words are AND-ed together with
    prefix matching (``susp`` matches ``suspension``).
    """
    phrases = re.findall(r'"([^"]+)"', q)
    rest = re.sub(r'"[^"]+"', " ", q)
    words = re.findall(r"[\w]+", rest)
    terms = [f'"{p.strip()}"' for p in phrases if p.strip()]
    terms += [f'"{w}"*' for w in words]
    return " ".join(terms)


def search(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[SearchHit]:
    fts = _to_fts_query(query)
    if not fts:
        return []
    rows = conn.execute(
        """
        SELECT d.source, d.location, d.title,
               snippet(manual_fts, 1, '[', ']', ' ... ', 24) AS snip,
               bm25(manual_fts, 5.0, 1.0) AS score
        FROM manual_fts JOIN manual_docs d ON d.id = manual_fts.rowid
        WHERE manual_fts MATCH ?
        ORDER BY score LIMIT ?
        """,
        (fts, limit),
    ).fetchall()
    return [SearchHit(r["source"], r["location"], r["title"], r["snip"], r["score"]) for r in rows]


def get_section(conn: sqlite3.Connection, source_substr: str, location: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM manual_docs WHERE source LIKE ? AND location = ? LIMIT 1",
        (f"%{source_substr}%", location),
    ).fetchone()


def stats(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT source, COUNT(*) AS sections FROM manual_docs GROUP BY source ORDER BY source"
    ).fetchall()
