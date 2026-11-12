"""Bot assembly, logging, background jobs and the long-polling entry point."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from html import escape
from pathlib import Path

import aiosqlite
import logging.handlers
import re

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramUnauthorizedError
from aiogram.types import BotCommand, FSInputFile
from aiogram.utils.backoff import BackoffConfig

from leadly.bot.handlers import admin, billing, cabinet, company, fallback, search, start, support
from leadly.bot.storage import SQLiteStorage
from leadly.bot.ui import CallbackAnswerMiddleware, ThrottleMiddleware, UserMiddleware
from leadly.bot.views import cabinet as cv
from leadly.config import Settings
from leadly.db import Database
from leadly.services import Services, build_services
from leadly.services.collector import collect_companies
from leadly.utils import MSK, today_msk

log = logging.getLogger("leadly")

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="find", description="🔎 Найти клиентов"),
    BotCommand(command="leads", description="📋 Мои лиды"),
    BotCommand(command="profile", description="👤 Мой профиль"),
    BotCommand(command="templates", description="📄 Мои шаблоны"),
    BotCommand(command="stats", description="📊 Моя статистика"),
    BotCommand(command="plans", description="⭐ Тарифы"),
    BotCommand(command="help", description="❓ Помощь"),
    BotCommand(command="cancel", description="Отменить текущее действие"),
    BotCommand(command="support", description="💬 Написать в поддержку"),
    BotCommand(command="paysupport", description="Поддержка по оплатам"),
    BotCommand(command="terms", description="Условия использования"),
    BotCommand(command="privacy", description="Политика конфиденциальности"),
]
REMINDER_INTERVAL = 30 * 60


class RedactSecrets(logging.Filter):
    """Masks the bot token and API keys if they ever end up in a log line."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.patterns = [re.escape(s) for s in secrets if s and len(s) > 8]
        self.patterns.append(r"\d{6,}:[A-Za-z0-9_-]{30,}")  # any Telegram bot token

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for p in self.patterns:
            msg = re.sub(p, "***", msg)
        record.msg, record.args = msg, None
        return True


class AdminAlertHandler(logging.Handler):
    """Forwards ERROR log records to admins in Telegram (batched, at most once per `interval` seconds)."""

    def __init__(self, interval: float = 120.0):
        super().__init__(level=logging.ERROR)
        self.interval = interval
        self.queue: asyncio.Queue | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop, self.queue = loop, asyncio.Queue(maxsize=200)

    def emit(self, record: logging.LogRecord) -> None:
        if not self.loop or record.name.startswith("leadly.alerts"):
            return
        line = f"{record.name}: {record.getMessage()}"[:400]
        try:
            self.loop.call_soon_threadsafe(self._put, line)
        except RuntimeError:  # loop closed during shutdown
            pass

    def _put(self, line: str) -> None:
        if self.queue and not self.queue.full():
            self.queue.put_nowait(line)

    async def run(self, bot: Bot, admin_ids: frozenset[int]) -> None:
        alert_log = logging.getLogger("leadly.alerts")
        while True:
            first = await self.queue.get()
            await asyncio.sleep(5)  # collect errors that come in a burst
            lines = [first]
            while not self.queue.empty():
                lines.append(self.queue.get_nowait())
            text = (f"🧯 <b>Leadly: ошибки ({len(lines)})</b>\n\n"
                    + "\n".join(f"<code>{escape(x)}</code>" for x in lines[-5:])
                    + "\n\nПодробнее: /admin → «Ошибки».")
            for admin in admin_ids:
                try:
                    await bot.send_message(admin, text)
                except Exception as e:
                    alert_log.warning("could not alert admin %s: %s", admin, type(e).__name__)
            await asyncio.sleep(self.interval)


ALERTS = AdminAlertHandler()


def setup_logging(settings: Settings) -> None:
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    redact = RedactSecrets([settings.bot_token, settings.anthropic_api_key, settings.openai_compat_api_key,
                            settings.twogis_api_key])
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.log_file:
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(settings.log_file, maxBytes=5_000_000,
                                                             backupCount=3, encoding="utf-8"))
    handlers.append(ALERTS)
    for hnd in handlers:
        hnd.setFormatter(fmt)
        hnd.addFilter(redact)
        root.addHandler(hnd)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def bot_defaults() -> DefaultBotProperties:
    return DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True)


