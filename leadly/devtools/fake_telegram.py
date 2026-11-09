"""Offline Telegram: feeds real Update objects to the real Dispatcher and records replies.

Used by tests and by the interactive console when api.telegram.org is unreachable.
No network calls are made.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import (AnswerCallbackQuery, AnswerPreCheckoutQuery, CreateInvoiceLink, EditMessageText,
                             EditUserStarSubscription, GetChatMember, GetMe, RefundStarPayment, SendDocument,
                             SendInvoice, SendMessage, TelegramMethod)
from aiogram.types import (Chat, ChatMemberLeft, ChatMemberMember, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message, Update, User)

from leadly.bot.app import bot_defaults, create_dispatcher
from leadly.config import Settings
from leadly.db import Database
from leadly.services import Services, build_services

BOT_USER = User(id=777000111, is_bot=True, first_name="Leadly", username="Lead1HuntBot")
FAKE_TOKEN = "123456:TEST-TOKEN-NOT-REAL"


@dataclass
class BotMessage:
    message_id: int
    chat_id: int
    text: str
    markup: InlineKeyboardMarkup | None
    document: str | None = None

    @property
    def buttons(self) -> list[InlineKeyboardButton]:
        if not self.markup:
            return []
        return [btn for row in self.markup.inline_keyboard for btn in row]


@dataclass
class FakeTelegramSession(BaseSession):
    messages: dict[int, BotMessage] = field(default_factory=dict)
    order: list[int] = field(default_factory=list)  # message ids, most recently touched last
    alerts: list[str] = field(default_factory=list)
    requests: list[TelegramMethod] = field(default_factory=list)
    invoices: list[SendInvoice] = field(default_factory=list)
    pre_checkout_answers: list[AnswerPreCheckoutQuery] = field(default_factory=list)
    refunds: list[RefundStarPayment] = field(default_factory=list)
    invoice_links: list[CreateInvoiceLink] = field(default_factory=list)
    star_cancellations: list[EditUserStarSubscription] = field(default_factory=list)
    channel_members: set[int] = field(default_factory=set)

    def __post_init__(self):
        super().__init__()
        self._ids = itertools.count(1000)

    def _touch(self, mid: int) -> None:
        if mid in self.order:
            self.order.remove(mid)
        self.order.append(mid)

    def _message(self, m: BotMessage) -> Message:
        return Message(message_id=m.message_id, date=datetime.now(timezone.utc),
                       chat=Chat(id=m.chat_id, type="private"), from_user=BOT_USER,
                       text=m.text, reply_markup=m.markup)

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:
        self.requests.append(method)
        if isinstance(method, GetMe):
            return BOT_USER
        if isinstance(method, SendMessage):
            m = BotMessage(next(self._ids), int(method.chat_id), method.text, method.reply_markup)
            self.messages[m.message_id] = m
            self._touch(m.message_id)
            return self._message(m)
        if isinstance(method, EditMessageText):
            m = self.messages[method.message_id]
            m.text, m.markup = method.text, method.reply_markup
            self._touch(m.message_id)
            return self._message(m)
        if isinstance(method, AnswerCallbackQuery):
            if method.text:
                self.alerts.append(method.text)
            return True
        if isinstance(method, SendInvoice):
            self.invoices.append(method)
            m = BotMessage(next(self._ids), int(method.chat_id), f"[invoice] {method.title}", None)
            self.messages[m.message_id] = m
            self._touch(m.message_id)
            return self._message(m)
        if isinstance(method, CreateInvoiceLink):
            self.invoice_links.append(method)
            return f"https://t.me/$fake-invoice-{len(self.invoice_links)}"
        if isinstance(method, EditUserStarSubscription):
            self.star_cancellations.append(method)
            return True
        if isinstance(method, GetChatMember):
            member = User(id=method.user_id, is_bot=False, first_name="u")
            return (ChatMemberMember(user=member) if method.user_id in self.channel_members
                    else ChatMemberLeft(user=member))
        if isinstance(method, AnswerPreCheckoutQuery):
            self.pre_checkout_answers.append(method)
            return True
        if isinstance(method, RefundStarPayment):
            self.refunds.append(method)
            return True
        if isinstance(method, SendDocument):
            m = BotMessage(next(self._ids), int(method.chat_id), method.caption or "", None,
                           document=getattr(method.document, "filename", "file"))
            self.messages[m.message_id] = m
            self._touch(m.message_id)
            return self._message(m)
        return True  # set_my_commands, delete_webhook, chat actions, ...

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover - not needed offline
        raise NotImplementedError
        yield b""


class TelegramSimulator:
    """A chat with the bot as one Telegram user."""

    def __init__(self, dp: Dispatcher, bot: Bot, session: FakeTelegramSession, svc: Services,
                 user_id: int = 100500, first_name: str = "Тест", username: str | None = "tester"):
        self.dp, self.bot, self.session, self.svc = dp, bot, session, svc
        self.user = {"id": user_id, "is_bot": False, "first_name": first_name, "username": username}
        self._update_ids = itertools.count(1)
        self._user_msg_ids = itertools.count(1)

    @classmethod
    async def create(cls, settings: Settings, search_sources=None, ai=None, site_checker=None,
                     throttle: bool = False, **user) -> "TelegramSimulator":
        db = Database(settings.db_path)
        await db.connect()
        svc = build_services(settings, db, search_sources=search_sources, ai=ai, site_checker=site_checker)
        session = FakeTelegramSession()
        bot = Bot(FAKE_TOKEN, session=session, default=bot_defaults())
        dp = create_dispatcher(svc, BOT_USER.username, throttle=throttle)
        return cls(dp, bot, session, svc, **user)

    def as_user(self, user_id: int, first_name: str = "Друг", username: str | None = None) -> "TelegramSimulator":
        """Another user talking to the same bot instance (shared DB and dispatcher)."""
        return TelegramSimulator(self.dp, self.bot, self.session, self.svc, user_id, first_name, username)

    async def close(self) -> None:
        await self.svc.close()
        await self.svc.db.close()

    # --- actions ---------------------------------------------------------------

    async def send(self, text: str, reply_to: BotMessage | None = None) -> BotMessage:
        payload: dict[str, Any] = {
            "message_id": next(self._user_msg_ids),
            "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": {"id": self.user["id"], "type": "private"},
            "from": self.user,
            "text": text,
        }
        if text.startswith("/"):
            payload["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        if reply_to:
            payload["reply_to_message"] = {
                "message_id": reply_to.message_id, "date": payload["date"], "text": reply_to.text,
                "chat": {"id": self.user["id"], "type": "private"},
                "from": {"id": self.bot.id, "is_bot": True, "first_name": "Leadly"}}
        await self._feed({"update_id": next(self._update_ids), "message": payload})
        return self.last

    async def press(self, label: str) -> BotMessage:
        """Press the first button whose text contains `label` on the newest message that has it."""
        msg, btn = self.find_button(label)
        if btn.callback_data is None:
            raise AssertionError(f"Button '{btn.text}' is not a callback button")
        await self._feed({
            "update_id": next(self._update_ids),
            "callback_query": {
                "id": str(next(self._update_ids)),
                "from": self.user,
                "chat_instance": "sim",
                "data": btn.callback_data,
                "message": {
                    "message_id": msg.message_id,
                    "date": int(datetime.now(timezone.utc).timestamp()),
                    "chat": {"id": msg.chat_id, "type": "private"},
                    "from": BOT_USER.model_dump(exclude_none=True),
                    "text": msg.text,
                },
            },
        })
        return self.last

    async def pre_checkout(self, payload: str, amount: int, currency: str = "XTR") -> None:
        await self._feed({"update_id": next(self._update_ids), "pre_checkout_query": {
            "id": str(next(self._update_ids)), "from": self.user, "currency": currency,
            "total_amount": amount, "invoice_payload": payload}})

    async def pay(self, payload: str, amount: int, charge_id: str, currency: str = "XTR",
                  recurring: bool = False, first: bool = True, expires: int | None = None) -> BotMessage:
        """Simulate Telegram delivering `successful_payment` after the user paid (or a subscription renewed)."""
        await self._feed({"update_id": next(self._update_ids), "message": {
            "message_id": next(self._user_msg_ids), "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": {"id": self.user["id"], "type": "private"}, "from": self.user,
            "successful_payment": {"currency": currency, "total_amount": amount, "invoice_payload": payload,
                                   "telegram_payment_charge_id": charge_id,
                                   "provider_payment_charge_id": "",
                                   **({"is_recurring": True, "is_first_recurring": first,
                                       "subscription_expiration_date": expires} if recurring else {})}}})
        return self.last

    async def _feed(self, raw: dict) -> None:
        update = Update.model_validate(raw, context={"bot": self.bot})
        await self.dp.feed_update(self.bot, update)

    # --- inspection --------------------------------------------------------------

    def _mine(self) -> list[BotMessage]:
        return [self.session.messages[i] for i in self.session.order
                if self.session.messages[i].chat_id == self.user["id"]]

    @property
    def last(self) -> BotMessage:
        mine = self._mine()
        if not mine:
            raise AssertionError("Bot has not replied yet")
        return mine[-1]

    def find_button(self, label: str) -> tuple[BotMessage, InlineKeyboardButton]:
        for msg in reversed(self._mine()):
            for btn in msg.buttons:
                if label in btn.text:
                    return msg, btn
        raise AssertionError(f"No button containing '{label}'. Last screen:\n{render(self.last)}")


def render(msg: BotMessage, numbered: bool = False) -> str:
    import re
    from html import unescape

    text = unescape(re.sub(r"<[^>]+>", "", msg.text))
    lines = [text]
    if msg.document:
        lines.append(f"[📎 файл: {msg.document}]")
    if msg.markup:
        n = 0
        for row in msg.markup.inline_keyboard:
            cells = []
            for btn in row:
                n += 1
                kind = " ↗" if btn.url else " ⧉" if btn.copy_text else ""
                cells.append(f"[{n}] {btn.text}{kind}" if numbered else f"[{btn.text}{kind}]")
            lines.append("  ".join(cells))
    return "\n".join(lines)
