"""Free limits, idempotency, Stars payments, subscription expiry, referrals, admin rights."""

from leadly.bot.app import send_reminders
from leadly.utils import add_days_iso, now_iso
from tests.conftest import press_find, run_search


async def test_free_search_limit_persists_and_start_does_not_reset(tg):
    m = await tg.send("/start")
    assert "🎁 <b>У тебя 3 бесплатных поиска (осталось 3).</b>" in m.text
    for left in (2, 1, 0):
        m = await run_search(tg)
        assert "Найдено" in m.text
        m = await tg.send("/start")
        assert (f"(осталось {left})" in m.text) if left else "Бесплатные поиски закончились" in m.text
    m = await press_find(tg)  # the trial is over: straight to the tariffs with payment buttons
    assert "Бесплатные поиски закончились" in m.text and "Тарифы Leadly" in m.text
    assert "PRO · 199 ⭐/мес · автопродление" in str(m.markup)
    m = await tg.send("/start")  # /start must not refill the trial
    assert "Бесплатные поиски закончились" in m.text
    # free functions remain available
    m = await tg.send("/leads")
    assert "Мои лиды" in m.text
    m = await tg.send("/stats")
    assert "Поисков: 3" in m.text


async def test_repeated_find_tap_is_not_charged_twice(tg):
    await run_search(tg)
    # pressing "Повторный поиск" with unchanged parameters = same token -> same results, no charge
    await tg.press("Новый поиск")
    m = await tg.send("/start")
    assert "(осталось 2)" in m.text
    rows = await tg.svc.db.fetchone("SELECT COUNT(*) AS n FROM usage_events WHERE kind = 'searches'")
    assert rows["n"] == 1
    # same filters screen, second tap on the same token
    await press_find(tg)
    await tg.press("Разработка сайта")
    await tg.press("Салоны красоты")
    await tg.press("Все категории")
    await tg.press("Казань")
    await tg.press("🔍 Найти")
    data = await tg.svc.db.fetchall("SELECT token FROM searches")
    assert len({r["token"] for r in data}) == 2


async def test_card_open_charged_once_per_company(tg):
    await run_search(tg)
    for _ in range(3):
        await tg.press("Лотос")
        await tg.press("К результатам")
    row = await tg.svc.db.fetchone("SELECT COUNT(*) AS n FROM usage_events WHERE kind = 'card_opens'")
    assert row["n"] == 1


async def test_proposal_limit_and_template_without_ai(tg):
    await run_search(tg)
    await tg.press("Лотос")
    m = await tg.press("Создать предложение")
    assert "<i>шаблон</i>" in m.text and "<blockquote>" in m.text and "ИИ" not in m.text
    m = await tg.press("По факту")
    assert "Подтверждённых проблем пока не найдено" in m.text  # no fact -> no fake "fact-based" message
    for _ in range(4):
        m = await tg.press("Другие варианты")
    assert "Предложение" in m.text
    m = await tg.press("Другие варианты")
    assert "лимит генераций предложений закончился" in m.text.lower()


async def _buy(tg, plan_button="PRO на 30 дней разово"):
    await tg.send("/plans")
    await tg.press(plan_button)
    return tg.session.invoices[-1]


async def test_stars_payment_activates_plan_once(tg):
    invoice = await _buy(tg)
    assert invoice.currency == "XTR" and invoice.prices[0].amount == 199
    await tg.pre_checkout(invoice.payload, 199)
    assert tg.session.pre_checkout_answers[-1].ok is True
    m = await tg.pay(invoice.payload, 199, "charge-1")
    assert "Оплата получена" in m.text and "PRO активен" in m.text
    notices = [x for x in tg.session.messages.values() if x.chat_id == 999 and "💰" in (x.text or "")]
    assert len(notices) == 1 and "199 ⭐" in notices[0].text
    m = await tg.send("/start")
    assert "Твой тариф: PRO" in m.text and "сегодня" in m.text
    # Telegram re-delivers the same update: no second subscription
    m = await tg.pay(invoice.payload, 199, "charge-1")
    assert "уже зачислен" in m.text
    subs = await tg.svc.db.fetchone("SELECT COUNT(*) AS n FROM subscriptions")
    assert subs["n"] == 1


async def test_pre_checkout_rejects_wrong_amount_and_foreign_or_used_invoice(tg):
    invoice = await _buy(tg)
    await tg.pre_checkout(invoice.payload, 1)
    assert tg.session.pre_checkout_answers[-1].ok is False
    other = tg.as_user(5555)
    await other.send("/start")
    await other.pre_checkout(invoice.payload, 199)
    assert tg.session.pre_checkout_answers[-1].ok is False
    await tg.pay(invoice.payload, 199, "charge-x")
    await tg.pre_checkout(invoice.payload, 199)  # already paid
    assert tg.session.pre_checkout_answers[-1].ok is False


