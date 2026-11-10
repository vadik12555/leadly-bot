"""Stars auto-renew subscriptions, trial, promo codes."""

import time


async def test_subscription_with_auto_renew_and_cancel(tg):
    await tg.send("/plans")
    m = await tg.press("PRO · 199 ⭐/мес · автопродление")
    link_req = tg.session.invoice_links[-1]
    assert link_req.subscription_period == 2592000 and link_req.currency == "XTR"
    assert any(b.url and "fake-invoice" in b.url for b in m.buttons)
    payload = link_req.payload
    await tg.pre_checkout(payload, 199)
    assert tg.session.pre_checkout_answers[-1].ok is True
    exp1 = int(time.time()) + 30 * 86400
    m = await tg.pay(payload, 199, "first-charge", recurring=True, first=True, expires=exp1)
    assert "Оплата получена" in m.text
    m = await tg.send("/plans")
    assert "Автопродление включено" in m.text and "Отключить автопродление" in str(m.markup)
    # Telegram renews automatically next month: same payload, new charge id
    exp2 = exp1 + 30 * 86400
    m = await tg.pay(payload, 199, "renew-1", recurring=True, first=False, expires=exp2)
    assert "Подписка продлена" in m.text
    m = await tg.pay(payload, 199, "renew-1", recurring=True, first=False, expires=exp2)  # re-delivered update
    assert "уже зачислен" in m.text
    subs = await tg.svc.db.fetchall("SELECT ends_at, auto_renew FROM subscriptions ORDER BY id")
    assert len(subs) == 2 and subs[1]["auto_renew"] == 1 and subs[0]["auto_renew"] == 0
    await tg.send("/plans")
    m = await tg.press("Отключить автопродление")
    assert tg.session.star_cancellations[-1].telegram_payment_charge_id == "first-charge"
    assert tg.session.star_cancellations[-1].is_canceled is True
    assert "Автопродление включено" not in m.text and "Твой тариф: <b>PRO</b>" in m.text


async def test_trial_once(tg):
    m = await tg.send("/plans")
    assert "бесплатно на 3 дня" in str(m.markup)
    m = await tg.press("бесплатно на 3 дня")
    assert "PRO активирован" in m.text
    m = await tg.send("/start")
    assert "Твой тариф: PRO" in m.text
    m = await tg.send("/plans")
    assert "бесплатно на 3 дня" not in str(m.markup)
    # even after the trial ends it cannot be taken again
    await tg.svc.db.execute("UPDATE subscriptions SET ends_at = '2000-01-01T00:00:00+00:00'")
    m = await tg.send("/plans")
    assert "Бесплатных поисков осталось: 3" in m.text and "бесплатно на 3 дня" not in str(m.markup)


async def test_promo_codes(tg):
    admin = tg.as_user(999)
    m = await admin.send("/promo START30 pro 30 1")
    assert "создан" in m.text
    await tg.send("/plans")
    await tg.press("Промокод или ключ")
    m = await tg.send("nope")
    assert "либо он отключён" in m.text
    m = await tg.send("start30")
    assert "Промокод активирован" in m.text and "PRO" in m.text
    other = tg.as_user(4242)
    await other.send("/plans")
    await other.press("Промокод или ключ")
    m = await other.send("START30")
    assert "уже использован" in m.text
    m = await tg.send("/start")
    assert "Твой тариф: PRO" in m.text


async def test_activation_keys(tg):
    import re
    admin = tg.as_user(999)
    m = await admin.send("/keys pro 30 3")
    keys = re.findall(r"LDY-[A-Z0-9]{4}-[A-Z0-9]{4}", m.text)
    assert len(set(keys)) == 3
    await tg.send("/plans")
    await tg.press("Промокод или ключ")
    m = await tg.send(keys[0].lower())
    assert "Промокод активирован" in m.text
    other = tg.as_user(5151)
    await other.send("/plans")
    await other.press("Промокод или ключ")
    m = await other.send(keys[0])
    assert "уже использован" in m.text  # one key = one activation
    m = await admin.send("/keys")
    assert "Выпущено ключей: 3, активировано: 1" in m.text


async def test_yearly_purchase(tg):
    await tg.send("/plans")
    m = await tg.press("На год · 1790 ⭐")
    inv = tg.session.invoices[-1]
    assert inv.prices[0].amount == 1790 and inv.payload.startswith("year:")
    await tg.pre_checkout(inv.payload, 1790)
    assert tg.session.pre_checkout_answers[-1].ok is True
    await tg.pay(inv.payload, 1790, "year-charge")
    sub = await tg.svc.db.fetchone("SELECT starts_at, ends_at FROM subscriptions")
    from datetime import datetime
    days = (datetime.fromisoformat(sub["ends_at"]) - datetime.fromisoformat(sub["starts_at"])).days
    assert days == 365


async def test_support_roundtrip_and_admin_reply(tg):
    admin_id = 999
    admin = tg.as_user(admin_id, "Owner")
    await tg.send("/start")
    await tg.send("/support")
    m = await tg.send("Не нахожу стоматологии в Казани")
    assert "отправлено" in m.text
    note = [x for x in tg.session.messages.values() if x.chat_id == admin_id and "#u" in (x.text or "")]
    assert note and "стоматологии" in note[0].text
    m = await admin.send("Проверь фильтр «Все категории»", reply_to=note[0])
    assert "Ответ отправлен" in m.text
    answers = [x for x in tg.session.messages.values()
               if x.chat_id == tg.user["id"] and "Ответ поддержки" in (x.text or "")]
    assert answers and "Все категории" in answers[0].text
