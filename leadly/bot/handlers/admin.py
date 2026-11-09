"""Admin panel. Only Telegram user ids listed in ADMIN_IDS can reach these handlers."""

from __future__ import annotations

import asyncio
import logging
from collections import deque

from aiogram import Bot, Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from leadly.bot.callbacks import Adm
from leadly.bot.ui import show
from leadly.bot.views import cabinet as cv
from leadly.bot.views import h
from leadly.bot.views import partners as pv
from leadly.services import Services
from leadly.utils import fmt_date

log = logging.getLogger(__name__)


class IsAdmin(BaseFilter):
    async def __call__(self, event, is_admin: bool = False) -> bool:
        return is_admin


def _error_lines(svc: Services, limit: int = 12) -> list[str]:
    path = svc.settings.log_file
    if not path or not path.exists():
        return []
    tail: deque[str] = deque(maxlen=limit)
    for p in sorted(path.parent.glob(path.name + "*"), key=lambda x: x.stat().st_mtime):
        with p.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if " ERROR " in line or " CRITICAL " in line:
                    tail.append(line.strip())
    return list(tail)


async def _home(target, svc: Services) -> None:
    await show(target, cv.admin_home(await svc.account.admin_overview(), svc.settings.product))


async def cmd_admin(message: Message, svc: Services) -> None:
    await _home(message, svc)


async def cb_admin(cb: CallbackQuery, callback_data: Adm, svc: Services, state: FSMContext, bot: Bot) -> None:
    act = callback_data.act
    if act == "home":
        return await _home(cb, svc)
    if act == "sources":
        rows = [(f"Поиск · {p.title}", *p.configured()) for p in svc.search_sources.providers]
        rows.append(("ИИ", *svc.ai.configured()))
        rows.append(("Оплата Stars", svc.settings.payments_enabled,
                     "включена" if svc.settings.payments_enabled else "выключена (PAYMENTS_ENABLED)"))
        return await show(cb, cv.admin_sources(rows, await svc.account.last_search_error()))
    if act == "errors":
        return await show(cb, cv.admin_errors(_error_lines(svc)))
    if act in ("bc_ok", "bc_no"):
        text = (await state.get_data()).get("broadcast")
        await state.update_data(broadcast=None)
        if act == "bc_no" or not text:
            return await show(cb, ("Рассылка отменена.", cv.kb([cv.b("← Админка", Adm(act="home"))])))
        asyncio.create_task(_broadcast(bot, svc, cb.from_user.id, text))
        return await show(cb, ("📣 Рассылка запущена. Пришлю итог, когда закончу.",
                               cv.kb([cv.b("← Админка", Adm(act="home"))])))
    if act == "reload":
        try:
            svc.rt.reload_product()
            await cb.answer("plans.json перечитан ✅", show_alert=True)
        except Exception as e:
            log.error("plans.json reload failed: %s", e)
            await cb.answer(f"Ошибка в plans.json: {e}"[:190], show_alert=True)
        await _home(cb, svc)


async def cmd_user(message: Message, command: CommandObject, svc: Services) -> None:
    arg = (command.args or "").strip()
    if not arg.lstrip("-").isdigit():
        return await message.answer("Использование: /user TELEGRAM_ID")
    card = await svc.account.user_card(int(arg))
    await message.answer(cv.admin_user(card, svc.settings.product) if card else "Пользователь не найден.")


async def cmd_grant(message: Message, command: CommandObject, svc: Services, bot: Bot) -> None:
    parts = (command.args or "").split()
    if len(parts) != 3 or not parts[0].isdigit() or not parts[2].isdigit():
        return await message.answer("Использование: /grant TELEGRAM_ID pro|pro_plus ДНЕЙ\n"
                                    "(ручная выдача — для тестов и компенсаций, без оплаты)")
    user_id, plan, days = int(parts[0]), parts[1], int(parts[2])
    if not await svc.db.get_user(user_id):
        return await message.answer("Пользователь ещё не запускал бота.")
    try:
        ends = await svc.billing.grant(user_id, plan, days)
    except ValueError:
        return await message.answer("Неизвестный платный тариф.")
    log.info("admin %s granted %s for %s days to %s", message.from_user.id, plan, days, user_id)
    await message.answer(f"Выдано: {plan} до {fmt_date(ends)}")


