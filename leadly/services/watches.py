"""Auto-search: saved searches that bring only *new* companies on the plan's schedule,
plus CRM follow-up reminders."""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from leadly import catalog as c
from leadly.config import WATCHES
from leadly.db import Database, User
from leadly.models import Company, SearchQuery
from leadly.providers.search import SearchAggregator, SearchProviderError, dedupe
from leadly.services.billing import BillingService
from leadly.utils import now_iso

log = logging.getLogger(__name__)


@dataclass
class WatchRun:
    watch: dict
    search_id: int | None
    new: int
    error: str | None = None


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


class WatchService:
    def __init__(self, db: Database, aggregator: SearchAggregator, billing: BillingService, checker=None):
        self.checker = checker
        self.db = db
        self.aggregator = aggregator
        self.billing = billing

    # --- management ----------------------------------------------------------------

    async def create_from_search(self, user: User, search_id: int) -> tuple[int | None, str]:
        """Returns (watch_id, status): created | exists | limit:<n> | not_found."""
        row = await self.db.fetchone("SELECT * FROM searches WHERE id = ? AND user_id = ? AND status = 'ok'",
                                     search_id, user.id)
        if not row:
            return None, "not_found"
        params = {"service": row["service"], "service_title": row["service_title"], "niche": row["niche"],
                  "niche_title": row["niche_title"], "sub": row["subcategory"], "cities": json.loads(row["cities"]),
                  "filters": json.loads(row["filters"])}
        key = json.dumps(params, ensure_ascii=False, sort_keys=True)
        for w in await self.db.fetchall("SELECT id, params FROM watches WHERE user_id = ? AND active = 1", user.id):
            if json.dumps(json.loads(w["params"]), ensure_ascii=False, sort_keys=True) == key:
                return w["id"], "exists"
        limit = (await self.billing.plan(user.id)).limit(WATCHES).amount
        count = (await self.db.fetchone("SELECT COUNT(*) AS n FROM watches WHERE user_id = ? AND active = 1",
                                        user.id))["n"]
        if limit is not None and count >= limit:
            return None, f"limit:{limit}"
        niche = c.get_niche(row["niche"], row["niche_title"])
        sub = niche.subcategory(row["subcategory"])
        title = f"{niche.label}{' · ' + sub.title if sub else ''} · {', '.join(params['cities'])}"
        now = now_iso()
        watch_id = await self.db.execute(
            "INSERT INTO watches (user_id, params, title, last_run_at, created_at) VALUES (?, ?, ?, ?, ?)",
            user.id, key, title, now, now)
        return watch_id, "created"

    async def list(self, user_id: int) -> list[dict]:
        return [dict(r) for r in await self.db.fetchall(
            "SELECT * FROM watches WHERE user_id = ? AND active = 1 ORDER BY id", user_id)]

    async def delete(self, user_id: int, watch_id: int) -> None:
        await self.db.execute("UPDATE watches SET active = 0 WHERE id = ? AND user_id = ?", watch_id, user_id)

    # --- scheduled runs ----------------------------------------------------------------

    async def due(self) -> list[dict]:
        out = []
        for w in await self.db.fetchall(
                "SELECT w.* FROM watches w JOIN users u ON u.id = w.user_id "
                "WHERE w.active = 1 AND u.notify_watches = 1 ORDER BY w.last_run_at"):
            plan = await self.billing.plan(w["user_id"])
            if w["last_run_at"] <= _ago(plan.watch_interval_days):
                out.append(dict(w))
        return out

    async def run(self, watch: dict) -> WatchRun:
        params = json.loads(watch["params"])
        user_id = watch["user_id"]
        plan = await self.billing.plan(user_id)
        await self.db.execute("UPDATE watches SET last_run_at = ? WHERE id = ?", now_iso(), watch["id"])
        companies: list[Company] = []
        errors = []
        for city in params["cities"]:
            try:
                found, _ = await self.aggregator.search(SearchQuery(
                    params["niche"], params["niche_title"], params.get("sub"), city,
                    max(plan.results_per_search * 3, 60)))
                companies += found
            except SearchProviderError as e:
                errors.append(e.user_message)
        if errors and not companies:
            return WatchRun(watch, None, 0, "; ".join(errors))
        excluded = {r["company_key"] for r in await self.db.fetchall(
            "SELECT company_key FROM seen_companies WHERE user_id = ? UNION "
            "SELECT company_key FROM hidden_companies WHERE user_id = ?", user_id, user_id)}
        filters = params.get("filters") or {}
        fresh = []
        for co in dedupe(companies):
            if excluded & set(co.keys()):
                continue
            if filters.get("site") == c.SITE_YES and not co.website:
                continue
            if filters.get("site") == c.SITE_NO and co.website:
                continue
            if filters.get("contacts") and not co.has_contacts:
                continue
            fresh.append(co)
        if self.checker and fresh:
            from leadly.services.enrich import check_sites, enrich_contacts, rank_key, score_companies
            if filters.get("hot") or filters.get("builder"):
                await check_sites(fresh, self.checker, self.db, limit=plan.results_per_search * 3)
            else:
                await enrich_contacts(fresh[: plan.results_per_search], self.checker, self.db,
                                      limit=plan.results_per_search)
            await score_companies(fresh, c.get_service(params["service"], params["service_title"]),
                                  params["niche"], self.db)
            fresh.sort(key=rank_key)
            from leadly.services.enrich import BUILDERS
            if filters.get("hot"):
                fresh = [co for co in fresh if co.signals]
            if filters.get("builder"):
                fresh = [co for co in fresh if co.cms in BUILDERS]
        fresh = fresh[: plan.results_per_search]
        if not fresh:
            return WatchRun(watch, None, 0)
        search_id = await self.db.execute(
            "INSERT INTO searches (user_id, token, service, service_title, niche, niche_title, subcategory, cities, "
            "filters, providers, is_demo, found, delivered, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?)",
            user_id, f"watch:{watch['id']}:{secrets.token_hex(4)}", params["service"], params["service_title"],
            params["niche"], params["niche_title"], params.get("sub"), json.dumps(params["cities"], ensure_ascii=False),
            json.dumps(filters), self.aggregator.title, int(self.aggregator.is_demo), len(fresh), len(fresh), now_iso())
        await self.db.executemany(
            "INSERT INTO search_results (search_id, idx, company_key, company) VALUES (?, ?, ?, ?)",
            [(search_id, i, co.key, json.dumps(co.to_dict(), ensure_ascii=False)) for i, co in enumerate(fresh)])
        await mark_seen(self.db, user_id, fresh)
        return WatchRun(watch, search_id, len(fresh))

    # --- CRM follow-ups --------------------------------------------------------------------

    async def due_followups(self, days: int) -> list[dict]:
        return [dict(r) for r in await self.db.fetchall(
            "SELECT l.* FROM leads l JOIN users u ON u.id = l.user_id WHERE l.status = 'contacted' "
            "AND l.followup_notified_at IS NULL AND l.contacted_at <= ? AND u.notify_followups = 1", _ago(days))]

    async def due_final_touches(self, days: int) -> list[dict]:
        return [dict(r) for r in await self.db.fetchall(
            "SELECT l.* FROM leads l JOIN users u ON u.id = l.user_id WHERE l.status = 'contacted' "
            "AND l.followup_notified_at IS NOT NULL AND l.followup2_at IS NULL AND l.contacted_at <= ? "
            "AND u.notify_followups = 1", _ago(days))]

    async def mark_final_touch(self, lead_id: int) -> None:
        await self.db.execute("UPDATE leads SET followup2_at = ? WHERE id = ?", now_iso(), lead_id)

    async def due_digests(self) -> list[dict]:
        """Users active in the last 7 days who haven't received a weekly digest for 6+ days."""
        week = _ago(7)
        rows = await self.db.fetchall(
            "SELECT u.id FROM users u WHERE (u.last_digest_at IS NULL OR u.last_digest_at <= ?) "
            "AND u.created_at <= ? AND ("
            "EXISTS (SELECT 1 FROM searches s WHERE s.user_id = u.id AND s.status = 'ok' AND s.created_at >= ?) OR "
            "EXISTS (SELECT 1 FROM leads l WHERE l.user_id = u.id AND l.updated_at >= ?))", _ago(6), _ago(6), week, week)
        out = []
        for r in rows:
            uid = r["id"]
            one = self.db.fetchone
            out.append({
                "user_id": uid,
                "searches": (await one("SELECT COUNT(*) AS n FROM searches WHERE user_id = ? AND status = 'ok' "
                                       "AND created_at >= ? AND token NOT LIKE 'watch:%'", uid, week))["n"],
                "found": (await one("SELECT COALESCE(SUM(delivered), 0) AS n FROM searches WHERE user_id = ? "
                                    "AND status = 'ok' AND created_at >= ?", uid, week))["n"],
                "saved": (await one("SELECT COUNT(*) AS n FROM leads WHERE user_id = ? AND created_at >= ?",
                                    uid, week))["n"],
                "contacted": (await one("SELECT COUNT(*) AS n FROM leads WHERE user_id = ? AND contacted_at >= ?",
                                        uid, week))["n"],
                "replied": (await one("SELECT COUNT(*) AS n FROM leads WHERE user_id = ? AND replied_at >= ?",
                                      uid, week))["n"],
                "deals": (await one("SELECT COUNT(*) AS n FROM leads WHERE user_id = ? AND deal_at >= ?",
                                    uid, week))["n"],
                "waiting": (await one("SELECT COUNT(*) AS n FROM leads WHERE user_id = ? AND status = 'new'",
                                      uid))["n"],
            })
        return out

    async def mark_digest(self, user_id: int) -> None:
        await self.db.execute("UPDATE users SET last_digest_at = ? WHERE id = ?", now_iso(), user_id)

    async def mark_followup(self, lead_id: int) -> None:
        await self.db.execute("UPDATE leads SET followup_notified_at = ? WHERE id = ?", now_iso(), lead_id)


async def mark_seen(db: Database, user_id: int, companies: list[Company]) -> None:
    now = now_iso()
    await db.executemany("INSERT OR IGNORE INTO seen_companies (user_id, company_key, created_at) VALUES (?, ?, ?)",
                         [(user_id, k, now) for co in companies for k in co.keys()])
