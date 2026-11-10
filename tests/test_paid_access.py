"""The live tariffs (config/plans.json): 3 free searches in total, then clients only after paying with Stars."""

import pytest

from leadly.config import BASE_DIR, load_product_config
from leadly.devtools.fake_telegram import TelegramSimulator
from tests.conftest import StubChecker, osm_aggregator, run_search

LIVE_PLANS = BASE_DIR / "config" / "plans.json"


@pytest.fixture
async def paid(settings, fake_api):
    live = settings.with_product(load_product_config(LIVE_PLANS))
    sim = await TelegramSimulator.create(live, search_sources=osm_aggregator(fake_api), site_checker=StubChecker())
    yield sim
    await sim.close()


async def use_trial(sim, times=3):
    for _ in range(times):
        m = await run_search(sim)
        assert "Найдено" in m.text


async def test_new_user_gets_three_free_searches(paid):
    m = await paid.send("/start")
    assert "🎁 <b>У тебя 3 бесплатных поиска</b>" in m.text and "от 199 ⭐" in m.text
    assert "0 из 0" not in m.text and "FREE" not in m.text
    labels = [b.text for b in m.buttons]
    assert "🔎 Найти клиентов" in labels and "⭐ Тарифы" in labels
    assert "🔔 Автопоиск" not in labels and "🤖 ИИ-анализ" not in labels  # not part of the trial


async def test_trial_search_gives_real_clients_and_cards_work(paid):
    m = await run_search(paid)
    assert "Найдено 4" in m.text and "Лотос" in str(m.markup)
    assert "Следить за новыми" not in str(m.markup)  # auto-search isn't in the trial: no button that refuses
    m = await paid.press("Лотос")
    labels = [b.text for b in m.buttons]
    assert any("Проверить сайт" in t for t in labels)
    m = await paid.press("Проверить сайт")
    assert "Проверка сайта" in m.text and "лимит" not in m.text.lower()
    await paid.press("К карточке")
    m = await paid.press("Создать предложение")
    assert "<blockquote>" in m.text
    m = await paid.send("/start")
    assert "У тебя 2 бесплатных поиска" in m.text


async def test_failed_and_repeated_searches_do_not_spend_the_trial(paid, fake_api):
    fake_api.overpass_status = 504
    m = await run_search(paid)
    assert "Лимит поиска не списан" in m.text
    fake_api.overpass_status = 200
    m = await paid.press("Повторить")
    assert "Найдено" in m.text
    # the same search (same token) delivered again — a double tap or a re-sent update — costs nothing
    row = await paid.svc.db.fetchone("SELECT token FROM searches WHERE status = 'ok'")
    user = await paid.svc.db.get_user(paid.user["id"])
    quota = await paid.svc.usage.charge(user, "searches", f"search:{row['token']}")
    assert quota.already
    m = await paid.send("/start")
    assert "У тебя 2 бесплатных поиска" in m.text  # one success: one search spent


async def test_after_the_trial_tariffs_and_payment(paid, fake_api):
    await use_trial(paid)
    m = await paid.send("/start")
    assert "Бесплатные поиски закончились" in m.text and "от 199 ⭐" in m.text
    calls = len(fake_api.calls)
    m = await paid.press("Найти клиентов")
    assert "Бесплатные поиски закончились" in m.text and "Тарифы Leadly" in m.text
    assert "PRO · 199 ⭐/мес · автопродление" in str(m.markup)
    m = await paid.send("/find")
    assert "Тарифы Leadly" in m.text
    assert len(fake_api.calls) == calls  # nothing was searched
    m = await paid.send("/leads")
    assert "Мои лиды" in m.text  # what was found earlier stays available


async def test_plans_offer_only_paid_options(paid):
    m = await paid.send("/plans")
    assert "Бесплатных поисков осталось: 3" in m.text
    assert "Пробный доступ" not in m.text and "бесплатно на" not in str(m.markup)
    assert "PRO · 199 ⭐/мес" in str(m.markup) and "PRO+ · 549 ⭐/мес" in str(m.markup)
    assert "ИИ-анализ" not in m.text and "Промокод" not in str(m.markup)


async def test_paying_unlocks_clients(paid):
    await use_trial(paid)
    await paid.send("/plans")
    await paid.press("PRO · 199 ⭐/мес · автопродление")
    payload = paid.session.invoice_links[-1].payload
    await paid.pre_checkout(payload, 199)
    assert paid.session.pre_checkout_answers[-1].ok is True
    m = await paid.pay(payload, 199, "charge-1", recurring=True, first=True)
    assert "Оплата получена" in m.text
    m = await run_search(paid)
    assert "Найдено 4" in m.text and "Лотос" in str(m.markup)
    m = await paid.send("/start")
    assert "<b>Твой тариф: PRO</b>" in m.text and "🔒" not in m.text and "бесплатн" not in m.text
    assert "ИИ-анализ" not in m.text and "ИИ-анализ" not in str(m.markup)
    await paid.press("Найти клиентов")
    await paid.press("Разработка сайта")
    await paid.press("Салоны красоты")
    m = await paid.press("Все категории")
    assert "Несколько городов" not in str(m.markup)  # PRO+ only: PRO users shouldn't get a button that refuses
    await paid.press("Казань")
    m = await paid.press("🔍 Найти")
    m = await paid.press("Лотос")
    assert "ИИ-анализ" not in str(m.markup)
