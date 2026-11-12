"""Search flow screens."""

from __future__ import annotations

from leadly import catalog as c
from leadly.bot.callbacks import Alt, Co, Flow, Flt, Geo, Menu, Nch, Res, Sub, Svc, Watch
from leadly.bot.views import Screen, b, cancel_btn, chunk, h, home_btn, kb, pager
from leadly.models import Company
from leadly.services.search import SearchView
from leadly.utils import fmt_date, plural, short

RESULTS_PER_PAGE = 5


def _step(n: int, title: str) -> str:
    return f"<b>Шаг {n} из 4 · {title}</b>\n"


def step_service() -> Screen:
    buttons = [b(s.label, Svc(code=s.code)) for s in c.SERVICES.values()]
    return (_step(1, "Услуга") + "\nЧто ты предлагаешь клиентам?",
            kb(*chunk(buttons, 2), [b("🧩 Другое", Svc(code="custom"))], [home_btn()]))


def ask_custom_service() -> Screen:
    return "✏️ Напиши свою услугу одним сообщением.\n\nНапример: <i>видеосъёмка для бизнеса</i>", kb([cancel_btn()])


def step_niche(service: c.Service) -> Screen:
    buttons = [b(n.label, Nch(code=n.code)) for n in c.NICHES.values()]
    return (_step(2, "Ниша") + f"Услуга: {h(service.label)}\n\nКаких клиентов ищем?",
            kb(*chunk(buttons, 2), [b("🔎 Другие компании", Nch(code="custom"))],
               [b("← Назад", Flow(to="service")), home_btn()]))


def ask_custom_niche() -> Screen:
    return ("✏️ Напиши, какие компании искать — Leadly найдёт их по названию.\n\n"
            "Например: <i>ветеринарная клиника</i>", kb([cancel_btn()]))


def step_subcategory(niche: c.Niche) -> Screen:
    buttons = [b(s.title, Sub(code=s.code)) for s in niche.subcategories]
    return (f"<b>Уточни категорию</b>\n{h(niche.label)}",
            kb(*chunk(buttons, 2), [b("Все категории", Sub(code="all"))], [b("← Назад", Flow(to="niche"))]))


def step_geo(service: c.Service, niche: c.Niche, my_city: str | None, multi_allowed: bool) -> Screen:
    buttons = [b(city, Geo(act="city", val=i)) for i, city in enumerate(c.POPULAR_CITIES)]
    rows = []
    if my_city:
        rows.append([b(f"📍 Мой город · {my_city}", Geo(act="my"))])
    rows += chunk(buttons, 2)
    rows.append([b("✏️ Другой город", Geo(act="other"))])
    rows.append([b("🗺 Несколько городов", Geo(act="multi"))] if multi_allowed else [])
    rows.append([b("← Назад", Flow(to="niche")), home_btn()])
    return (_step(3, "Город") + f"{h(service.label)} · {h(niche.label)}\n\nГде ищем клиентов?", kb(*rows))


def ask_city() -> Screen:
    return ("✏️ Напиши название города. Можно указать район, чтобы искать точнее: "
            "<i>Москва, Хамовники</i> или <i>Казань, Вахитовский район</i>.", kb([cancel_btn()]))


def multi_city(selected: list[int]) -> Screen:
    buttons = [b(("✓ " if i in selected else "") + city, Geo(act="toggle", val=i))
               for i, city in enumerate(c.POPULAR_CITIES)]
    done = f"Готово · {len(selected)}" if selected else f"Отметь до {c.MAX_CITIES} городов"
    return ("🗺 <b>Несколько городов</b>", kb(*chunk(buttons, 2), [b(done, Geo(act="done"))],
                                             [b("← Назад", Flow(to="geo"))]))


def step_filters(service: c.Service, niche: c.Niche, sub: str | None, cities: list[str],
                 site: str, contacts: bool, sources: str, hot: bool = False, builder: bool = False) -> Screen:
    sub_title = niche.subcategory(sub).title if sub and niche.subcategory(sub) else None
    lines = [_step(4, "Фильтры") + f"{h(service.label)} · {h(niche.label)}"
             + (f" · {h(sub_title)}" if sub_title else "") + f" · 📍 {h(', '.join(cities))}", "",
             "Какие компании показывать?",
             f"<i>Источник: {h(sources)}. Фильтр «сайт не найден» означает, что его нет в источнике — "
             "у компании он всё равно может быть.</i>"]
    if niche.note:
        lines.append(f"<i>{h(niche.note)}</i>")
    site_buttons = [[b(("● " if site == code else "○ ") + title, Flt(act="site", val=code))]
                    for code, title in c.SITE_FILTERS.items()]
    return "\n".join(lines), kb(
        *site_buttons,
        [b(("☑ " if contacts else "☐ ") + "📞 Только с публичными контактами", Flt(act="contacts"))],
        [b(("☑ " if hot else "☐ ") + "🔥 Только горячие: проблема на сайте подтверждена", Flt(act="hot"))],
        [b(("☑ " if builder else "☐ ") + "🧱 Сайт на конструкторе (Tilda, Wix, Taplink)", Flt(act="builder"))],
        [b("🔍 Найти компании", Flt(act="go"))],
        [b("← Назад", Flow(to="geo")), home_btn()],
    )


