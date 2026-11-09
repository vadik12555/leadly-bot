"""Company card actions, proposals, CRM, "📋 Мои лиды", and the menu pickers for AI/proposals."""

from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from leadly import catalog as c
from leadly.bot import views
from leadly.bot.callbacks import Co, LList, Menu, Pay
from leadly.bot.states import LeadInput
from leadly.bot.ui import NO_KB, USER_TEXT, edit, show
from leadly.bot.views import company as cv
from leadly.db import User
from leadly.services import Services
from leadly.services.leads import RESULT, CompanyContext
from leadly.utils import fmt_money

log = logging.getLogger(__name__)


async def _card(target, ctx: CompanyContext, svc: Services) -> None:
    insight, _ = await svc.leads.insight(ctx)
    notes = await svc.leads.notes(ctx.lead_id) if ctx.lead and ctx.kind == "s" else None
    await show(target, cv.card(ctx, insight, notes))


async def _blocked(cb, svc: Services, user: User, kind: str, quota) -> None:
    await show(cb, views.limit_reached(kind, quota, await svc.billing.plan(user.id)))


async def cb_company(cb: CallbackQuery, callback_data: Co, user: User, svc: Services, state: FSMContext,
                     bot: Bot) -> None:
    ctx = await svc.leads.resolve(user.id, callback_data.k, callback_data.ref, callback_data.i)
    if not ctx:
        return await cb.answer("Компания не найдена — возможно, лид удалён.", show_alert=True)
    handler = ACTIONS.get(callback_data.a)
    if handler:
        await handler(cb, ctx, callback_data.x, user=user, svc=svc, state=state, bot=bot)


# --- card actions ------------------------------------------------------------------


async def a_open(cb, ctx, x, *, user, svc, state, **_):
    await state.set_state(None)
    quota = await svc.leads.open_card(user, ctx)
    if not quota.ok:
        return await _blocked(cb, svc, user, "card_opens", quota)
    await _card(cb, ctx, svc)


async def a_more(cb, ctx, x, *, svc, **_):
    insight, report = await svc.leads.insight(ctx)
    await show(cb, cv.details(ctx, insight, report))


async def a_site(cb, ctx, x, *, user, svc, bot, **_):
    if not await svc.leads.cached_report(ctx.company):
        loading = await show(cb, (f"🌐 <b>Проверяю сайт {views.h(ctx.company.domain or '')}…</b>\n"
                                  "Открываю главную страницу, это до 15 секунд.", NO_KB))
    else:
        loading = None
    result = await svc.leads.check_site(user, ctx)
    target = (lambda screen: edit(bot, loading, screen)) if loading else (lambda screen: show(cb, screen))
    if result.quota:
        return await target(views.limit_reached("site_checks", result.quota, await svc.billing.plan(user.id)))
    if result.error:
        return await target((f"ℹ️ {views.h(result.error)}", views.kb(cv.back_row(ctx))))
    insight, report = await svc.leads.insight(ctx)
    await target(cv.site_report(ctx, report, insight))


async def a_contact(cb, ctx, x, *, user, svc, bot, **_):
    loading = None
    if ctx.company.website and not ctx.company.is_demo:
        loading = await show(cb, ("📇 <b>Ищу контакты на сайте компании…</b>", NO_KB))
        result = await svc.leads.check_site(user, ctx, contacts=True)
        if result.quota:
            return await edit(bot, loading, views.limit_reached("site_checks", result.quota,
                                                                await svc.billing.plan(user.id)))
    report = await svc.leads.cached_report(ctx.company)
    screen = cv.contacts(ctx, report)
    await (edit(bot, loading, screen) if loading else show(cb, screen))