def create_dispatcher(svc: Services, bot_username: str, throttle: bool = True) -> Dispatcher:
    dp = Dispatcher(storage=SQLiteStorage(svc.db), svc=svc, bot_username=bot_username)
    if throttle:
        dp.update.outer_middleware(ThrottleMiddleware(exempt=svc.settings.admin_ids))
    dp.update.outer_middleware(UserMiddleware(svc))
    dp.callback_query.middleware(CallbackAnswerMiddleware())
    dp.include_routers(
        start.create_router(),
        admin.create_router(),
        billing.create_router(),
        search.create_router(),
        company.create_router(),
        cabinet.create_router(),
        support.create_router(),
        fallback.create_router(),  # must stay last
    )
    dp.errors.register(fallback.on_error)
    return dp


async def send_reminders(bot: Bot, svc: Services) -> int:
    """Notify about subscriptions ending soon or already ended. Returns number of messages sent."""
    sent = 0
    expiring, expired = await svc.billing.due_reminders()
    for which, rows in (("expiring", expiring), ("expired", expired)):
        for sub in rows:
            plan = svc.settings.product.plan(sub["plan"])
            screen = (cv.subscription_expiring(plan.title, sub["ends_at"]) if which == "expiring"
                      else cv.subscription_expired(plan.title))
            try:
                await bot.send_message(sub["user_id"], screen[0], reply_markup=screen[1])
                sent += 1
            except TelegramForbiddenError:
                pass  # user blocked the bot — nothing to do
            except TelegramNetworkError:
                return sent  # try again on the next run, don't mark as notified
            await svc.billing.mark_notified(sub["id"], which)
    return sent


async def send_followups(bot: Bot, svc: Services) -> int:
    """CRM: remind about companies contacted N days ago without an answer."""
    sent = 0
    for lead in await svc.watches.due_followups(svc.settings.product.followup_days):
        name = json.loads(lead["company"]).get("name", "компания")
        screen = cv.followup_reminder(lead["id"], name, lead["contacted_at"])
        try:
            await bot.send_message(lead["user_id"], screen[0], reply_markup=screen[1])
            sent += 1
        except TelegramForbiddenError:
            pass
        except TelegramNetworkError:
            return sent
        await svc.watches.mark_followup(lead["id"])
    return sent


async def send_final_touches(bot: Bot, svc: Services) -> int:
    sent = 0
    for lead in await svc.watches.due_final_touches(svc.settings.product.followup_days + 4):
        name = json.loads(lead["company"]).get("name", "компания")
        screen = cv.final_touch_reminder(lead["id"], name)
        try:
            await bot.send_message(lead["user_id"], screen[0], reply_markup=screen[1])
            sent += 1
        except TelegramForbiddenError:
            pass
        except TelegramNetworkError:
            return sent
        await svc.watches.mark_final_touch(lead["id"])
    return sent


async def send_digests(bot: Bot, svc: Services) -> int:
    sent = 0
    for d in await svc.watches.due_digests():
        screen = cv.weekly_digest(d)
        try:
            await bot.send_message(d["user_id"], screen[0], reply_markup=screen[1])
            sent += 1
        except TelegramForbiddenError:
            pass
        except TelegramNetworkError:
            return sent
        await svc.watches.mark_digest(d["user_id"])
    return sent


async def run_watches(bot: Bot, svc: Services, pause: float = 3.0) -> int:
    """Auto-search: run due saved searches and notify about new companies. Returns notifications sent."""
    sent = 0
    for watch in await svc.watches.due():
        result = await svc.watches.run(watch)
        if result.error:
            log.warning("auto-search %s failed: %s", watch["id"], result.error)
        elif result.search_id:
            screen = cv.watch_found(watch["title"], result.new, result.search_id)
            try:
                await bot.send_message(watch["user_id"], screen[0], reply_markup=screen[1])
                sent += 1
            except TelegramForbiddenError:
                await svc.watches.delete(watch["user_id"], watch["id"])  # user blocked the bot
            except TelegramNetworkError:
                return sent
        await asyncio.sleep(pause)  # be gentle with free data sources
    return sent


