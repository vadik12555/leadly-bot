"""Search providers and the aggregator that merges and de-duplicates their results."""

from __future__ import annotations

import asyncio
import logging

from leadly.models import Company, SearchQuery
from leadly.providers.search.base import SearchProvider, SearchProviderError
from leadly.providers.search.demo import DemoSearchProvider
from leadly.providers.search.osm import GeoCache, OSMSearchProvider
from leadly.providers.search.twogis import TwoGisSearchProvider

log = logging.getLogger(__name__)


def dedupe(companies: list[Company]) -> list[Company]:
    """Drop duplicates by domain, phone, or name+city; the first occurrence wins."""
    seen: set[str] = set()
    out = []
    for co in companies:
        keys = co.keys()
        if any(k in seen for k in keys):
            continue
        seen.update(keys)
        out.append(co)
    return out


class SearchAggregator:
    """Queries every configured provider, tolerates partial failures, merges results."""

    def __init__(self, providers: list[SearchProvider]):
        if not providers:
            raise ValueError("no search providers configured")
        self.providers = providers

    @property
    def is_demo(self) -> bool:
        return all(p.is_demo for p in self.providers)

    @property
    def title(self) -> str:
        return " + ".join(p.title for p in self.providers)

    @property
    def attribution(self) -> str:
        return " · ".join(p.attribution for p in self.providers if p.attribution)

    async def search(self, query: SearchQuery) -> tuple[list[Company], list[str]]:
        """Returns (companies, warnings). Raises SearchProviderError only if every provider failed."""
        results = await asyncio.gather(*(p.search(query) for p in self.providers), return_exceptions=True)
        merged: list[Company] = []
        warnings: list[str] = []
        errors: list[SearchProviderError] = []
        for provider, res in zip(self.providers, results):
            if isinstance(res, SearchProviderError):
                errors.append(res)
                warnings.append(f"{provider.title}: {res.user_message}")
            elif isinstance(res, BaseException):
                log.error("provider %s crashed", provider.code, exc_info=res)
                err = SearchProviderError(f"{provider.title}: внутренняя ошибка источника.")
                errors.append(err)
                warnings.append(err.user_message)
            else:
                merged += res
        if errors and len(errors) == len(self.providers):
            if len(errors) == 1:
                raise errors[0]
            raise SearchProviderError("Все источники данных сейчас недоступны: " + "; ".join(warnings),
                                      retryable=any(e.retryable for e in errors))
        return dedupe(merged), warnings

    async def close(self) -> None:
        for p in self.providers:
            await p.close()


def build_search(settings, geo_cache: GeoCache | None = None) -> SearchAggregator:
    providers: list[SearchProvider] = []
    for code in settings.search_providers:
        if code == "osm":
            providers.append(OSMSearchProvider(settings.overpass_urls, settings.nominatim_url,
                                               settings.osm_contact, geo_cache, company_cache=geo_cache))
        elif code == "2gis":
            if settings.twogis_api_key:
                providers.append(TwoGisSearchProvider(settings.twogis_api_key))
            else:
                log.warning("SEARCH_PROVIDERS contains 2gis but TWOGIS_API_KEY is empty — skipped")
        elif code == "demo":
            providers.append(DemoSearchProvider())
        else:
            raise ValueError(f"Неизвестный источник в SEARCH_PROVIDERS: {code}")
    if any(p.is_demo for p in providers) and not all(p.is_demo for p in providers):
        raise ValueError("Демо-источник нельзя смешивать с реальными: уберите demo из SEARCH_PROVIDERS")
    return SearchAggregator(providers)


__all__ = ["SearchProvider", "SearchProviderError", "SearchAggregator", "OSMSearchProvider",
           "TwoGisSearchProvider", "DemoSearchProvider", "build_search", "dedupe"]
