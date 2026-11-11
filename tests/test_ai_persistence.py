"""AI provider behaviour, restart persistence."""

from leadly.devtools.fake_telegram import TelegramSimulator
from leadly.models import AIAnalysis
from leadly.providers.ai import AIProvider, AIProviderError
from tests.conftest import osm_aggregator, run_search


class FakeAI(AIProvider):
    name = "fake"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.contexts = []

    async def analyze(self, ctx):
        self.contexts.append(ctx)
        if self.fail:
            raise AIProviderError("ИИ-сервис недоступен из сети сервера.")
        return AIAnalysis(summary="Салон красоты в Казани с сайтом.", checked=["Сайт указан в OSM"],
                          opportunities=[{"text": "Проверить онлайн-запись", "confidence": "низкая"}],
                          recommendation="Уточнить, как клиенты записываются.", confidence="низкая", provider="fake")

    async def write_proposals(self, ctx, round_=0):
        if self.fail:
            raise AIProviderError("ИИ временно перегружен запросами.")
        return {"short": f"Привет от ИИ #{round_}", "business": "Деловое от ИИ", "fact": ""}


async def _sim(settings, fake_api, ai):
    from tests.conftest import StubChecker
    return await TelegramSimulator.create(settings, search_sources=osm_aggregator(fake_api), ai=ai,
                                          site_checker=StubChecker())


async def test_no_ai_key_hides_ai_everywhere(tg):
    m = await tg.send("/start")
    assert "ИИ" not in m.text and "ИИ-анализ" not in str(m.markup)
    await run_search(tg)
    m = await tg.press("Лотос")
    assert "ИИ-анализ" not in str(m.markup)
    m = await tg.send("/plans")
    assert "ИИ-анализ" not in m.text


async def test_ai_analysis_and_proposals_with_provider(settings, fake_api):
    ai = FakeAI()
    sim = await _sim(settings, fake_api, ai)
    try:
        await run_search(sim)
        await sim.press("Лотос")
        m = await sim.press("ИИ-анализ")
        assert "Салон красоты в Казани" in m.text and "уверенность: низкая" in m.text
        ctx = ai.contexts[0]
        assert ctx.company_name == "Студия «Лотос»" and "phone" not in ctx.as_prompt().lower()
        assert "+7 843" not in ctx.as_prompt()  # contacts are not sent to the AI vendor
        await sim.press("К карточке")
        await sim.press("ИИ-анализ")  # cached: no second charge
        row = await sim.svc.db.fetchone("SELECT COUNT(*) AS n FROM usage_events WHERE kind = 'ai_analyses'")
        assert row["n"] == 1
        m = await sim.press("Создать предложение")
        assert "Привет от ИИ #0" in m.text and "· <i>ИИ</i>" in m.text
        m = await sim.press("Другие варианты")
        assert "Привет от ИИ #1" in m.text
        m = await sim.press("По факту")
        assert "Подтверждённых проблем" in m.text  # empty "fact" from AI is not shown as a message
    finally:
        await sim.close()


async def test_ai_failure_refunds_and_falls_back(settings, fake_api):
    sim = await _sim(settings, fake_api, FakeAI(fail=True))
    try:
        await run_search(sim)
        await sim.press("Лотос")
        m = await sim.press("ИИ-анализ")
        assert "ИИ-анализ не выполнен" in m.text and "Лимит не списан" in m.text
        row = await sim.svc.db.fetchone("SELECT COUNT(*) AS n FROM usage_events WHERE kind = 'ai_analyses'")
        assert row["n"] == 0
        await sim.press("К карточке")
        m = await sim.press("Создать предложение")
        assert "перегружен" in m.text and "Показан шаблон" in m.text
    finally:
        await sim.close()


async def test_edit_and_copy_proposal(tg):
    await run_search(tg)
    await tg.press("Лотос")
    await tg.press("Создать предложение")
    await tg.press("Деловое")
    m = await tg.press("Изменить текст")
    assert "Пришли исправленный текст" in m.text
    m = await tg.send("Добрый день! Это мой собственный текст предложения.")
    assert "Моя версия" in m.text and "мой собственный текст" in m.text
    btn = next(b for b in m.buttons if "Скопировать" in b.text)
    assert btn.copy_text.text.startswith("Добрый день! Это мой")  # native Telegram copy (<= 256 chars)
    await tg.press("Изменить текст")
    long_text = "Добрый день! " + "Очень подробное предложение. " * 12
    await tg.send(long_text)
    await tg.press("Скопировать")  # > 256 chars: bot sends the text as a separate tap-to-copy message
    assert "Нажми на текст" in tg.last.text and "<code>" in tg.last.text


async def test_data_survives_restart(settings, fake_api):
    sim = await _sim(settings, fake_api, None)
    await run_search(sim)
    await sim.press("Лотос")
    await sim.press("Сохранить")
    await sim.press("К результатам")
    await sim.press("Новый поиск")
    await sim.press("Разработка сайта")
    await sim.press("Стоматологии")
    await sim.press("Другой город")  # waiting for text input when the bot restarts
    await sim.close()

    sim2 = await _sim(settings, fake_api, None)
    try:
        m = await sim2.send("Казань")  # FSM state restored from SQLite
        assert "Шаг 4 из 4" in m.text and "Стоматологии" in m.text
        m = await sim2.send("/start")
        assert "(осталось 2)" in m.text  # usage survived
        m = await sim2.send("/leads")
        assert "Все · 1" in str(m.markup)
    finally:
        await sim2.close()
