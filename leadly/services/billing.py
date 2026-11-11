"""Subscriptions and Telegram Stars payments. Plan rights are always resolved server-side
from the subscriptions table; nothing is activated without a confirmed payment."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from leadly.config import Plan
from leadly.db import Database
from datetime import datetime, timezone

from leadly.utils import add_days_iso, now_iso

log = logging.getLogger(__name__)

CURRENCY = "XTR"  # Telegram Stars


@dataclass
class PaymentResult:
    status: str  # activated | renewed | duplicate | unknown_payload | mismatch
    plan: Plan | None = None
    ends_at: str | None = None


class BillingService:
    def __init__(self, db: Database, runtime):
        self.db = db
        self.rt = runtime

    @property
    def product(self):
        return self.rt.settings.product

    # --- plan resolution ---------------------------------------------------------

    async def current(self, user_id: int) -> tuple[Plan, dict | None]:
        now = now_iso()
        rows = await self.db.fetchall(
            "SELECT * FROM subscriptions WHERE user_id = ? AND status = 'active' AND starts_at <= ? AND ends_at > ?",
            user_id, now, now)
        best: tuple[Plan, dict | None] = (self.product.plan("free"), None)
        for r in rows:
            plan = self.product.plan(r["plan"])
            if plan.tier > best[0].tier or (plan.tier == best[0].tier and best[1]
                                             and r["ends_at"] > best[1]["ends_at"]):
                best = (plan, dict(r))
        return best

    async def plan(self, user_id: int) -> Plan:
        return (await self.current(user_id))[0]

    # --- Stars checkout ------------------------------------------------------------

    async def create_invoice(self, user_id: int, plan_code: str, recurring: bool = False,
                             yearly: bool = False) -> tuple[Plan, str]:
        plan = self.product.plans.get(plan_code)
        if not plan or not plan.is_paid or (yearly and not plan.year_price_stars):
            raise ValueError("plan is not purchasable")
        if recurring and plan.period_days != 30:  # Telegram Stars subscriptions renew only every 30 days
            raise ValueError("plan can't auto-renew")
        kind = "year" if yearly else ("rec" if recurring else "sub")
        payload = f"{kind}:{plan.code}:{user_id}:{secrets.token_hex(6)}"
        amount = plan.year_price_stars if yearly else plan.price_stars
        await self.db.execute(
            "INSERT INTO payments (user_id, plan, amount, currency, status, payload, recurring, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)", user_id, plan.code, amount, CURRENCY, payload,
            int(recurring), now_iso())
        return plan, payload

    async def validate_pre_checkout(self, user_id: int, payload: str, currency: str,
                                    amount: int) -> tuple[bool, str | None]:
        row = await self.db.fetchone("SELECT * FROM payments WHERE payload = ?", payload)
        if not row or row["user_id"] != user_id:
            return False, "Счёт не найден. Открой «⭐ Тарифы» и создай новый."
        if row["status"] != "pending":
            return False, "Этот счёт уже оплачен или отменён. Создай новый в «⭐ Тарифы»."
        if currency != CURRENCY or amount != row["amount"]:
            return False, "Сумма счёта изменилась. Создай новый счёт в «⭐ Тарифы»."
        if row["plan"] not in self.product.plans:
            return False, "Этот тариф больше недоступен."
        return True, None

    async def confirm_payment(self, user_id: int, payload: str, charge_id: str, provider_charge_id: str | None,
                              currency: str, amount: int, is_recurring: bool = False,
                              is_first_recurring: bool = False, expires_unix: int | None = None) -> PaymentResult:
        """Handle `successful_payment` (first payment or a Stars subscription renewal). Idempotent by charge id."""
        expires_at = (datetime.fromtimestamp(expires_unix, timezone.utc).isoformat(timespec="seconds")
                      if expires_unix else None)
        async with self.db.transaction():
            dup = await self.db.fetchone("SELECT id FROM payments WHERE telegram_charge_id = ?", charge_id)
            if dup:
                return PaymentResult("duplicate")
            row = await self.db.fetchone("SELECT * FROM payments WHERE payload = ?", payload)
            if row and row["user_id"] == user_id and row["status"] == "paid" and row["recurring"] \
                    and is_recurring and not is_first_recurring:
                # automatic monthly renewal of a Stars subscription
                plan = self.product.plan(row["plan"])
                pid = await self.db.execute(
                    "INSERT INTO payments (user_id, plan, amount, currency, status, payload, telegram_charge_id, "
                    "provider_charge_id, recurring, created_at, paid_at) VALUES (?, ?, ?, ?, 'paid', ?, ?, ?, 1, ?, ?)",
                    user_id, plan.code, amount, currency, f"{payload}:renew:{charge_id}", charge_id,
                    provider_charge_id, now_iso(), now_iso(), commit=False)
                ends_at = await self._extend(user_id, plan, "stars", pid, ends_at=expires_at, auto_renew=True,
                                             star_charge_id=row["telegram_charge_id"])
                return PaymentResult("renewed", plan, ends_at)
            if not row or row["user_id"] != user_id or row["status"] != "pending":
                log.error("successful_payment with unknown payload for user %s", user_id)
                await self.db.execute(
                    "INSERT INTO payments (user_id, plan, amount, currency, status, payload, telegram_charge_id, "
                    "provider_charge_id, error, created_at, paid_at) VALUES (?, 'unknown', ?, ?, 'paid', ?, ?, ?, ?, ?, ?)",
                    user_id, amount, currency, f"orphan:{charge_id}", charge_id, provider_charge_id,
                    "unknown payload", now_iso(), now_iso(), commit=False)
                return PaymentResult("unknown_payload")
            if currency != row["currency"] or amount != row["amount"]:
                await self.db.execute("UPDATE payments SET status = 'paid', telegram_charge_id = ?, "
                                      "provider_charge_id = ?, error = 'amount mismatch', paid_at = ? WHERE id = ?",
                                      charge_id, provider_charge_id, now_iso(), row["id"], commit=False)
                return PaymentResult("mismatch")
            plan = self.product.plan(row["plan"])
            recurring = bool(row["recurring"] and is_recurring)
            days = 365 if payload.startswith("year:") else None
            ends_at = await self._extend(user_id, plan, "stars", row["id"], days=days,
                                         ends_at=expires_at if recurring else None,
                                         auto_renew=recurring, star_charge_id=charge_id if recurring else None)
            await self.db.execute("UPDATE payments SET status = 'paid', telegram_charge_id = ?, "
                                  "provider_charge_id = ?, paid_at = ? WHERE id = ?",
                                  charge_id, provider_charge_id, now_iso(), row["id"], commit=False)
            return PaymentResult("activated", plan, ends_at)

    async def _extend(self, user_id: int, plan: Plan, source: str, payment_id: int | None,
                      days: int | None = None, ends_at: str | None = None, auto_renew: bool = False,
                      star_charge_id: str | None = None) -> str:
        now = now_iso()
        last = await self.db.fetchone(
            "SELECT MAX(ends_at) AS e FROM subscriptions WHERE user_id = ? AND plan = ? AND status = 'active' "
            "AND ends_at > ?", user_id, plan.code, now)
        starts = last["e"] if last and last["e"] else now
        ends = ends_at if ends_at and ends_at > starts else add_days_iso(days or plan.period_days or 30, starts)
        if auto_renew:
            # the previous period of the same Stars subscription no longer needs a renewal flag
            await self.db.execute("UPDATE subscriptions SET auto_renew = 0 WHERE user_id = ? AND star_charge_id = ?",
                                  user_id, star_charge_id, commit=False)
        await self.db.execute(
            "INSERT INTO subscriptions (user_id, plan, starts_at, ends_at, status, source, payment_id, auto_renew, "
            "star_charge_id, created_at) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
            user_id, plan.code, starts, ends, source, payment_id, int(auto_renew), star_charge_id, now, commit=False)
        return ends

    async def auto_renewing(self, user_id: int) -> dict | None:
        row = await self.db.fetchone(
            "SELECT * FROM subscriptions WHERE user_id = ? AND status = 'active' AND auto_renew = 1 "
            "AND ends_at > ? ORDER BY ends_at DESC LIMIT 1", user_id, now_iso())
        return dict(row) if row else None

    async def mark_auto_renew_cancelled(self, user_id: int, star_charge_id: str) -> None:
        await self.db.execute("UPDATE subscriptions SET auto_renew = 0 WHERE user_id = ? AND star_charge_id = ?",
                              user_id, star_charge_id)

    # --- trial & promo codes -----------------------------------------------------------

    async def trial_available(self, user) -> bool:
        if not self.product.trial_plan or not self.product.trial_days or user.trial_used_at:
            return False
        return (await self.plan(user.id)).tier == 0

    async def start_trial(self, user) -> tuple[Plan, str] | None:
        async with self.db.transaction():
            row = await self.db.fetchone("SELECT trial_used_at FROM users WHERE id = ?", user.id)
            if row["trial_used_at"] or not self.product.trial_plan:
                return None
            plan = self.product.plan(self.product.trial_plan)
            await self.db.execute("UPDATE users SET trial_used_at = ? WHERE id = ?", now_iso(), user.id, commit=False)
            ends = await self._extend(user.id, plan, "trial", None, self.product.trial_days)
        user.trial_used_at = now_iso()
        return plan, ends

    async def create_promo(self, code: str, plan_code: str, days: int, max_uses: int, admin_id: int) -> None:
        plan = self.product.plans.get(plan_code)
        if not plan or not plan.is_paid or days <= 0 or max_uses <= 0:
            raise ValueError("bad promo parameters")
        await self.db.execute(
            "INSERT INTO promo_codes (code, plan, days, max_uses, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            code.upper(), plan.code, days, max_uses, admin_id, now_iso())

    async def create_keys(self, plan_code: str, days: int, count: int, admin_id: int) -> list[str]:
        """Single-use activation keys (gifts, partners, sales by invoice outside Telegram)."""
        if count <= 0 or count > 200:
            raise ValueError("count must be 1..200")
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I to avoid typos
        keys = []
        while len(keys) < count:
            key = "LDY-" + "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2))
            try:
                await self.create_promo(key, plan_code, days, 1, admin_id)
                keys.append(key)
            except ValueError:
                raise
            except Exception:  # extremely unlikely collision: generate another
                continue
        return keys

    async def keys_summary(self) -> dict:
        row = await self.db.fetchone("SELECT COUNT(*) AS total, SUM(uses) AS used FROM promo_codes WHERE code LIKE 'LDY-%'")
        return {"total": row["total"] or 0, "used": row["used"] or 0}

    async def redeem_promo(self, user_id: int, code: str) -> tuple[str, Plan | None, str | None]:
        """Returns (status, plan, ends_at); status: ok | unknown | used_up | already."""
        code = code.strip().upper()
        async with self.db.transaction():
            promo = await self.db.fetchone("SELECT * FROM promo_codes WHERE code = ? AND active = 1", code)
            if not promo:
                return "unknown", None, None
            if await self.db.fetchone("SELECT 1 FROM promo_redemptions WHERE code = ? AND user_id = ?", code, user_id):
                return "already", None, None
            if promo["uses"] >= promo["max_uses"]:
                return "used_up", None, None
            await self.db.execute("INSERT INTO promo_redemptions (code, user_id, created_at) VALUES (?, ?, ?)",
                                  code, user_id, now_iso(), commit=False)
            await self.db.execute("UPDATE promo_codes SET uses = uses + 1 WHERE code = ?", code, commit=False)
            plan = self.product.plan(promo["plan"])
            ends = await self._extend(user_id, plan, "promo", None, promo["days"])
        return "ok", plan, ends

    # --- admin operations -----------------------------------------------------------

    async def grant(self, user_id: int, plan_code: str, days: int) -> str:
        plan = self.product.plans.get(plan_code)
        if not plan or not plan.is_paid:
            raise ValueError("unknown paid plan")
        async with self.db.transaction():
            pid = await self.db.execute(
                "INSERT INTO payments (user_id, plan, amount, currency, status, payload, created_at, paid_at) "
                "VALUES (?, ?, 0, 'manual', 'manual', ?, ?, ?)",
                user_id, plan.code, f"manual:{user_id}:{secrets.token_hex(6)}", now_iso(), now_iso(), commit=False)
            return await self._extend(user_id, plan, "manual", pid, days)

    async def payment_by_charge(self, charge_id: str):
        return await self.db.fetchone("SELECT * FROM payments WHERE telegram_charge_id = ?", charge_id)

    async def mark_refunded(self, charge_id: str) -> None:
        async with self.db.transaction():
            row = await self.payment_by_charge(charge_id)
            if not row:
                return
            await self.db.execute("UPDATE payments SET status = 'refunded' WHERE id = ?", row["id"], commit=False)
            await self.db.execute("UPDATE subscriptions SET status = 'refunded' WHERE payment_id = ?", row["id"],
                                  commit=False)

    # --- reminders ----------------------------------------------------------------------

    async def due_reminders(self) -> tuple[list[dict], list[dict]]:
        now = now_iso()
        soon = add_days_iso(self.product.reminder_days)
        expiring = await self.db.fetchall(
            "SELECT * FROM subscriptions WHERE status = 'active' AND notified_expiring = 0 AND auto_renew = 0 "
            "AND ends_at > ? AND ends_at <= ?", now, soon)
        expired = await self.db.fetchall(
            "SELECT * FROM subscriptions WHERE status = 'active' AND notified_expired = 0 AND ends_at <= ?", now)
        # don't warn about a subscription that is already followed by a newer one
        result_expiring, result_expired = [], []
        for rows, out in ((expiring, result_expiring), (expired, result_expired)):
            for r in rows:
                newer = await self.db.fetchone(
                    "SELECT 1 FROM subscriptions WHERE user_id = ? AND status = 'active' AND ends_at > ? "
                    "AND id != ?", r["user_id"], r["ends_at"], r["id"])
                if newer:
                    await self.mark_notified(r["id"], "both")
                else:
                    out.append(dict(r))
        return result_expiring, result_expired

    async def mark_notified(self, sub_id: int, which: str) -> None:
        cols = {"expiring": "notified_expiring = 1", "expired": "notified_expired = 1",
                "both": "notified_expiring = 1, notified_expired = 1"}[which]
        await self.db.execute(f"UPDATE subscriptions SET {cols} WHERE id = ?", sub_id)
