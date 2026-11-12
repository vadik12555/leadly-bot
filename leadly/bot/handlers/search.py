"""Search flow: service -> niche (-> subcategory) -> city -> filters -> results."""

from __future__ import annotations

import logging
import secrets

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from leadly import catalog as c
from leadly.bot import views
from leadly.bot.callbacks import Alt, Flow, Flt, Geo, Menu, Nch, Res, Sub, Svc
from leadly.bot.states import SearchFlow
from leadly.bot.ui import NO_KB, USER_TEXT, edit, show
from leadly.bot.views import search as sv
from leadly.config import SEARCHES
from leadly.db import User
from leadly.services import Services
from leadly.services.search import SearchRequest

log = logging.getLogger(__name__)


def _clean(text: str, limit: int) -> str:
    text = " ".join(text.split())[:limit]
    return text[:1].upper() + text[1:]


async def _ctx(state: FSMContext) -> dict:
    return await state.get_data()


def _service(data: dict) -> c.Service | None:
    code = data.get("service")
    if code == "custom" or code in c.SERVICES:
        return c.get_service(code, data.get("service_custom"))
    return None


def _niche(data: dict) -> c.Niche | None:
    code = data.get("niche")
    if code == "custom" or code in c.NICHES:
        return c.get_niche(code, data.get("niche_custom"))
    return None


async def _render(state: FSMContext, user: User, svc: Services, step: str):
    """Screen for a step; falls back to the earliest step whose data is missing."""
    data = await _ctx(state)
    service = _service(data)
    if not service or step == "service":
        return sv.step_service()
    niche = _niche(data)
    if not niche or step == "niche":
        return sv.step_niche(service)
    if step == "sub" and len(niche.subcategories) > 1:
        return sv.step_subcategory(niche)
    plan = await svc.billing.plan(user.id)
    if not data.get("cities") or step == "geo":
        return sv.step_geo(service, niche, user.city, svc.settings.product.allows(plan, "multi_city"))
    if not data.get("token"):
        await state.update_data(token=secrets.token_hex(8))  # idempotency key for the "find" tap
    return sv.step_filters(service, niche, data.get("sub"), data["cities"], data.get("site", c.SITE_ANY),
                           bool(data.get("contacts")), svc.search_sources.title, bool(data.get("hot")),
                           bool(data.get("builder")))


async def _go(target, state: FSMContext, user: User, svc: Services, step: str) -> None:
    await show(target, await _render(state, user, svc, step))


async def _changed(state: FSMContext, **values) -> None:
    """Any parameter change invalidates the previous search token."""
    await state.update_data(token=None, **values)


async def _locked(user: User, svc: Services) -> bool:
    """No free searches left (or none at all) and no subscription: send to payment before the four-step
    form, not after it. A daily limit of a paid plan is not a lock — it resets tomorrow."""
    quota = await svc.usage.peek(user, SEARCHES)
    return not quota.ok and quota.period != "day"


async def _to_plans(target, user: User, svc: Services) -> None:
    from leadly.bot.handlers.billing import _plans
    await _plans(target, user, svc)


async def cb_find(cb: CallbackQuery, state: FSMContext, user: User, svc: Services) -> None:
    await state.clear()
    if await _locked(user, svc):
        return await _to_plans(cb, user, svc)
    await _go(cb, state, user, svc, "service")


async def cmd_find(message: Message, state: FSMContext, user: User, svc: Services) -> None:
    await state.clear()
    if await _locked(user, svc):
        return await _to_plans(message, user, svc)
    await _go(message, state, user, svc, "service")


async def cb_back(cb: CallbackQuery, callback_data: Flow, state: FSMContext, user: User, svc: Services) -> None:
    await state.set_state(None)
    await _go(cb, state, user, svc, callback_data.to)


async def cb_service(cb: CallbackQuery, callback_data: Svc, state: FSMContext, user: User, svc: Services) -> None:
    if callback_data.code == "custom":
        await state.set_state(SearchFlow.custom_service)
        return await show(cb, sv.ask_custom_service())
    if callback_data.code not in c.SERVICES:
        return
    await _changed(state, service=callback_data.code, service_custom=None)
    await _go(cb, state, user, svc, "niche")


async def msg_custom_service(message: Message, state: FSMContext, user: User, svc: Services) -> None:
    text = _clean(message.text or "", 60)
    if len(text) < 3:
        return await message.answer("Слишком коротко. Напиши услугу словами, например <i>видеосъёмка</i>.")
    await _changed(state, service="custom", service_custom=text)
    await state.set_state(None)
    await _go(message, state, user, svc, "niche")


