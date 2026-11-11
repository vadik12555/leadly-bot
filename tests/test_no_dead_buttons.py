"""Press every button reachable from /start: none may crash, be stale or lead to "not connected"."""

from datetime import datetime, timezone

import pytest

from leadly.config import BASE_DIR, load_product_config
from leadly.devtools.fake_telegram import BOT_USER, TelegramSimulator
from tests.conftest import StubChecker, osm_aggregator, run_search

LIVE_PLANS = BASE_DIR / "config" / "plans.json"
SKIP = ("Удалить", "удалить", "Отправить всем", "Не показывать")  # destructive: would hide the rest of the tree
MAX_PRESSES = 500


async def _press(tg, msg, btn):
    await tg._feed({
        "update_id": next(tg._update_ids),
        "callback_query": {
            "id": str(next(tg._update_ids)), "from": tg.user, "chat_instance": "sim", "data": btn.callback_data,
            "message": {"message_id": msg.message_id, "date": int(datetime.now(timezone.utc).timestamp()),
                        "chat": {"id": msg.chat_id, "type": "private"},
                        "from": BOT_USER.model_dump(exclude_none=True), "text": msg.text}}})


def _snapshot(tg):
    return {m.message_id: (m.text, str(m.markup)) for m in tg._mine()}


async def crawl(tg, start=None) -> tuple[list[str], set[str]]:
    """Depth-first walk from a screen (default /start). Returns (bad buttons, labels pressed)."""
    broken, seen, labels = [], set(), set()

    async def walk(msg, path):
        for btn in list(msg.buttons):
            if not btn.callback_data or btn.callback_data in seen or len(seen) >= MAX_PRESSES:
                continue
            if any(word in btn.text for word in SKIP):
                continue
            seen.add(btn.callback_data)
            labels.add(btn.text)
            before, alerts = _snapshot(tg), len(tg.session.alerts)
            await _press(tg, msg, btn)
            new_alerts = tg.session.alerts[alerts:]
            label = " > ".join(path + [btn.text])
            if any("устарела" in a or "пошло не так" in a for a in new_alerts):
                broken.append(f"{label}: {new_alerts}")
            changed = [m for m in tg._mine() if _snapshot(tg).get(m.message_id) != before.get(m.message_id)]
            if any("не подключён" in m.text for m in changed):
                broken.append(f"{label}: leads to a 'not connected' screen")
            for m in changed:
                await walk(m, path + [btn.text])

    await walk(start or await tg.send("/start"), [])
    return broken, labels


@pytest.fixture
async def live(settings, fake_api):
    s = settings.with_product(load_product_config(LIVE_PLANS))
    sim = await TelegramSimulator.create(s, search_sources=osm_aggregator(fake_api), site_checker=StubChecker())
    yield sim
    await sim.close()


async def test_no_dead_buttons_on_free_searches(live):
    broken, labels = await crawl(live, await run_search(live))
    broken2, labels2 = await crawl(live)
    broken, labels = broken + broken2, labels | labels2
    print(f"\nfree searches: {len(labels)} buttons\nBROKEN: {broken}")
    assert not broken and "⭐ Купить доступ" in labels and "🎁 3 бесплатных поиска — попробовать" in labels
    assert "🔔 Автопоиск" not in labels and not any("Следить" in t for t in labels)  # not in the trial


async def test_no_dead_buttons_after_the_trial(live):
    for _ in range(3):
        await run_search(live)
    start = await live.send("/start")
    assert "⭐ Купить доступ" in [b.text for b in start.buttons]
    assert start.buttons[0].text == "⭐ Неделя PRO за 79 звёзд"
    broken, labels = await crawl(live, start)
    print(f"\ntrial over: {len(labels)} buttons\nBROKEN: {broken}")
    assert not broken and any("⭐/мес" in t for t in labels)  # payment buttons were reached


async def test_no_dead_buttons_with_subscription(live):
    await live.send("/start")
    await live.svc.billing.grant(live.user["id"], "pro", 30)
    # from real results first (cards, proposals, saving), then the menu (saved leads, profile, tariffs)
    broken, labels = await crawl(live, await run_search(live))
    broken2, labels2 = await crawl(live)
    broken, labels = broken + broken2, labels | labels2
    print(f"\npro: {len(labels)} buttons\nBROKEN: {broken}\nPRESSED: {sorted(labels)}")
    assert not broken
    # the walk really got through the search into a company card
    assert any("Проверить сайт" in t for t in labels) and any("Создать предложение" in t for t in labels)
    assert not any("ИИ" in t for t in labels)