async def test_no_activation_without_confirmed_payment(tg):
    await _buy(tg)  # invoice created, never paid
    m = await tg.send("/start")
    assert "У тебя 3 бесплатных поиска" in m.text and "Твой тариф" not in m.text


async def test_unknown_payload_is_recorded_for_manual_review(tg):
    m = await tg.pay("sub:pro:1:forged", 199, "charge-orphan")
    assert "вручную" in m.text
    m = await tg.send("/start")
    assert "У тебя 3 бесплатных поиска" in m.text
    row = await tg.svc.db.fetchone("SELECT * FROM payments WHERE telegram_charge_id = 'charge-orphan'")
    assert row["error"] == "unknown payload"


async def test_renewal_extends_and_expiry_reminders(tg):
    invoice = await _buy(tg)
    await tg.pay(invoice.payload, 199, "c1")
    invoice2 = await _buy(tg)
    await tg.pay(invoice2.payload, 199, "c2")
    subs = await tg.svc.db.fetchall("SELECT starts_at, ends_at FROM subscriptions ORDER BY id")
    assert subs[1]["starts_at"] == subs[0]["ends_at"]  # renewal starts when the current period ends
    # move everything so that the last period ends in 2 days
    await tg.svc.db.execute("UPDATE subscriptions SET starts_at = ?, ends_at = ?", add_days_iso(-28),
                            add_days_iso(2))
    await tg.svc.db.execute("DELETE FROM subscriptions WHERE id = 1")
    assert await send_reminders(tg.bot, tg.svc) == 1
    assert "закончится" in tg.last.text
    assert await send_reminders(tg.bot, tg.svc) == 0  # only once
    await tg.svc.db.execute("UPDATE subscriptions SET ends_at = ?", add_days_iso(-1))
    assert await send_reminders(tg.bot, tg.svc) == 1
    assert "закончился" in tg.last.text and "FREE" not in tg.last.text
    m = await tg.send("/start")
    assert "У тебя 3 бесплатных поиска" in m.text  # back to the unused free searches


async def test_referral_once_no_self_invite(tg):
    await tg.send("/start")
    m = await tg.press("Пригласить друга")
    assert f"https://t.me/Lead1HuntBot?start=ref_{tg.user['id']}" in m.text
    friend = tg.as_user(200300)
    await friend.send(f"/start ref_{tg.user['id']}")
    assert "Тебя пригласил друг" in friend.last.text
    assert "пришёл новый пользователь" in tg.last.text
    await friend.send(f"/start ref_{tg.user['id']}")  # repeated start
    await tg.send(f"/start ref_{tg.user['id']}")  # self-invite
    me = await tg.svc.db.get_user(tg.user["id"])
    fr = await tg.svc.db.get_user(200300)
    assert me.bonus_searches == 2 and fr.bonus_searches == 1 and fr.referred_by == me.id and me.referred_by is None
    m = await tg.press("Пригласить друга")
    assert "Приглашено: 1" in m.text


async def test_bonus_search_used_after_trial(tg):
    await tg.send("/start")
    await tg.svc.db.execute("UPDATE users SET bonus_searches = 1 WHERE id = ?", tg.user["id"])
    m = await tg.send("/start")
    assert "У тебя 4 бесплатных поиска (осталось 4)" in m.text  # 3 free + 1 bonus
    for _ in range(3):
        await run_search(tg)
    m = await run_search(tg)
    assert "Найдено" in m.text  # bonus search spent
    m = await tg.send("/start")
    assert "Бесплатные поиски закончились" in m.text
    m = await press_find(tg)
    assert "Бесплатные поиски закончились" in m.text


async def test_admin_only_for_admin_ids(tg):
    m = await tg.send("/admin")
    assert "Админка" not in m.text  # regular user gets the fallback, not the panel
    m = await tg.send("/grant 100500 pro 30")
    assert "Выдано" not in m.text
    admin = tg.as_user(999, "Admin")
    m = await admin.send("/admin")
    assert "Админка Leadly" in m.text
    m = await admin.press("Источники")
    assert "OpenStreetMap" in m.text and "ИИ" in m.text
    await tg.send("/start")
    m = await admin.send("/grant 100500 pro 7")
    assert "Выдано: pro" in m.text
    m = await tg.send("/start")
    assert "Твой тариф: PRO" in m.text
    m = await admin.send("/user 100500")
    assert "manual" in m.text


async def test_admin_refund(tg):
    invoice = await _buy(tg)
    await tg.pay(invoice.payload, 199, "charge-r")
    admin = tg.as_user(999)
    m = await admin.send("/refund charge-r")
    assert "Возврат выполнен" in m.text
    assert tg.session.refunds[-1].telegram_payment_charge_id == "charge-r"
    m = await tg.send("/start")
    assert "У тебя 3 бесплатных поиска" in m.text and "Твой тариф" not in m.text


