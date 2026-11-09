"""End-to-end scenarios through real Telegram updates (offline simulator)."""

from tests.conftest import run_search


async def test_start_shows_value_limits_and_menu(tg):
    m = await tg.send("/start")
    assert "Leadly" in m.text and "OpenStreetMap" in m.text
    assert "пример" in m.text  # example result for first-time users
    assert "Поиски: 3 из 3" in m.text
    labels = [b.text for b in m.buttons]
    for label in ("🔎 Найти клиентов", "📋 Мои лиды", "✍️ Создать предложение", "⭐ Тарифы",
                  "📊 Моя статистика", "❓ Помощь"):
        assert label in labels


async def test_every_menu_button_does_something(tg):
    await tg.send("/start")
    expected = {"🔎 Найти клиентов": "Шаг 1 из 4", "📋 Мои лиды": "Мои лиды",
                "✍️ Создать предложение": "Создать предложение", "⭐ Тарифы": "Тарифы Leadly",
                "📊 Моя статистика": "Моя статистика", "🎁 Пригласить друга": "ref_", "❓ Помощь": "Как работает"}
    for label, marker in expected.items():
        m = await tg.press(label)
        assert marker in m.text, label
        assert "Я работаю через кнопки" not in m.text
        await tg.press("Меню")


async def test_search_shows_real_source_data_without_inventing(tg, fake_api):
    m = await run_search(tg)
    assert "Найдено 4 компании" in m.text  # element without a name is skipped
    assert "Источник: OpenStreetMap" in m.text and "ODbL" in m.text
    assert "ДЕМО" not in m.text
    # query really went to Overpass with the beauty tags inside the city area
    q = next(c for c in fake_api.calls if c.startswith("overpass:"))
    assert 'area(id:3600001234)' in q and '"shop"="beauty"' in q and '"shop"="hairdresser"' in q
    # companies with a website and contacts come first
    assert m.text.index("Лотос") < m.text.index("Без контактов")
    m = await tg.press("Лотос")
    assert "Источник: OpenStreetMap" in m.text and "улица Баумана, 5" in m.text
    await tg.press("К результатам")
    m = await tg.press("Без контактов")
    assert "Сайт: <i>не найден в источнике</i>" in m.text
    assert "Телефон: <i>не найден в источнике</i>" in m.text
    assert "нет сайта" not in m.text.lower()  # absence in the source is never stated as a fact about the company
    m = await tg.press("Подробнее")
    assert "не проверено" in m.text and "openstreetmap.org/node/303" in str(m.markup)


async def test_results_pagination_back_and_next(tg, fake_api):
    fake_api.elements = [{"type": "node", "id": i, "tags": {"shop": "beauty", "name": f"Салон {i:02d}"}}
                         for i in range(1, 13)]
    m = await run_search(tg)
    assert "1 / 3" in str(m.markup)
    m = await tg.press("›")
    assert "6. " in m.text
    m = await tg.press("6. ")
    m = await tg.press("Следующая")
    assert "Салон 07" in m.text
    m = await tg.press("К результатам")
    assert "2 / 3" in str(m.markup)  # returned to the page of the company, no new search


async def test_subcategory_and_filters(tg, fake_api):
    await tg.send("/start")
    await tg.press("Найти клиентов")
    await tg.press("Создание лендинга")
    await tg.press("Салоны красоты")
    await tg.press("Парикмахерские")
    await tg.press("Казань")
    await tg.press("Сайт не найден")
    m = await tg.press("🔍 Найти")
    q = [c for c in fake_api.calls if c.startswith("overpass:")][-1]
    assert '"shop"="hairdresser"' in q and '"shop"="beauty"' not in q
    assert "Лотос" not in m.text  # has a website -> filtered out


async def test_invalid_input_and_cancel(tg):
    await tg.send("/start")
    await tg.press("Найти клиентов")
    await tg.press("Другое")
    m = await tg.send("ab")
    assert "Слишком коротко" in m.text
    m = await tg.send("/cancel")
    assert "Leadly" in m.text
    m = await tg.send("видеосъёмка")
    assert "Я работаю через кнопки" in m.text  # state really cleared
    await tg.press("Найти клиентов")
    await tg.press("Разработка сайта")
    await tg.press("Стоматологии")
    await tg.press("Другой город")
    m = await tg.send("12345")
    assert "буквами" in m.text
    m = await tg.send("Казань")
    assert "Шаг 4 из 4" in m.text


