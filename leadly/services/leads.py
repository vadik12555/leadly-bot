"""Companies (from search results or saved leads): card, site check, contacts, AI, proposals, CRM.

A company is addressed either as a search result ("r", search_id, idx) or as a
saved lead ("s", lead_id). Both resolve to the same CompanyContext.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from leadly import catalog as c
from leadly.config import AI_ANALYSES, CARD_OPENS, PROPOSALS, SAVED_LEADS, SITE_CHECKS
from leadly.db import Database, User
from leadly.models import AIAnalysis, Company, Insight, SiteReport
from leadly.providers.ai import AIContext, AINotConfigured, AIProvider, AIProviderError, NoAIProvider
from leadly.services.billing import BillingService
from leadly.services.enrich import load_report, store_report
from leadly.services.insights import build_insight
from leadly.services.site_check import SiteChecker, url_key
from leadly.services.usage import Quota, UsageService
from leadly.services.writer import template_proposals
from leadly.utils import now_iso

log = logging.getLogger(__name__)

RESULT, SAVED = "r", "s"
SITE_CACHE_HOURS = 24


@dataclass
class CompanyContext:
    kind: str
    ref: int
    idx: int
    company: Company
    service: c.Service
    niche_code: str | None
    search_id: int | None
    lead: dict | None = None
    ai: bool = False  # AI is connected: only then the card offers AI analysis

    @property
    def bookable(self) -> bool:
        niche = c.NICHES.get(self.niche_code or "")
        return bool(niche and niche.bookable)

    @property
    def lead_id(self) -> int | None:
        return self.lead["id"] if self.lead else None


@dataclass
class ActionResult:
    ok: bool
    quota: Quota | None = None  # set when a limit blocked the action
    error: str | None = None  # user-facing error from an external service


class LeadService:
    def __init__(self, db: Database, usage: UsageService, billing: BillingService, ai: AIProvider,
                 checker: SiteChecker, profiles=None):
        self.profiles = profiles
        self.db = db
        self.usage = usage
        self.billing = billing
        self.ai = ai
        self.checker = checker

    @property
    def ai_ready(self) -> bool:
        return not isinstance(self.ai, NoAIProvider) and self.ai.configured()[0]

    # --- resolving ---------------------------------------------------------------

    async def resolve(self, user_id: int, kind: str, ref: int, idx: int = 0) -> CompanyContext | None:
        if kind == RESULT:
            row = await self.db.fetchone(
                "SELECT r.company, s.service, s.service_title, s.niche FROM search_results r "
                "JOIN searches s ON s.id = r.search_id WHERE r.search_id = ? AND r.idx = ? AND s.user_id = ?",
                ref, idx, user_id)
            if not row:
                return None
            company = Company.from_dict(json.loads(row["company"]))
            lead = await self.db.fetchone("SELECT * FROM leads WHERE user_id = ? AND company_key = ?",
                                          user_id, company.key)
            return CompanyContext(RESULT, ref, idx, company, c.get_service(row["service"], row["service_title"]),
                                  row["niche"], ref, dict(lead) if lead else None, self.ai_ready)
        lead = await self.db.fetchone("SELECT * FROM leads WHERE id = ? AND user_id = ?", ref, user_id)
        if not lead:
            return None
        return CompanyContext(SAVED, ref, 0, Company.from_dict(json.loads(lead["company"])),
                              c.get_service(lead["service"], lead["service_title"]), lead["niche"],
                              lead["search_id"], dict(lead), self.ai_ready)

    async def search_size(self, search_id: int) -> int:
        row = await self.db.fetchone("SELECT COUNT(*) AS n FROM search_results WHERE search_id = ?", search_id)
        return row["n"]

    # --- card & site ---------------------------------------------------------------

    async def open_card(self, user: User, ctx: CompanyContext) -> Quota:
        if ctx.kind == SAVED:
            return Quota(True, 0, None, "total", already=True)  # saved leads are always viewable
        return await self.usage.charge(user, CARD_OPENS, f"card:{ctx.company.key}")

    async def cached_report(self, company: Company) -> SiteReport | None:
        return await load_report(self.db, company.website)

    async def check_site(self, user: User, ctx: CompanyContext, contacts: bool = False) -> ActionResult:
        company = ctx.company
        if not company.website:
            return ActionResult(False, error="В источнике у компании не указан сайт — проверять нечего.")
        if company.is_demo:
            return ActionResult(False, error="Это демо-компания: её сайт не существует, проверка недоступна.")
        cached = await self.cached_report(company)
        if cached and (not contacts or cached.contacts_page is None or cached.contacts_checked or cached.emails):
            return ActionResult(True)
        ref = f"{'contacts' if contacts else 'site'}:{url_key(company.website)}"
        quota = await self.usage.charge(user, SITE_CHECKS, ref)
        if not quota.ok:
            return ActionResult(False, quota=quota)
        report = await self.checker.check(company.website, with_contacts_page=contacts)
        await store_report(self.db, company.website, report)
        return ActionResult(True)

    async def insight(self, ctx: CompanyContext) -> tuple[Insight, SiteReport | None]:
        report = await self.cached_report(ctx.company)
        return build_insight(ctx.company, report, ctx.service, ctx.bookable), report

    # --- AI analysis ----------------------------------------------------------------

    async def cached_analysis(self, ctx: CompanyContext) -> AIAnalysis | None:
        row = await self.db.fetchone("SELECT analysis FROM ai_analyses WHERE company_key = ? AND service = ?",
                                     ctx.company.key, ctx.service.title)
        return AIAnalysis.from_dict(json.loads(row["analysis"])) if row else None

    def _ai_context(self, ctx: CompanyContext, insight: Insight, profile=None) -> AIContext:
        co = ctx.company
        return AIContext(service=ctx.service.title, company_name=co.name, category=co.category, city=co.city,
                         website=co.domain, facts=insight.facts, not_checked=insight.not_checked,
                         hypotheses=insight.opportunities,
                         executor=profile.to_dict() if profile and not profile.is_empty else None)

    async def analyze(self, user: User, ctx: CompanyContext) -> tuple[AIAnalysis | None, ActionResult]:
        cached = await self.cached_analysis(ctx)
        if cached:
            return cached, ActionResult(True)
        if not self.ai_ready:
            return None, ActionResult(False, error=AINotConfigured().user_message)
        ref = f"ai:{ctx.company.key}:{ctx.service.title}"
        quota = await self.usage.charge(user, AI_ANALYSES, ref)
        if not quota.ok:
            return None, ActionResult(False, quota=quota)
        insight, _ = await self.insight(ctx)
        try:
            analysis = await self.ai.analyze(self._ai_context(ctx, insight))
        except AIProviderError as e:
            await self.usage.refund(user, AI_ANALYSES, ref)
            return None, ActionResult(False, error=e.user_message)
        await self.db.execute("INSERT OR REPLACE INTO ai_analyses (company_key, service, analysis, created_at) "
                              "VALUES (?, ?, ?, ?)", ctx.company.key, ctx.service.title,
                              json.dumps(analysis.to_dict(), ensure_ascii=False), now_iso())
        return analysis, ActionResult(True)

    # --- proposals ------------------------------------------------------------------

    async def latest_proposals(self, user_id: int, ctx: CompanyContext) -> tuple[dict[str, str | None], str | None]:
        rows = await self.db.fetchall(
            "SELECT variant, text, source FROM proposals WHERE id IN (SELECT MAX(id) FROM proposals "
            "WHERE user_id = ? AND company_key = ? AND service = ? GROUP BY variant)",
            user_id, ctx.company.key, ctx.service.title)
        texts = {r["variant"]: r["text"] for r in rows}
        source = next((r["source"] for r in rows if r["variant"] != "edited"), None)
        return texts, source

    async def generate_proposals(self, user: User, ctx: CompanyContext,
                                 round_: int = 0) -> tuple[dict[str, str | None], str, ActionResult]:
        ref = f"prop:{ctx.company.key}:{ctx.service.title}:{round_}"
        quota = await self.usage.charge(user, PROPOSALS, ref)
        if not quota.ok:
            return {}, "", ActionResult(False, quota=quota)
        if quota.already:
            texts, source = await self.latest_proposals(user.id, ctx)
            if texts:
                return texts, source or "template", ActionResult(True)
        insight, report = await self.insight(ctx)
        profile = await self.profiles.get(user.id) if self.profiles else None
        source, note = "template", None
        texts: dict[str, str | None]
        if self.ai_ready:
            try:
                texts = await self.ai.write_proposals(self._ai_context(ctx, insight, profile), round_)
                source = "ai"
            except AIProviderError as e:
                note = e.user_message
                texts = template_proposals(ctx.company, report, insight, ctx.service, round_, profile)
        else:
            texts = template_proposals(ctx.company, report, insight, ctx.service, round_, profile)
        now = now_iso()
        for variant, text in texts.items():
            if text:
                await self.db.execute(
                    "INSERT INTO proposals (user_id, company_key, service, variant, text, source, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", user.id, ctx.company.key, ctx.service.title, variant, text,
                    source, now)
        if not texts.get("fact"):
            # an older "fact" text must not be shown next to fresh variants
            await self.db.execute("DELETE FROM proposals WHERE user_id = ? AND company_key = ? AND service = ? "
                                  "AND variant = 'fact'", user.id, ctx.company.key, ctx.service.title)
        if ctx.lead and ctx.lead["status"] in ("new", "check"):
            await self.set_status(ctx, "proposal")
        return texts, source, ActionResult(True, error=note)

    async def save_edited(self, user_id: int, ctx: CompanyContext, text: str) -> None:
        await self.db.execute(
            "INSERT INTO proposals (user_id, company_key, service, variant, text, source, created_at) "
            "VALUES (?, ?, ?, 'edited', ?, 'user', ?)", user_id, ctx.company.key, ctx.service.title, text, now_iso())

    # --- hide -------------------------------------------------------------------------

    async def hide(self, user_id: int, ctx: CompanyContext) -> None:
        await self.db.executemany(
            "INSERT OR IGNORE INTO hidden_companies (user_id, company_key, created_at) VALUES (?, ?, ?)",
            [(user_id, k, now_iso()) for k in ctx.company.keys()])

    # --- CRM ---------------------------------------------------------------------------

    async def save(self, user: User, ctx: CompanyContext) -> tuple[int | None, bool, int | None]:
        """Returns (lead_id, created, limit_if_blocked). Duplicates by any company identity are merged."""
        if ctx.lead:
            return ctx.lead["id"], False, None
        keys = ctx.company.keys()
        marks = ",".join("?" * len(keys))
        dup = await self.db.fetchone(f"SELECT * FROM leads WHERE user_id = ? AND company_key IN ({marks})",
                                     user.id, *keys)
        if dup:
            ctx.lead = dict(dup)
            return dup["id"], False, None
        limit = (await self.billing.plan(user.id)).limit(SAVED_LEADS).amount
        if limit is not None:
            row = await self.db.fetchone("SELECT COUNT(*) AS n FROM leads WHERE user_id = ?", user.id)
            if row["n"] >= limit:
                return None, False, limit
        now = now_iso()
        lead_id = await self.db.execute(
            "INSERT OR IGNORE INTO leads (user_id, company_key, company, service, service_title, niche, search_id, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)",
            user.id, ctx.company.key, json.dumps(ctx.company.to_dict(), ensure_ascii=False), ctx.service.code,
            ctx.service.title, ctx.niche_code, ctx.search_id, now, now)
        lead = await self.db.fetchone("SELECT * FROM leads WHERE user_id = ? AND company_key = ?",
                                      user.id, ctx.company.key)
        ctx.lead = dict(lead)
        return lead["id"], bool(lead_id), None

    async def save_all_with_contacts(self, user: User, search_id: int) -> tuple[int, int | None]:
        """Save every company with contacts from a search. Returns (saved_now, limit_if_reached)."""
        rows = await self.db.fetchall("SELECT idx FROM search_results WHERE search_id = ? ORDER BY idx", search_id)
        saved = 0
        for r in rows:
            ctx = await self.resolve(user.id, RESULT, search_id, r["idx"])
            if not ctx or ctx.lead or not ctx.company.has_contacts:
                continue
            lead_id, created, limit = await self.save(user, ctx)
            if lead_id is None:
                return saved, limit
            saved += int(created)
        return saved, None

    async def set_status(self, ctx: CompanyContext, status: str) -> None:
        if not ctx.lead or status not in c.STATUSES:
            return
        fields = {"status": status, "updated_at": now_iso()}
        for stage in range(1, c.STATUSES[status].stage + 1):
            col = c.STAGE_COLUMNS[stage]
            if not ctx.lead.get(col):
                fields[col] = fields["updated_at"]
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self.db.execute(f"UPDATE leads SET {sets} WHERE id = ?", *fields.values(), ctx.lead["id"])
        ctx.lead.update(fields)

    async def set_deal_amount(self, ctx: CompanyContext, amount: int | None) -> None:
        if ctx.lead:
            await self.db.execute("UPDATE leads SET deal_amount = ?, updated_at = ? WHERE id = ?",
                                  amount, now_iso(), ctx.lead["id"])
            ctx.lead["deal_amount"] = amount

    async def add_note(self, ctx: CompanyContext, text: str) -> None:
        if ctx.lead:
            await self.db.execute("INSERT INTO lead_notes (lead_id, text, created_at) VALUES (?, ?, ?)",
                                  ctx.lead["id"], text, now_iso())
            await self.db.execute("UPDATE leads SET updated_at = ? WHERE id = ?", now_iso(), ctx.lead["id"])

    async def notes(self, lead_id: int, limit: int = 5) -> list[dict]:
        rows = await self.db.fetchall("SELECT * FROM lead_notes WHERE lead_id = ? ORDER BY id DESC LIMIT ?",
                                      lead_id, limit)
        return [dict(r) for r in rows]

    async def delete(self, ctx: CompanyContext) -> None:
        if ctx.lead:
            await self.db.execute("DELETE FROM leads WHERE id = ?", ctx.lead["id"])
            ctx.lead = None

    async def list(self, user_id: int, status: str | None, page: int, per_page: int):
        where, args = ("user_id = ? AND status = ?", (user_id, status)) if status else ("user_id = ?", (user_id,))
        rows = await self.db.fetchall(f"SELECT * FROM leads WHERE {where} ORDER BY updated_at DESC, id DESC "
                                      "LIMIT ? OFFSET ?", *args, per_page, page * per_page)
        total = (await self.db.fetchone(f"SELECT COUNT(*) AS n FROM leads WHERE {where}", *args))["n"]
        return [dict(r) for r in rows], total

    async def counts(self, user_id: int) -> dict[str, int]:
        rows = await self.db.fetchall("SELECT status, COUNT(*) AS n FROM leads WHERE user_id = ? GROUP BY status",
                                      user_id)
        return {r["status"]: r["n"] for r in rows}

    async def recent(self, user_id: int, limit: int = 8) -> list[dict]:
        return [dict(r) for r in await self.db.fetchall(
            "SELECT * FROM leads WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?", user_id, limit)]

    async def export_csv(self, user_id: int) -> bytes:
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Название", "Категория", "Город", "Адрес", "Телефон", "Email", "Сайт", "Источник",
                    "Ссылка на источник", "Статус", "Сумма сделки", "Сохранён"])
        for r in await self.db.fetchall("SELECT * FROM leads WHERE user_id = ? ORDER BY id", user_id):
            co = Company.from_dict(json.loads(r["company"]))
            w.writerow([co.name, co.category, co.city, co.address or "", co.phone or "", co.email or "",
                        co.website or "", co.source_title, co.source_url or "", c.STATUSES[r["status"]].title,
                        r["deal_amount"] or "", r["created_at"][:10]])
        return ("﻿" + buf.getvalue()).encode("utf-8")