async def backup_database(svc: Services, keep: int = 7) -> Path | None:
    """One SQLite backup per day in data/backups, keeping the last `keep`. Returns the new file or None."""
    if svc.db.path == ":memory:":
        return None
    folder = Path(svc.db.path).parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"leadly-{today_msk()}.db"
    if dest.exists():
        return None
    async with aiosqlite.connect(dest) as target:
        await svc.db.conn.backup(target)
    for old in sorted(folder.glob("leadly-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    log.info("database backup saved: %s", dest.name)
    return dest


async def send_backup(bot: Bot, svc: Services, path: Path) -> None:
    for admin in svc.settings.admin_ids:
        try:
            await bot.send_document(admin, FSInputFile(path), caption=f"💾 Еженедельный бэкап базы Leadly · {path.stem}")
        except Exception as e:
            log.warning("could not send backup to admin %s: %s", admin, type(e).__name__)


async def daily_backup(bot: Bot, svc: Services) -> None:
    path = await backup_database(svc)
    if path and datetime.now(MSK).weekday() == 0:  # Mondays: also send a copy to admins
        await send_backup(bot, svc, path)


async def background_jobs(bot: Bot, svc: Services) -> None:
    while True:
        for name, job in (("subscription reminders", send_reminders), ("follow-ups", send_followups),
                          ("final touches", send_final_touches), ("auto-search", run_watches),
                          ("weekly digest", send_digests), ("backup", daily_backup)):
            try:
                await job(bot, svc)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s failed", name)
        await asyncio.sleep(REMINDER_INTERVAL)


def system_proxy() -> str | None:
    """Proxy from HTTPS_PROXY/ALL_PROXY or, on Windows, from system settings (where most VPN apps put it)."""
    import urllib.request
    proxies = urllib.request.getproxies()
    proxy = proxies.get("https") or proxies.get("all") or proxies.get("http")
    if not proxy:
        return None
    if "://" not in proxy:
        proxy = "http://" + proxy
    return proxy.replace("socks://", "socks5://", 1)


async def run_polling(settings: Settings) -> int:
    if not settings.bot_token:
        log.error("BOT_TOKEN не задан. Скопируй .env.example в .env и впиши токен от @BotFather.")
        return 2

    db = Database(settings.db_path)
    await db.connect()
    from leadly.services.seed import load_seed
    await load_seed(db)  # the company base from the repository: searches don't depend on Overpass
    svc = build_services(settings, db)
    proxy = settings.telegram_proxy or system_proxy()
    if proxy:
        log.info("подключение к Telegram через прокси %s", proxy.split("@")[-1])
    session = AiohttpSession(proxy=proxy, timeout=60)
    bot = Bot(settings.bot_token, session=session, default=bot_defaults())
    reminders: asyncio.Task | None = None
    collector: asyncio.Task | None = None
    try:
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            log.error("Telegram отклонил BOT_TOKEN. Проверь токен в @BotFather (/mybots → API Token).")
            return 2
        except TelegramNetworkError as e:
            # fail fast with a clear reason instead of looping on "fetch failed"
            log.error("Нет связи с api.telegram.org (%s). Проверь интернет/DNS сервера; если Telegram "
                      "заблокирован в сети — включи VPN в режиме TUN, задай TELEGRAM_PROXY или запусти бота "
                      "на другом сервере. Подробности: %s", type(e).__name__, e.message[:300])
            return 3
        await bot.set_my_commands(COMMANDS)
        dp = create_dispatcher(svc, me.username or settings.bot_username)
        for p in svc.search_sources.providers:
            log.info("источник %s: %s", p.title, p.configured()[1])
        log.info("ИИ: %s · оплата Stars: %s · админы: %s", svc.ai.configured()[1],
                 "вкл" if settings.payments_enabled else "выкл", len(settings.admin_ids) or "не заданы")
        if svc.search_sources.is_demo:
            log.warning("ВКЛЮЧЁН ДЕМО-ИСТОЧНИК: пользователи увидят вымышленные компании")
        reminders = asyncio.create_task(background_jobs(bot, svc))
        collector = asyncio.create_task(collect_companies(svc.search_sources))
        alerts = None
        if settings.admin_ids:
            ALERTS.attach(asyncio.get_running_loop())
            alerts = asyncio.create_task(ALERTS.run(bot, settings.admin_ids))
        log.info("Leadly запущен как @%s (long polling)", me.username)
        await bot.delete_webhook(drop_pending_updates=False)
        # network hiccups during polling are retried with exponential backoff (1 s → 60 s), not in a tight loop
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(),
                               backoff_config=BackoffConfig(min_delay=1, max_delay=60, factor=1.6, jitter=0.1),
                               handle_signals=True)
        return 0
    finally:
        if reminders:
            reminders.cancel()
        if collector:
            collector.cancel()
        if "alerts" in locals() and alerts:
            alerts.cancel()
        await svc.close()
        await bot.session.close()
        await db.close()
        log.info("Leadly остановлен")
