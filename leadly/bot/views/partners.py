"""Partner program screens: the owner's list and the partner's own cabinet."""

from __future__ import annotations

from leadly.bot.views import h
from leadly.services.partners import PartnerStats
from leadly.utils import plural


def _people(n: int) -> str:
    return f"{n} {plural(n, 'человек', 'человека', 'человек')}"


def partner_block(s: PartnerStats, link: str) -> str:
    return (f"<b>{h(s.name)}</b> · {s.percent}% · код <code>{s.code}</code>" + ("" if s.active else " · ⛔ выключен")
            + f"\nСсылка: {link}"
            + f"\nПришло: {_people(s.users)}, купили: {_people(s.buyers)}, оплачено: {s.paid_stars} ⭐"
            + f"\nЗаработано: {s.earned} ⭐ · выплачено: {s.paid_out} ⭐ · <b>к выплате: {s.owed} ⭐</b>")


def admin_list(rows: list[tuple[PartnerStats, str]]) -> str:
    if not rows:
        return ("🤝 <b>Партнёры</b>\n\nПока никого нет.\n\n"
                "Добавить: <code>/partner_add @username</code> — блогер должен один раз запустить бота, "
                "тогда он будет видеть свою статистику и получать уведомления об оплатах.\n"
                "Или без аккаунта: <code>/partner_add Название канала</code>.")
    total = sum(s.owed for s, _ in rows)
    blocks = "\n\n".join(partner_block(s, link) for s, link in rows)
    return (f"🤝 <b>Партнёры</b> · всего к выплате: <b>{total} ⭐</b>\n\n{blocks}\n\n"
            "Отметить выплату: <code>/partner_paid КОД ЗВЁЗД</code>\n"
            "Выключить ссылку: <code>/partner_off КОД</code> · включить: <code>/partner_on КОД</code>")


def partner_cabinet(s: PartnerStats, link: str, support: str) -> str:
    return (f"🤝 <b>Партнёрская программа Leadly</b>\n\n"
            f"Твоя ссылка:\n{link}\n\n"
            f"Ты получаешь <b>{s.percent}%</b> с каждой оплаты людей, которые пришли по ссылке, "
            "включая продления подписки.\n\n"
            f"Пришло: {_people(s.users)}\n"
            f"Купили: {_people(s.buyers)}\n"
            f"Оплачено ими: {s.paid_stars} ⭐\n"
            f"Твой доход: {s.earned} ⭐\n"
            f"Выплачено: {s.paid_out} ⭐\n"
            f"<b>К выплате: {s.owed} ⭐</b>"
            + (f"\n\nПо выплатам: {h(support)}" if support else ""))


def not_a_partner(support: str) -> str:
    return ("🤝 Партнёрская программа Leadly: 30% с каждой оплаты людей, которых ты приведёшь по своей ссылке."
            + (f"\n\nЧтобы стать партнёром, напиши {h(support)}" if support
               else "\n\nЧтобы стать партнёром, напиши администратору бота."))


def partner_payment_notice(commission: int, amount: int, percent: int, owed: int) -> str:
    return (f"💰 <b>Оплата по твоей ссылке!</b>\n\n+{commission} ⭐ — твои {percent}% от {amount} ⭐.\n"
            f"К выплате сейчас: {owed} ⭐. Статистика — /partner")


def partner_payout_notice(stars: int, owed: int) -> str:
    return f"✅ Тебе выплачено {stars} ⭐ по партнёрской программе. Осталось к выплате: {owed} ⭐."
