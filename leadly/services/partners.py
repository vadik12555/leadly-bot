"""Partner program: a blogger brings users with a personal link and earns a share of every payment they make.

Only new users are attached (the first link wins), a partner can't attach himself, and refunded payments
don't count. Payouts are made by the owner outside the bot and recorded here.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from leadly.db import Database, User
from leadly.utils import now_iso

PREFIX = "p_"  # /start p_<code>


@dataclass
class PartnerStats:
    id: int
    code: str
    name: str
    telegram_id: int | None
    percent: int
    active: bool
    users: int  # came via the link
    buyers: int  # of them, paid at least once
    paid_stars: int  # everything they paid (refunds excluded)
    earned: int  # partner's share
    paid_out: int

    @property
    def owed(self) -> int:
        return self.earned - self.paid_out


def commission(stars: int, percent: int) -> int:
    return stars * percent // 100  # whole stars, rounded down


class PartnerService:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def link(bot_username: str, code: str) -> str:
        return f"https://t.me/{bot_username}?start={PREFIX}{code}"

    async def create(self, name: str, percent: int, telegram_id: int | None = None) -> str:
        if not 1 <= percent <= 90:
            raise ValueError("percent must be 1..90")
        if telegram_id and await self.by_telegram(telegram_id):
            raise ValueError("already a partner")
        alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
        while True:
            code = "".join(secrets.choice(alphabet) for _ in range(6))
            if not await self.db.fetchone("SELECT 1 FROM partners WHERE code = ?", code):
                break
        await self.db.execute("INSERT INTO partners (code, name, telegram_id, percent, created_at) "
                              "VALUES (?, ?, ?, ?, ?)", code, name[:60], telegram_id, percent, now_iso())
        return code

    async def attach(self, user: User, payload: str | None, is_new: bool) -> dict | None:
        """`/start p_<code>` from a user who has just appeared. Returns the partner or None."""
        if not is_new or not payload or not payload.startswith(PREFIX):
            return None
        partner = await self.db.fetchone("SELECT * FROM partners WHERE code = ? AND active = 1",
                                         payload[len(PREFIX):].strip().lower())
        if not partner or partner["telegram_id"] == user.id:
            return None
        await self.db.execute("INSERT OR IGNORE INTO partner_users (user_id, partner_id, created_at) "
                              "VALUES (?, ?, ?)", user.id, partner["id"], now_iso())
        return dict(partner)

    async def partner_of(self, user_id: int) -> dict | None:
        row = await self.db.fetchone("SELECT p.* FROM partner_users pu JOIN partners p ON p.id = pu.partner_id "
                                     "WHERE pu.user_id = ?", user_id)
        return dict(row) if row else None

    async def by_code(self, code: str) -> dict | None:
        row = await self.db.fetchone("SELECT * FROM partners WHERE code = ?", code.strip().lower())
        return dict(row) if row else None

    async def by_telegram(self, telegram_id: int) -> dict | None:
        row = await self.db.fetchone("SELECT * FROM partners WHERE telegram_id = ? ORDER BY id LIMIT 1", telegram_id)
        return dict(row) if row else None

    async def stats(self, partner: dict) -> PartnerStats:
        users = (await self.db.fetchone("SELECT COUNT(*) AS n FROM partner_users WHERE partner_id = ?",
                                        partner["id"]))["n"]
        # only real Stars payments made after the user came via the link
        rows = await self.db.fetchall(
            "SELECT p.user_id, p.amount FROM payments p JOIN partner_users pu ON pu.user_id = p.user_id "
            "WHERE pu.partner_id = ? AND p.status = 'paid' AND p.currency = 'XTR' AND p.amount > 0 "
            "AND p.error IS NULL AND p.paid_at >= pu.created_at", partner["id"])
        paid_out = (await self.db.fetchone("SELECT COALESCE(SUM(stars), 0) AS s FROM partner_payouts "
                                           "WHERE partner_id = ?", partner["id"]))["s"]
        return PartnerStats(
            id=partner["id"], code=partner["code"], name=partner["name"], telegram_id=partner["telegram_id"],
            percent=partner["percent"], active=bool(partner["active"]), users=users,
            buyers=len({r["user_id"] for r in rows}), paid_stars=sum(r["amount"] for r in rows),
            earned=sum(commission(r["amount"], partner["percent"]) for r in rows), paid_out=paid_out)

    async def all_stats(self) -> list[PartnerStats]:
        return [await self.stats(dict(p)) for p in await self.db.fetchall("SELECT * FROM partners ORDER BY id")]

    async def record_payout(self, partner: dict, stars: int) -> None:
        await self.db.execute("INSERT INTO partner_payouts (partner_id, stars, created_at) VALUES (?, ?, ?)",
                              partner["id"], stars, now_iso())

    async def set_active(self, partner: dict, active: bool) -> None:
        await self.db.execute("UPDATE partners SET active = ? WHERE id = ?", int(active), partner["id"])
