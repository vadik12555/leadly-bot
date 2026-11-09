"""User statistics, referrals and admin overview — all computed from database records."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from leadly.config import CARD_OPENS
from leadly.db import Database, User
from leadly.utils import now_iso


class AccountService:
    def __init__(self, db: Database, runtime):
        self.db = db
        self.rt = runtime

    async def stats(self, user_id: int) -> dict[str, int]:
        one = self.db.fetchone
        searches = (await one("SELECT COUNT(*) AS n FROM searches WHERE user_id = ? AND status = 'ok'", user_id))["n"]
        viewed = (await one("SELECT COUNT(*) AS n FROM usage_events WHERE user_id = ? AND kind = ?",
                            user_id, CARD_OPENS))["n"]
        leads = await one(
            "SELECT COUNT(*) AS saved, COUNT(contacted_at) AS contacted, COUNT(replied_at) AS replied, "
            "COUNT(interested_at) AS interested, COUNT(deal_at) AS deals, "
            "COALESCE(SUM(CASE WHEN status = 'deal' THEN deal_amount END), 0) AS revenue "
            "FROM leads WHERE user_id = ?", user_id)
        proposals = (await one("SELECT COUNT(DISTINCT company_key) AS n FROM proposals WHERE user_id = ?",
                               user_id))["n"]
        return {"searches": searches, "viewed": viewed, "proposals": proposals,
                **{k: (v or 0) for k, v in dict(leads).items()}}

    # --- referrals -------------------------------------------------------------------

    def referral_link(self, user: User, bot_username: str) -> str:
        return f"https://t.me/{bot_username}?start=ref_{user.id}"

    async def apply_referral(self, invited: User, payload: str | None, is_new: bool) -> int | None:
        """Link a newly registered user to the referrer from `/start ref_<id>`. One-time, no self-invites."""
        if not is_new or not payload or not payload.startswith("ref_") or invited.referred_by:
            return None
        try:
            referrer_id = int(payload[4:])
        except ValueError:
            return None
        if referrer_id == invited.id or not await self.db.get_user(referrer_id):
            return None
        product = self.rt.settings.product
        async with self.db.transaction():
            exists = await self.db.fetchone("SELECT 1 FROM referrals WHERE invited_id = ?", invited.id)
            if exists:
                return None
            await self.db.execute("INSERT INTO referrals (invited_id, referrer_id, bonus, created_at) "
                                  "VALUES (?, ?, ?, ?)", invited.id, referrer_id, product.referral_bonus_referrer,
                                  now_iso(), commit=False)
            await self.db.execute("UPDATE users SET referred_by = ?, bonus_searches = bonus_searches + ? "
                                  "WHERE id = ?", referrer_id, product.referral_bonus_invited, invited.id,
                                  commit=False)
            await self.db.execute("UPDATE users SET bonus_searches = bonus_searches + ? WHERE id = ?",
                                  product.referral_bonus_referrer, referrer_id, commit=False)
        invited.referred_by = referrer_id
        invited.bonus_searches += product.referral_bonus_invited
        return referrer_id

    async def referral_stats(self, user_id: int) -> tuple[int, int]:
        row = await self.db.fetchone("SELECT COUNT(*) AS n, COALESCE(SUM(bonus), 0) AS b FROM referrals "
                                     "WHERE referrer_id = ?", user_id)
        return row["n"], row["b"]

    # --- admin -------------------------------------------------------------------------

    async def admin_overview(self) -> dict:
        one = self.db.fetchone
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
        day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        now = now_iso()
        plans = await self.db.fetchall(
            "SELECT plan, COUNT(DISTINCT user_id) AS n FROM subscriptions WHERE status = 'active' "
            "AND starts_at <= ? AND ends_at > ? GROUP BY plan", now, now)
        return {
            "users": (await one("SELECT COUNT(*) AS n FROM users"))["n"],
            "active_7d": (await one("SELECT COUNT(*) AS n FROM users WHERE last_seen_at >= ?", week_ago))["n"],
            "active_1d": (await one("SELECT COUNT(*) AS n FROM users WHERE last_seen_at >= ?", day_ago))["n"],
            "paid_by_plan": {r["plan"]: r["n"] for r in plans},
            "searches": (await one("SELECT COUNT(*) AS n FROM searches WHERE status = 'ok'"))["n"],
            "searches_failed": (await one("SELECT COUNT(*) AS n FROM searches WHERE status = 'error'"))["n"],
            "searches_24h": (await one("SELECT COUNT(*) AS n FROM searches WHERE created_at >= ?", day_ago))["n"],
            "leads": (await one("SELECT COUNT(*) AS n FROM leads"))["n"],
            "stars_paid": (await one("SELECT COALESCE(SUM(amount), 0) AS n FROM payments WHERE status = 'paid' "
                                     "AND currency = 'XTR'"))["n"],
            "f_users": (await one("SELECT COUNT(*) AS n FROM users"))["n"],
            "f_users_7d": (await one("SELECT COUNT(*) AS n FROM users WHERE created_at >= ?", week_ago))["n"],
            "f_searched": (await one("SELECT COUNT(DISTINCT user_id) AS n FROM searches WHERE status = 'ok'"))["n"],
            "f_searched_7d": (await one("SELECT COUNT(DISTINCT s.user_id) AS n FROM searches s JOIN users u "
                                        "ON u.id = s.user_id WHERE s.status = 'ok' AND u.created_at >= ?",
                                        week_ago))["n"],
            "f_saved": (await one("SELECT COUNT(DISTINCT user_id) AS n FROM leads"))["n"],
            "f_saved_7d": (await one("SELECT COUNT(DISTINCT l.user_id) AS n FROM leads l JOIN users u "
                                     "ON u.id = l.user_id WHERE u.created_at >= ?", week_ago))["n"],
            "f_paid": (await one("SELECT COUNT(DISTINCT user_id) AS n FROM payments WHERE status = 'paid' "
                                 "AND currency = 'XTR'"))["n"],
            "f_paid_7d": (await one("SELECT COUNT(DISTINCT p.user_id) AS n FROM payments p JOIN users u "
                                    "ON u.id = p.user_id WHERE p.status = 'paid' AND p.currency = 'XTR' "
                                    "AND u.created_at >= ?", week_ago))["n"],
            "watches": (await one("SELECT COUNT(*) AS n FROM watches WHERE active = 1"))["n"],
        }

    async def last_search_error(self) -> dict | None:
        row = await self.db.fetchone("SELECT error, created_at FROM searches WHERE status = 'error' "
                                     "ORDER BY id DESC LIMIT 1")
        return dict(row) if row else None

    async def user_card(self, user_id: int) -> dict | None:
        user = await self.db.get_user(user_id)
        if not user:
            return None
        subs = await self.db.fetchall("SELECT * FROM subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 5",
                                      user_id)
        pays = await self.db.fetchall("SELECT * FROM payments WHERE user_id = ? AND status != 'pending' "
                                      "ORDER BY id DESC LIMIT 5", user_id)
        return {"user": user, "subscriptions": [dict(s) for s in subs], "payments": [dict(p) for p in pays],
                "stats": await self.stats(user_id)}


    # --- personal data ---------------------------------------------------------------------

    async def delete_user_data(self, user_id: int) -> None:
        """Erase the user's personal data. Payment records are kept: they are financial documents."""
        async with self.db.transaction():
            for sql in (
                "DELETE FROM lead_notes WHERE lead_id IN (SELECT id FROM leads WHERE user_id = ?)",
                "DELETE FROM leads WHERE user_id = ?",
                "DELETE FROM search_results WHERE search_id IN (SELECT id FROM searches WHERE user_id = ?)",
                "DELETE FROM searches WHERE user_id = ?",
                "DELETE FROM proposals WHERE user_id = ?",
                "DELETE FROM hidden_companies WHERE user_id = ?",
                "DELETE FROM seen_companies WHERE user_id = ?",
                "DELETE FROM watches WHERE user_id = ?",
                "DELETE FROM profiles WHERE user_id = ?",
                "DELETE FROM templates WHERE user_id = ?",
                "DELETE FROM fsm WHERE key LIKE '%:' || ? || ':%'",
                "UPDATE users SET username = NULL, first_name = NULL, city = NULL WHERE id = ?",
            ):
                await self.db.execute(sql, user_id, commit=False)


    # --- channel bonus -----------------------------------------------------------------------

    async def claim_channel_bonus(self, user: User, bonus: int) -> bool:
        """One-time bonus for subscribing to the owner's channel (subscription checked by the caller)."""
        async with self.db.transaction():
            row = await self.db.fetchone("SELECT channel_bonus_at FROM users WHERE id = ?", user.id)
            if row["channel_bonus_at"]:
                return False
            await self.db.execute("UPDATE users SET channel_bonus_at = ?, bonus_searches = bonus_searches + ? "
                                  "WHERE id = ?", now_iso(), bonus, user.id, commit=False)
        user.channel_bonus_at = now_iso()
        user.bonus_searches += bonus
        return True


    async def niche_stats(self, user_id: int) -> list[dict]:
        from leadly import catalog as c
        rows = await self.db.fetchall(
            "SELECT COALESCE(niche, 'custom') AS niche, COUNT(*) AS saved, COUNT(contacted_at) AS contacted, "
            "COUNT(replied_at) AS replied, COUNT(deal_at) AS deals FROM leads WHERE user_id = ? "
            "GROUP BY COALESCE(niche, 'custom') ORDER BY deals DESC, replied DESC, saved DESC", user_id)
        out = []
        for r in rows:
            niche = c.NICHES.get(r["niche"])
            out.append({**dict(r), "label": niche.label if niche else "🔎 Другие"})
        return out

    async def set_notify(self, user: User, what: str) -> None:
        col = {"watches": "notify_watches", "followups": "notify_followups"}[what]
        value = 0 if getattr(user, col) else 1
        await self.db.execute(f"UPDATE users SET {col} = ? WHERE id = ?", value, user.id)
        setattr(user, col, value)


    # --- daily goal & streak -------------------------------------------------------------------

    async def goal_progress(self, user: User) -> tuple[int, int, int]:
        """(written today, goal, streak in days). A day counts if at least one company was contacted (MSK)."""
        from datetime import date, timedelta
        from leadly.utils import MSK
        rows = await self.db.fetchall("SELECT contacted_at FROM leads WHERE user_id = ? AND contacted_at IS NOT NULL",
                                      user.id)
        days = {datetime.fromisoformat(r["contacted_at"]).astimezone(MSK).date() for r in rows}
        today = datetime.now(MSK).date()
        written_today = sum(1 for r in rows
                            if datetime.fromisoformat(r["contacted_at"]).astimezone(MSK).date() == today)
        streak, day = 0, today if today in days else today - timedelta(days=1)
        while day in days:
            streak += 1
            day -= timedelta(days=1)
        return written_today, user.daily_goal, streak

    async def set_goal(self, user: User, goal: int) -> None:
        goal = max(1, min(goal, 50))
        await self.db.execute("UPDATE users SET daily_goal = ? WHERE id = ?", goal, user.id)
        user.daily_goal = goal