async def cmd_refund(message: Message, command: CommandObject, svc: Services, bot: Bot) -> None:
    charge_id = (command.args or "").strip()
    payment = await svc.billing.payment_by_charge(charge_id) if charge_id else None
    if not payment:
        return await message.answer("Использование: /refund TELEGRAM_CHARGE_ID (платёж должен быть в базе)")
    if payment["status"] == "refunded":
        return await message.answer("Этот платёж уже возвращён.")
    try:
        await bot.refund_star_payment(user_id=payment["user_id"], telegram_payment_charge_id=charge_id)
    except Exception as e:
        log.error("refund failed for %s: %s", charge_id, e)
        return await message.answer(f"Telegram не выполнил возврат: {e}")
    await svc.billing.mark_refunded(charge_id)
    log.info("admin %s refunded %s", message.from_user.id, charge_id)
    await message.answer("Возврат выполнен, подписка по этому платежу отключена.")


async def cmd_promo(message: Message, command: CommandObject, svc: Services) -> None:
    parts = (command.args or "").split()
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        return await message.answer("Использование: /promo КОД pro|pro_plus ДНЕЙ МАКС_АКТИВАЦИЙ\n"
                                    "Например: /promo START30 pro 30 100")
    code, plan, days, uses = parts[0].upper(), parts[1], int(parts[2]), int(parts[3])
    try:
        await svc.billing.create_promo(code, plan, days, uses, message.from_user.id)
    except ValueError:
        return await message.answer("Неверные параметры: тариф должен быть платным, дни и активации больше 0.")
    except Exception:
        return await message.answer("Такой промокод уже существует.")
    await message.answer(f"Промокод <code>{code}</code> создан: {plan} на {days} дн., до {uses} активаций.")


async def cmd_keys(message: Message, command: CommandObject, svc: Services) -> None:
    parts = (command.args or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        stats = await svc.billing.keys_summary()
        return await message.answer("Использование: /keys pro|pro_plus ДНЕЙ КОЛИЧЕСТВО\nНапример: /keys pro 30 10\n\n"
                                    f"Выпущено ключей: {stats['total']}, активировано: {stats['used']}.")
    plan, days, count = parts[0], int(parts[1]), int(parts[2])
    try:
        keys = await svc.billing.create_keys(plan, days, count, message.from_user.id)
    except ValueError:
        return await message.answer("Неверные параметры: платный тариф, дни > 0, количество 1–200.")
    log.info("admin %s issued %s keys for %s/%s days", message.from_user.id, len(keys), plan, days)
    body = "\n".join(f"<code>{k}</code>" for k in keys)
    await message.answer(f"🔑 Ключи {plan} на {days} дн. (каждый — одна активация):\n\n{body}\n\n"
                         "Пользователь вводит ключ в «⭐ Тарифы» → «🎟 Промокод или ключ».")


async def cmd_broadcast(message: Message, command: CommandObject, state: FSMContext, svc: Services) -> None:
    text = (command.args or "").strip()
    if not text:
        return await message.answer("Использование: /broadcast ТЕКСТ (поддерживается HTML-разметка Telegram)")
    total = (await svc.db.fetchone("SELECT COUNT(*) AS n FROM users"))["n"]
    await state.update_data(broadcast=text[:3500])
    await message.answer(f"Предпросмотр рассылки (получателей: {total}):\n\n{text[:3500]}",
                         reply_markup=cv.kb([cv.b("📣 Отправить всем", Adm(act="bc_ok")),
                                             cv.b("Отмена", Adm(act="bc_no"))]))


async def _broadcast(bot: Bot, svc: Services, admin_id: int, text: str) -> None:
    sent = failed = 0
    for row in await svc.db.fetchall("SELECT id FROM users ORDER BY id"):
        try:
            await bot.send_message(row["id"], text)
            sent += 1
        except Exception:
            failed += 1  # blocked the bot or deleted the account
        await asyncio.sleep(0.05)  # stay well under Telegram's ~30 messages/second limit
    log.info("broadcast by %s: sent %s, failed %s", admin_id, sent, failed)
    try:
        await bot.send_message(admin_id, f"📣 Рассылка завершена: доставлено {sent}, не доставлено {failed}.")
    except Exception:
        pass


async def _partner_rows(svc: Services, bot_username: str):
    return [(s, svc.partners.link(bot_username, s.code)) for s in await svc.partners.all_stats()]


async def cmd_partner_add(message: Message, command: CommandObject, svc: Services, bot_username: str) -> None:
    """/partner_add @username [30%] or /partner_add TELEGRAM_ID or /partner_add Название канала"""
    args = (command.args or "").split()
    percent = svc.settings.product.partner_percent
    if args and args[-1].rstrip("%").isdigit() and args[-1].endswith("%"):
        percent = int(args.pop().rstrip("%"))
    if not args:
        return await message.answer("Использование:\n<code>/partner_add @username</code> — блогер уже запускал бота, "
                                    "он будет видеть статистику (/partner) и получать уведомления\n"
                                    "<code>/partner_add Название канала</code> — без аккаунта\n"
                                    f"Процент по умолчанию {percent}%, другой: <code>/partner_add @username 40%</code>")
    first, telegram_id, name = args[0], None, " ".join(args)
    if first.startswith("@") or first.isdigit():
        row = (await svc.db.fetchone("SELECT id, username FROM users WHERE id = ?", int(first)) if first.isdigit()
               else await svc.db.fetchone("SELECT id, username FROM users WHERE lower(username) = ?",
                                          first.lstrip("@").lower()))
        if not row:
            return await message.answer(f"{h(first)} ещё не запускал бота. Попроси его открыть бота и нажать «Старт», "
                                        "потом повтори команду. Или добавь без аккаунта: "
                                        "<code>/partner_add Название канала</code>")
        telegram_id, name = row["id"], f"@{row['username']}" if row["username"] else str(row["id"])
    try:
        code = await svc.partners.create(name, percent, telegram_id)
    except ValueError as e:
        text = "Он уже партнёр — смотри /partners." if "already" in str(e) else "Процент должен быть от 1 до 90."
        return await message.answer(text)
    link = svc.partners.link(bot_username, code)
    log.info("admin %s added partner %s (%s%%)", message.from_user.id, code, percent)
    await message.answer(f"🤝 Партнёр <b>{h(name)}</b> добавлен, {percent}% с оплат.\n\nЕго ссылка:\n{link}\n\n"
                         "Отправь её блогеру." + ("\nСвою статистику он увидит по команде /partner." if telegram_id
                                                  else ""))


async def cmd_partners(message: Message, svc: Services, bot_username: str) -> None:
    await message.answer(pv.admin_list(await _partner_rows(svc, bot_username)), disable_web_page_preview=True)


async def cmd_partner_paid(message: Message, command: CommandObject, svc: Services, bot: Bot) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) <= 0:
        return await message.answer("Использование: <code>/partner_paid КОД ЗВЁЗД</code> — после того как ты "
                                    "перевёл партнёру деньги. Коды и суммы — /partners")
    partner = await svc.partners.by_code(parts[0])
    if not partner:
        return await message.answer("Партнёр с таким кодом не найден. Коды — /partners")
    stars = int(parts[1])
    owed = (await svc.partners.stats(partner)).owed
    if stars > owed:
        return await message.answer(f"Это больше, чем ты должен ({owed} ⭐). Проверь сумму.")
    await svc.partners.record_payout(partner, stars)
    left = owed - stars
    log.info("admin %s recorded payout %s stars to partner %s", message.from_user.id, stars, partner["code"])
    await message.answer(f"✅ Записал выплату {stars} ⭐ партнёру {h(partner['name'])}. Осталось к выплате: {left} ⭐.")
    if partner["telegram_id"]:
        try:
            await bot.send_message(partner["telegram_id"], pv.partner_payout_notice(stars, left))
        except Exception:
            pass


