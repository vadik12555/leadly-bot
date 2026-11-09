"""Company card, details, site check, contacts, AI analysis, proposals, CRM screens."""

from __future__ import annotations

import json
from urllib.parse import quote

from aiogram.types import CopyTextButton, InlineKeyboardButton as Btn

from leadly import catalog as c
from leadly.bot.callbacks import Co, LList, Menu, Pay, Res
from leadly.bot.views import Screen, b, cancel_btn, chunk, h, home_btn, kb, pager, url_btn
from leadly.bot.views.search import RESULTS_PER_PAGE
from leadly.models import AIAnalysis, Company, Insight, SiteReport
from leadly.services.leads import RESULT, SAVED, CompanyContext
from leadly.services.writer import NO_FACT_NOTE, VARIANTS
from leadly.utils import fmt_date, fmt_money, short

NOT_FOUND = "<i>не найден в источнике</i>"
DEMO_LINE = "⚠️ <b>ДЕМО:</b> вымышленная компания."
LEADS_PER_PAGE = 8


def co_cb(ctx: CompanyContext, action: str, x: str = "") -> Co:
    return Co(k=ctx.kind, ref=ctx.ref, i=ctx.idx, a=action, x=x)


def _head(co: Company) -> list[str]:
    lines = [DEMO_LINE, ""] if co.is_demo else []
    lines += [f"<b>{h(co.name)}</b>", f"{h(co.category)} · {h(co.city)}"]
    if co.address:
        lines.append(f"📍 {h(co.address)}")
    lines.append(f"🌐 Сайт: {h(co.domain) if co.website else NOT_FOUND}")
    site_mark = " <i>(с сайта компании)</i>"
    lines.append(f"📞 Телефон: {h(co.phone) if co.phone else NOT_FOUND}"
                 + (site_mark if "phone" in co.contacts_from_site else ""))
    if co.email:
        lines.append(f"✉️ Email: {h(co.email)}" + (site_mark if "email" in co.contacts_from_site else ""))
    lines.append(f"<i>Источник: {h(co.source_title)} · получено {fmt_date(co.fetched_at)}</i>")
    return lines


