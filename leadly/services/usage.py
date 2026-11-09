"""Usage limits stored in the database.

Every charge carries an idempotency `ref`: repeating the same action (double tap,
re-opening the same company card) never spends the limit twice.
"""

from __future__ import annotations

from dataclasses import dataclass

from leadly.config import SEARCHES, USAGE_KINDS
from leadly.db import Database, User
from leadly.services.billing import BillingService
from leadly.utils import now_iso, today_msk


@dataclass
class Quota:
    ok: bool
    used: int
    limit: int | None
    period: str
    via_bonus: bool = False
    already: bool = False  # this exact action was charged before (free repeat)

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(0, self.limit - self.used)


def period_key(period: str) -> str:
    return today_msk() if period == "day" else "all"


class UsageService:
    def __init__(self, db: Database, billing: BillingService):
        self.db = db
        self.billing = billing

    async def _used(self, user_id: int, kind: str, period: str) -> int:
        row = await self.db.fetchone(
            "SELECT COALESCE(SUM(amount), 0) AS n FROM usage_events WHERE user_id = ? AND kind = ? "
            "AND period_key = ?", user_id, kind, period_key(period))
        return row["n"]

    async def peek(self, user: User, kind: str, amount: int = 1, ref: str | None = None) -> Quota:
        plan = await self.billing.plan(user.id)
        limit = plan.limit(kind)
        used = await self._used(user.id, kind, limit.period)
        if ref and await self.db.fetchone("SELECT 1 FROM usage_events WHERE user_id = ? AND kind = ? AND ref = ?",
                                          user.id, kind, ref):
            return Quota(True, used, limit.amount, limit.period, already=True)
        if limit.amount is None or used + amount <= limit.amount:
            return Quota(True, used, limit.amount, limit.period)
        if kind == SEARCHES and user.bonus_searches > 0:
            return Quota(True, used, limit.amount, limit.period, via_bonus=True)
        return Quota(False, used, limit.amount, limit.period)

    async def charge(self, user: User, kind: str, ref: str, amount: int = 1) -> Quota:
        """Check and spend atomically. Returns quota.ok=False when the limit is exhausted."""
        async with self.db.transaction():
            quota = await self.peek(user, kind, amount, ref)
            if not quota.ok or quota.already:
                return quota
            if quota.via_bonus:
                await self.db.execute("UPDATE users SET bonus_searches = MAX(0, bonus_searches - 1) WHERE id = ?",
                                      user.id, commit=False)
                user.bonus_searches = max(0, user.bonus_searches - 1)
            await self.db.execute(
                "INSERT OR IGNORE INTO usage_events (user_id, kind, ref, amount, period_key, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)", user.id, kind, ref, amount,
                "bonus" if quota.via_bonus else period_key(quota.period), now_iso(), commit=False)
            quota.used += amount
            return quota

    async def refund(self, user: User, kind: str, ref: str) -> None:
        """Undo a charge when the action failed (e.g. data source was down)."""
        async with self.db.transaction():
            row = await self.db.fetchone("SELECT * FROM usage_events WHERE user_id = ? AND kind = ? AND ref = ?",
                                         user.id, kind, ref)
            if not row:
                return
            await self.db.execute("DELETE FROM usage_events WHERE id = ?", row["id"], commit=False)
            if row["period_key"] == "bonus":
                await self.db.execute("UPDATE users SET bonus_searches = bonus_searches + 1 WHERE id = ?",
                                      user.id, commit=False)
                user.bonus_searches += 1

    async def remaining(self, user: User, kind: str) -> int | None:
        return (await self.peek(user, kind, 0)).remaining

    async def summary(self, user: User) -> dict[str, Quota]:
        return {kind: await self.peek(user, kind, 0) for kind in USAGE_KINDS}