async def a_ai(cb, ctx, x, *, user, svc, bot, **_):
    cached = await svc.leads.cached_analysis(ctx)
    if cached:
        return await show(cb, cv.ai_analysis(ctx, cached))
    if not svc.leads.ai_ready:
        insight, _ = await svc.leads.insight(ctx)
        return await show(cb, cv.ai_not_configured(ctx, insight, "ИИ-анализ пока не подключён."))
    loading = await show(cb, ("🤖 <b>ИИ анализирует компанию…</b>\nОбычно 10–40 секунд.", NO_KB))
    analysis, result = await svc.leads.analyze(user, ctx)
    if result.quota:
        return await edit(bot, loading, views.limit_reached("ai_analyses", result.quota,
                                                            await svc.billing.plan(user.id)))
    if result.error:
        return await edit(bot, loading, cv.ai_error(ctx, result.error))
    await edit(bot, loading, cv.ai_analysis(ctx, analysis))


async def a_prop(cb, ctx, x, *, user, svc, bot, round_=None, **_):
    texts, source = await svc.leads.latest_proposals(user.id, ctx)
    empty = (await svc.profiles.get(user.id)).is_empty
    if texts and round_ is None:
        variant = "edited" if texts.get("edited") else "short"
        return await show(cb, cv.proposals(ctx, texts, variant, source or "template", profile_empty=empty))
    if round_ is None:
        round_ = 0
    loading = await show(cb, ("✍️ <b>Готовлю варианты сообщения…</b>", NO_KB)) if svc.leads.ai_ready else None
    texts, source, result = await svc.leads.generate_proposals(user, ctx, round_)
    screen = (views.limit_reached("proposals", result.quota, await svc.billing.plan(user.id)) if result.quota
              else cv.proposals(ctx, texts, "short", source, result.error, profile_empty=empty))
    await (edit(bot, loading, screen) if loading else show(cb, screen))


async def a_regen(cb, ctx, x, *, user, svc, **kw):
    # each generation stores one "short" variant, so its count is the next round number
    rounds = await svc.db.fetchone(
        "SELECT COUNT(*) AS n FROM proposals WHERE user_id = ? AND company_key = ? AND service = ? "
        "AND variant = 'short'", user.id, ctx.company.key, ctx.service.title)
    await a_prop(cb, ctx, x, user=user, svc=svc, round_=rounds["n"], **kw)


async def a_variant(cb, ctx, x, *, user, svc, **_):
    texts, source = await svc.leads.latest_proposals(user.id, ctx)
    if x not in ("short", "business", "fact", "edited"):
        return
    empty = (await svc.profiles.get(user.id)).is_empty
    await show(cb, cv.proposals(ctx, texts, x, "user" if x == "edited" else (source or "template"),
                                profile_empty=empty))


async def a_copy(cb, ctx, x, *, user, svc, bot, **_):
    texts, _ = await svc.leads.latest_proposals(user.id, ctx)
    if not texts.get(x):
        return await cb.answer("Сначала создай предложение", show_alert=True)
    await bot.send_message(cb.from_user.id, cv.copy_message(texts[x]))
    await cb.answer("Отправил текст отдельным сообщением")


async def a_edit(cb, ctx, x, *, user, svc, state, **_):
    texts, _ = await svc.leads.latest_proposals(user.id, ctx)
    current = texts.get(x) or texts.get("short") or ""
    await state.set_state(LeadInput.edit_proposal)
    await state.update_data(company_ctx=[ctx.kind, ctx.ref, ctx.idx])
    await show(cb, cv.ask_edit(ctx, current))


async def a_save(cb, ctx, x, *, user, svc, **_):
    lead_id, created, limit = await svc.leads.save(user, ctx)
    if lead_id is None:
        return await show(cb, cv.saved_limit(limit or 0))
    await cb.answer("⭐ Сохранено в «Мои лиды»" if created else "Уже в «Мои лиды»")
    if x == "prop":
        texts, source = await svc.leads.latest_proposals(user.id, ctx)
        return await show(cb, cv.proposals(ctx, texts, "short", source or "template"))
    await _card(cb, ctx, svc)


