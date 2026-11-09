"""Screens: each function returns (html_text, inline_keyboard). No I/O here."""

from __future__ import annotations

from html import escape as h

from aiogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup

from leadly.bot.callbacks import Menu
from leadly.config import (AI_ANALYSES, CARD_OPENS, PROPOSALS, RESULTS, SEARCHES, SITE_CHECKS, Plan)
from leadly.services.usage import Quota

Screen = tuple[str, InlineKeyboardMarkup]

KIND_NAMES = {
    SEARCHES: "поисков",
    RESULTS: "компаний в выдаче",
    CARD_OPENS: "открытий карточек",
    SITE_CHECKS: "проверок сайтов",
    AI_ANALYSES: "ИИ-анализов",
    PROPOSALS: "генераций предложений",
}
KIND_LABELS = {
    SEARCHES: "Поиски",
    RESULTS: "Компаний в выдаче",
    CARD_OPENS: "Открытия карточек",
    SITE_CHECKS: "Проверки сайтов",
    AI_ANALYSES: "ИИ-анализ",
    PROPOSALS: "Предложения",
}


def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def b(text: str, cb) -> Btn:
    return Btn(text=text, callback_data=cb.pack() if hasattr(cb, "pack") else cb)


def url_btn(text: str, url: str) -> Btn:
    return Btn(text=text, url=url)


def home_btn() -> Btn:
    return b("🏠 Меню", Menu(to="home"))


def cancel_btn() -> Btn:
    return b("✖️ Отмена", Menu(to="cancel"))


