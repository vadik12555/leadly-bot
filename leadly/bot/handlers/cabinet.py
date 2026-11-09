"""Statistics and referral program."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from leadly.bot.callbacks import Menu, Notif, Prof, Tpl, Watch
from leadly.bot.states import ProfileInput, TemplateInput
from leadly.bot.ui import USER_TEXT, show
from leadly.bot.views import cabinet as cv
from leadly.config import AI_ANALYSES
from leadly.db import User
from leadly.services import Services
from leadly.services.profile import FIELDS as PROFILE_FIELDS


async def _stats(target, user: User, svc: Services) -> None:
    plan, sub = await svc.billing.current(user.id)
    summary = {k: q for k, q in (await svc.usage.summary(user)).items()
               if q.limit != 0 and (k != AI_ANALYSES or svc.leads.ai_ready)}
    await show(target, cv.stats_screen(await svc.account.stats(user.id), plan, sub["ends_at"] if sub else None,
                                       summary, await svc.account.niche_stats(user.id),
                                       await svc.account.goal_progress(user)))


async def cb_stats(cb: CallbackQuery, user: User, svc: Services) -> None:
    await _stats(cb, user, svc)


async def cmd_stats(message: Message, user: User, svc: Services) -> None:
    await _stats(message, user, svc)


async def _referral(target, user: User, svc: Services, bot_username: str) -> None:
    invited, bonus = await svc.account.referral_stats(user.id)
    await show(target, cv.referral_screen(svc.account.referral_link(user, bot_username), invited, bonus,
                                          svc.settings.product))


async def cb_referral(cb: CallbackQuery, user: User, svc: Services, bot_username: str) -> None:
    await _referral(cb, user, svc, bot_username)


async def cmd_referral(message: Message, user: User, svc: Services, bot_username: str) -> None:
    await _referral(message, user, svc, bot_username)


async def _profile(target, user: User, svc: Services) -> None:
    await show(target, cv.profile_screen(await svc.profiles.get(user.id), PROFILE_FIELDS))


async def cb_profile(cb: CallbackQuery, user: User, svc: Services, state: FSMContext) -> None:
    await state.set_state(None)
    await _profile(cb, user, svc)


async def cmd_profile(message: Message, user: User, svc: Services) -> None:
    await _profile(message, user, svc)


async def cb_profile_edit(cb: CallbackQuery, callback_data: Prof, user: User, svc: Services,
                          state: FSMContext) -> None:
    field = callback_data.field
    if field not in PROFILE_FIELDS:
        return
    label, _limit, example = PROFILE_FIELDS[field]
    current = getattr(await svc.profiles.get(user.id), field)
    await state.set_state(ProfileInput.field)
    await state.update_data(profile_field=field)
    await show(cb, cv.ask_profile_field(label, example, current))


async def msg_profile_field(message: Message, user: User, svc: Services, state: FSMContext) -> None:
    field = (await state.get_data()).get("profile_field")
    error = await svc.profiles.set(user.id, field, message.text or "")
    if error:
        return await message.answer(f"{error} Попробуй ещё раз или /cancel.")
    await state.set_state(None)
    await message.answer("✅ Сохранено")
    await _profile(message, user, svc)


async def _watches(target, user: User, svc: Services) -> None:
    plan = await svc.billing.plan(user.id)
    await show(target, cv.watches_screen(await svc.watches.list(user.id), plan.limit("watches").amount,
                                         plan.watch_interval_days))


async def cb_watches(cb: CallbackQuery, user: User, svc: Services) -> None:
    await _watches(cb, user, svc)


async def cb_watch(cb: CallbackQuery, callback_data: Watch, user: User, svc: Services) -> None:
    if callback_data.act == "add":
        watch_id, status = await svc.watches.create_from_search(user, callback_data.id)
        if status.startswith("limit:"):
            return await cb.answer(f"На твоём тарифе можно следить за {status[6:]} нишами. "
                                   "Удали одну или посмотри тарифы.", show_alert=True)
        if status == "not_found":
            return await cb.answer("Поиск не найден", show_alert=True)
        await cb.answer("Уже отслеживается 🔔" if status == "exists"
                        else "🔔 Готово! Пришлю новые компании, когда появятся.", show_alert=True)
        return
    if callback_data.act == "del":
        await svc.watches.delete(user.id, callback_data.id)
        await cb.answer("Автопоиск удалён")
    await _watches(cb, user, svc)


async def _templates(target, user: User, svc: Services) -> None:
    from leadly.services.templates import MAX_TEMPLATES, VARIABLES
    await show(target, cv.templates_screen(await svc.templates.list(user.id), VARIABLES, MAX_TEMPLATES))


async def cb_templates(cb: CallbackQuery, user: User, svc: Services, state: FSMContext) -> None:
    await state.set_state(None)
    await _templates(cb, user, svc)


async def cmd_templates(message: Message, user: User, svc: Services) -> None:
    await _templates(message, user, svc)


async def cb_tpl(cb: CallbackQuery, callback_data: Tpl, user: User, svc: Services, state: FSMContext) -> None:
    if callback_data.act == "new":
        await state.set_state(TemplateInput.title)
        return await show(cb, cv.ask_template_title())
    if callback_data.act == "del":
        await svc.templates.delete(user.id, callback_data.id)
        await cb.answer("Шаблон удалён")
    await _templates(cb, user, svc)


async def msg_tpl_title(message: Message, state: FSMContext) -> None:
    from leadly.services.templates import VARIABLES
    title = (message.text or "").strip()
    if not title:
        return await message.answer("Напиши название шаблона или /cancel.")
    await state.update_data(tpl_title=title[:60])
    await state.set_state(TemplateInput.text)
    await show(message, cv.ask_template_text(VARIABLES))


async def msg_tpl_text(message: Message, state: FSMContext, user: User, svc: Services) -> None:
    text = (message.text or "").strip()
    if len(text) < 10:
        return await message.answer("Слишком короткий текст. Пришли полный текст шаблона или /cancel.")
    title = (await state.get_data()).get("tpl_title", "Шаблон")
    await state.set_state(None)
    if not await svc.templates.add(user.id, title, text):
        await message.answer("Достигнут лимит шаблонов (20). Удали ненужные.")
    else:
        await message.answer("✅ Шаблон сохранён. Используй его в «✍️ Создать предложение» → «📄 Мои шаблоны».")
    await _templates(message, user, svc)


async def cb_notify(cb: CallbackQuery, user: User) -> None:
    await show(cb, cv.notify_screen(user))


async def cb_notify_toggle(cb: CallbackQuery, callback_data: Notif, user: User, svc: Services) -> None:
    if callback_data.what in ("watches", "followups"):
        await svc.account.set_notify(user, callback_data.what)
    await show(cb, cv.notify_screen(user))


async def cb_goal(cb: CallbackQuery, callback_data: Menu, user: User, svc: Services) -> None:
    await svc.account.set_goal(user, int(callback_data.to[4:]))
    await cb.answer(f"🎯 Цель: {user.daily_goal} компаний в день")
    await _stats(cb, user, svc)


async def cmd_partner(message: Message, user: User, svc: Services, bot_username: str) -> None:
    from leadly.bot.views import partners as pv
    partner = await svc.partners.by_telegram(user.id)
    support = svc.settings.support_contact
    if not partner:
        return await message.answer(pv.not_a_partner(support))
    await message.answer(pv.partner_cabinet(await svc.partners.stats(partner),
                                            svc.partners.link(bot_username, partner["code"]), support),
                         disable_web_page_preview=True)


def create_router() -> Router:
    r = Router(name="cabinet")
    r.message.register(cmd_partner, Command("partner"))
    r.callback_query.register(cb_stats, Menu.filter(F.to == "stats"))
    r.callback_query.register(cb_goal, Menu.filter(F.to.in_({"goal3", "goal5", "goal10"})))
    r.message.register(cmd_stats, Command("stats"))
    r.callback_query.register(cb_referral, Menu.filter(F.to == "ref"))
    r.message.register(cmd_referral, Command("ref"))
    r.callback_query.register(cb_watches, Menu.filter(F.to == "watches"))
    r.callback_query.register(cb_watch, Watch.filter())
    r.callback_query.register(cb_notify, Menu.filter(F.to == "notify"))
    r.callback_query.register(cb_notify_toggle, Notif.filter())
    r.callback_query.register(cb_templates, Menu.filter(F.to == "templates"))
    r.message.register(cmd_templates, Command("templates"))
    r.callback_query.register(cb_tpl, Tpl.filter())
    r.message.register(msg_tpl_title, TemplateInput.title, USER_TEXT)
    r.message.register(msg_tpl_text, TemplateInput.text, USER_TEXT)
    r.callback_query.register(cb_profile, Menu.filter(F.to == "profile"))
    r.message.register(cmd_profile, Command("profile"))
    r.callback_query.register(cb_profile_edit, Prof.filter(F.act == "edit"))
    r.message.register(msg_profile_field, ProfileInput.field, USER_TEXT)
    return r