async def a_hide(cb, ctx, x, *, user, svc, **kw):
    await svc.leads.hide(user.id, ctx)
    await cb.answer("Больше не покажу эту компанию в новых поисках")
    if ctx.kind == RESULT:
        await a_next(cb, ctx, x, user=user, svc=svc, **kw)


async def a_next(cb, ctx, x, *, user, svc, **kw):
    if ctx.kind != RESULT:
        return
    total = await svc.leads.search_size(ctx.ref)
    if ctx.idx + 1 >= total:
        return await cb.answer("Это последняя компания в выдаче", show_alert=True)
    nxt = await svc.leads.resolve(user.id, RESULT, ctx.ref, ctx.idx + 1)
    await a_open(cb, nxt, "", user=user, svc=svc, **kw)


async def a_status(cb, ctx, x, **_):
    await show(cb, cv.status_picker(ctx))


async def a_set_status(cb, ctx, x, *, user, svc, state, **_):
    if x not in c.STATUSES or not ctx.lead:
        return
    await svc.leads.set_status(ctx, x)
    await cb.answer(f"Статус: {c.STATUSES[x].label}")
    if x == "deal":
        from leadly.bot.views import cabinet as cabv
        link = svc.account.referral_link(user, svc.settings.bot_username)
        share = cabv.share_success(link)
        await cb.bot.send_message(cb.from_user.id, share[0], reply_markup=share[1])
        await state.set_state(LeadInput.deal_amount)
        await state.update_data(company_ctx=[ctx.kind, ctx.ref, ctx.idx])
        return await show(cb, cv.ask_deal_amount(ctx))
    await _card(cb, ctx, svc)


async def a_note(cb, ctx, x, *, state, **_):
    if not ctx.lead:
        return await cb.answer("Сначала сохрани лида", show_alert=True)
    await state.set_state(LeadInput.note)
    await state.update_data(company_ctx=[ctx.kind, ctx.ref, ctx.idx])
    await show(cb, cv.ask_note(ctx))


async def a_delete(cb, ctx, x, **_):
    await show(cb, cv.confirm_delete(ctx))


async def a_delete_ok(cb, ctx, x, *, user, svc, **_):
    await svc.leads.delete(ctx)
    await cb.answer("Лид удалён")
    await _leads_home(cb, user, svc)


async def a_followup(cb, ctx, x, *, user, svc, bot, **_):
    from leadly.services.writer import followup_text
    text = followup_text(ctx.company, ctx.service, await svc.profiles.get(user.id))
    if x == "copy":
        return await bot.send_message(cb.from_user.id, cv.copy_message(text))
    await show(cb, cv.followup(ctx, text))


async def a_followup_copy(cb, ctx, x, **kw):
    await a_followup(cb, ctx, "copy", **kw)


async def a_templates(cb, ctx, x, *, user, svc, **_):
    await show(cb, cv.templates_picker(ctx, await svc.templates.list(user.id)))


async def a_template(cb, ctx, x, *, user, svc, state, bot, **_):
    from leadly.services.templates import render
    tpl = await svc.templates.get(user.id, int(x)) if x.isdigit() else None
    if not tpl:
        return await cb.answer("Шаблон не найден", show_alert=True)
    report = await svc.leads.cached_report(ctx.company)
    text, missing = render(tpl["text"], ctx.company, ctx.service, await svc.profiles.get(user.id), report)
    await state.update_data(last_template_text=text)
    await show(cb, cv.template_result(ctx, tpl["title"], text, missing))


async def a_template_copy(cb, ctx, x, *, state, bot, **_):
    text = (await state.get_data()).get("last_template_text")
    if text:
        await bot.send_message(cb.from_user.id, cv.copy_message(text))


async def a_template_save(cb, ctx, x, *, user, svc, **_):
    from leadly.services.templates import templatize
    texts, _ = await svc.leads.latest_proposals(user.id, ctx)
    text = texts.get(x)
    if not text:
        return await cb.answer("Нечего сохранять", show_alert=True)
    tid = await svc.templates.add(user.id, f"Из «{ctx.company.name[:30]}»", templatize(text, ctx.company))
    await cb.answer("💾 Сохранено в «📄 Мои шаблоны»" if tid else "Достигнут лимит шаблонов (20)", show_alert=True)


