"""Plans, Telegram Stars payments (one-time invoices and auto-renewing subscriptions), trial, promo codes."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from leadly.bot.callbacks import Menu, Pay
from leadly.bot.states import PromoInput
from leadly.bot.ui import USER_TEXT, show
from leadly.bot.views import cabinet as cv
from leadly.bot.views import partners as pv
from leadly.db import User
from leadly.services.partners import commission
from leadly.services import Services
from leadly.services.billing import CURRENCY
from leadly.bot.views import h
from leadly.utils import fmt_date

log = logging.getLogger(__name__)

SUBSCRIPTION_PERIOD = 2592000  # 30 days — the only period Telegram allows for Stars subscriptions


async def _plans(target, user: User, svc: Services) -> None:
    plan, sub = await svc.billing.current(user.id)
    await show(target, cv.plans_screen(svc.settings.product, plan, sub["ends_at"] if sub else None,
                                       svc.settings.payments_enabled, user.bonus_searches,
                                       await svc.billing.trial_available(user),
                                       await svc.billing.auto_renewing(user.id), ai=svc.leads.ai_ready,
                                       promo=bool(await svc.db.fetchone("SELECT 1 FROM promo_codes LIMIT 1"))))


async def cb_plans(cb: CallbackQuery, user: User, svc: Services, state: FSMContext) -> None:
    await state.set_state(None)
    await _plans(cb, user, svc)


async def cmd_plans(message: Message, user: User, svc: Services) -> None:
    await _plans(message, user, svc)


async def cb_buy(cb: CallbackQuery, callback_data: Pay, user: User, svc: Services, bot: Bot) -> None:
    """One-time purchase for one period."""
    if not svc.settings.payments_enabled:
        return await cb.answer("Оплата временно недоступна", show_alert=True)
    try:
        plan, payload = await svc.billing.create_invoice(user.id, callback_data.plan)
    except ValueError:
        return await cb.answer("Этот тариф недоступен", show_alert=True)
    title, description = cv.invoice_texts(plan)
    await bot.send_invoice(
        chat_id=cb.from_user.id, title=title, description=description, payload=payload,
        currency=CURRENCY, prices=[LabeledPrice(label=f"Leadly {plan.title}", amount=plan.price_stars)],
    )


async def cb_buy_year(cb: CallbackQuery, callback_data: Pay, user: User, svc: Services, bot: Bot) -> None:
    """One-time purchase for 365 days at a discount."""
    if not svc.settings.payments_enabled:
        return await cb.answer("Оплата временно недоступна", show_alert=True)
    try:
        plan, payload = await svc.billing.create_invoice(user.id, callback_data.plan, yearly=True)
    except ValueError:
        return await cb.answer("Годовой вариант недоступен", show_alert=True)
    title, description = cv.invoice_texts(plan, yearly=True)
    await bot.send_invoice(
        chat_id=cb.from_user.id, title=title, description=description, payload=payload, currency=CURRENCY,
        prices=[LabeledPrice(label=f"Leadly {plan.title} на год", amount=plan.year_price_stars)],
    )


async def cb_subscribe(cb: CallbackQuery, callback_data: Pay, user: User, svc: Services, bot: Bot) -> None:
    """Auto-renewing Stars subscription (createInvoiceLink with subscription_period)."""
    if not svc.settings.payments_enabled:
        return await cb.answer("Оплата временно недоступна", show_alert=True)
    if await svc.billing.auto_renewing(user.id):
        return await cb.answer("У тебя уже есть подписка с автопродлением", show_alert=True)
    try:
        plan, payload = await svc.billing.create_invoice(user.id, callback_data.plan, recurring=True)
    except ValueError:
        return await cb.answer("Этот тариф недоступен", show_alert=True)
    title, description = cv.invoice_texts(plan, recurring=True)
    link = await bot.create_invoice_link(
        title=title, description=description, payload=payload, currency=CURRENCY,
        prices=[LabeledPrice(label=f"Leadly {plan.title}", amount=plan.price_stars)],
        subscription_period=SUBSCRIPTION_PERIOD,
    )
    await show(cb, cv.subscription_link(plan, link))


async def cb_cancel_renew(cb: CallbackQuery, user: User, svc: Services, bot: Bot) -> None:
    sub = await svc.billing.auto_renewing(user.id)
    if not sub:
        return await cb.answer("Автопродление уже выключено", show_alert=True)
    try:
        await bot.edit_user_star_subscription(user_id=user.id, telegram_payment_charge_id=sub["star_charge_id"],
                                              is_canceled=True)
    except Exception as e:
        log.error("cancel star subscription failed for %s: %s", user.id, e)
        return await cb.answer("Telegram не принял отмену. Попробуй позже или отключи в настройках Telegram.",
                               show_alert=True)
    await svc.billing.mark_auto_renew_cancelled(user.id, sub["star_charge_id"])
    await cb.answer("Автопродление отключено. Доступ сохранится до конца оплаченного периода.", show_alert=True)
    await _plans(cb, user, svc)


async def cb_trial(cb: CallbackQuery, user: User, svc: Services) -> None:
    if not await svc.billing.trial_available(user):
        return await cb.answer("Пробный период уже использован или у тебя платный тариф", show_alert=True)
    result = await svc.billing.start_trial(user)
    if not result:
        return await cb.answer("Пробный период уже использован", show_alert=True)
    await show(cb, cv.trial_started(*result))


async def cb_promo(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PromoInput.code)
    await show(cb, cv.ask_promo())


async def msg_promo(message: Message, state: FSMContext, user: User, svc: Services) -> None:
    code = (message.text or "").strip()
    if not code or len(code) > 40:
        return await message.answer("Пришли промокод одним словом или /cancel.")
    status, plan, ends = await svc.billing.redeem_promo(user.id, code)
    if status != "ok":
        return await message.answer(cv.PROMO_ERRORS[status] + " Попробуй другой или /cancel.")
    await state.set_state(None)
    await show(message, cv.promo_ok(plan, ends))
    await _notify_admins(message, svc, f"🔑 Активирован код <code>{h(code.upper())}</code>: {plan.title} до "
                         f"{fmt_date(ends)}\nОт: {_who(message)} (id {message.from_user.id})")


async def on_pre_checkout(query: PreCheckoutQuery, svc: Services) -> None:
    ok, error = await svc.billing.validate_pre_checkout(query.from_user.id, query.invoice_payload,
                                                        query.currency, query.total_amount)
    await query.answer(ok=ok, error_message=error)


async def on_successful_payment(message: Message, svc: Services) -> None:
    sp = message.successful_payment
    result = await svc.billing.confirm_payment(
        user_id=message.from_user.id, payload=sp.invoice_payload, charge_id=sp.telegram_payment_charge_id,
        provider_charge_id=sp.provider_payment_charge_id, currency=sp.currency, amount=sp.total_amount,
        is_recurring=bool(sp.is_recurring), is_first_recurring=bool(sp.is_first_recurring),
        expires_unix=sp.subscription_expiration_date)
    log.info("payment %s from user %s: %s", sp.telegram_payment_charge_id, message.from_user.id, result.status)
    if result.status in ("activated", "renewed"):
        await show(message, cv.payment_ok(result.plan, result.ends_at, renewed=result.status == "renewed"))
        partner_line = await _credit_partner(message, svc, sp.total_amount)
        await _notify_admins(message, svc, f"💰 <b>{'Продление' if result.status == 'renewed' else 'Оплата'}</b>: "
                             f"{result.plan.title} · {sp.total_amount} ⭐\nОт: {_who(message)} (id {message.from_user.id})"
                             f"\nДо: {fmt_date(result.ends_at)}" + partner_line)
    elif result.status == "duplicate":
        await message.answer("Этот платёж уже зачислен ✅")
    else:
        log.error("payment needs manual review: %s (%s)", sp.telegram_payment_charge_id, result.status)
        await message.answer(cv.payment_problem(svc.settings.support_contact))


async def _credit_partner(message: Message, svc: Services, amount: int) -> str:
    """Tell the partner about his share. Returns a line for the admin notice ('' without a partner)."""
    partner = await svc.partners.partner_of(message.from_user.id)
    if not partner:
        return ""
    share = commission(amount, partner["percent"])
    stats = await svc.partners.stats(partner)
    if partner["telegram_id"]:
        try:
            await message.bot.send_message(partner["telegram_id"], pv.partner_payment_notice(
                share, amount, partner["percent"], stats.owed))
        except Exception as e:  # the partner may have blocked the bot: the share is counted anyway
            log.info("partner %s not notified: %s", partner["code"], type(e).__name__)
    return f"\n🤝 Партнёр: {h(partner['name'])} · {partner['percent']}% = {share} ⭐ (к выплате {stats.owed} ⭐)"


def _who(message: Message) -> str:
    u = message.from_user
    return f"@{h(u.username)}" if u.username else h(u.full_name)


async def _notify_admins(message: Message, svc: Services, text: str) -> None:
    for admin in svc.settings.admin_ids:
        try:
            await message.bot.send_message(admin, text)
        except Exception as e:
            log.warning("payment notice to admin %s failed: %s", admin, e)


def create_router() -> Router:
    r = Router(name="billing")
    r.callback_query.register(cb_plans, Menu.filter(F.to == "plans"))
    r.message.register(cmd_plans, Command("pro", "plans"))
    r.callback_query.register(cb_buy, Pay.filter(F.act == "buy"))
    r.callback_query.register(cb_subscribe, Pay.filter(F.act == "sub"))
    r.callback_query.register(cb_buy_year, Pay.filter(F.act == "year"))
    r.callback_query.register(cb_cancel_renew, Pay.filter(F.act == "cancel"))
    r.callback_query.register(cb_trial, Pay.filter(F.act == "trial"))
    r.callback_query.register(cb_promo, Pay.filter(F.act == "promo"))
    r.message.register(msg_promo, PromoInput.code, USER_TEXT)
    r.pre_checkout_query.register(on_pre_checkout)
    r.message.register(on_successful_payment, F.successful_payment)
    return r