async def _toggle_partner(message: Message, command: CommandObject, svc: Services, active: bool) -> None:
    partner = await svc.partners.by_code((command.args or "").strip()) if command.args else None
    if not partner:
        return await message.answer(f"Использование: <code>/partner_{'on' if active else 'off'} КОД</code>")
    await svc.partners.set_active(partner, active)
    await message.answer(f"Ссылка партнёра {h(partner['name'])} " + (
        "снова работает." if active else "выключена: новые люди по ней не закрепляются. "
                                         "За тех, кто уже пришёл, процент продолжает начисляться."))


async def cmd_partner_off(message: Message, command: CommandObject, svc: Services) -> None:
    await _toggle_partner(message, command, svc, False)


async def cmd_partner_on(message: Message, command: CommandObject, svc: Services) -> None:
    await _toggle_partner(message, command, svc, True)


def create_router() -> Router:
    r = Router(name="admin")
    # applied to the whole router: non-admins never reach these handlers (they get the fallback reply)
    r.message.filter(IsAdmin())
    r.callback_query.filter(IsAdmin())
    r.message.register(cmd_admin, Command("admin"))
    r.message.register(cmd_user, Command("user"))
    r.message.register(cmd_grant, Command("grant"))
    r.message.register(cmd_refund, Command("refund"))
    r.message.register(cmd_promo, Command("promo"))
    r.message.register(cmd_keys, Command("keys"))
    r.message.register(cmd_broadcast, Command("broadcast"))
    r.message.register(cmd_partner_add, Command("partner_add"))
    r.message.register(cmd_partners, Command("partners"))
    r.message.register(cmd_partner_paid, Command("partner_paid"))
    r.message.register(cmd_partner_off, Command("partner_off"))
    r.message.register(cmd_partner_on, Command("partner_on"))
    r.callback_query.register(cb_admin, Adm.filter())
    return r