async def cb_niche(cb: CallbackQuery, callback_data: Nch, state: FSMContext, user: User, svc: Services) -> None:
    if callback_data.code == "custom":
        await state.set_state(SearchFlow.custom_niche)
        return await show(cb, sv.ask_custom_niche())
    niche = c.NICHES.get(callback_data.code)
    if not niche:
        return
    await _changed(state, niche=niche.code, niche_custom=None, sub=None)
    await _go(cb, state, user, svc, "sub" if len(niche.subcategories) > 1 else "geo")


async def msg_custom_niche(message: Message, state: FSMContext, user: User, svc: Services) -> None:
    text = _clean(message.text or "", 50)
    if len(text) < 3:
        return await message.answer("Слишком коротко. Например: <i>ветеринарная клиника</i>.")
    await _changed(state, niche="custom", niche_custom=text, sub=None)
    await state.set_state(None)
    await _go(message, state, user, svc, "geo")


async def cb_sub(cb: CallbackQuery, callback_data: Sub, state: FSMContext, user: User, svc: Services) -> None:
    await _changed(state, sub=None if callback_data.code == "all" else callback_data.code)
    await _go(cb, state, user, svc, "geo")


async def cb_geo(cb: CallbackQuery, callback_data: Geo, state: FSMContext, user: User, svc: Services) -> None:
    act, val = callback_data.act, callback_data.val
    if act == "my" and user.city:
        await _changed(state, cities=[user.city])
        return await _go(cb, state, user, svc, "filters")
    if act == "city" and 0 <= val < len(c.POPULAR_CITIES):
        await _changed(state, cities=[c.POPULAR_CITIES[val]])
        return await _go(cb, state, user, svc, "filters")
    if act == "other":
        await state.set_state(SearchFlow.city)
        return await show(cb, sv.ask_city())
    if act in ("multi", "toggle", "done"):
        plan = await svc.billing.plan(user.id)
        if not svc.settings.product.allows(plan, "multi_city"):
            return await cb.answer("Поиск по нескольким городам доступен в тарифе PRO+.", show_alert=True)
        selected: list[int] = (await _ctx(state)).get("multi", [])
        if act == "toggle" and 0 <= val < len(c.POPULAR_CITIES):
            if val in selected:
                selected = [i for i in selected if i != val]
            elif len(selected) < c.MAX_CITIES:
                selected = selected + [val]
            else:
                await cb.answer(f"Не больше {c.MAX_CITIES} городов", show_alert=True)
            await state.update_data(multi=selected)
        if act == "done":
            if not selected:
                return await cb.answer("Отметь хотя бы один город", show_alert=True)
            await _changed(state, cities=[c.POPULAR_CITIES[i] for i in sorted(selected)])
            return await _go(cb, state, user, svc, "filters")
        await show(cb, sv.multi_city(selected))


async def msg_city(message: Message, state: FSMContext, user: User, svc: Services) -> None:
    city = _clean(message.text or "", 60)
    if len(city) < 2 or any(ch.isdigit() for ch in city):
        return await message.answer("Напиши название города буквами, например <i>Тверь</i>.")
    await svc.db.execute("UPDATE users SET city = ? WHERE id = ?", city, user.id)
    user.city = city
    await _changed(state, cities=[city])
    await state.set_state(None)
    await _go(message, state, user, svc, "filters")


async def cb_filter(cb: CallbackQuery, callback_data: Flt, state: FSMContext, user: User, svc: Services,
                    bot: Bot) -> None:
    data = await _ctx(state)
    if callback_data.act == "site" and callback_data.val in c.SITE_FILTERS:
        await _changed(state, site=callback_data.val)
        return await _go(cb, state, user, svc, "filters")
    if callback_data.act == "contacts":
        await _changed(state, contacts=not data.get("contacts"))
        return await _go(cb, state, user, svc, "filters")
    if callback_data.act == "hot":
        await _changed(state, hot=not data.get("hot"))
        return await _go(cb, state, user, svc, "filters")
    if callback_data.act == "builder":
        await _changed(state, builder=not data.get("builder"))
        return await _go(cb, state, user, svc, "filters")
    if callback_data.act == "go":
        await run_search(cb, state, user, svc, bot)


