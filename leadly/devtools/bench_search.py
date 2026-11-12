"""Search speed benchmark with real data sources (no Telegram, no demo).

    python -m leadly.devtools.bench_search --db data/bench.db [--json out.json]

Runs the same 6 searches a user would make («Разработка сайта» × 3 niches × Москва/Казань) through the
real SearchService and prints seconds per stage: city geocoding (Nominatim), companies from the source
(stored base or Overpass), site checks / contacts from sites, ranking. Each search uses a new user on the
free plan. Point --db at a copy of a database: the run writes searches, site checks and caches into it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path

from leadly import catalog as c
from leadly.config import load_settings
from leadly.db import Database
from leadly.services import build_services
from leadly.services import enrich as enrich_mod
from leadly.services.search import SearchRequest
from leadly.utils import now_iso

NICHES = ("beauty", "dental", "auto")
CITIES = ("Москва", "Казань")
SERVICE = "site"


class Stopwatch:
    def __init__(self):
        self.stages: dict[str, float] = {}
        self.calls: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str):
        t = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + time.perf_counter() - t

    def count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1


def instrument(svc, sw_ref: list) -> None:
    """Wrap the stages of the real code with timers (no behaviour change)."""
    from leadly.providers.search import OSMSearchProvider
    for p in svc.search_sources.providers:
        if not isinstance(p, OSMSearchProvider):
            continue
        for name, stage in (("resolve_city", "geocode"), ("_overpass", "overpass"), ("stored", "base")):
            orig = getattr(p, name)

            async def wrapped(*a, _orig=orig, _stage=stage, **kw):
                with sw_ref[0].stage(_stage):
                    return await _orig(*a, **kw)
            setattr(p, name, wrapped)
        orig_get = p._get_json

        async def get_json(*a, _orig=orig_get, **kw):
            sw_ref[0].count("nominatim")
            return await _orig(*a, **kw)
        p._get_json = get_json
        orig_post = p._post_json

        async def post_json(*a, _orig=orig_post, **kw):
            sw_ref[0].count("overpass_http")
            return await _orig(*a, **kw)
        p._post_json = post_json
    for name, stage in (("enrich_contacts", "sites"), ("check_sites", "sites"), ("score_companies", "rank")):
        orig = getattr(enrich_mod, name)

        async def wrapped(*a, _orig=orig, _stage=stage, **kw):
            with sw_ref[0].stage(_stage):
                return await _orig(*a, **kw)
        setattr(enrich_mod, name, wrapped)


async def delivered(svc, search_id: int) -> tuple[int, int, int]:
    """(companies shown, with phone or website, with any contact)."""
    rows = await svc.db.fetchall("SELECT company FROM search_results WHERE search_id = ?", search_id)
    cos = [json.loads(r["company"]) for r in rows]
    reach = sum(1 for co in cos if co.get("phone") or co.get("website"))
    contacts = sum(1 for co in cos if co.get("phone") or co.get("email") or co.get("socials"))
    return len(cos), reach, contacts


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--json")
    ap.add_argument("--repeat", action="store_true", help="run every search twice (second time from caches)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    settings = load_settings(db_path=Path(args.db), log_file=None)
    if "demo" in settings.search_providers:
        raise SystemExit("demo source configured: the benchmark needs real sources")
    db = Database(settings.db_path)
    await db.connect()
    svc = build_services(settings, db)
    sw_ref = [Stopwatch()]
    instrument(svc, sw_ref)
    service = c.SERVICES[SERVICE]
    rows = []
    uid = 9_000_000_000 + int(time.time()) % 1_000_000 * 100
    plan = [(n, city) for city in CITIES for n in NICHES]
    if args.repeat:
        plan = [x for x in plan for _ in range(2)]
    for niche_code, city in plan:
        uid += 1
        user, _ = await db.upsert_user(uid, None, "bench", now_iso())
        sw = sw_ref[0] = Stopwatch()
        req = SearchRequest(service=service, niche=c.NICHES[niche_code], subcategory=None, cities=[city])
        t = time.perf_counter()
        out = await svc.search.run(user, f"bench{uid}", req)
        first = time.perf_counter() - t
        bg = 0.0
        pending = getattr(svc.search, "pending", None)
        if pending:
            t2 = time.perf_counter()
            await asyncio.gather(*list(pending), return_exceptions=True)
            bg = time.perf_counter() - t2
        shown = reach = contacts = 0
        if out.search_id and not out.error:
            shown, reach, contacts = await delivered(svc, out.search_id)
        row = {"niche": c.NICHES[niche_code].title, "city": city, "first_results_s": round(first, 2),
               "background_s": round(bg, 2), **{k: round(v, 2) for k, v in sw.stages.items()},
               "nominatim_calls": sw.calls.get("nominatim", 0), "overpass_calls": sw.calls.get("overpass_http", 0),
               "shown": shown, "with_phone_or_site": reach, "with_contacts": contacts,
               "error": out.error or ""}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    await svc.close()
    await db.close()
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
