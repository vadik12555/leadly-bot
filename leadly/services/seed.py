"""Company base shipped with the code (seed/companies.json.gz).

data/ is not in git, so a server deployed from GitHub starts with an empty database and every search
would wait for the public Overpass servers (often overloaded: then users get nothing at all).
The seed holds the stored companies and city geocodes (table geo_cache only — no user data) and is
loaded on every start: rows the server doesn't have, or has in an older copy, are written.

Refresh the seed after importing new OSM data:
    python -m leadly.services.seed export            # data/leadly.db -> seed/companies.json.gz
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import sqlite3
import sys
from pathlib import Path

from leadly.config import BASE_DIR
from leadly.db import Database

log = logging.getLogger(__name__)

SEED_PATH = BASE_DIR / "seed" / "companies.json.gz"


def _read(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


async def load_seed(db: Database, path: Path = SEED_PATH) -> int:
    """Write seed rows that are missing or newer than the stored ones. Returns how many were written."""
    if not path.exists():
        return 0
    try:
        rows = await asyncio.to_thread(_read, path)
    except Exception:
        log.exception("company seed %s is unreadable", path)
        return 0
    stored = {r["query"]: r["created_at"] for r in await db.fetchall("SELECT query, created_at FROM geo_cache")}
    fresh = [r for r in rows if r["q"] not in stored or r["t"] > stored[r["q"]]]
    if fresh:
        await db.executemany("INSERT OR REPLACE INTO geo_cache (query, data, created_at) VALUES (?, ?, ?)",
                             [(r["q"], json.dumps(r["d"], ensure_ascii=False), r["t"]) for r in fresh])
    companies = sum(1 for r in fresh if r["q"].startswith("companies:"))
    log.info("company seed: %s rows written (%s niches with companies), %s already up to date",
             len(fresh), companies, len(rows) - len(fresh))
    return len(fresh)


def export(db_path: Path, out: Path = SEED_PATH) -> int:
    """geo_cache of a database -> gzip JSON. Only stored companies and city geocodes, never user data."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = [{"q": q, "d": json.loads(d), "t": t}
                for q, d, t in db.execute("SELECT query, data, created_at FROM geo_cache ORDER BY query")]
    finally:
        db.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    return len(rows)


if __name__ == "__main__":
    if sys.argv[1:2] != ["export"]:
        raise SystemExit("usage: python -m leadly.services.seed export [path/to/leadly.db]")
    source = Path(sys.argv[2]) if len(sys.argv) > 2 else BASE_DIR / "data" / "leadly.db"
    print(f"{export(source)} rows -> {SEED_PATH}")