async def run_search(cb: CallbackQuery, state: FSMContext, user: User, svc: Services, bot: Bot) -> None:
    data = await _ctx(state)
    service, niche = _service(data), _niche(data)
    if not service or not niche or not data.get("cities"):
        await cb.answer("Параметры поиска сброшены — начнём заново")
        return await _go(cb, state, user, svc, "service")
    token = data.get("token") or secrets.token_hex(8)
    await state.update_data(token=token)
    await cb.answer("Ищу…")
    loading = await show(cb, (sv.searching(niche, data["cities"], svc.search_sources.title,
                                           bool(data.get("hot") or data.get("builder"))), NO_KB))
    req = SearchRequest(service, niche, data.get("sub"), data["cities"], data.get("site", c.SITE_ANY),
                        bool(data.get("contacts")), bool(data.get("hot")), bool(data.get("builder")))
    try:
        outcome = await svc.search.run(user, token, req)
    except Exception:
        log.exception("search failed")
        return await edit(bot, loading, sv.search_error("Внутренняя ошибка. Мы уже видим её в логах."))
    if outcome.blocked:
        plan = await svc.billing.plan(user.id)
        return await edit(bot, loading, views.limit_reached(outcome.blocked, outcome.quota, plan))
    if outcome.error:
        return await edit(bot, loading, sv.search_error(outcome.error))
    view = await svc.search.get(user.id, outcome.search_id)
    first = await svc.db.fetchone("SELECT COUNT(*) AS n FROM searches WHERE user_id = ? AND status = 'ok' "
                                  "AND token NOT LIKE 'watch:%'", user.id)
    tip = first["n"] == 1 and bool(view.items) and not outcome.reused
    await edit(bot, loading, sv.results(view, 0, svc.search_sources.attribution, outcome.warnings, tip=tip,
                                        watch=await _can_watch(user, svc), neighbour=await _neighbour(view, svc)))


async def _neighbour(view, svc: Services) -> str | None:
    """Only needed when the search found too little (cached geocoder answer, no waiting)."""
    if view.found >= sv.FEW_RESULTS or len(view.cities) != 1:
        return None
    return await svc.search.neighbour_city(view.cities[0])


async def cb_widen(cb: CallbackQuery, callback_data: Alt, state: FSMContext, user: User, svc: Services,
                   bot: Bot) -> None:
    """Widen a search that found too little: all categories of the niche, the nearest big city, another niche."""
    view = await svc.search.get(user.id, callback_data.sid)
    if not view:
        return await cb.answer("Этот поиск не найден", show_alert=True)
    await state.set_state(None)
    custom = lambda x: x.title if x.code == "custom" else None  # noqa: E731
    params = dict(service=view.service.code, service_custom=custom(view.service), niche=view.niche.code,
                  niche_custom=custom(view.niche), sub=view.subcategory, cities=view.cities,
                  site=view.filters.get("site", c.SITE_ANY), contacts=bool(view.filters.get("contacts")),
                  hot=bool(view.filters.get("hot")), builder=bool(view.filters.get("builder")))
    if callback_data.act == "niche":
        await _changed(state, **params)
        return await _go(cb, state, user, svc, "niche")
    if callback_data.act == "allcat":
        params["sub"] = None
    elif callback_data.act == "near":
        city = await svc.search.neighbour_city(view.cities[0]) if len(view.cities) == 1 else None
        if not city:
            return await cb.answer("Не получилось определить соседний город", show_alert=True)
        params["cities"] = [city]
    else:
        return
    if await _locked(user, svc):
        return await _to_plans(cb, user, svc)
    await _changed(state, **params)
    await run_search(cb, state, user, svc, bot)


async def cb_results(cb: CallbackQuery, callback_data: Res, user: User, svc: Services) -> None:
    view = await svc.search.get(user.id, callback_data.sid)
    if not view:
        return await cb.answer("Этот поиск не найден", show_alert=True)
    page = callback_data.page
    if page == -1:  # "save all with contacts"
        saved, limit = await svc.leads.save_all_with_contacts(user, view.id)
        note = f"⭐ Сохранено: {saved}." + (f" Достигнут лимит лидов ({limit}) — смотри «⭐ Тарифы»." if limit else "")
        await cb.answer(note, show_alert=True)
        view = await svc.search.get(user.id, view.id)
        page = 0
    await show(cb, sv.results(view, page, svc.search_sources.attribution, watch=await _can_watch(user, svc),
                              neighbour=await _neighbour(view, svc)))


async def _can_watch(user: User, svc: Services) -> bool:
    return (await svc.billing.plan(user.id)).limit("watches").amount != 0


def create_router() -> Router:
    r = Router(name="search")
    r.callback_query.register(cb_find, Menu.filter(F.to == "find"))
    r.message.register(cmd_find, Command("find"))
    r.callback_query.register(cb_back, Flow.filter())
    r.callback_query.register(cb_service, Svc.filter())
    r.message.register(msg_custom_service, SearchFlow.custom_service, USER_TEXT)
    r.callback_query.register(cb_niche, Nch.filter())
    r.message.register(msg_custom_niche, SearchFlow.custom_niche, USER_TEXT)
    r.callback_query.register(cb_sub, Sub.filter())
    r.callback_query.register(cb_geo, Geo.filter())
    r.message.register(msg_city, SearchFlow.city, USER_TEXT)
    r.callback_query.register(cb_filter, Flt.filter())
    r.callback_query.register(cb_results, Res.filter())
    r.callback_query.register(cb_widen, Alt.filter())
    return r