def chunk(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def pager(page: int, pages: int, make) -> list[Btn]:
    if pages <= 1:
        return []
    return ([b("‹", make(page - 1))] if page > 0 else []) + [b(f"{page + 1} / {pages}", make(page))] \
        + ([b("›", make(page + 1))] if page < pages - 1 else [])


def quota_line(quota: Quota) -> str:
    if quota.limit is None:
        return "без ограничений"
    left = max(0, quota.limit - quota.used)
    suffix = " сегодня" if quota.period == "day" else ""
    return f"{left} из {quota.limit}{suffix}"


def paywall() -> Screen:
    return ("🔒 <b>Поиск клиентов доступен по подписке</b>\n\n"
            "Оплати тариф звёздами Telegram — и бот сразу начнёт выдавать компании с контактами.",
            kb([b("⭐ Купить доступ", Menu(to="plans"))], [home_btn()]))


def limit_reached(kind: str, quota: Quota, plan: Plan) -> Screen:
    if quota.limit == 0:  # this plan has no access at all: nothing "ran out", it has to be bought
        return paywall()
    what = KIND_NAMES.get(kind, "действий")
    if quota.period == "day":
        text = (f"⏳ <b>Дневной лимит {what} исчерпан</b> ({quota.used}/{quota.limit}).\n\n"
                "Лимит обновится завтра в 00:00 по Москве.")
    else:
        text = (f"⏳ <b>Бесплатный лимит {what} закончился</b> ({quota.used}/{quota.limit}).\n\n"
                "Сохранённые лиды, CRM, статистика и уже найденные результаты остаются доступны.")
    if plan.tier < 2:
        text += "\n\nВ платных тарифах лимиты больше — смотри «⭐ Тарифы»."
    rows = [[b("⭐ Тарифы", Menu(to="plans"))]]
    if kind == SEARCHES and quota.period != "day":
        rows.append([b("🎁 Пригласить друга — бонусные поиски", Menu(to="ref"))])
    rows.append([home_btn()])
    return text, kb(*rows)


def main_menu_kb(channel: str | None = None, channel_bonus: int = 0, referral: bool = False, ai: bool = False,
                 locked: bool = False) -> InlineKeyboardMarkup:
    extra = []
    if channel:
        extra.append([url_btn("📣 Наш канал", f"https://t.me/{channel}")]
                     + ([b(f"🎁 +{channel_bonus} поиска за подписку", Menu(to="chbonus"))] if channel_bonus else []))
    if locked:  # nothing else works without a subscription, so nothing else is offered
        return kb([b("🔎 Найти клиентов", Menu(to="find"))], [b("⭐ Купить доступ", Menu(to="plans"))],
                  [b("❓ Помощь", Menu(to="help"))], *extra)
    return kb(
        [b("🔎 Найти клиентов", Menu(to="find"))],
        [b("📋 Мои лиды", Menu(to="leads")), b("🔔 Автопоиск", Menu(to="watches"))],
        ([b("🤖 ИИ-анализ", Menu(to="ai"))] if ai else []) + [b("✍️ Создать предложение", Menu(to="prop"))],
        [b("⭐ Тарифы", Menu(to="plans")), b("👤 Мой профиль", Menu(to="profile"))],
        [b("📊 Моя статистика", Menu(to="stats"))] + ([b("🎁 Пригласить друга", Menu(to="ref"))] if referral else []),
        [b("❓ Помощь", Menu(to="help"))],
        *extra,
    )


def welcome(plan: Plan, summary: dict[str, Quota], sources: str, is_demo: bool, first_time: bool,
            trial_days: int = 0, channel: str | None = None, channel_bonus: int = 0,
            goal: tuple[int, int, int] | None = None, referral: bool = False, price_from: int = 0,
            ai: bool = False) -> Screen:
    lines = []
    if is_demo:
        lines += ["⚠️ <b>ДЕМО-РЕЖИМ:</b> компании в выдаче вымышленные, источник данных не подключён.", ""]
    lines += [
        "<b>Leadly</b> — поиск клиентов для фрилансеров и студий 🎯",
        "",
        f"Выбираешь услугу, нишу и город — Leadly находит компании в открытых источниках "
        f"(<i>{h(sources)}</i>), проверяет их сайты и помогает написать первое сообщение.",
    ]
    if first_time:
        lines += [
            "",
            "<b>Как выглядит результат</b> <i>(пример)</i>",
            "<i>Салон красоты, Казань · сайт указан · проверка сайта: нет мобильной адаптации, "
            "в подвале © 2018 → гипотеза: предложить обновление сайта с мобильной версией.</i>",
        ]
    if price_from:  # no free access: a price instead of "0 of 0" counters
        lines += ["", f"🔒 <b>Доступ к поиску — от {price_from} ⭐ в месяц.</b> Нажми «⭐ Купить доступ»."]
    else:
        lines += ["", f"<b>Твой тариф: {h(plan.title)}</b>"]
        for kind in (SEARCHES, RESULTS, PROPOSALS) + ((AI_ANALYSES,) if ai else ()):
            lines.append(f"• {KIND_LABELS[kind]}: {quota_line(summary[kind])}")
    if trial_days:
        lines.append(f"🎁 PRO на {trial_days} дня бесплатно — в «⭐ Тарифы»")
    if goal and (goal[0] or goal[2]):
        done, target, streak = goal
        lines.append(f"🎯 Сегодня написал {done} из {target}" + (" ✅" if done >= target else "")
                     + (f" · серия {streak} дн. 🔥" if streak > 1 else ""))
    lines += ["", "Leadly никому не пишет сам — ты решаешь, кому и что отправить."]
    return "\n".join(lines), main_menu_kb(channel, channel_bonus, referral, ai, locked=bool(price_from))


def help_screen(sources: str, attribution: str, ai_status: str, support: str) -> Screen:
    text = (
        "❓ <b>Как работает Leadly</b>\n\n"
        "1. <b>🔎 Найти клиентов</b>: услуга → ниша → город → фильтры.\n"
        f"2. Leadly ищет компании в источниках: <i>{h(sources)}</i>.\n"
        "3. В карточке видно, откуда данные и когда получены. Если поля нет — значит, его нет в источнике, "
        "а не у компании.\n"
        "4. <b>🌐 Проверить сайт</b> — Leadly открывает главную страницу и показывает только то, "
        "что реально увидел.\n"
        "5. <b>✍️ Создать предложение</b> — три варианта первого сообщения без выдуманных фактов.\n"
        "6. <b>⭐ Сохранить</b> — лид попадает в «📋 Мои лиды»: статусы, заметки, воронка. Через 3 дня после "
        "статуса «Связался» бот напомнит написать ещё раз и даст готовый текст.\n"
        "7. <b>🔔 Автопоиск</b> — Leadly сам присылает новые компании по выбранной нише и городу.\n"
        "8. <b>👤 Мой профиль</b> — имя, портфолио и контакт подставляются в предложения.\n\n"
        + (f"🤖 ИИ: {h(ai_status)}\n\n" if ai_status else "") +
        "<b>Важно:</b> Leadly не отправляет сообщения компаниям и не делает рассылок. "
        "Пиши только тем, кому твоя услуга действительно может быть полезна.\n\n"
        "/cancel — отменить текущее действие · /menu — главное меню"
        + (f"\n\nПоддержка: {h(support)}" if support else "")
        + (f"\n\n<i>{h(attribution)}</i>" if attribution else "")
    )
    return text, kb([b("🔎 Найти клиентов", Menu(to="find"))], [b("💬 Написать в поддержку", Menu(to="support"))],
                    [home_btn()])


__all__ = ["h", "Screen", "kb", "b", "url_btn", "home_btn", "cancel_btn", "chunk", "pager", "quota_line",
           "limit_reached", "main_menu_kb", "welcome", "help_screen", "KIND_NAMES", "KIND_LABELS"]
