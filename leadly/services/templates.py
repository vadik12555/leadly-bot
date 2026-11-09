"""User message templates with variables filled from the company, the check and the user's profile."""

from __future__ import annotations

import re

from leadly import catalog as c
from leadly.db import Database
from leadly.models import Company, SiteReport
from leadly.utils import now_iso

MAX_TEMPLATES = 20
VARIABLES = {
    "компания": "название компании",
    "город": "город",
    "категория": "сфера компании",
    "сайт": "домен сайта (если есть)",
    "наблюдение": "подтверждённый факт с проверки сайта (если есть)",
    "услуга": "твоя услуга",
    "имя": "твоё имя из профиля",
    "портфолио": "ссылка на портфолио из профиля",
    "контакт": "твой контакт из профиля",
    "цена": "цены из профиля",
}


def render(text: str, company: Company, service: c.Service, profile=None,
           report: SiteReport | None = None) -> tuple[str, list[str]]:
    """Fill variables. Returns (text, names of variables that had no value)."""
    from leadly.services.writer import _observation
    values = {
        "компания": company.name, "город": company.city, "категория": company.category.lower(),
        "сайт": company.domain or "", "наблюдение": _observation(company, report, service) or "",
        "услуга": service.title.lower(),
        "имя": getattr(profile, "name", None) or "", "портфолио": getattr(profile, "portfolio", None) or "",
        "контакт": getattr(profile, "contact", None) or "", "цена": getattr(profile, "price", None) or "",
    }
    missing: list[str] = []

    def sub(m: re.Match) -> str:
        key = m.group(1).strip().lower()
        if key not in values:
            return m.group(0)
        if not values[key]:
            missing.append(key)
        return values[key]

    out = re.sub(r"\{([^{}]{1,20})\}", sub, text)
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    return out, sorted(set(missing))


def templatize(text: str, company: Company) -> str:
    """Turn a ready message back into a reusable template."""
    out = text.replace(company.name, "{компания}")
    if company.domain:
        out = out.replace(company.domain, "{сайт}")
    return out


class TemplateService:
    def __init__(self, db: Database):
        self.db = db

    async def list(self, user_id: int) -> list[dict]:
        return [dict(r) for r in await self.db.fetchall(
            "SELECT * FROM templates WHERE user_id = ? ORDER BY id", user_id)]

    async def get(self, user_id: int, template_id: int) -> dict | None:
        row = await self.db.fetchone("SELECT * FROM templates WHERE id = ? AND user_id = ?", template_id, user_id)
        return dict(row) if row else None

    async def add(self, user_id: int, title: str, text: str) -> int | None:
        count = (await self.db.fetchone("SELECT COUNT(*) AS n FROM templates WHERE user_id = ?", user_id))["n"]
        if count >= MAX_TEMPLATES:
            return None
        return await self.db.execute(
            "INSERT INTO templates (user_id, title, text, created_at) VALUES (?, ?, ?, ?)",
            user_id, title.strip()[:60] or "Шаблон", text.strip()[:3500], now_iso())

    async def delete(self, user_id: int, template_id: int) -> None:
        await self.db.execute("DELETE FROM templates WHERE id = ? AND user_id = ?", template_id, user_id)
