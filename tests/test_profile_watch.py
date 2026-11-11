"""Executor profile in proposals; auto-search watches; follow-up reminders."""

from tests.conftest import press_find, run_search


async def test_profile_fills_proposals(tg):
    await run_search(tg)
    await tg.press("Лотос")
    m = await tg.press("Создать предложение")
    await tg.press("Деловое")
    assert "[Ваше имя]" in tg.last.text and "Заполни «👤 Мой профиль»" in tg.last.text
    await tg.press("Заполнить профиль")
    await tg.press("Имя")
    m = await tg.send("Алексей")
    assert "Алексей" in m.text and "Заполнено 1 из 5" in m.text
    await tg.press("Портфолио")
    m = await tg.send("просто текст")
    assert "Нужна ссылка" in m.text
    m = await tg.send("https://behance.net/alex")
    assert "behance.net/alex" in m.text
    await tg.press("Контакт для клиентов")
    await tg.send("@alex_web")
    await tg.press("Цены")
    m = await tg.send("от 30 000 ₽")
    await tg.press("Цены")
    m = await tg.send("-")  # clears the field
    assert "<b>Цены:</b> <i>не заполнено</i>" in m.text
    # new generation uses the profile
    await run_search(tg)
    await tg.press("Лотос")
    await tg.press("Создать предложение")
    await tg.press("Другие варианты")
    m = await tg.press("Деловое")
    assert "Меня зовут Алексей" in m.text and "behance.net/alex" in m.text and "@alex_web" in m.text
    assert "[Ваше имя]" not in m.text


async def test_auto_search_brings_only_new_companies(tg, fake_api):
    from leadly.bot.app import run_watches
    m = await run_search(tg)
    m = await tg.press("Следить за новыми")
    assert any("Готово" in a for a in tg.session.alerts)
    await tg.press("Следить за новыми")  # second tap: no duplicate
    assert any("Уже отслеживается" in a for a in tg.session.alerts)
    m = await tg.send("/start")
    m = await tg.press("Автопоиск")
    assert "Отслеживается: 1 из 1" in m.text and "Салоны красоты" in m.text
    # not due yet (FREE: once per 7 days)
    assert await run_watches(tg.bot, tg.svc, pause=0) == 0
    # a week later a new salon appears in the source
    await tg.svc.db.execute("UPDATE watches SET last_run_at = '2000-01-01T00:00:00+00:00'")
    fake_api.elements = fake_api.elements + [
        {"type": "node", "id": 909, "tags": {"shop": "beauty", "name": "Новый салон Ирис", "phone": "+7 843 000"}}]
    assert await run_watches(tg.bot, tg.svc, pause=0) == 1
    assert "1 новая компания" in tg.last.text
    m = await tg.press("Посмотреть")
    assert "Новый салон Ирис" in m.text and "Лотос" not in m.text  # already seen companies are skipped
    await tg.svc.db.execute("UPDATE watches SET last_run_at = '2000-01-01T00:00:00+00:00'")
    assert await run_watches(tg.bot, tg.svc, pause=0) == 0  # nothing new -> no message


async def test_watch_limit_on_free(tg):
    await run_search(tg)
    await tg.press("Следить за новыми")
    await run_search(tg, niche="Стоматологии", sub=None)
    await tg.press("Следить за новыми")
    assert any("можно следить за 1" in a for a in tg.session.alerts)
    m = await tg.send("/start")
    m = await tg.press("Автопоиск")
    m = await tg.press("🗑 1.")
    assert "Пока ничего не отслеживается" in m.text


async def test_followup_reminder(tg):
    from leadly.bot.app import send_followups
    await run_search(tg)
    await tg.press("Лотос")
    await tg.press("Сохранить")
    await tg.press("В моих лидах")
    await tg.press("Статус")
    await tg.press("Связался")
    assert await send_followups(tg.bot, tg.svc) == 0  # too early
    await tg.svc.db.execute("UPDATE leads SET contacted_at = '2000-01-01T00:00:00+00:00'")
    assert await send_followups(tg.bot, tg.svc) == 1
    assert "Пора напомнить о себе" in tg.last.text
    assert await send_followups(tg.bot, tg.svc) == 0  # only once
    m = await tg.press("Текст напоминания")
    assert "писал(а) вам несколько дней назад" in m.text
    m = await tg.press("Ответили")
    assert "Статус: <b>👀 Ответили</b>" in m.text


