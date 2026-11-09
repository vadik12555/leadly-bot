"""Background collection of companies, so that paying users never depend on overloaded public servers.

One request at a time with a pause, the way the Overpass usage policy asks. A busy server only
postpones a niche until the next pass; searches read what is already stored.
"""

from __future__ import annotations

import asyncio
import logging

from leadly import catalog as c
from leadly.models import SearchQuery
from leadly.providers.search import OSMSearchProvider, SearchProviderError
from leadly.providers.search.osm import is_fresh

log = logging.getLogger(__name__)

REFRESH_AFTER_DAYS = 3
PAUSE = 60.0  # seconds between requests
RETRY_PASS_AFTER = 300.0  # something failed: walk the list again soon
IDLE_PASS_AFTER = 3600.0  # everything is fresh


def targets() -> list[SearchQuery]:
    """Every niche in one city after another would leave other cities empty for hours: go niche by niche."""
    return [SearchQuery(niche.code, niche.title, None, city, 1000)
            for niche in c.NICHES.values() for city in c.POPULAR_CITIES]


async def collect_once(provider: OSMSearchProvider, pause: float = PAUSE) -> tuple[int, int]:
    """One pass over everything that is missing or old. Returns (stored, failed)."""
    stored, failed = 0, 0
    for query in targets():
        existing = await provider.stored(query)
        if existing and is_fresh(existing[1], REFRESH_AFTER_DAYS):
            continue
        try:
            count = await provider.refresh(query)
            stored += 1
            log.info("collected %s · %s: %s objects", query.city, query.niche_title, count)
        except SearchProviderError as e:
            failed += 1
            log.info("not collected %s · %s: %s", query.city, query.niche_title, e.user_message)
        await asyncio.sleep(pause)
    return stored, failed


async def collect_companies(sources) -> None:
    """Runs for the bot's whole life. Does nothing unless OpenStreetMap is a configured source."""
    provider = next((p for p in sources.providers if isinstance(p, OSMSearchProvider) and p.company_cache), None)
    if not provider:
        return
    while True:
        try:
            _, failed = await collect_once(provider)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("company collection failed")
            failed = 1
        await asyncio.sleep(RETRY_PASS_AFTER if failed else IDLE_PASS_AFTER)
