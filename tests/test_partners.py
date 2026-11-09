"""Partner program: 30% of every payment made by users who came via a blogger's link."""

import re
import time

ADMIN, BLOGGER, BUYER, OTHER = 999, 555001, 555002, 555003


async def setup_partner(tg):
    blogger = tg.as_user(BLOGGER, "Блогер", "blogger_kzn")
    await blogger.send("/start")  # the blogger opened the bot once
    admin = tg.as_user(ADMIN, "Админ", "owner")
    m = await admin.send("/partner_add @blogger_kzn")
    assert "30% с оплат" in m.text
    link = re.search(r"https://t\.me/\S+\?start=(p_\w+)", m.text)
    assert link, m.text
    return admin, blogger, link.group(1)


async def buy(user, charge: str, recurring_first=True):
    await user.send("/plans")
    await user.press("PRO · 199 ⭐/мес · автопродление")
    payload = user.session.invoice_links[-1].payload
    await user.pre_checkout(payload, 199)
    await user.pay(payload, 199, charge, recurring=True, first=recurring_first,
                   expires=int(time.time()) + 30 * 86400)
    return payload


def texts_to(tg, chat_id):
    return [m.text for m in tg.session.messages.values() if m.chat_id == chat_id]


async def test_blogger_earns_30_percent_and_sees_it(tg):
    admin, blogger, start = await setup_partner(tg)
    buyer = tg.as_user(BUYER, "Покупатель", "buyer")
    await buyer.send(f"/start {start}")
    payload = await buy(buyer, "charge-1")
    assert any("+59 ⭐" in t and "от 199 ⭐" in t for t in texts_to(tg, BLOGGER))  # 30% of 199, rounded down
    assert any("Партнёр: @blogger_kzn" in t and "= 59 ⭐" in t for t in texts_to(tg, ADMIN))
    # the monthly renewal counts too
    await buyer.pay(payload, 199, "renew-1", recurring=True, first=False, expires=int(time.time()) + 60 * 86400)
    m = await blogger.send("/partner")
    assert "Купили: 1 человек" in m.text and "Твой доход: 118 ⭐" in m.text and "К выплате: 118 ⭐" in m.text
    assert start in m.text


async def test_payout_is_recorded_and_both_sides_see_it(tg):
    admin, blogger, start = await setup_partner(tg)
    buyer = tg.as_user(BUYER, "Покупатель", "buyer")
    await buyer.send(f"/start {start}")
    await buy(buyer, "charge-1")
    code = start[2:]
    m = await admin.send(f"/partner_paid {code} 100")
    assert "больше, чем ты должен (59 ⭐)" in m.text
    m = await admin.send(f"/partner_paid {code} 59")
    assert "Осталось к выплате: 0 ⭐" in m.text
    assert any("выплачено 59 ⭐" in t for t in texts_to(tg, BLOGGER))
    m = await admin.send("/partners")
    assert "к выплате: 0 ⭐" in m.text and "выплачено: 59 ⭐" in m.text


async def test_cannot_be_gamed(tg):
    admin, blogger, start = await setup_partner(tg)
    # an existing user can't be re-attached by clicking a link later
    old = tg.as_user(OTHER, "Старый", "old")
    await old.send("/start")
    await old.send(f"/start {start}")
    # the blogger can't earn on his own purchases
    await blogger.send(f"/start {start}")
    await buy(blogger, "own-charge")
    await buy(old, "old-charge")
    m = await admin.send("/partners")
    assert "Пришло: 0 человек" in m.text and "Заработано: 0 ⭐" in m.text


async def test_refund_takes_the_share_back(tg):
    admin, blogger, start = await setup_partner(tg)
    buyer = tg.as_user(BUYER, "Покупатель", "buyer")
    await buyer.send(f"/start {start}")
    await buy(buyer, "charge-r")
    await admin.send("/refund charge-r")
    m = await blogger.send("/partner")
    assert "Твой доход: 0 ⭐" in m.text


async def test_switched_off_link_stops_new_users_only(tg):
    admin, blogger, start = await setup_partner(tg)
    first = tg.as_user(BUYER, "Первый", "first")
    await first.send(f"/start {start}")
    await admin.send(f"/partner_off {start[2:]}")
    late = tg.as_user(OTHER, "Поздний", "late")
    await late.send(f"/start {start}")
    await buy(first, "charge-a")
    await buy(late, "charge-b")
    m = await blogger.send("/partner")
    assert "Пришло: 1 человек" in m.text and "Твой доход: 59 ⭐" in m.text


async def test_partner_without_account_and_custom_percent(tg):
    admin = tg.as_user(ADMIN, "Админ", "owner")
    m = await admin.send("/partner_add Канал про фриланс 40%")
    assert "Канал про фриланс" in m.text and "40% с оплат" in m.text
    start = re.search(r"start=(p_\w+)", m.text).group(1)
    buyer = tg.as_user(BUYER, "Покупатель", "buyer")
    await buyer.send(f"/start {start}")
    await buy(buyer, "charge-c")
    m = await admin.send("/partners")
    assert "Заработано: 79 ⭐" in m.text  # 40% of 199


async def test_only_admin_manages_partners_and_strangers_get_an_offer(tg):
    stranger = tg.as_user(OTHER, "Чужой", "x")
    m = await stranger.send("/partner_add @x")
    assert "Партнёр" not in m.text
    m = await stranger.send("/partner")
    assert "30%" in m.text and "@support_test" in m.text


async def test_unknown_blogger_gets_clear_instructions(tg):
    admin = tg.as_user(ADMIN, "Админ", "owner")
    m = await admin.send("/partner_add @nobody_here")
    assert "ещё не запускал бота" in m.text
