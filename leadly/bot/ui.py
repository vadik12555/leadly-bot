"""Telegram I/O helpers and middlewares."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, TelegramObject

from leadly.services import Services
from leadly.utils import now_iso

log = logging.getLogger(__name__)

NO_KB = InlineKeyboardMarkup(inline_keyboard=[])
# free text typed by the user (commands are handled by their own handlers)
USER_TEXT = F.text & ~F.text.startswith("/")

Screen = tuple[str, InlineKeyboardMarkup | None]


async def show(target: CallbackQuery | Message, screen: Screen, new: bool = False) -> Message | None:
    """Edit the message under a pressed button, or send a new one (text input, commands)."""
    text, markup = screen
    if isinstance(target, CallbackQuery):
        msg = target.message
        if not new and isinstance(msg, Message):
            try:
                edited = await msg.edit_text(text, reply_markup=markup or NO_KB)
                return edited if isinstance(edited, Message) else msg
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    return msg
                log.info("edit failed, sending a new message: %s", e)
        return await target.bot.send_message(target.from_user.id, text, reply_markup=markup)
    return await target.answer(text, reply_markup=markup)


async def edit(bot: Bot, msg: Message, screen: Screen) -> None:
    """Replace a loading message with the final screen."""
    text, markup = screen
    try:
        await bot.edit_message_text(text, chat_id=msg.chat.id, message_id=msg.message_id, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            await bot.send_message(msg.chat.id, text, reply_markup=markup)


class UserMiddleware(BaseMiddleware):
    """Registers every Telegram user (by numeric id) and injects `user`, `is_new_user`, `is_admin`."""

    def __init__(self, svc: Services):
        self.svc = svc

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)
        user, created = await self.svc.db.upsert_user(tg_user.id, tg_user.username, tg_user.first_name, now_iso())
        data["user"] = user
        data["is_new_user"] = created
        data["is_admin"] = tg_user.id in self.svc.settings.admin_ids
        return await handler(event, data)


class CallbackAnswerMiddleware(BaseMiddleware):
    """Always answers callback queries so the Telegram button spinner never hangs."""

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        try:
            return await handler(event, data)
        finally:
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except Exception:  # already answered or expired
                    pass


class ThrottleMiddleware(BaseMiddleware):
    """Drops updates from a user who sends more than `limit` actions in `window` seconds."""

    def __init__(self, limit: int = 20, window: float = 10.0, exempt: frozenset[int] = frozenset()):
        import collections
        import time
        self.limit, self.window, self.exempt = limit, window, exempt
        self._clock = time.monotonic
        self._events: dict[int, collections.deque] = collections.defaultdict(collections.deque)
        self._warned: dict[int, float] = {}

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None or tg_user.id in self.exempt:
            return await handler(event, data)
        now = self._clock()
        q = self._events[tg_user.id]
        while q and now - q[0] > self.window:
            q.popleft()
        q.append(now)
        if len(q) <= self.limit:
            return await handler(event, data)
        if now - self._warned.get(tg_user.id, 0) > self.window:
            self._warned[tg_user.id] = now
            log.info("throttled user %s", tg_user.id)
            cb = getattr(event, "callback_query", None)
            msg = getattr(event, "message", None)
            try:
                if cb:
                    await cb.answer("Слишком часто — подожди пару секунд 🙂")
                elif msg:
                    await msg.answer("Слишком много сообщений подряд — подожди пару секунд 🙂")
            except Exception:
                pass
        return None
