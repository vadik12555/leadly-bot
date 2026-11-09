"""/start (with referral deep links), main menu, help, cancel, legal commands."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from leadly.bot import views
from leadly.bot.callbacks import Menu
from leadly.bot.ui import show
from leadly.bot.views import cabinet as cv
from leadly.config import SEARCHES
from leadly.db import User
from leadly.services import Services
from leadly.utils import plural

log = logging.getLogger(__name__)


async def welcome_screen(svc: Services, user: User, first_time: bool = False):
    plan = await svc.billing.plan(user.id)
    summary = await svc.usage.summary(user)
    src = svc.search_sources
    trial = svc.settings.product.trial_days if await svc.billing.trial_available(user) else 0
    channel = svc.settings.channel_username or None
    bonus = svc.settings.product.channel_bonus if channel and not user.channel_bonus_at else 0
    goal = await svc.account.goal_progress(user)
    product = svc.settings.product
    locked = summary[SEARCHES].limit == 0 and not user.bonus_searches
    return views.welcome(plan, summary, src.title, src.is_demo, first_time, trial, channel, bonus, goal,
                         referral=product.referral_enabled, price_from=product.lowest_price if locked else 0,
                         ai=svc.leads.ai_ready)


async def cmd_start(message: Message, command: CommandObject, user: User, is_new_user: bool,
                    svc: Services, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    referrer_id = await svc.account.apply_referral(user, command.args, is_new_user)
    partner = await svc.partners.attach(user, command.args, is_new_user)
    if partner:
        log.info("user %s came via partner %s", user.id, partner["code"])
    await show(message, await welcome_screen(svc, user, first_time=is_new_user))
    if referrer_id:
        product = svc.settings.product
        bonus = product.referral_bonus_invited
        if bonus:
            await message.answer(f"🎁 Тебя пригласил друг — начислено +{bonus} "
                                 f"{plural(bonus, 'бонусный поиск', 'бонусных поиска', 'бонусных поисков')}.")
        r = product.referral_bonus_referrer
        try:
            if r:
                await bot.send_message(referrer_id, f"🎁 По твоей ссылке пришёл новый пользователь. Начислено +{r} "
                                                    f"{plural(r, 'бонусный поиск', 'бонусных поиска', 'бонусных поисков')}.")
        except Exception:  # referrer may have blocked the bot
            log.info("could not notify referrer %s", referrer_id)


async def cmd_menu(message: Message, state: FSMContext, svc: Services, user: User) -> None:
    await state.clear()
    await show(message, await welcome_screen(svc, user))


async def cmd_cancel(message: Message, state: FSMContext, svc: Services, user: User) -> None:
    had_state = await state.get_state()
    await state.clear()
    if had_state:
        await message.answer("Действие отменено.")
    await show(message, await welcome_screen(svc, user))


async def cb_home(cb: CallbackQuery, state: FSMContext, svc: Services, user: User) -> None:
    await state.clear()
    await show(cb, await welcome_screen(svc, user))


async def cb_cancel(cb: CallbackQuery, state: FSMContext, svc: Services, user: User) -> None:
    await state.set_state(None)
    await cb.answer("Отменено")
    await show(cb, await welcome_screen(svc, user))


def _help(svc: Services):
    src = svc.search_sources
    ai_status = svc.ai.configured()[1] if svc.leads.ai_ready else ""
    return views.help_screen(src.title, src.attribution, ai_status, svc.settings.support_contact)


async def cb_help(cb: CallbackQuery, svc: Services) -> None:
    await show(cb, _help(svc))


async def cmd_help(message: Message, svc: Services) -> None:
    await show(message, _help(svc))


async def cmd_terms(message: Message, svc: Services) -> None:
    await message.answer(cv.terms(svc.settings.support_contact))


async def cmd_paysupport(message: Message, svc: Services) -> None:
    await message.answer(cv.paysupport(svc.settings.support_contact))


async def cmd_privacy(message: Message, svc: Services) -> None:
    await message.answer(cv.privacy(svc.settings.operator_name, svc.settings.support_contact))


async def cmd_delete_data(message: Message) -> None:
    await show(message, cv.confirm_delete_data())


async def cb_erase(cb: CallbackQuery, user: User, svc: Services, state: FSMContext) -> None:
    await state.clear()
    await svc.account.delete_user_data(user.id)
    log.info("user %s erased their data", user.id)
    await show(cb, ("✅ Твои данные удалены. Если захочешь вернуться — просто нажми /start.", None))


async def cb_channel_bonus(cb: CallbackQuery, user: User, svc: Services, bot: Bot) -> None:
    channel = svc.settings.channel_username
    bonus = svc.settings.product.channel_bonus
    if not channel or not bonus:
        return await cb.answer()
    if user.channel_bonus_at:
        return await cb.answer("Бонус за подписку уже получен 🙂", show_alert=True)
    try:
        member = await bot.get_chat_member(chat_id=f"@{channel}", user_id=user.id)
    except Exception as e:
        log.error("channel subscription check failed (add the bot as an admin of @%s): %s", channel, e)
        return await cb.answer("Не получилось проверить подписку. Попробуй позже.", show_alert=True)
    if member.status not in ("member", "administrator", "creator"):
        return await cb.answer(f"Сначала подпишись на @{channel}, потом нажми кнопку ещё раз.", show_alert=True)
    if await svc.account.claim_channel_bonus(user, bonus):
        await cb.answer(f"🎁 Спасибо за подписку! +{bonus} {plural(bonus, 'поиск', 'поиска', 'поисков')}",
                        show_alert=True)
    await show(cb, await welcome_screen(svc, user))


def create_router() -> Router:
    r = Router(name="start")
    r.message.register(cmd_start, CommandStart())
    r.message.register(cmd_menu, Command("menu"))
    r.message.register(cmd_cancel, Command("cancel"))
    r.message.register(cmd_help, Command("help"))
    r.message.register(cmd_terms, Command("terms"))
    r.message.register(cmd_paysupport, Command("paysupport"))
    r.message.register(cmd_privacy, Command("privacy"))
    r.message.register(cmd_delete_data, Command("delete_my_data"))
    r.callback_query.register(cb_erase, Menu.filter(F.to == "erase_ok"))
    r.callback_query.register(cb_home, Menu.filter(F.to == "home"))
    r.callback_query.register(cb_cancel, Menu.filter(F.to == "cancel"))
    r.callback_query.register(cb_help, Menu.filter(F.to == "help"))
    r.callback_query.register(cb_channel_bonus, Menu.filter(F.to == "chbonus"))
    return r