def searching(niche: c.Niche, cities: list[str], sources: str, hot: bool = False) -> str:
    extra = ("Проверяю сайты найденных компаний на проблемы, которые решает твоя услуга. "
             "Обычно это занимает до 20 секунд." if hot else
             "Обычно это несколько секунд. Телефоны и почту с сайтов компаний бот подтянет "
             "в карточки сразу после показа выдачи.")
    return f"🔎 <b>Ищу компании…</b>\n{h(niche.label)} · 📍 {h(', '.join(cities))}\n\nИсточник: {h(sources)}. {extra}"


def search_error(message: str) -> Screen:
    return (f"⚠️ <b>Поиск не выполнен</b>\n\n{h(message)}\n\nЛимит поиска не списан.",
            kb([b("🔄 Повторить", Flt(act="go"))], [b("⚙️ Изменить параметры", Flow(to="filters"))], [home_btn()]))


def _found_title(n: int) -> str:
    return f"{plural(n, 'Найдена', 'Найдено', 'Найдено')} {n} {plural(n, 'компания', 'компании', 'компаний')}"


def _marks(co: Company) -> str:
    return ("🌐" if co.website else "—") + (" 📞" if co.phone else "") + (" ✉️" if co.email else "")


FEW_RESULTS = 5  # fewer companies than this: offer ways to widen the search


def widen_buttons(view: SearchView, neighbour: str | None) -> list[list]:
    """«Все категории ниши» (if a subcategory was chosen), «Соседний крупный город», «Другая ниша»."""
    rows = []
    if view.subcategory and view.niche.code != "custom":
        rows.append([b("📂 Все категории ниши", Alt(act="allcat", sid=view.id))])
    if neighbour and len(view.cities) == 1:
        rows.append([b(f"📍 Соседний крупный город · {neighbour}", Alt(act="near", sid=view.id))])
    rows.append([b("🔀 Другая ниша", Alt(act="niche", sid=view.id))])
    return rows


def results(view: SearchView, page: int, attribution: str, warnings: list[str] | None = None,
            tip: bool = False, watch: bool = True, neighbour: str | None = None) -> Screen:
    """neighbour: the nearest big city, offered when the search found fewer than FEW_RESULTS companies."""
    if not view.items:
        return ("🤷 <b>В источнике нет компаний с такими параметрами</b>\n"
                f"{h(view.niche.label)} · 📍 {h(', '.join(view.cities))}\n\n"
                "Можно расширить поиск: все категории ниши, соседний крупный город или другая ниша.\n"
                "Лимит поиска не списан.",
                kb(*widen_buttons(view, neighbour), [b("⚙️ Изменить фильтры", Flow(to="filters"))],
                   [b("🔄 Новый поиск", Menu(to="find"))], [home_btn()]))
    pages = max(1, -(-len(view.items) // RESULTS_PER_PAGE))
    page = max(0, min(page, pages - 1))
    chunk_items = view.items[page * RESULTS_PER_PAGE:(page + 1) * RESULTS_PER_PAGE]
    lines = []
    if view.is_demo:
        lines += ["⚠️ <b>ДЕМО:</b> вымышленные компании, не реальный поиск.", ""]
    lines += [f"<b>{_found_title(view.found)}</b>",
              f"{h(view.niche.label)} · 📍 {h(', '.join(view.cities))}",
              f"<i>Источник: {h(view.providers)} · {fmt_date(view.created_at)}</i>"]
    with_contacts = sum(1 for _, co in view.items if co.has_contacts)
    lines.append(f"📞 С контактами: {with_contacts} из {len(view.items)}")
    if len(view.items) < view.found:
        lines.append(f"Показаны первые {len(view.items)} (лимит тарифа).")
    few = view.found < FEW_RESULTS
    if few:
        lines.append("Нашлось немного — можно расширить поиск кнопками ниже.")
    for w in warnings or []:
        lines.append(f"⚠️ {h(w)}")
    lines.append("")
    hot = sum(1 for _, co in view.items if co.signals)
    if hot:
        lines.append(f"🔥 С подтверждёнными проблемами сайта: {hot}")
    for idx, co in chunk_items:
        fire = f" · 🔥 {h(co.signals[0])}" + (f" +{len(co.signals) - 1}" if len(co.signals) > 1 else "") \
            if co.signals else ""
        cms = f" · 🧱 {h(co.cms)}" if co.cms else ""
        lines.append(f"{idx + 1}. <b>{h(short(co.name, 40))}</b> — {h(co.category)} · {_marks(co)}{cms}{fire}")
    lines += ["", "🌐 сайт указан · 📞 телефон · ✉️ email · 🔥 проблема подтверждена проверкой",
              f"<i>{h(attribution)}</i>"]
    if tip:
        from leadly.bot.views.cabinet import first_search_tip
        lines += ["", first_search_tip()]
    rows = [[b(f"{idx + 1}. {'🔥 ' if co.signals else ''}{short(co.name, 40)}", Co(k="r", ref=view.id, i=idx))]
            for idx, co in chunk_items]
    rows.append(pager(page, pages, lambda p: Res(sid=view.id, page=p)))
    if with_contacts:
        rows.append([b(f"⭐ Сохранить всех с контактами ({with_contacts})", Res(sid=view.id, page=-1))])
    if few:
        rows += widen_buttons(view, neighbour)
    if watch:  # hidden when the plan has no auto-search at all
        rows.append([b("🔔 Следить за новыми", Watch(act="add", id=view.id))])
    rows.append([b("🔄 Новый поиск", Menu(to="find")), home_btn()])
    return "\n".join(lines), kb(*rows)
