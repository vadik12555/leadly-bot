"""2GIS Places API (catalog.api.2gis.com/3.0/items). Requires TWOGIS_API_KEY.

Keys: https://dev.2gis.com/ (demo key on request, then a paid plan). Contacts
(phones/sites) are returned only on plans that include them; the provider shows
whatever the API returns and never fills gaps.
Note: parsing follows the public API docs and is covered by tests on sample
payloads; it has not been run against the live API from the dev environment.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

import aiohttp

from leadly import catalog as c
from leadly.models import Company, SearchQuery
from leadly.providers.http import make_session
from leadly.providers.search.base import SearchProvider, SearchProviderError
from leadly.utils import now_iso

log = logging.getLogger(__name__)

API_URL = "https://catalog.api.2gis.com/3.0/items"
FIELDS = "items.point,items.address,items.contact_groups,items.rubrics,items.full_address_name"
PAGE_SIZE = 10
MAX_PAGES = 5


class TwoGisSearchProvider(SearchProvider):
    code = "2gis"
    title = "2ГИС"
    attribution = "Данные © 2ГИС"

    def __init__(self, api_key: str, session_factory: Callable[[], aiohttp.ClientSession] | None = None):
        self.api_key = api_key
        self._session_factory = session_factory or (lambda: make_session(timeout=20))
        self._session: aiohttp.ClientSession | None = None

    def configured(self) -> tuple[bool, str]:
        return (True, "ключ задан") if self.api_key else (False, "нет TWOGIS_API_KEY")

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = self._session_factory()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def search(self, query: SearchQuery) -> list[Company]:
        if not self.api_key:
            raise SearchProviderError("2ГИС не подключён (нет ключа).", retryable=False)
        if query.niche == "custom":
            text, category = query.niche_title, query.niche_title
        else:
            niche = c.NICHES[query.niche]
            sub = niche.subcategory(query.subcategory) or niche.subcategories[0]
            text, category = sub.query, sub.title
        fetched = now_iso()
        out: list[Company] = []
        for page in range(1, MAX_PAGES + 1):
            items, total = await self._page(f"{text} {query.city}", page)
            out += [co for co in (self._to_company(i, category, query.city, fetched) for i in items) if co]
            if len(out) >= query.limit or page * PAGE_SIZE >= total or not items:
                break
        return out[: query.limit]

    async def _page(self, q: str, page: int) -> tuple[list[dict], int]:
        params = {"q": q, "type": "branch", "fields": FIELDS, "page": str(page),
                  "page_size": str(PAGE_SIZE), "locale": "ru_RU", "key": self.api_key}
        try:
            async with self.session.get(API_URL, params=params) as resp:
                if resp.status == 429:
                    raise SearchProviderError("2ГИС: превышен лимит запросов.")
                if resp.status >= 400:
                    raise SearchProviderError(f"2ГИС вернул ошибку {resp.status}.")
                data = await resp.json(content_type=None)
        except SearchProviderError:
            raise
        except asyncio.TimeoutError:
            raise SearchProviderError("2ГИС не ответил вовремя.") from None
        except (aiohttp.ClientError, ValueError) as e:
            raise SearchProviderError(f"2ГИС недоступен ({type(e).__name__}).") from None
        meta = data.get("meta") or {}
        code = meta.get("code", 200)
        if code == 404:  # "nothing found"
            return [], 0
        if code != 200:
            message = (meta.get("error") or {}).get("message", "")
            if code in (401, 403):
                raise SearchProviderError("2ГИС отклонил ключ доступа.", retryable=False)
            raise SearchProviderError(f"2ГИС: ошибка {code} {message}".strip())
        result = data.get("result") or {}
        return result.get("items") or [], int(result.get("total") or 0)

    def _to_company(self, item: dict, category: str, city: str, fetched: str) -> Company | None:
        name = (item.get("name") or "").strip()
        if not name:
            return None
        phone = email = website = None
        socials: dict[str, str] = {}
        for group in item.get("contact_groups") or []:
            for contact in group.get("contacts") or []:
                kind, value = contact.get("type"), contact.get("value") or ""
                text = contact.get("text") or value
                if kind == "phone" and not phone:
                    phone = text
                elif kind == "email" and not email:
                    email = value
                elif kind == "website" and not website:
                    # value may be a 2GIS redirect link; `text` holds the real domain
                    website = text if text.startswith("http") else f"http://{text}"
                elif kind in ("vkontakte", "telegram", "instagram", "whatsapp"):
                    socials.setdefault(kind.capitalize(), contact.get("url") or value)
        firm_id = str(item.get("id", "")).split("_")[0]
        rubrics = [r.get("name") for r in item.get("rubrics") or [] if r.get("name")]
        return Company(
            source=self.code, source_title=self.title, source_id=str(item.get("id", "")),
            name=name, category=rubrics[0] if rubrics else category, city=city,
            address=item.get("address_name") or item.get("full_address_name"),
            phone=phone, email=email, website=website,
            source_url=f"https://2gis.ru/firm/{firm_id}" if firm_id else None,
            socials=socials, fetched_at=fetched,
        )