async def test_unknown_city_is_reported_and_not_charged(tg):
    await tg.send("/start")
    await tg.press("Найти клиентов")
    await tg.press("Разработка сайта")
    await tg.press("Стоматологии")
    await tg.press("Другой город")
    await tg.send("Нигдеград")
    m = await tg.press("🔍 Найти")
    assert "не найден" in m.text and "Лимит поиска не списан" in m.text
    m = await tg.send("/start")
    assert "Поиски: 3 из 3" in m.text


async def test_source_failure_does_not_crash_or_charge(tg, fake_api):
    fake_api.overpass_status = 504
    m = await run_search(tg)
    assert "Поиск не выполнен" in m.text and "перегружен" in m.text
    assert "Лимит поиска не списан" in m.text
    fake_api.overpass_status = 200
    m = await tg.press("Повторить")
    assert "Найдено" in m.text
    m = await tg.send("/start")
    assert "Поиски: 2 из 3" in m.text  # only the successful attempt was charged


async def test_save_lead_and_no_duplicates(tg):
    await run_search(tg)
    await tg.press("Лотос")
    await tg.press("Сохранить")
    assert "⭐ Сохранено в «Мои лиды»" in tg.session.alerts
    # same company found by a second search must not create a second lead
    await run_search(tg, niche="Салоны красоты", sub="Салоны красоты")
    m = await tg.press("Лотос")
    assert "В моих лидах" in str(m.markup)
    count = await tg.svc.db.fetchone("SELECT COUNT(*) AS n FROM leads")
    assert count["n"] == 1


async def test_crm_status_notes_deal_delete_and_stats(tg):
    await run_search(tg)
    await tg.press("Лотос")
    await tg.press("Сохранить")
    await tg.press("В моих лидах")
    await tg.press("Статус")
    m = await tg.press("Связался")
    assert "Статус: <b>💬 Связался</b>" in m.text
    await tg.press("Заметка")
    m = await tg.send("Перезвонить в пятницу")
    assert "Перезвонить в пятницу" in m.text
    await tg.press("Статус")
    await tg.press("Сделка")
    m = await tg.send("сорок тысяч")
    assert "числом" in m.text
    m = await tg.send("40 000")
    assert "40 000 ₽" in m.text
    m = await tg.send("/stats")
    for line in ("Поисков: 1", "Просмотрено компаний: 1", "Сохранено лидов: 1", "Связался: 1", "Сделок: 1 · 40 000 ₽"):
        assert line in m.text, line
    m = await tg.send("/leads")
    m = await tg.press("Сделка · 1")
    await tg.press("Лотос")
    await tg.press("Удалить")
    m = await tg.press("Да, удалить")
    assert "Пока пусто" in m.text


async def test_hide_company_from_future_searches(tg):
    await run_search(tg)
    await tg.press("Лотос")
    await tg.press("Не показывать")
    m = await run_search(tg)
    assert "Лотос" not in m.text


async def test_lead_list_filter_and_pagination(tg, fake_api):
    fake_api.elements = [{"type": "node", "id": i, "tags": {"shop": "beauty", "name": f"Салон {i:02d}"}}
                         for i in range(1, 11)]
    await run_search(tg)
    for i in range(1, 11):
        await tg.press(f"{i}. ") if i <= 5 else None
        if i == 6:
            await tg.press("›")
        if i > 5:
            await tg.press(f"{i}. ")
        await tg.press("Сохранить")
        await tg.press("К результатам")
    m = await tg.send("/leads")
    assert "Все · 10" in str(m.markup)
    m = await tg.press("Все · 10")
    assert "1 / 2" in str(m.markup)


async def test_search_enriches_contacts_from_sites(settings, fake_api):
    from leadly.devtools.fake_telegram import TelegramSimulator
    from tests.conftest import StubChecker, osm_aggregator

    class ContactsOnSite(StubChecker):
        async def check(self, url, with_contacts_page=False):
            report = await super().check(url, with_contacts_page)
            report.phones, report.emails = ["+7 843 999-88-77"], ["info@massage-plus.ru"]
            return report

    sim = await TelegramSimulator.create(settings, search_sources=osm_aggregator(fake_api),
                                         site_checker=ContactsOnSite())
    try:
        m = await run_search(sim)
        assert "📞 С контактами: 3 из 4" in m.text  # "Массаж Плюс" had only a site and email in OSM
        m = await sim.press("Массаж Плюс")
        assert "+7 843 999-88-77 <i>(с сайта компании)</i>" in m.text
        assert "info@massage.example" in m.text  # the source's own email is kept, not overwritten
        assert "На сайте компании найден телефон" in m.text
    finally:
        await sim.close()


