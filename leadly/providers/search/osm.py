"""OpenStreetMap provider: Nominatim (city -> area) + Overpass API (businesses).

Free, no API key, legal to use under ODbL with attribution. Coverage of small
businesses varies by city; missing fields mean "not in OSM", never "doesn't exist".
Usage policies: https://operations.osmfoundation.org/policies/nominatim/ ,
https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

import aiohttp

from leadly import catalog as c
from leadly.models import Company, SearchQuery
from leadly.providers.http import USER_AGENT, make_session
from leadly.providers.search.base import SearchProvider, SearchProviderError
from leadly.utils import now_iso

log = logging.getLogger(__name__)

AREA_OFFSET = 3_600_000_000  # Overpass area id = relation id + 3.6e9
MAX_ELEMENTS = 300
NICHE_ELEMENTS = 1000  # a whole niche is stored once and filtered by subcategory locally
COMPANY_CACHE_DAYS = 7  # stored companies younger than this are served without asking Overpass
# public Overpass servers are often overloaded for a minute or two: go around the mirrors a few times
OVERPASS_ROUNDS = 3
OVERPASS_ATTEMPT_TIMEOUT = 60  # busy servers answer in 30-50 s
OVERPASS_BUDGET = 150  # seconds a user may wait in total


class GeoCache(Protocol):
    async def get(self, key: str) -> dict | None: ...
    async def set(self, key: str, value: dict) -> None: ...


class CompanyCache(Protocol):
    async def stamped(self, key: str) -> tuple[dict, str] | None: ...  # (data, stored_at)
    async def set(self, key: str, value: dict) -> None: ...


def is_fresh(stored_at: str, days: float) -> bool:
    return datetime.now(timezone.utc) - datetime.fromisoformat(stored_at) < timedelta(days=days)


def pick_place(places: list[dict], city: str) -> dict | None:
    """The place actually called `city`, a relation if there is one (it has exact borders).
    "Оренбург" also returns the relation of Орск: being a relation must not beat having the right name."""
    wanted = city.strip().lower()

    def named(place: dict) -> bool:
        first = (place.get("display_name") or "").split(",")[0].strip().lower()
        return first.removeprefix("городской округ ").strip() == wanted

    pool = [p for p in places if named(p)] or places
    return next((p for p in pool if p.get("osm_type") == "relation"), None) or (pool[0] if pool else None)


class MemoryGeoCache:
    def __init__(self):
        self._data: dict[str, dict] = {}

    async def get(self, key):
        return self._data.get(key)

    async def set(self, key, value):
        self._data[key] = value


def _first(tags: dict, *keys: str) -> str | None:
    for k in keys:
        v = (tags.get(k) or "").strip()
        if v:
            return v.split(";")[0].strip()
    return None


def _website(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if not re.match(r"^https?://", raw, re.I):
        raw = "http://" + raw
    return raw


def _safe_regex(text: str) -> str:
    """Letters, digits, spaces and dashes only — safe inside an Overpass regex literal."""
    return re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()[:40]


class OSMSearchProvider(SearchProvider):
    code = "osm"
    title = "OpenStreetMap"
    attribution = "Данные © участники OpenStreetMap, лицензия ODbL"
    nominatim_pause = 1.0  # seconds between geocoder requests (Nominatim usage policy)
    overpass_pause = 5.0  # seconds between rounds over the mirrors

    def __init__(self, overpass_urls: list[str], nominatim_url: str, contact: str = "",
                 geo_cache: GeoCache | None = None,
                 session_factory: Callable[[], aiohttp.ClientSession] | None = None,
                 company_cache: CompanyCache | None = None):
        self.company_cache = company_cache
        self.overpass_urls = overpass_urls
        self.nominatim_url = nominatim_url
        ua = USER_AGENT + (f" contact: {contact}" if contact else "")
        self._session_factory = session_factory or (lambda: make_session(timeout=45, user_agent=ua))
        self._session: aiohttp.ClientSession | None = None
        self.geo_cache = geo_cache or MemoryGeoCache()
        self._nominatim_lock = asyncio.Lock()  # Nominatim policy: max 1 request/second

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = self._session_factory()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # --- geocoding ---------------------------------------------------------------

    async def resolve_city(self, city: str) -> dict:
        key = city.strip().lower()
        cached = await self.geo_cache.get(key)
        if cached:
            return cached
        params = {"q": city, "format": "jsonv2", "limit": "5", "accept-language": "ru",
                  "countrycodes": "ru,by,kz,uz,kg,am,ge,az"}
        async with self._nominatim_lock:
            data = await self._get_json(f"{self.nominatim_url}/search", params, "геокодер OpenStreetMap")
            await asyncio.sleep(self.nominatim_pause)
        place = pick_place(data, city)
        if not place:
            raise SearchProviderError(f"Город «{city}» не найден. Проверь написание.", retryable=False)
        bbox = place.get("boundingbox") or []
        result = {
            "name": (place.get("display_name") or city).split(",")[0],
            "area_id": AREA_OFFSET + int(place["osm_id"]) if place.get("osm_type") == "relation" else None,
            # Nominatim bbox: [south, north, west, east]
            "bbox": [float(bbox[0]), float(bbox[2]), float(bbox[1]), float(bbox[3])] if len(bbox) == 4 else None,
        }
        if not result["area_id"] and not result["bbox"]:
            raise SearchProviderError(f"Не удалось определить границы города «{city}».", retryable=False)
        await self.geo_cache.set(key, result)
        return result

    # --- search ------------------------------------------------------------------

    def build_query(self, query: SearchQuery, geo: dict) -> tuple[str, dict[tuple[str, str], str]]:
        if geo.get("area_id"):
            head, scope = f"area(id:{geo['area_id']})->.a;", "(area.a)"
        else:
            s, w, n, e = geo["bbox"]
            head, scope = "", f"({s},{w},{n},{e})"
        labels: dict[tuple[str, str], str] = {}
        lines = []
        if query.niche == "custom":
            pattern = _safe_regex(query.niche_title)
            if not pattern:
                raise SearchProviderError("Опиши нишу словами, например «ветеринарная клиника».", retryable=False)
            for k in c.CUSTOM_NICHE_KEYS:
                lines.append(f'nwr["{k}"]["name"~"{pattern}",i]{scope};')
        else:
            niche = c.NICHES[query.niche]
            subs = [niche.subcategory(query.subcategory)] if query.subcategory else list(niche.subcategories)
            for sub in filter(None, subs):
                for k, v in sub.osm:
                    labels[(k, v)] = sub.title
                    lines.append(f'nwr["{k}"="{v}"]["name"]{scope};')
        body = "\n".join(lines)
        limit = MAX_ELEMENTS if query.subcategory else NICHE_ELEMENTS
        return f"[out:json][timeout:25];\n{head}\n(\n{body}\n);\nout tags center {limit};", labels

    # --- stored companies ----------------------------------------------------------

    @staticmethod
    def _cache_key(query: SearchQuery) -> str:
        niche = query.niche_title.lower() if query.niche == "custom" else query.niche
        return f"companies:{query.city.strip().lower()}|{niche}"

    async def stored(self, query: SearchQuery) -> tuple[dict, str] | None:
        return await self.company_cache.stamped(self._cache_key(query)) if self.company_cache else None

    async def _remember(self, query: SearchQuery, data: dict) -> None:
        if self.company_cache and not query.subcategory:  # only a whole niche can answer every subcategory
            await self.company_cache.set(self._cache_key(query), data)

    async def refresh(self, query: SearchQuery) -> int:
        """Background collection for a whole niche in a city. Returns how many objects were stored."""
        ql, _ = self.build_query(query, await self.resolve_city(query.city))
        data = await self._overpass(ql)
        await self._remember(query, data)
        return len(data.get("elements", []))

    # --- search ------------------------------------------------------------------

    async def search(self, query: SearchQuery) -> list[Company]:
        geo = await self.resolve_city(query.city)
        ql, labels = self.build_query(query, geo)
        stored = await self.stored(query)
        if stored and is_fresh(stored[1], COMPANY_CACHE_DAYS):
            data, fetched = stored
        else:
            try:
                data, fetched = await self._overpass(ql), now_iso()
                await self._remember(query, data)
            except SearchProviderError:
                if not stored:
                    raise
                log.warning("overpass unavailable, serving stored companies from %s", stored[1])
                data, fetched = stored  # the card shows this date, so an old copy stays honest
        companies = []
        for el in data.get("elements", []):
            tags = el.get("tags") or {}
            if query.subcategory and not any(tags.get(k) == v for k, v in labels):
                continue  # a stored niche holds every subcategory
            company = self._to_company(el, query, labels, fetched)
            if company:
                companies.append(company)
        # most actionable first: website + contacts, then contacts, then the rest
        companies.sort(key=lambda co: (not co.website, not co.has_contacts, co.name.lower()))
        return companies[: query.limit]

    def _to_company(self, el: dict, query: SearchQuery, labels: dict, fetched: str) -> Company | None:
        tags = el.get("tags") or {}
        name = _first(tags, "name:ru", "name")
        if not name:
            return None
        category = next((label for (k, v), label in labels.items() if tags.get(k) == v), None)
        category = category or query.niche_title
        street = _first(tags, "addr:street", "addr:place")
        house = _first(tags, "addr:housenumber")
        address = ", ".join(x for x in (street, house) if x) or None
        socials = {}
        for label, keys in (("VK", ("contact:vk", "vk")), ("Telegram", ("contact:telegram", "telegram")),
                            ("Instagram", ("contact:instagram", "instagram")),
                            ("WhatsApp", ("contact:whatsapp", "whatsapp")), ("Viber", ("contact:viber",)),
                            ("Одноклассники", ("contact:ok",)), ("Facebook", ("contact:facebook", "facebook"))):
            value = _first(tags, *keys)
            if value:
                socials[label] = value
        return Company(
            source=self.code, source_title=self.title,
            source_id=f"{el.get('type')}/{el.get('id')}",
            name=name, category=category,
            city=_first(tags, "addr:city") or query.city,
            address=address,
            phone=_first(tags, "contact:phone", "phone", "contact:mobile", "mobile", "phone:mobile"),
            email=_first(tags, "contact:email", "email"),
            website=_website(_first(tags, "contact:website", "website", "contact:url", "url", "brand:website")),
            source_url=f"https://www.openstreetmap.org/{el.get('type')}/{el.get('id')}",
            socials=socials,
            opening_hours=_first(tags, "opening_hours"),
            fetched_at=fetched,
        )

    # --- HTTP ----------------------------------------------------------------------

    async def _overpass(self, ql: str) -> dict[str, Any]:
        last: SearchProviderError | None = None
        deadline = time.monotonic() + OVERPASS_BUDGET
        for round_no in range(OVERPASS_ROUNDS):
            if round_no:
                await asyncio.sleep(self.overpass_pause)
            for url in self.overpass_urls:
                left = deadline - time.monotonic()
                if left < 5:
                    break
                try:
                    data = await self._post_json(url, {"data": ql}, min(OVERPASS_ATTEMPT_TIMEOUT, left))
                    # a server that ran out of time answers 200 with no elements and a remark:
                    # that is a failure, not "no companies in this city"
                    if data.get("remark") and not data.get("elements"):
                        raise SearchProviderError("OpenStreetMap перегружен или не ответил вовремя.")
                    return data
                except SearchProviderError as e:
                    last = e
                    log.warning("overpass endpoint failed: %s (%s)", url, e.user_message)
        raise last or SearchProviderError("OpenStreetMap сейчас недоступен.")

    async def _get_json(self, url: str, params: dict, what: str) -> Any:
        return await self._request("GET", url, what, params=params)

    async def _post_json(self, url: str, form: dict, timeout: float) -> Any:
        return await self._request("POST", url, "OpenStreetMap", data=form,
                                   timeout=aiohttp.ClientTimeout(total=timeout))

    async def _request(self, method: str, url: str, what: str, **kwargs) -> Any:
        try:
            async with self.session.request(method, url, **kwargs) as resp:
                if resp.status == 429:
                    raise SearchProviderError(f"{what}: слишком много запросов, попробуй через минуту.")
                if resp.status in (502, 503, 504):
                    raise SearchProviderError(f"{what} перегружен или не ответил вовремя.")
                if resp.status >= 400:
                    raise SearchProviderError(f"{what} вернул ошибку {resp.status}.")
                return await resp.json(content_type=None)
        except SearchProviderError:
            raise
        except asyncio.TimeoutError:
            raise SearchProviderError(f"{what} не ответил вовремя.") from None
        except aiohttp.ClientError as e:
            raise SearchProviderError(f"{what} недоступен ({type(e).__name__}).") from None
        except ValueError:
            raise SearchProviderError(f"{what} вернул некорректный ответ.") from None


__all__ = ["OSMSearchProvider", "MemoryGeoCache", "GeoCache", "CompanyCache", "is_fresh"]
