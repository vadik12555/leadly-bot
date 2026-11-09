"""Search use-case: limits -> providers (per city) -> filters -> persisted results."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from leadly import catalog as c
from leadly.config import RESULTS, SEARCHES
from leadly.db import Database, User
from leadly.models import Company, SearchQuery
from leadly.providers.search import SearchAggregator, SearchProviderError, dedupe
from leadly.services.billing import BillingService
from leadly.services.usage import Quota, UsageService
from leadly.utils import now_iso

log = logging.getLogger(__name__)


@dataclass
class SearchRequest:
    service: c.Service
    niche: c.Niche
    subcategory: str | None
    cities: list[str]
    site_filter: str = c.SITE_ANY
    contacts_only: bool = False
    hot: bool = False  # only companies whose site check confirmed a problem the service solves
    builder: bool = False  # only sites built on website builders (Tilda, Wix…)


@dataclass
class SearchOutcome:
    search_id: int | None = None
    blocked: str | None = None  # usage kind whose limit stopped the search
    quota: Quota | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    reused: bool = False


@dataclass
class SearchView:
    id: int
    service: c.Service
    niche: c.Niche
    subcategory: str | None
    cities: list[str]
    filters: dict
    found: int
    items: list[tuple[int, Company]]
    is_demo: bool
    providers: str
    created_at: str


class SearchService:
    def __init__(self, db: Database, aggregator: SearchAggregator, usage: UsageService, billing: BillingService,
                 checker=None):
        self.checker = checker
        self.db = db
        self.aggregator = aggregator
        self.usage = usage
        self.billing = billing

    async def run(self, user: User, token: str, req: SearchRequest) -> SearchOutcome:
        existing = await self.db.fetchone("SELECT id, status, user_id FROM searches WHERE token = ?", token)
        if existing and existing["user_id"] == user.id and existing["status"] == "ok":
            return SearchOutcome(search_id=existing["id"], reused=True)  # repeated tap: show the same results
        if existing and existing["status"] == "running":
            return SearchOutcome(error="Этот поиск уже выполняется — подожди несколько секунд.")

        results_left = await self.usage.remaining(user, RESULTS)
        if results_left == 0:
            return SearchOutcome(blocked=RESULTS, quota=await self.usage.peek(user, RESULTS))
        ref = f"search:{token}"
        quota = await self.usage.charge(user, SEARCHES, ref)
        if not quota.ok:
            return SearchOutcome(blocked=SEARCHES, quota=quota)

        plan = await self.billing.plan(user.id)
        filters = {"site": req.site_filter, "contacts": req.contacts_only, "hot": req.hot, "builder": req.builder}
        if existing:  # previous attempt with this token failed: retry in place
            search_id = existing["id"]
            await self.db.execute("UPDATE searches SET status = 'running', error = NULL WHERE id = ?", search_id)
        else:
            search_id = await self.db.execute(
                "INSERT INTO searches (user_id, token, service, service_title, niche, niche_title, subcategory, "
                "cities, filters, providers, is_demo, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)",
                user.id, token, req.service.code, req.service.title, req.niche.code, req.niche.title,
                req.subcategory, json.dumps(req.cities, ensure_ascii=False), json.dumps(filters),
                self.aggregator.title, int(self.aggregator.is_demo), now_iso())

        companies: list[Company] = []
        warnings: list[str] = []
        errors: list[str] = []
        fetch_limit = max(plan.results_per_search * 3, 60)
        for city in req.cities:
            query = SearchQuery(req.niche.code, req.niche.title, req.subcategory, city, fetch_limit)
            try:
                found, warns = await self.aggregator.search(query)
                companies += found
                warnings += warns
            except SearchProviderError as e:
                errors.append(e.user_message if len(req.cities) == 1 else f"{city}: {e.user_message}")
            except Exception:
                log.exception("search crashed")
                errors.append("внутренняя ошибка поиска")
        if errors and not companies and len(errors) == len(req.cities):
            await self.usage.refund(user, SEARCHES, ref)
            await self.db.execute("UPDATE searches SET status = 'error', error = ? WHERE id = ?",
                                  "; ".join(errors), search_id)
            return SearchOutcome(search_id=search_id, error="; ".join(errors))
        warnings += errors

        hidden = {r["company_key"] for r in await self.db.fetchall(
            "SELECT company_key FROM hidden_companies WHERE user_id = ?", user.id)}
        pool = []
        for co in dedupe(companies):
            if hidden & set(co.keys()):
                continue
            if req.site_filter == c.SITE_YES and not co.website:
                continue
            if req.site_filter == c.SITE_NO and co.website:
                continue
            pool.append(co)
        if self.checker:
            from leadly.services.enrich import check_sites, enrich_contacts, rank_key, score_companies
            if req.hot or req.builder:
                await check_sites(pool, self.checker, self.db, limit=plan.results_per_search * 3)
            else:
                # many companies list only a website in the source: take phone/email from their own site
                await enrich_contacts(pool[: plan.results_per_search * 2], self.checker, self.db,
                                      limit=plan.results_per_search)
            await score_companies(pool, req.service, req.niche.code, self.db)
            pool.sort(key=rank_key)
        from leadly.services.enrich import BUILDERS
        filtered = [co for co in pool
                    if (co.has_contacts or not req.contacts_only) and (co.signals or not req.hot)
                    and (co.cms in BUILDERS or not req.builder)]

        deliver = min(len(filtered), plan.results_per_search,
                      results_left if results_left is not None else len(filtered))
        chosen = filtered[:deliver]
        await self.db.executemany(
            "INSERT OR REPLACE INTO search_results (search_id, idx, company_key, company) VALUES (?, ?, ?, ?)",
            [(search_id, i, co.key, json.dumps(co.to_dict(), ensure_ascii=False)) for i, co in enumerate(chosen)])
        if deliver:
            await self.usage.charge(user, RESULTS, f"results:{search_id}", deliver)
            from leadly.services.watches import mark_seen
            await mark_seen(self.db, user.id, chosen)  # auto-search will only bring companies not shown yet
        else:
            await self.usage.refund(user, SEARCHES, ref)  # an empty result doesn't cost a search
        await self.db.execute("UPDATE searches SET status = 'ok', found = ?, delivered = ? WHERE id = ?",
                              len(filtered), deliver, search_id)
        return SearchOutcome(search_id=search_id, warnings=warnings)

    async def get(self, user_id: int, search_id: int) -> SearchView | None:
        row = await self.db.fetchone("SELECT * FROM searches WHERE id = ? AND user_id = ?", search_id, user_id)
        if not row or row["status"] != "ok":
            return None
        rows = await self.db.fetchall("SELECT idx, company FROM search_results WHERE search_id = ? ORDER BY idx",
                                      search_id)
        return SearchView(
            id=search_id,
            service=c.get_service(row["service"], row["service_title"]),
            niche=c.get_niche(row["niche"], row["niche_title"]),
            subcategory=row["subcategory"],
            cities=json.loads(row["cities"]),
            filters=json.loads(row["filters"]),
            found=row["found"],
            items=[(r["idx"], Company.from_dict(json.loads(r["company"]))) for r in rows],
            is_demo=bool(row["is_demo"]),
            providers=row["providers"],
            created_at=row["created_at"],
        )

    async def last_search_id(self, user_id: int) -> int | None:
        row = await self.db.fetchone("SELECT id FROM searches WHERE user_id = ? AND status = 'ok' "
                                     "ORDER BY id DESC LIMIT 1", user_id)
        return row["id"] if row else None
