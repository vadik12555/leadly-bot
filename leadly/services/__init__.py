"""Business logic. Knows nothing about Telegram."""

from __future__ import annotations

import json
from dataclasses import dataclass

from leadly.config import Settings, load_product_config
from leadly.db import Database
from leadly.providers.ai import AIProvider, build_ai
from leadly.providers.search import SearchAggregator, build_search
from leadly.utils import now_iso


class Runtime:
    """Mutable holder so product config (plans/limits) can be reloaded without a restart."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def reload_product(self) -> None:
        self.settings = self.settings.with_product(load_product_config(self.settings.plans_path))


class DbGeoCache:
    def __init__(self, db: Database):
        self.db = db

    async def get(self, key: str) -> dict | None:
        row = await self.db.fetchone("SELECT data FROM geo_cache WHERE query = ?", key)
        return json.loads(row["data"]) if row else None

    async def stamped(self, key: str) -> tuple[dict, str] | None:
        row = await self.db.fetchone("SELECT data, created_at FROM geo_cache WHERE query = ?", key)
        return (json.loads(row["data"]), row["created_at"]) if row else None

    async def set(self, key: str, value: dict) -> None:
        await self.db.execute("INSERT OR REPLACE INTO geo_cache (query, data, created_at) VALUES (?, ?, ?)",
                              key, json.dumps(value, ensure_ascii=False), now_iso())


@dataclass
class Services:
    rt: Runtime
    db: Database
    search_sources: SearchAggregator
    ai: AIProvider
    billing: "BillingService"
    usage: "UsageService"
    search: "SearchService"
    leads: "LeadService"
    account: "AccountService"
    profiles: "ProfileService"
    watches: "WatchService"
    templates: "TemplateService"
    partners: "PartnerService"

    @property
    def settings(self) -> Settings:
        return self.rt.settings

    async def close(self) -> None:
        await self.search_sources.close()
        await self.ai.close()


def build_services(settings: Settings, db: Database, search_sources: SearchAggregator | None = None,
                   ai: AIProvider | None = None, site_checker=None) -> Services:
    from leadly.services.account import AccountService
    from leadly.services.billing import BillingService
    from leadly.services.leads import LeadService
    from leadly.services.profile import ProfileService
    from leadly.services.search import SearchService
    from leadly.services.site_check import SiteChecker
    from leadly.services.templates import TemplateService
    from leadly.services.usage import UsageService
    from leadly.services.watches import WatchService

    rt = Runtime(settings)
    search_sources = search_sources or build_search(settings, DbGeoCache(db))
    ai = ai or build_ai(settings)
    billing = BillingService(db, rt)
    usage = UsageService(db, billing)
    profiles = ProfileService(db)
    checker = site_checker or SiteChecker()
    return Services(
        rt=rt, db=db, search_sources=search_sources, ai=ai, billing=billing, usage=usage,
        search=SearchService(db, search_sources, usage, billing, checker),
        leads=LeadService(db, usage, billing, ai, checker, profiles),
        account=AccountService(db, rt),
        profiles=profiles,
        watches=WatchService(db, search_sources, billing, checker),
        templates=TemplateService(db),
        partners=PartnerService(db),
    )


from leadly.services.account import AccountService  # noqa: E402  (type names for the dataclass)
from leadly.services.billing import BillingService  # noqa: E402
from leadly.services.leads import LeadService  # noqa: E402
from leadly.services.profile import ProfileService  # noqa: E402
from leadly.services.search import SearchService  # noqa: E402
from leadly.services.usage import UsageService  # noqa: E402
from leadly.services.watches import WatchService  # noqa: E402
from leadly.services.templates import TemplateService  # noqa: E402
from leadly.services.partners import PartnerService  # noqa: E402