async def test_paysupport_and_terms(tg):
    m = await tg.send("/paysupport")
    assert "@support_test" in m.text
    m = await tg.send("/terms")
    assert "Telegram Stars" in m.text and "спам" in m.text


async def test_flood_protection(settings, fake_api):
    from leadly.devtools.fake_telegram import TelegramSimulator
    from tests.conftest import osm_aggregator
    from tests.conftest import StubChecker
    sim = await TelegramSimulator.create(settings, search_sources=osm_aggregator(fake_api), throttle=True,
                                         site_checker=StubChecker())
    try:
        for _ in range(25):
            await sim.send("/help")
        assert sum("Слишком много сообщений" in m.text for m in sim.session.messages.values()) == 1
        replies = [m for m in sim.session.messages.values() if "Как работает Leadly" in m.text]
        assert len(replies) == 20  # the rest were dropped
    finally:
        await sim.close()


async def test_admin_broadcast_and_funnel(tg):
    import asyncio
    await tg.send("/start")
    friend = tg.as_user(31337)
    await friend.send("/start")
    admin = tg.as_user(999)
    m = await admin.send("/broadcast Привет! Новая функция: автопоиск 🔔")
    assert "получателей: 3" in m.text
    await admin.press("Отправить всем")
    await asyncio.sleep(0.5)
    got = [m for m in tg.session.messages.values() if "Новая функция: автопоиск" in m.text and m.chat_id != 999]
    assert {m.chat_id for m in got} >= {tg.user["id"], 31337}
    assert any("Рассылка завершена" in m.text for m in tg.session.messages.values())
    m = await admin.send("/admin")
    assert "Воронка" in m.text and "Зашли: 3" in m.text


async def test_admin_error_alerts():
    import asyncio
    import logging
    from leadly.bot.app import AdminAlertHandler

    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    handler = AdminAlertHandler(interval=0)
    handler.attach(asyncio.get_running_loop())
    logger = logging.getLogger("leadly.test_alerts")
    logger.addHandler(handler)
    task = asyncio.create_task(handler.run(FakeBot(), frozenset({1, 2})))
    try:
        logger.error("источник упал")
        logger.warning("это не ошибка")
        import leadly.bot.app as app_mod
        orig = asyncio.sleep

        async def fast(_):
            await orig(0)
        app_mod.asyncio.sleep = fast
        for _ in range(20):
            await orig(0.01)
            if len(sent) == 2:
                break
        app_mod.asyncio.sleep = orig
        assert len(sent) == 2 and "источник упал" in sent[0][1] and "не ошибка" not in sent[0][1]
    finally:
        task.cancel()
        logger.removeHandler(handler)


async def test_privacy_and_data_deletion(tg):
    m = await tg.send("/privacy")
    assert "Политика конфиденциальности" in m.text and "/delete_my_data" in m.text
    await run_search(tg)
    await tg.press("Лотос")
    await tg.press("Сохранить")
    await tg.send("/delete_my_data")
    m = await tg.press("Да, удалить всё")
    assert "данные удалены" in m.text
    for table in ("leads", "searches", "search_results", "seen_companies", "proposals"):
        row = await tg.svc.db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")
        assert row["n"] == 0, table
    user = await tg.svc.db.get_user(tg.user["id"])
    assert user.username is None and user.first_name is None


async def test_daily_backup_and_notification_settings(tg, tmp_path):
    from leadly.bot.app import backup_database, send_backup
    path = await backup_database(tg.svc)
    assert path and path.exists() and path.stat().st_size > 0
    assert await backup_database(tg.svc) is None  # once per day
    await send_backup(tg.bot, tg.svc, path)
    assert any(m.document for m in tg.session.messages.values() if m.chat_id == 999)
    # notification toggles
    await tg.send("/profile")
    m = await tg.press("Уведомления")
    assert "Автопоиск — новые компании по отслеживаемым нишам: 🔔 вкл" in m.text
    m = await tg.press("Напоминания: 🔔 вкл")
    assert "Напоминания по лидам — «пора напомнить о себе»: 🔕 выкл" in m.text
    assert (await tg.svc.db.get_user(tg.user["id"])).notify_followups == 0


async def test_niche_stats_and_first_search_tip(tg):
    m = await run_search(tg)
    assert "Как получить первого клиента" in m.text
    m = await run_search(tg)
    assert "Как получить первого клиента" not in m.text  # only once
    await tg.press("Лотос")
    await tg.press("Сохранить")
    m = await tg.send("/stats")
    assert "По нишам" in m.text and "💇 Салоны красоты: 1 сохр." in m.text
    m = await tg.send("/plans")
    assert "Почему это окупается" in m.text