async def a_audit(cb, ctx, x, *, user, svc, bot, **_):
    from leadly.services.report import build_audit_html
    if not ctx.company.website or ctx.company.is_demo:
        return await cb.answer("У компании нет сайта в источнике — аудит сделать не из чего.", show_alert=True)
    if not await svc.leads.cached_report(ctx.company):
        await cb.answer("Проверяю сайт…")
        result = await svc.leads.check_site(user, ctx)
        if result.quota:
            return await show(cb, views.limit_reached("site_checks", result.quota, await svc.billing.plan(user.id)))
        if result.error:
            return await cb.answer(result.error, show_alert=True)
    report = await svc.leads.cached_report(ctx.company)
    html = build_audit_html(ctx.company, report, ctx.service, ctx.bookable, await svc.profiles.get(user.id))
    name = (ctx.company.domain or "site").replace(".", "-")
    await bot.send_document(cb.from_user.id, BufferedInputFile(html.encode("utf-8"), filename=f"audit-{name}.html"),
                            caption="📄 Экспресс-аудит сайта. Перешли этот файл компании вместе с сообщением — "
                                    "конкретные наблюдения повышают шанс ответа. Открывается в любом браузере.")


async def a_final_touch(cb, ctx, x, *, user, svc, **_):
    from leadly.services.writer import final_touch_text
    text = final_touch_text(ctx.company, ctx.service, await svc.profiles.get(user.id))
    await show(cb, cv.followup(ctx, text))


async def a_similar(cb, ctx, x, *, user, svc, state, **_):
    """Start a new search with the same service, niche and subcategory — the user only picks a city."""
    from leadly.bot.handlers.search import _go
    sub = None
    if ctx.search_id:
        row = await svc.db.fetchone("SELECT subcategory FROM searches WHERE id = ?", ctx.search_id)
        sub = row["subcategory"] if row else None
    niche = ctx.niche_code or "custom"
    await state.clear()
    await state.update_data(service=ctx.service.code,
                            service_custom=ctx.service.title if ctx.service.code == "custom" else None,
                            niche=niche, niche_custom=ctx.company.category if niche == "custom" else None, sub=sub)
    await cb.answer("Выбери город — найду похожие компании")
    await _go(cb, state, user, svc, "geo")


ACTIONS = {
    "open": a_open, "more": a_more, "site": a_site, "contact": a_contact, "ai": a_ai, "prop": a_prop,
    "regen": a_regen, "pv": a_variant, "copy": a_copy, "edit": a_edit, "save": a_save, "hide": a_hide,
    "next": a_next, "status": a_status, "setst": a_set_status, "note": a_note, "del": a_delete,
    "delok": a_delete_ok, "fup": a_followup, "fupc": a_followup_copy,
    "tpls": a_templates, "tpl": a_template, "tplc": a_template_copy, "tsave": a_template_save,
    "audit": a_audit, "fup2": a_final_touch, "similar": a_similar,
}


# --- text inputs -----------------------------------------------------------------------


async def _ctx_from_state(state: FSMContext, user: User, svc: Services) -> CompanyContext | None:
    ref = (await state.get_data()).get("company_ctx")
    await state.set_state(None)
    return await svc.leads.resolve(user.id, *ref) if ref else None


async def msg_note(message: Message, state: FSMContext, user: User, svc: Services) -> None:
    text = (message.text or "").strip()[:1000]
    ctx = await _ctx_from_state(state, user, svc)
    if not ctx or not text:
        return await message.answer("Не получилось сохранить заметку. Открой лида ещё раз.")
    await svc.leads.add_note(ctx, text)
    await message.answer("📝 Заметка сохранена")
    await _card(message, ctx, svc)