async def test_hot_filter_keeps_only_confirmed_problems(settings, fake_api):
    from leadly.devtools.fake_telegram import TelegramSimulator
    from tests.conftest import StubChecker, osm_aggregator

    class OneBadSite(StubChecker):
        async def check(self, url, with_contacts_page=False):
            report = await super().check(url, with_contacts_page)
            if "massage-plus" in url:
                report.has_viewport, report.copyright_year = False, 2015
            return report

    sim = await TelegramSimulator.create(settings, search_sources=osm_aggregator(fake_api), site_checker=OneBadSite())
    try:
        await sim.send("/start")
        await sim.press("Найти клиентов")
        await sim.press("Редизайн сайта")
        await sim.press("Салоны красоты")
        await sim.press("Все категории")
        await sim.press("Казань")
        m = await sim.press("Только горячие")
        assert "☑ 🔥" in str(m.markup)
        m = await sim.press("🔍 Найти")
        assert "Массаж Плюс" in m.text and "Лотос" not in m.text
        assert "🔥 нет мобильной версии +1" in m.text
        m = await sim.press("🔥 Массаж Плюс")
        assert "На главной нет meta viewport" in m.text
    finally:
        await sim.close()



async def test_map_links_and_save_all_with_contacts(tg):
    m = await run_search(tg)
    assert "Сохранить всех с контактами (3)" in str(m.markup)
    m = await tg.press("Сохранить всех с контактами")
    assert any("Сохранено: 3" in a for a in tg.session.alerts)
    count = await tg.svc.db.fetchone("SELECT COUNT(*) AS n FROM leads")
    assert count["n"] == 3
    await tg.press("Сохранить всех с контактами")  # again: nothing new, no duplicates
    assert any("Сохранено: 0" in a for a in tg.session.alerts)
    m = await tg.press("Лотос")
    urls = [b.url for b in m.buttons if b.url]
    assert any(u.startswith("https://yandex.ru/maps/?text=") for u in urls)
    assert any(u.startswith("https://2gis.ru/search/") for u in urls)


async def test_channel_button_and_bonus(settings, fake_api):
    from dataclasses import replace
    from leadly.devtools.fake_telegram import TelegramSimulator
    from tests.conftest import StubChecker, osm_aggregator
    s = replace(settings, channel_username="leadly_channel")
    sim = await TelegramSimulator.create(s, search_sources=osm_aggregator(fake_api), site_checker=StubChecker())
    try:
        m = await sim.send("/start")
        assert any(b.url == "https://t.me/leadly_channel" for b in m.buttons)
        await sim.press("поиска за подписку")
        assert any("Сначала подпишись" in a for a in sim.session.alerts)
        sim.session.channel_members.add(sim.user["id"])
        m = await sim.press("поиска за подписку")
        assert any("Спасибо за подписку" in a for a in sim.session.alerts)
        assert "за подписку" not in str(m.markup)  # one-time
        user = await sim.svc.db.get_user(sim.user["id"])
        assert user.bonus_searches == 2
    finally:
        await sim.close()


async def test_audit_document_for_client(tg):
    await run_search(tg)
    await tg.press("Лотос")
    await tg.press("Аудит сайта для клиента")
    doc = tg.last
    assert doc.document and doc.document.startswith("audit-lotos-kzn-ru")
    assert "Перешли этот файл компании" in doc.text


async def test_builder_filter(settings, fake_api):
    from leadly.devtools.fake_telegram import TelegramSimulator
    from tests.conftest import StubChecker, osm_aggregator

    class TildaSite(StubChecker):
        async def check(self, url, with_contacts_page=False):
            report = await super().check(url, with_contacts_page)
            report.cms = "Tilda" if "lotos" in url else "WordPress"
            return report

    sim = await TelegramSimulator.create(settings, search_sources=osm_aggregator(fake_api), site_checker=TildaSite())
    try:
        await sim.send("/start")
        await sim.press("Найти клиентов")
        await sim.press("Разработка сайта")
        await sim.press("Салоны красоты")
        await sim.press("Все категории")
        await sim.press("Казань")
        await sim.press("Сайт на конструкторе")
        m = await sim.press("🔍 Найти")
        assert "Лотос" in m.text and "🧱 Tilda" in m.text and "Массаж Плюс" not in m.text
    finally:
        await sim.close()