def card(ctx: CompanyContext, insight: Insight, notes: list[dict] | None = None) -> Screen:
    co = ctx.company
    lines = _head(co) + ["", "<b>Что известно</b>"]
    lines += [f"• {h(f)}" for f in insight.facts[:4]]
    if insight.opportunities:
        lines += ["", "<b>Возможная причина интереса</b> <i>(гипотеза)</i>", f"💡 {h(insight.opportunities[0])}",
                  f"Уверенность: {insight.confidence}"]
    if ctx.kind == SAVED and ctx.lead:
        status = c.STATUSES.get(ctx.lead["status"], c.STATUSES["new"])
        lines += ["", f"Статус: <b>{status.label}</b>"]
        if ctx.lead.get("deal_amount"):
            lines.append(f"Сумма сделки: {fmt_money(ctx.lead['deal_amount'])}")
        for n in notes or []:
            lines.append(f"📝 {fmt_date(n['created_at'])}: {h(short(n['text'], 200))}")
        lines.append(f"<i>Сохранён {fmt_date(ctx.lead['created_at'])}</i>")

    rows = []
    first = [b("🔎 Подробнее", co_cb(ctx, "more"))]
    if co.website:
        first.append(b("🌐 Проверить сайт", co_cb(ctx, "site")))
    rows.append(first)
    ai_btn = [b("🤖 ИИ-анализ", co_cb(ctx, "ai"))] if ctx.ai else []
    rows.append([b("📇 Найти контакт", co_cb(ctx, "contact")), b("✍️ Создать предложение", co_cb(ctx, "prop"))])
    if co.website and not co.is_demo:
        rows.append([b("📄 Аудит сайта для клиента", co_cb(ctx, "audit"))])
    if not co.is_demo:
        where = quote(f"{co.name}, {co.address or co.city}")
        rows.append([url_btn("🗺 Яндекс Карты", f"https://yandex.ru/maps/?text={where}"),
                     url_btn("2ГИС", f"https://2gis.ru/search/{where}")])
    if ctx.kind == RESULT:
        save = (b("✅ В моих лидах", Co(k=SAVED, ref=ctx.lead_id)) if ctx.lead
                else b("⭐ Сохранить", co_cb(ctx, "save")))
        rows.append([*ai_btn, save])
        rows.append([b("🙈 Не показывать снова", co_cb(ctx, "hide")), b("➡️ Следующая", co_cb(ctx, "next"))])
        rows.append([b("← К результатам", Res(sid=ctx.ref, page=ctx.idx // RESULTS_PER_PAGE)), home_btn()])
    else:
        rows.append([*ai_btn, b("🔄 Статус", co_cb(ctx, "status"))])
        rows.append([b("📝 Заметка", co_cb(ctx, "note")), b("🔁 Найти похожие", co_cb(ctx, "similar"))])
        rows.append([b("🗑 Удалить", co_cb(ctx, "del"))])
        rows.append([b("← Мои лиды", LList(st=ctx.lead["status"] if ctx.lead else "all")), home_btn()])
    return "\n".join(lines), kb(*rows)


def back_row(ctx: CompanyContext) -> list[Btn]:
    return [b("← К карточке", co_cb(ctx, "open")), home_btn()]


def details(ctx: CompanyContext, insight: Insight, report: SiteReport | None) -> Screen:
    co = ctx.company
    lines = _head(co)
    if co.opening_hours:
        lines.append(f"🕒 Часы работы: {h(co.opening_hours)}")
    for name, value in co.socials.items():
        lines.append(f"🔗 {h(name)}: {h(value)}")
    lines += ["", "<b>Подтверждено</b>"] + [f"✅ {h(f)}" for f in insight.facts]
    if insight.not_checked:
        lines += ["", "<b>Не проверено</b>"] + [f"❔ {h(x)}" for x in insight.not_checked]
    if insight.opportunities:
        lines += ["", "<b>Гипотезы</b>"] + [f"💡 {h(x)}" for x in insight.opportunities]
        lines.append(f"Уверенность: {insight.confidence}")
    if report and report.ok and report.title:
        lines += ["", f"<i>Заголовок сайта: {h(report.title)}</i>"]
    rows = []
    if co.source_url:
        rows.append([url_btn(f"🗺 Открыть в {co.source_title}", co.source_url)])
    rows.append(back_row(ctx))
    return "\n".join(lines), kb(*rows)


def site_report(ctx: CompanyContext, report: SiteReport, insight: Insight) -> Screen:
    co = ctx.company
    lines = [f"🌐 <b>Проверка сайта</b> · {h(co.domain or '')}", f"<i>{fmt_date(report.checked_at)}, главная страница</i>",
             ""]
    if report.robots_blocked:
        lines.append("⛔ Сайт запрещает автоматическую проверку (robots.txt). Открой его вручную.")
    elif not report.ok:
        lines.append(f"❌ Сайт не открылся: {h(report.error or 'ошибка')}")
        lines.append("<i>Это может быть временный сбой — проверь позже или вручную.</i>")
    else:
        def yn(v, yes, no):
            return "❔ не определено" if v is None else (yes if v else no)
        lines += [
            f"Статус: {report.status} · загрузка {report.response_ms / 1000:.1f} с",
            f"HTTPS: {yn(report.https, '✅ да', '⚠️ нет')}",
            f"Мобильная адаптация (meta viewport): {yn(report.has_viewport, '✅ есть', '⚠️ нет')}",
            f"Платформа: {h(report.cms) if report.cms else 'не определена'}",
            f"© в подвале: {report.copyright_year or 'не найден'}",
            f"Форма на главной: {yn(report.has_form, '✅ есть', 'нет')}",
            f"Онлайн-запись: {', '.join(report.booking_widgets) if report.booking_widgets else 'виджеты не найдены'}",
            f"Аналитика: {', '.join(report.analytics) if report.analytics else 'счётчики не найдены'}",
        ]
        if report.title:
            lines.append(f"Title: <i>{h(short(report.title, 100))}</i>")
    if insight.opportunities:
        lines += ["", "<b>Что можно предложить</b> <i>(гипотезы)</i>"] + [f"💡 {h(x)}" for x in insight.opportunities]
    lines += ["", "<i>Проверяется только главная страница; отсутствие элемента на ней не означает, "
                  "что его нет на сайте.</i>"]
    rows = [[url_btn("Открыть сайт", report.final_url or report.url)]] if report.ok else []
    rows.append([b("📄 Аудит для клиента", co_cb(ctx, "audit")), b("✍️ Предложение", co_cb(ctx, "prop"))])
    rows.append(back_row(ctx))
    return "\n".join(lines), kb(*rows)


def contacts(ctx: CompanyContext, report: SiteReport | None) -> Screen:
    co = ctx.company
    lines = [f"📇 <b>Контакты</b> · {h(co.name)}", "", f"<b>{h(co.source_title)}</b>"]
    found = False
    for label, value in (("📞 Телефон", co.phone), ("✉️ Email", co.email), ("🌐 Сайт", co.website)):
        if value:
            lines.append(f"{label}: {h(value)}")
            found = True
    for name, value in co.socials.items():
        lines.append(f"🔗 {h(name)}: {h(value)}")
        found = True
    if not found:
        lines.append("Контакты в источнике не указаны.")
    if report and report.ok:
        lines += ["", f"<b>Сайт компании</b> <i>({fmt_date(report.checked_at)})</i>"]
        site_found = False
        for e in report.emails:
            lines.append(f"✉️ {h(e)}")
            site_found = True
        for p in report.phones:
            lines.append(f"📞 {h(p)}")
            site_found = True
        for name, value in report.socials.items():
            lines.append(f"🔗 {h(name)}: {h(value)}")
            site_found = True
        if report.contacts_page:
            lines.append(f"Страница контактов: {h(report.contacts_page)}")
        if not site_found:
            lines.append("На сайте публичные контакты не найдены.")
    elif report and not report.ok:
        lines += ["", f"Сайт проверить не удалось: {h(report.error or '')}"]
    elif not co.website:
        lines += ["", "<i>Сайт в источнике не указан — искать контакты на сайте негде.</i>"]
    lines += ["", "<i>Используй контакты для единичного делового обращения. Без рассылок.</i>"]
    return "\n".join(lines), kb([b("✍️ Создать предложение", co_cb(ctx, "prop"))], back_row(ctx))


def ai_not_configured(ctx: CompanyContext, insight: Insight, message: str) -> Screen:
    lines = [f"🤖 <b>ИИ-анализ</b> · {h(ctx.company.name)}", "", f"ℹ️ {h(message)}", "",
             "Пока можно опираться на проверенные факты:"]
    lines += [f"✅ {h(f)}" for f in insight.facts]
    lines += [f"❔ {h(x)}" for x in insight.not_checked]
    lines += [f"💡 {h(x)}" for x in insight.opportunities]
    return "\n".join(lines), kb([b("✍️ Шаблон предложения", co_cb(ctx, "prop"))], back_row(ctx))


def ai_error(ctx: CompanyContext, message: str) -> Screen:
    return (f"⚠️ <b>ИИ-анализ не выполнен</b>\n\n{h(message)}\n\nЛимит не списан.",
            kb([b("🔄 Повторить", co_cb(ctx, "ai"))], back_row(ctx)))


def ai_analysis(ctx: CompanyContext, a: AIAnalysis) -> Screen:
    lines = [f"🤖 <b>ИИ-анализ</b> · {h(ctx.company.name)}", f"Услуга: {h(ctx.service.title)}", ""]
    if a.summary:
        lines += [h(a.summary), ""]
    if a.checked:
        lines += ["<b>Что удалось проверить</b>"] + [f"✅ {h(x)}" for x in a.checked] + [""]
    if a.opportunities:
        lines.append("<b>Возможности</b>")
        lines += [f"💡 {h(o['text'])} <i>(уверенность: {h(o['confidence'])})</i>" for o in a.opportunities]
        lines.append("")
    lines += ["<b>Рекомендация</b>", h(a.recommendation), "", f"Общая уверенность: <b>{h(a.confidence)}</b>",
              "<i>ИИ работает только с фактами из карточки и проверки сайта; гипотезы стоит уточнить.</i>"]
    return "\n".join(lines), kb([b("✍️ Создать предложение", co_cb(ctx, "prop"))], back_row(ctx))


def proposals(ctx: CompanyContext, texts: dict[str, str | None], variant: str, source: str,
              note: str | None = None, profile_empty: bool = False) -> Screen:
    order = ["short", "business", "fact"] + (["edited"] if texts.get("edited") else [])
    names = {**VARIANTS, "edited": "Моя версия"}
    text = texts.get(variant)
    src = {"ai": "ИИ", "template": "шаблон", "user": "отредактировано тобой"}.get(source, source)
    lines = [f"✍️ <b>Предложение</b> · {h(ctx.company.name)}", f"Вариант: {names[variant]} · <i>{src}</i>", ""]
    if note:
        lines += [f"⚠️ {h(note)} Показан шаблон.", ""]
    lines.append(f"<blockquote>{h(text)}</blockquote>" if text else f"<i>{NO_FACT_NOTE}</i>")
    hours = ctx.company.opening_hours
    lines += ["", f"🕒 Компания работает: <code>{h(hours)}</code> — пиши в рабочее время, лучше в первой половине дня."
              if hours else "🕒 Лучшее время для первого сообщения — будни, 10:00–12:00 или 14:00–17:00.",
              "<i>Проверь текст перед отправкой. Leadly ничего не отправляет сам.</i>"]
    if profile_empty:
        lines.append("💡 Заполни «👤 Мой профиль» — имя, портфолио и контакт подставятся в текст автоматически.")
    tabs = [b(("• " if v == variant else "") + names[v], co_cb(ctx, "pv", v)) for v in order]
    rows = chunk(tabs, 3)
    action_row = [b("🔄 Другие варианты", co_cb(ctx, "regen"))]
    if text:
        action_row.append(Btn(text="📋 Скопировать", copy_text=CopyTextButton(text=text)) if len(text) <= 256
                          else b("📋 Скопировать", co_cb(ctx, "copy", variant)))
    rows.append(action_row)
    if text:
        rows.append([b("✏️ Изменить текст", co_cb(ctx, "edit", variant)), b("💾 В шаблоны", co_cb(ctx, "tsave", variant))])
    rows.append([b("📄 Мои шаблоны", co_cb(ctx, "tpls"))])
    if ctx.kind == RESULT and not ctx.lead:
        rows.append([b("⭐ Сохранить лида", co_cb(ctx, "save", "prop"))])
    if profile_empty:
        rows.append([b("👤 Заполнить профиль", Menu(to="profile"))])
    rows.append(back_row(ctx))
    return "\n".join(lines), kb(*rows)


def followup(ctx: CompanyContext, text: str) -> Screen:
    copy = (Btn(text="📋 Скопировать", copy_text=CopyTextButton(text=text)) if len(text) <= 256
            else b("📋 Скопировать", co_cb(ctx, "fupc")))
    return (f"✍️ <b>Напоминание</b> · {h(ctx.company.name)}\n\n<blockquote>{h(text)}</blockquote>\n\n"
            "<i>Отправь сам тем же способом, что и первое сообщение.</i>",
            kb([copy], [b("👀 Ответили", co_cb(ctx, "setst", "replied")),
                        b("❌ Отказ", co_cb(ctx, "setst", "refused"))], back_row(ctx)))


def templates_picker(ctx: CompanyContext, rows: list[dict]) -> Screen:
    if not rows:
        return ("📄 <b>Мои шаблоны</b>\n\nШаблонов пока нет. Сохрани удачный текст кнопкой «💾 В шаблоны» "
                "или создай свой в «👤 Мой профиль» → «📄 Мои шаблоны».",
                kb([b("➕ Создать шаблон", Menu(to="templates"))], back_row(ctx)))
    buttons = [[b(f"📄 {r['title'][:40]}", co_cb(ctx, "tpl", str(r["id"])))] for r in rows]
    return f"📄 <b>Выбери шаблон</b> для {h(ctx.company.name)}", kb(*buttons, back_row(ctx))


def template_result(ctx: CompanyContext, title: str, text: str, missing: list[str]) -> Screen:
    lines = [f"📄 <b>{h(title)}</b> · {h(ctx.company.name)}", "", f"<blockquote>{h(text)}</blockquote>"]
    if missing:
        lines += ["", "⚠️ Не хватило данных для: " + ", ".join(f"{{{m}}}" for m in missing)
                  + ". Проверь текст или заполни профиль."]
    copy = (Btn(text="📋 Скопировать", copy_text=CopyTextButton(text=text)) if len(text) <= 256
            else b("📋 Скопировать", co_cb(ctx, "tplc", "")))
    return "\n".join(lines), kb([copy], [b("📄 Другой шаблон", co_cb(ctx, "tpls"))], back_row(ctx))


def copy_message(text: str) -> str:
    return f"📋 Нажми на текст, чтобы скопировать:\n\n<code>{h(text)}</code>"


def ask_edit(ctx: CompanyContext, current: str) -> Screen:
    return ("✏️ Пришли исправленный текст одним сообщением — сохраню его как «Моя версия».\n\n"
            f"Текущий текст:\n<code>{h(current)}</code>", kb([cancel_btn()]))


# --- CRM -------------------------------------------------------------------------------


def status_picker(ctx: CompanyContext) -> Screen:
    current = ctx.lead["status"] if ctx.lead else "new"
    buttons = [b(("• " if current == s.code else "") + s.label, co_cb(ctx, "setst", s.code))
               for s in c.STATUSES.values()]
    return (f"🔄 <b>Статус лида</b>\n{h(ctx.company.name)}", kb(*chunk(buttons, 2), back_row(ctx)))


def ask_note(ctx: CompanyContext) -> Screen:
    return f"📝 Напиши заметку к лиду <b>{h(ctx.company.name)}</b> одним сообщением.", kb([cancel_btn()])


def ask_deal_amount(ctx: CompanyContext) -> Screen:
    return ("💰 <b>Сделка!</b> Укажи сумму в рублях, например <code>50000</code> — она попадёт в статистику.",
            kb([b("Пропустить", co_cb(ctx, "open"))]))


def confirm_delete(ctx: CompanyContext) -> Screen:
    return (f"🗑 Удалить <b>{h(ctx.company.name)}</b> из «Мои лиды»? Заметки тоже удалятся.",
            kb([b("🗑 Да, удалить", co_cb(ctx, "delok")), b("Отмена", co_cb(ctx, "open"))]))


def saved_limit(limit: int) -> Screen:
    return (f"⭐ <b>Достигнут лимит сохранённых лидов</b> ({limit}).\n\nУдали ненужные или посмотри тарифы.",
            kb([b("⭐ Тарифы", Menu(to="plans"))], [b("📋 Мои лиды", Menu(to="leads"))], [home_btn()]))


def leads_home(counts: dict[str, int], can_export: bool) -> Screen:
    total = sum(counts.values())
    if not total:
        return ("📋 <b>Мои лиды</b>\n\nПока пусто. Найди компании и нажми «⭐ Сохранить» в карточке.",
                kb([b("🔎 Найти клиентов", Menu(to="find"))], [home_btn()]))
    buttons = [b(f"{s.label} · {counts.get(s.code, 0)}", LList(st=s.code)) for s in c.STATUSES.values()]
    rows = chunk(buttons, 2) + [[b(f"📋 Все · {total}", LList(st="all"))]]
    rows.append([b("📤 Экспорт в CSV", Pay(act="export"))] if can_export else [])
    rows.append([home_btn()])
    return f"📋 <b>Мои лиды</b> · {total}\n\nВыбери статус.", kb(*rows)


def leads_list(rows_db: list[dict], status: str, page: int, total: int) -> Screen:
    title = "Все лиды" if status == "all" else c.STATUSES[status].label
    if not total:
        return f"📋 <b>{title}</b>\n\nЗдесь пока пусто.", kb([b("← Мои лиды", LList(st="home"))], [home_btn()])
    pages = max(1, -(-total // LEADS_PER_PAGE))
    rows = []
    for r in rows_db:
        co = Company.from_dict(json.loads(r["company"]))
        mark = c.STATUSES[r["status"]].emoji + " " if status == "all" else ""
        rows.append([b(f"{mark}{short(co.name, 34)} · {co.city}", Co(k=SAVED, ref=r["id"]))])
    rows.append(pager(page, pages, lambda p: LList(st=status, page=p)))
    rows.append([b("← Мои лиды", LList(st="home")), home_btn()])
    return f"📋 <b>{title}</b> · {total}", kb(*rows)


def picker(purpose: str, leads: list[dict], last_search: int | None) -> Screen:
    title = "🤖 <b>ИИ-анализ</b>" if purpose == "ai" else "✍️ <b>Создать предложение</b>"
    action = "ai" if purpose == "ai" else "prop"
    if not leads and not last_search:
        return (f"{title}\n\nСначала найди компанию: анализ и предложения делаются для конкретного бизнеса.",
                kb([b("🔎 Найти клиентов", Menu(to="find"))], [home_btn()]))
    rows = []
    for r in leads:
        co = Company.from_dict(json.loads(r["company"]))
        rows.append([b(f"⭐ {short(co.name, 40)}", Co(k=SAVED, ref=r["id"], a=action))])
    if last_search:
        rows.append([b("🔎 Выбрать из последнего поиска", Res(sid=last_search))])
    rows.append([home_btn()])
    return f"{title}\n\nВыбери компанию из сохранённых лидов или последнего поиска.", kb(*rows)


__all__ = ["card", "details", "site_report", "contacts", "ai_not_configured", "ai_error", "ai_analysis",
           "proposals", "copy_message", "ask_edit", "status_picker", "ask_note", "ask_deal_amount",
           "confirm_delete", "saved_limit", "leads_home", "leads_list", "picker", "co_cb"]
