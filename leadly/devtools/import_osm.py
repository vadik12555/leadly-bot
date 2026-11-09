"""Fill the company store from OpenStreetMap regional extracts, without the public Overpass servers.

    python -m leadly.devtools.import_osm data/osm [extra city ...]

Reads every *.osm.pbf in the folder (https://download.geofabrik.de/russia.html). A city is its
bounding box from Nominatim. Safe to run while the bot works. Needs `pip install osmium`.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import defaultdict
from pathlib import Path

import osmium
from osmium.filter import IdFilter, KeyFilter

from leadly import catalog as c
from leadly.config import load_settings
from leadly.db import Database
from leadly.models import SearchQuery
from leadly.providers.search import OSMSearchProvider
from leadly.providers.search.osm import NICHE_ELEMENTS
from leadly.services import DbGeoCache

CONTACT_TAGS = ("phone", "contact:phone", "mobile", "contact:mobile", "email", "contact:email",
                "website", "contact:website", "url", "contact:url")
WANTED = {pair: niche.code for niche in c.NICHES.values() for sub in niche.subcategories for pair in sub.osm}
KEYS = sorted({key for key, _ in WANTED})


def niches_of(tags: dict) -> set[str]:
    return {code for (key, value), code in WANTED.items() if tags.get(key) == value}


def native_path(path: Path) -> str:
    """osmium can't open non-ASCII paths on Windows (C:\\Users\\Агат\\…): use the 8.3 short name."""
    if sys.platform != "win32":
        return str(path)
    import ctypes
    buffer = ctypes.create_unicode_buffer(1024)
    return buffer.value if ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, 1024) else str(path)


def read_file(path: Path) -> list[dict]:
    """Named businesses of the catalog's niches, in the shape Overpass returns them."""
    elements: list[dict] = []
    waiting: dict[int, list[dict]] = defaultdict(list)  # a building gets the position of its first node
    file = f"{native_path(path.parent)}/{path.name}"  # the extension must survive: it tells the format
    source = osmium.FileProcessor(file, osmium.osm.NODE | osmium.osm.WAY).with_filter(KeyFilter(*KEYS))
    for obj in source:
        tags = {t.k: t.v for t in obj.tags}
        if not tags.get("name") or not niches_of(tags):
            continue
        if obj.is_node():
            elements.append({"type": "node", "id": obj.id, "tags": tags,
                             "center": {"lat": obj.location.lat, "lon": obj.location.lon}})
        elif obj.is_way() and len(obj.nodes):
            element = {"type": "way", "id": obj.id, "tags": tags}
            waiting[obj.nodes[0].ref].append(element)
            elements.append(element)
    if waiting:
        for node in osmium.FileProcessor(file, osmium.osm.NODE).with_filter(IdFilter(waiting.keys())):
            for element in waiting[node.id]:
                element["center"] = {"lat": node.location.lat, "lon": node.location.lon}
    return [e for e in elements if "center" in e]


def in_bbox(element: dict, bbox: list[float]) -> bool:
    south, west, north, east = bbox
    return south <= element["center"]["lat"] <= north and west <= element["center"]["lon"] <= east


def split_by_niche(elements: list[dict], bbox: list[float]) -> dict[str, list[dict]]:
    """Companies of one city per niche, the ones with contacts first (a niche is capped)."""
    niches: dict[str, list[dict]] = defaultdict(list)
    for element in elements:
        if in_bbox(element, bbox):
            for code in niches_of(element["tags"]):
                niches[code].append(element)
    has_contact = lambda e: any(e["tags"].get(k) for k in CONTACT_TAGS)  # noqa: E731
    return {code: sorted(items, key=has_contact, reverse=True)[:NICHE_ELEMENTS] for code, items in niches.items()}


async def main(folder: Path, extra_cities: list[str]) -> None:
    logging.basicConfig(level=logging.WARNING)
    sys.stdout.reconfigure(encoding="utf-8")
    settings = load_settings()
    db = Database(settings.db_path)
    await db.connect()
    cache = DbGeoCache(db)
    provider = OSMSearchProvider(settings.overpass_urls, settings.nominatim_url, settings.osm_contact, cache,
                                 company_cache=cache)
    try:
        elements: dict[tuple[str, int], dict] = {}
        for path in sorted(folder.glob("*.osm.pbf")):
            found = await asyncio.to_thread(read_file, path)
            elements.update({(e["type"], e["id"]): e for e in found})  # neighbouring extracts overlap
            print(f"{path.name}: {len(found)} companies", flush=True)
        for city in [*c.POPULAR_CITIES, *extra_cities]:
            geo = await provider.resolve_city(city)
            niches = split_by_niche(list(elements.values()), geo["bbox"])
            for code, items in niches.items():
                query = SearchQuery(code, c.NICHES[code].title, None, city, NICHE_ELEMENTS)
                await cache.set(provider._cache_key(query), {"elements": items})
            total = sum(len(v) for v in niches.values())
            contacts = sum(1 for v in niches.values() for e in v if any(e["tags"].get(k) for k in CONTACT_TAGS))
            print(f"{city}: {total} companies, {contacts} with contacts, niches {len(niches)}/{len(c.NICHES)} | "
                  + ", ".join(f"{code}={len(v)}" for code, v in niches.items()), flush=True)
    finally:
        await provider.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2:]))
