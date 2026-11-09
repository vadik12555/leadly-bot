"""The live tariffs (config/plans.json): no free access, clients only after paying with Stars."""

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


async def test_nothing_free_on_the_first_screen(paid):
    m = await paid.send("/start")
    assert "от 199 ⭐" in m.text
    assert "0 из 0" not in m.text and "бесплатно" not in m.text
    assert [b.text for b in m.buttons] == ["🔎 Найти клиентов", "⭐ Купить доступ", "❓ Помощь"]
    assert "FREE" not in m.text


async def test_search_is_locked_before_the_form(paid, fake_api):
    await paid.send("/start")
    m = await paid.press("Найти клиентов")
    assert "доступен по подписке" in m.text and "Купить доступ" in str(m.markup)
    m = await paid.send("/find")
    assert "доступен по подписке" in m.text
    assert not any(c.startswith("overpass:") for c in fake_api.calls)


async def test_plans_offer_only_paid_options(paid):
    m = await paid.send("/plans")
    assert "FREE" not in m.text
    assert "бесплатно на" not in str(m.markup)
    assert "PRO · 199 ⭐/мес" in str(m.markup) and "PRO+ · 549 ⭐/мес" in str(m.markup)
    assert "ИИ-анализ" not in m.text and "Промокод" not in str(m.markup)


async def test_paying_unlocks_clients(paid):
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
    assert "<b>Твой тариф: PRO</b>" in m.text and "🔒" not in m.text
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
