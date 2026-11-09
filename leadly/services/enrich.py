"""Fill missing phone/email from the company's own website (public contact data only)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from leadly.db import Database
from leadly.models import Company, SiteReport
from leadly.services.site_check import SiteChecker, url_key

log = logging.getLogger(__name__)

SITE_CACHE_HOURS = 24


async def load_report(db: Database, website: str | None) -> SiteReport | None:
    if not website:
        return None
    row = await db.fetchone("SELECT report, checked_at FROM site_checks WHERE url_key = ?", url_key(website))
    if not row:
        return None
    age = datetime.now(timezone.utc) - datetime.fromisoformat(row["checked_at"])
    return SiteReport.from_dict(json.loads(row["report"])) if age < timedelta(hours=SITE_CACHE_HOURS) else None


async def store_report(db: Database, website: str, report: SiteReport) -> None:
    await db.execute("INSERT OR REPLACE INTO site_checks (url_key, report, checked_at) VALUES (?, ?, ?)",
                     url_key(website), json.dumps(report.to_dict(), ensure_ascii=False), report.checked_at)


def apply_site_contacts(co: Company, report: SiteReport) -> bool:
    """Copy contacts found on the site into empty fields. Returns True if something was added."""
    if not report.ok:
        return False
    added = False
    if not co.phone and report.phones:
        co.phone = report.phones[0]
        co.contacts_from_site.append("phone")
        added = True
    if not co.email and report.emails:
        co.email = report.emails[0]
        co.contacts_from_site.append("email")
        added = True
    for name, value in report.socials.items():
        if name not in co.socials:
            co.socials[name] = value
            added = True
    return added


async def enrich_contacts(companies: list[Company], checker: SiteChecker, db: Database,
                          limit: int = 12, timeout: float = 15.0) -> int:
    """Look up contacts on websites of companies that have a site but no phone or email.
    Bounded in count and time; partial results are kept. Returns how many companies gained contacts."""
    targets = [co for co in companies if co.website and not co.is_demo and not (co.phone and co.email)][:limit]
    if not targets:
        return 0
    sem = asyncio.Semaphore(6)
    gained = 0

    async def one(co: Company) -> None:
        nonlocal gained
        async with sem:
            report = await load_report(db, co.website)
            if report is None or (report.ok and report.contacts_page and not report.emails):
                report = await checker.check(co.website, with_contacts_page=True)
                await store_report(db, co.website, report)
            if apply_site_contacts(co, report):
                gained += 1

    try:
        await asyncio.wait_for(asyncio.gather(*(one(co) for co in targets), return_exceptions=True), timeout)
    except asyncio.TimeoutError:
        log.info("contact enrichment stopped by timeout, %s companies enriched", gained)
    return gained


async def check_sites(companies: list[Company], checker: SiteChecker, db: Database,
                      limit: int = 30, timeout: float = 30.0) -> None:
    """Make sure websites are checked (cached reports are reused). Bounded in count and time."""
    targets = [co for co in companies if co.website and not co.is_demo][:limit]
    sem = asyncio.Semaphore(8)

    async def one(co: Company) -> None:
        async with sem:
            if await load_report(db, co.website) is None:
                await store_report(db, co.website, await checker.check(co.website, with_contacts_page=True))

    try:
        await asyncio.wait_for(asyncio.gather(*(one(co) for co in targets), return_exceptions=True), timeout)
    except asyncio.TimeoutError:
        log.info("site checks stopped by timeout")


async def score_companies(companies: list[Company], service, niche_code: str | None, db: Database) -> None:
    """Attach problems confirmed by cached site checks; no new network requests."""
    from leadly import catalog as c
    from leadly.services.insights import build_insight
    niche = c.NICHES.get(niche_code or "")
    for co in companies:
        report = await load_report(db, co.website) if co.website else None
        if report:
            apply_site_contacts(co, report)
            co.signals = build_insight(co, report, service, bool(niche and niche.bookable)).signals
            co.cms = report.cms


def rank_key(co: Company):
    """Best prospects first: confirmed problems, then reachable contacts, then a known website."""
    return (-len(co.signals), not co.has_contacts, not co.website)


BUILDERS = ("Tilda", "Wix", "Taplink")