async def msg_deal(message: Message, state: FSMContext, user: User, svc: Services) -> None:
    digits = re.sub(r"\D", "", message.text or "")
    if not digits or len(digits) > 10 or int(digits) <= 0:
        return await message.answer("Нужна сумма числом, например <code>50000</code>. Или /cancel.")
    ctx = await _ctx_from_state(state, user, svc)
    if not ctx:
        return await message.answer("Лид не найден.")
    await svc.leads.set_deal_amount(ctx, int(digits))
    await message.answer(f"💰 Сумма сделки: {fmt_money(int(digits))}")
    await _card(message, ctx, svc)


async def msg_edit_proposal(message: Message, state: FSMContext, user: User, svc: Services) -> None:
    text = (message.text or "").strip()
    if len(text) < 10:
        return await message.answer("Текст слишком короткий. Пришли полный текст сообщения или /cancel.")
    ctx = await _ctx_from_state(state, user, svc)
    if not ctx:
        return await message.answer("Компания не найдена.")
    await svc.leads.save_edited(user.id, ctx, text[:3500])
    texts, _ = await svc.leads.latest_proposals(user.id, ctx)
    await show(message, cv.proposals(ctx, texts, "edited", "user"))


# --- my leads ------------------------------------------------------------------------------


async def _leads_home(target, user: User, svc: Services) -> None:
    counts = await svc.leads.counts(user.id)
    plan = await svc.billing.plan(user.id)
    await show(target, cv.leads_home(counts, svc.settings.product.allows(plan, "export")))


async def cb_leads(cb: CallbackQuery, user: User, svc: Services, state: FSMContext) -> None:
    await state.set_state(None)
    await _leads_home(cb, user, svc)


async def cmd_leads(message: Message, user: User, svc: Services, state: FSMContext) -> None:
    await state.set_state(None)
    await _leads_home(message, user, svc)


async def cb_leads_list(cb: CallbackQuery, callback_data: LList, user: User, svc: Services) -> None:
    st = callback_data.st
    if st == "home":
        return await _leads_home(cb, user, svc)
    status = None if st == "all" else st
    if status and status not in c.STATUSES:
        return
    page = max(0, callback_data.page)
    rows, total = await svc.leads.list(user.id, status, page, cv.LEADS_PER_PAGE)
    await show(cb, cv.leads_list(rows, st, page, total))


async def cb_export(cb: CallbackQuery, user: User, svc: Services, bot: Bot) -> None:
    plan = await svc.billing.plan(user.id)
    if not svc.settings.product.allows(plan, "export"):
        return await cb.answer("Экспорт доступен в платных тарифах", show_alert=True)
    data = await svc.leads.export_csv(user.id)
    await bot.send_document(cb.from_user.id, BufferedInputFile(data, filename="leadly-leads.csv"),
                            caption="📤 Твои лиды в CSV (открывается в Excel / Google Таблицах)")


async def cb_pick(cb: CallbackQuery, callback_data: Menu, user: User, svc: Services) -> None:
    leads = await svc.leads.recent(user.id)
    last = await svc.search.last_search_id(user.id)
    await show(cb, cv.picker(callback_data.to, leads, last))


def create_router() -> Router:
    r = Router(name="company")
    r.callback_query.register(cb_company, Co.filter())
    r.message.register(msg_note, LeadInput.note, USER_TEXT)
    r.message.register(msg_deal, LeadInput.deal_amount, USER_TEXT)
    r.message.register(msg_edit_proposal, LeadInput.edit_proposal, USER_TEXT)
    r.callback_query.register(cb_leads, Menu.filter(F.to == "leads"))
    r.message.register(cmd_leads, Command("leads"))
    r.callback_query.register(cb_leads_list, LList.filter())
    r.callback_query.register(cb_export, Pay.filter(F.act == "export"))
    r.callback_query.register(cb_pick, Menu.filter(F.to.in_({"ai", "prop"})))
    return r
