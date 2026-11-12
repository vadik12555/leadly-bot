"""Stored companies: a paying user gets results even while the public servers are down."""

from datetime import datetime, timedelta, timezone

import pytest

from leadly.models import SearchQuery
from leadly.providers.search import OSMSearchProvider, SearchProviderError
from leadly.services import DbGeoCache
from leadly.services import collector
from leadly.utils import now_iso


class Store:
    def __init__(self):
        self.items: dict[str, tuple[dict, str]] = {}

    async def stamped(self, key):
        return self.items.get(key)

    async def set(self, key, value):
        self.items[key] = (value, now_iso())


def make(api, store) -> OSMSearchProvider:
    provider = OSMSearchProvider([f"{api.base}/overpass"], f"{api.base}/nominatim", company_cache=store)
    provider.nominatim_pause = provider.overpass_pause = 0
    return provider


def niche(city="Казань") -> SearchQuery:
    return SearchQuery("beauty", "Салоны красоты", None, city, 100)


def overpass_calls(api) -> int:
    return sum(1 for call in api.calls if call.startswith("overpass:"))


async def test_stored_niche_is_served_without_touching_the_servers(fake_api):
    provider = make(fake_api, Store())
    assert await provider.refresh(niche()) == 5
    calls = overpass_calls(fake_api)
    fake_api.overpass_status = 504  # the servers die
    found = await provider.search(niche())
    assert {c.name for c in found} == {"Студия «Лотос»", "Парикмахерская Стиль", "Без контактов", "Массаж Плюс"}
    assert overpass_calls(fake_api) == calls
    await provider.close()


async def test_subcategory_is_filtered_from_the_stored_niche(fake_api):
    provider = make(fake_api, Store())
    await provider.refresh(niche())
    fake_api.overpass_status = 504
    found = await provider.search(SearchQuery("beauty", "Салоны красоты", "hair", "Казань", 100))
    assert [c.name for c in found] == ["Парикмахерская Стиль"]
    await provider.close()


async def test_old_copy_beats_an_error_and_shows_its_date(fake_api):
    store = Store()
    provider = make(fake_api, store)
    await provider.refresh(niche())
    key, (data, _) = next(iter(store.items.items()))
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    store.items[key] = (data, old)
    fake_api.overpass_status = 504
    found = await provider.search(niche())
    assert found and found[0].fetched_at == old
    await provider.close()


async def test_old_copy_is_served_at_once_and_replaced_in_the_background(fake_api):
    import asyncio
    store = Store()
    provider = make(fake_api, store)
    await provider.refresh(niche())
    key, (data, _) = next(iter(store.items.items()))
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    store.items[key] = (data, old)
    calls = overpass_calls(fake_api)
    found = await provider.search(niche())
    assert found and found[0].fetched_at == old  # the user doesn't wait for the servers
    await provider.search(niche())  # a second search while refreshing: still one refresh
    await asyncio.gather(*provider._tasks)
    assert overpass_calls(fake_api) == calls + 1
    assert datetime.now(timezone.utc) - datetime.fromisoformat(store.items[key][1]) < timedelta(minutes=1)
    found = await provider.search(niche())
    assert found[0].fetched_at != old  # the next search gets the refreshed copy
    await provider.close()


async def test_very_old_copy_waits_for_the_servers(fake_api):
    store = Store()
    provider = make(fake_api, store)
    await provider.refresh(niche())
    key, (data, _) = next(iter(store.items.items()))
    store.items[key] = (data, (datetime.now(timezone.utc) - timedelta(days=200)).isoformat())
    found = await provider.search(niche())
    assert datetime.now(timezone.utc) - datetime.fromisoformat(found[0].fetched_at) < timedelta(minutes=1)
    await provider.close()


async def test_subcategory_answer_is_stored_and_reused(fake_api):
    store = Store()
    provider = make(fake_api, store)
    hair = SearchQuery("beauty", "Салоны красоты", "hair", "Казань", 100)
    assert [c.name for c in await provider.search(hair)] == ["Парикмахерская Стиль"]
    calls = overpass_calls(fake_api)
    fake_api.overpass_status = 504
    assert [c.name for c in await provider.search(hair)] == ["Парикмахерская Стиль"]  # from the store
    assert overpass_calls(fake_api) == calls
    await provider.close()


async def test_city_is_geocoded_once(fake_api):
    provider = make(fake_api, Store())
    await provider.search(niche())
    await provider.search(SearchQuery("dental", "Стоматологии", None, "Казань", 100))
    assert sum(1 for call in fake_api.calls if call.startswith("nominatim")) == 1
    await provider.close()


async def test_mirrors_are_hedged_first_answer_wins(fake_api, monkeypatch):
    """A silent mirror doesn't hold the user: the next one is asked after HEDGE_AFTER seconds."""
    import asyncio
    import time
    from leadly.providers.search import osm
    monkeypatch.setattr(osm, "HEDGE_AFTER", 0.2)
    provider = OSMSearchProvider(["http://slow.mirror/overpass", f"{fake_api.base}/overpass"],
                                 f"{fake_api.base}/nominatim")
    provider.nominatim_pause = provider.overpass_pause = 0
    real = provider._post_json

    async def post(url, form, timeout):
        if url.startswith("http://slow.mirror"):
            await asyncio.sleep(30)
        return await real(url, form, timeout)
    provider._post_json = post
    t = time.monotonic()
    found = await provider.search(niche())
    assert found and time.monotonic() - t < 5
    await provider.close()


async def test_nothing_stored_and_servers_down_is_an_honest_error(fake_api):
    provider = make(fake_api, Store())
    fake_api.overpass_status = 504
    with pytest.raises(SearchProviderError):
        await provider.search(niche())
    await provider.close()


async def test_stores_survive_in_the_database(tg):
    cache = DbGeoCache(tg.svc.db)
    await cache.set("companies:казань|beauty", {"elements": [{"id": 1}]})
    data, stamp = await cache.stamped("companies:казань|beauty")
    assert data == {"elements": [{"id": 1}]} and stamp


async def test_collector_fills_everything_and_a_busy_server_only_postpones(fake_api, monkeypatch):
    monkeypatch.setattr(collector, "targets", lambda: [niche("Казань"), niche("Уфа")])
    store = Store()
    provider = make(fake_api, store)
    fake_api.overpass_status = 504
    assert await collector.collect_once(provider, pause=0) == (0, 2)
    fake_api.overpass_status = 200
    assert await collector.collect_once(provider, pause=0) == (2, 0)
    assert await collector.collect_once(provider, pause=0) == (0, 0)  # fresh: no requests
    await provider.close()


def test_collector_covers_every_niche_in_every_city():
    from leadly import catalog as c
    assert len(collector.targets()) == len(c.NICHES) * len(c.POPULAR_CITIES)
