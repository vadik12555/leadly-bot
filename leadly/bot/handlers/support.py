"""In-bot support: the user writes a message, every admin receives it and answers by replying to it."""

from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from leadly.bot.callbacks import Menu
from leadly.bot.handlers.admin import IsAdmin
from leadly.bot.states import SupportInput
from leadly.bot.ui import USER_TEXT, show
from leadly.bot.views import b, cancel_btn, h, home_btn, kb
from leadly.db import User
from leadly.services import Services

log = logging.getLogger(__name__)

TAG = re.compile(r"#u(\d+)")


async def _ask(target, state: FSMContext) -> None:
    await state.set_state(SupportInput.text)
    await show(target, ("💬 <b>Поддержка</b>\n\nОпиши вопрос, проблему или идею одним сообщением — его получит "
                        "владелец бота, ответ придёт сюда.", kb([cancel_btn()])))


async def cmd_support(message: Message, state: FSMContext) -> None:
    await _ask(message, state)


async def cb_support(cb: CallbackQuery, state: FSMContext) -> None:
    await _ask(cb, state)


async def msg_support(message: Message, state: FSMContext, user: User, svc: Services, bot: Bot) -> None:
    text = (message.text or "").strip()[:3000]
    if not text:
        return await message.answer("Пришли вопрос текстом или /cancel.")
    plan, _ = await svc.billing.current(user.id)
    who = f"@{h(user.username)}" if user.username else h(user.first_name or "без имени")
    note = (f"💬 <b>Сообщение в поддержку</b> #u{user.id}\nОт: {who} · тариф {plan.title}\n\n{h(text)}\n\n"
            "<i>Ответь на это сообщение (Reply) — ответ уйдёт пользователю.</i>")
    delivered = 0
    for admin in svc.settings.admin_ids:
        try:
            await bot.send_message(admin, note)
            delivered += 1
        except Exception as e:
            log.error("support message to admin %s failed: %s", admin, e)
    await state.set_state(None)
    if not delivered:
        contact = svc.settings.support_contact
        return await message.answer("Не получилось передать сообщение. " +
                                    (f"Напиши напрямую: {h(contact)}" if contact else "Попробуй позже."))
    await show(message, ("✅ Сообщение отправлено. Ответ придёт сюда, в этот чат.",
                         kb([b("🔎 Найти клиентов", Menu(to="find"))], [home_btn()])))


async def admin_reply(message: Message, bot: Bot) -> None:
    m = TAG.search(message.reply_to_message.text or "")
    text = (message.text or "").strip()
    if not m or not text:
        return
    try:
        await bot.send_message(int(m.group(1)), f"💬 <b>Ответ поддержки</b>\n\n{h(text)}",
                               reply_markup=kb([b("Ответить", Menu(to="support"))]))
    except Exception as e:
        return await message.answer(f"Не доставлено: {h(str(e))}")
    await message.answer("✅ Ответ отправлен.")


def create_router() -> Router:
    r = Router(name="support")
    r.message.register(admin_reply, IsAdmin(), F.reply_to_message.from_user.is_bot,
                       F.reply_to_message.text.regexp(TAG, mode="search"), F.text)
    r.message.register(cmd_support, Command("support"))
    r.callback_query.register(cb_support, Menu.filter(F.to == "support"))
    r.message.register(msg_support, SupportInput.text, USER_TEXT)
    return r