async def test_templates_create_use_and_save(tg):
    await tg.send("/profile")
    await tg.press("Имя")
    await tg.send("Алексей")
    await tg.send("/templates")
    await tg.press("Новый шаблон")
    await tg.send("Для салонов")
    m = await tg.send("Здравствуйте! Я {имя}. Видел {компания} в городе {город}. Портфолио: {портфолио}")
    assert any("Шаблон сохранён" in x.text for x in tg.session.messages.values()) and "Для салонов" in m.text
    await run_search(tg)
    await tg.press("Лотос")
    await tg.press("Создать предложение")
    await tg.press("Мои шаблоны")
    m = await tg.press("📄 Для салонов")
    assert "Я Алексей. Видел Студия «Лотос» в городе Казань." in m.text
    assert "Не хватило данных для: {портфолио}" in m.text
    await tg.press("К карточке")
    await tg.press("Создать предложение")
    await tg.press("Деловое")
    await tg.press("В шаблоны")
    assert any("Сохранено в «📄 Мои шаблоны»" in a for a in tg.session.alerts)
    row = await tg.svc.db.fetchone("SELECT text FROM templates WHERE title LIKE 'Из «%'")
    assert "Лотос" not in row["text"]  # the company name never ends up inside a reusable template
    m = await tg.send("/templates")
    await tg.press("🗑 1.")
    m = tg.last
    assert "Для салонов" not in m.text


async def test_final_touch_similar_and_weekly_digest(tg):
    from leadly.bot.app import send_digests, send_final_touches, send_followups
    await run_search(tg)
    await tg.press("Лотос")
    await tg.press("Сохранить")
    await tg.press("В моих лидах")
    await tg.press("Статус")
    await tg.press("Связался")
    assert await send_digests(tg.bot, tg.svc) == 0  # new user: no "weekly" digest yet
    await tg.svc.db.execute("UPDATE leads SET contacted_at = '2000-01-01T00:00:00+00:00'")
    assert await send_final_touches(tg.bot, tg.svc) == 0  # first the regular follow-up
    assert await send_followups(tg.bot, tg.svc) == 1
    assert await send_final_touches(tg.bot, tg.svc) == 1
    assert "Последнее касание" in tg.last.text
    m = await tg.press("Текст последнего сообщения")
    assert "пишу в последний раз" in m.text
    assert await send_final_touches(tg.bot, tg.svc) == 0
    # similar companies: preset service/niche, only the city is asked
    m = await tg.press("К карточке")
    m = await tg.press("Найти похожие")
    assert "Шаг 3 из 4" in m.text and "Салоны красоты" in m.text
    # digest after a week of use
    await tg.svc.db.execute("UPDATE users SET created_at = '2000-01-01T00:00:00+00:00'")
    assert await send_digests(tg.bot, tg.svc) == 1
    assert "Твоя неделя в Leadly" in tg.last.text and "Сохранено лидов: 1" in tg.last.text
    assert await send_digests(tg.bot, tg.svc) == 0  # not twice a week


async def test_daily_goal_streak_share_and_hints(tg):
    m = await run_search(tg)
    await tg.press("Лотос")
    await tg.press("Сохранить")
    m = await tg.press("Создать предложение")
    assert "🕒" in m.text
    await tg.send("/leads")
    await tg.press("Подготовлено предложение · 1")
    await tg.press("Лотос")
    await tg.press("Статус")
    await tg.press("Связался")
    m = await tg.send("/stats")
    assert "1/5" in m.text
    m = await tg.press("Цель 3/день")
    assert "1/3" in tg.last.text
    m = await tg.send("/start")
    assert "Сегодня написал 1 из 3" in m.text
    await tg.send("/leads")
    await tg.press("Связался · 1")
    await tg.press("Лотос")
    await tg.press("Статус")
    await tg.press("Сделка")
    shares = [x for x in tg.session.messages.values() if "Поздравляю со сделкой" in (x.text or "")]
    assert shares and "t.me/share/url" in str(shares[0].markup)


async def test_city_prompt_mentions_districts(tg):
    await tg.send("/start")
    await press_find(tg)
    await tg.press("Разработка сайта")
    await tg.press("Салоны красоты")
    await tg.press("Все категории")
    m = await tg.press("Другой город")
    assert "Хамовники" in m.text
