"""Unit tests: site checker, insights, providers parsing, AI adapters, migrations, logging safety."""

import json
import logging
from types import SimpleNamespace

import pytest
from aiohttp import web

from leadly import catalog as c
from leadly.bot.app import RedactSecrets, run_polling
from leadly.db import Database
from leadly.models import Company, SiteReport
from leadly.providers.ai.base import AIContext, AIProviderError
from leadly.providers.ai.openai_compat import OpenAICompatProvider
from leadly.providers.search import SearchAggregator, SearchProviderError, TwoGisSearchProvider, dedupe
from leadly.providers.search.base import SearchProvider
from leadly.models import SearchQuery
from leadly.services.insights import build_insight
from leadly.services.site_check import SiteChecker

OLD_SITE = """<html><head><title>Салон Лотос</title><meta charset="utf-8"></head><body>
<form action="/send"></form><a href="tel:+7 843 111-22-33">позвонить</a>
<a href="mailto:hello@lotos-kzn.ru">hello@lotos-kzn.ru</a> <a href="https://vk.com/lotos_kzn">VK</a>
<a href="/contacts">Контакты</a><footer>© 2016 Лотос</footer></body></html>"""
MODERN_SITE = """<html><head><title>Студия</title><meta name="viewport" content="width=device-width">
<meta name="description" content="Лучшая студия"><script src="https://static.tildacdn.com/x.js"></script>
<script>ym(123, "init")</script></head><body><div data-yclients="1">https://n123.yclients.com/</div>
<footer>© 2020–2026</footer></body></html>"""


def _h(make):
    async def handler(request):
        return make()
    return handler


@pytest.fixture
async def site_server():
    app = web.Application()
    app.router.add_get("/", _h(lambda: web.Response(text=OLD_SITE, content_type="text/html")))
    app.router.add_get("/moved", _h(lambda: web.Response(status=302, headers={"Location": "/modern"})))
    app.router.add_get("/contacts", _h(lambda: web.Response(text="<p>sales@lotos-kzn.ru</p>", content_type="text/html")))
    app.router.add_get("/modern", _h(lambda: web.Response(text=MODERN_SITE, content_type="text/html")))
    app.router.add_get("/down", _h(lambda: web.Response(status=503)))
    app.router.add_get("/robots.txt", _h(lambda: web.Response(text="User-agent: *\nDisallow: /private\n")))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    await runner.cleanup()


async def test_site_check_records_only_observed_facts(site_server):
    report = await SiteChecker(allow_private=True).check(site_server + "/", with_contacts_page=True)
    assert report.ok and report.status == 200 and report.https is False
    assert report.has_viewport is False and report.copyright_year == 2016 and report.has_form is True
    assert report.booking_widgets == [] and report.analytics == []
    assert "hello@lotos-kzn.ru" in report.emails and "sales@lotos-kzn.ru" in report.emails  # from /contacts
    assert report.phones == ["+7 843 111-22-33"] and report.socials["VK"] == "https://vk.com/lotos_kzn"

    modern = await SiteChecker(allow_private=True).check(site_server + "/moved")  # follows the redirect
    assert modern.has_viewport and modern.cms == "Tilda" and modern.copyright_year == 2026
    assert modern.booking_widgets == ["YCLIENTS"] and modern.analytics == ["Яндекс Метрика"]
    assert modern.description == "Лучшая студия"


async def test_site_check_errors_and_robots(site_server):
    down = await SiteChecker(allow_private=True).check(site_server + "/down")
    assert not down.ok and "503" in down.error
    blocked = await SiteChecker(allow_private=True).check(site_server + "/private/page")
    assert blocked.robots_blocked and not blocked.ok
    bad = await SiteChecker().check("ftp://example.com")
    assert not bad.ok


async def test_site_check_blocks_internal_addresses(site_server):
    report = await SiteChecker().check(site_server + "/")  # 127.0.0.1 without allow_private
    assert not report.ok and "внутреннюю сеть" in report.error
    for url in ("http://10.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://[::1]/", "http://localhost/"):
        report = await SiteChecker(timeout=3).check(url)
        assert not report.ok, url


async def test_site_check_host_validation(site_server):
    checker = SiteChecker(allow_private=True)
    checker._check_host(site_server)  # allowed in this mode
    strict = SiteChecker()
    with pytest.raises(Exception):
        strict._check_host("http://127.0.0.1:8080/")


def _company(**kw) -> Company:
    base = dict(source="osm", source_title="OpenStreetMap", source_id="node/1", name="Салон", category="Салоны",
                city="Казань", fetched_at="2026-10-29T10:00:00+00:00")
    base.update(kw)
    return Company(**base)


def test_insight_never_claims_absence_from_missing_source_data():
    ins = build_insight(_company(), None, c.SERVICES["site"], bookable=True)
    assert any("сайт не указан" in f for f in ins.facts)
    assert any("не проверено" in x for x in ins.not_checked)
    assert all("нет сайта" not in f.lower() for f in ins.facts)
    assert ins.confidence == "низкая" and ins.opportunities[0].startswith("Если сайта действительно нет")


def test_insight_from_site_report_is_grounded():
    report = SiteReport(url="http://x.ru", checked_at="2026-10-29T10:00:00+00:00", ok=True, status=200,
                        https=False, response_ms=900, has_viewport=False, copyright_year=2016, has_form=False)
    ins = build_insight(_company(website="http://x.ru"), report, c.SERVICES["redesign"], today=None)
    assert "На главной нет meta viewport" in ins.facts and "В подвале сайта указан © 2016" in ins.facts
    assert ins.confidence == "высокая"
    seo = build_insight(_company(website="http://x.ru"), report, c.SERVICES["seo"])
    assert any("meta description" in o for o in seo.opportunities)
    down = SiteReport(url="http://x.ru", checked_at="2026-10-29T10:00:00+00:00", ok=False, error="домен не найден")
    ins = build_insight(_company(website="http://x.ru"), down, c.SERVICES["site"])
    assert any("не открылся" in f for f in ins.facts) and ins.confidence == "средняя"


def test_dedupe_by_domain_phone_name():
    a = _company(website="https://www.lotos.ru", source_id="1")
    b = _company(name="Другое имя", website="http://lotos.ru/", source="2gis", source_id="2")
    c_ = _company(name="Третий", phone="8 (843) 111-22-33", source_id="3")
    d = _company(name="Четвёртый", phone="+7 843 111 22 33", source_id="4")
    e = _company(name="«Салон»", source_id="5")
    assert [x.source_id for x in dedupe([a, b, c_, d, e])] == ["1", "3"]


class Boom(SearchProvider):
    code, title = "boom", "Boom"

    async def search(self, query):
        raise SearchProviderError("лежит")


class Ok(SearchProvider):
    code, title = "ok", "Ok"

    async def search(self, query):
        return [_company(source_id="ok")]


async def test_aggregator_tolerates_partial_failure():
    agg = SearchAggregator([Boom(), Ok()])
    companies, warnings = await agg.search(SearchQuery("beauty", "Салоны", None, "Казань"))
    assert len(companies) == 1 and warnings == ["Boom: лежит"]
    with pytest.raises(SearchProviderError):
        await SearchAggregator([Boom()]).search(SearchQuery("beauty", "Салоны", None, "Казань"))


TWOGIS_PAYLOAD = {"meta": {"code": 200}, "result": {"total": 1, "items": [{
    "id": "70000001_hash", "name": "Лотос, салон красоты", "address_name": "Баумана, 5",
    "rubrics": [{"name": "Салоны красоты"}],
    "contact_groups": [{"contacts": [
        {"type": "phone", "value": "+78431112233", "text": "+7 (843) 111-22-33"},
        {"type": "website", "value": "http://link.2gis.ru/abc", "text": "lotos-kzn.ru"},
        {"type": "email", "value": "hello@lotos-kzn.ru"}]}]}]}}


async def test_twogis_parsing_and_errors(monkeypatch):
    calls = []

    async def handler(request):
        calls.append(dict(request.query))
        if request.query["key"] == "bad":
            return web.json_response({"meta": {"code": 403, "error": {"message": "key"}}})
        return web.json_response(TWOGIS_PAYLOAD)

    app = web.Application()
    app.router.add_get("/3.0/items", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/3.0/items"
    monkeypatch.setattr("leadly.providers.search.twogis.API_URL", url)
    try:
        p = TwoGisSearchProvider("good")
        res = await p.search(SearchQuery("beauty", "Салоны красоты", None, "Казань", limit=5))
        co = res[0]
        assert (co.name, co.phone, co.website, co.email) == ("Лотос, салон красоты", "+7 (843) 111-22-33",
                                                             "http://lotos-kzn.ru", "hello@lotos-kzn.ru")
        assert co.source_url == "https://2gis.ru/firm/70000001" and calls[0]["q"] == "салон красоты Казань"
        await p.close()
        bad = TwoGisSearchProvider("bad")
        with pytest.raises(SearchProviderError, match="ключ"):
            await bad.search(SearchQuery("beauty", "Салоны", None, "Казань"))
        await bad.close()
    finally:
        await runner.cleanup()


CTX = AIContext(service="Разработка сайта", company_name="Лотос", category="Салоны", city="Казань",
                website="lotos.ru", facts=["указан сайт"], not_checked=[], hypotheses=[])


async def test_openai_compatible_provider():
    async def handler(request):
        body = await request.json()
        if request.headers.get("Authorization") != "Bearer k":
            return web.Response(status=401)
        content = json.dumps({"short": "Коротко", "business": "Деловое", "fact": ""}, ensure_ascii=False)
        assert body["response_format"] == {"type": "json_object"}
        return web.json_response({"choices": [{"message": {"content": "```json\n" + content + "\n```"}}]})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1"
    try:
        p = OpenAICompatProvider(base, "k", "m")
        assert await p.write_proposals(CTX) == {"short": "Коротко", "business": "Деловое", "fact": None}
        await p.close()
        bad = OpenAICompatProvider(base, "wrong", "m")
        with pytest.raises(AIProviderError, match="ключ"):
            await bad.analyze(CTX)
        await bad.close()
        assert OpenAICompatProvider("", "", "").configured()[0] is False
    finally:
        await runner.cleanup()


async def test_anthropic_provider_request_shape():
    from leadly.providers.ai.anthropic_provider import AnthropicAIProvider

    captured = {}

    class FakeMessages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            text = json.dumps({"summary": "s", "checked": [], "opportunities": [], "recommendation": "r",
                               "confidence": "низкая"})
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])

    client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()))
    p = AnthropicAIProvider("key", client=client)
    result = await p.analyze(CTX)
    assert result.summary == "s" and result.provider == "anthropic"
    assert captured["model"] == "claude-opus-5-5"
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in captured["betas"]

    class Refusing(FakeMessages):
        async def create(self, **kwargs):
            return SimpleNamespace(stop_reason="refusal", content=[])

    p2 = AnthropicAIProvider("key", client=SimpleNamespace(beta=SimpleNamespace(messages=Refusing())))
    with pytest.raises(AIProviderError):
        await p2.analyze(CTX)


async def test_migrations_are_idempotent_and_keep_legacy_data(tmp_path):
    path = tmp_path / "old.db"
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, plan TEXT)")
    con.execute("INSERT INTO users VALUES (1, 'pro')")
    con.commit()
    con.close()
    from leadly.db import MIGRATIONS
    db = Database(path)
    try:
        await db.connect()
        assert (await db.fetchone("SELECT COUNT(*) FROM legacy_users"))[0] == 1
        assert (await db.fetchone("PRAGMA user_version"))[0] == len(MIGRATIONS)
    finally:
        await db.close()
    db = Database(path)
    try:
        await db.connect()  # second start: nothing to migrate, no errors
        assert (await db.fetchone("SELECT COUNT(*) FROM users"))[0] == 0
    finally:
        await db.close()


def test_tokens_are_masked_in_logs(caplog):
    token = "1234567890:AAH" + "x" * 32
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "token %s and key %s", (token, "sk-secret-123456"),
                               None)
    RedactSecrets(["sk-secret-123456"]).filter(record)
    assert token not in record.getMessage() and "sk-secret-123456" not in record.getMessage()
    assert record.getMessage().count("***") == 2


async def test_run_without_token_exits_cleanly(settings):
    assert await run_polling(settings) == 2


def test_plans_config_is_valid(settings):
    product = settings.product
    assert product.plans["free"].limit("searches").amount == 3 and product.plans["pro"].price_stars > 0
    assert all(p.tier == i for i, p in enumerate(product.plans.values()))


async def test_contacts_are_enriched_from_company_site(site_server, tmp_path):
    from leadly.services.enrich import enrich_contacts, load_report
    db = Database(tmp_path / "e.db")
    await db.connect()
    try:
        with_site = _company(name="С сайтом", website=site_server + "/", source_id="a")
        has_all = _company(name="Всё есть", website=site_server + "/modern", phone="+7 1", email="a@b.ru",
                           source_id="b")
        no_site = _company(name="Без сайта", source_id="c")
        gained = await enrich_contacts([with_site, has_all, no_site], SiteChecker(allow_private=True), db)
        assert gained == [with_site]
        assert with_site.phone == "+7 843 111-22-33" and with_site.email == "hello@lotos-kzn.ru"
        assert with_site.contacts_from_site == ["phone", "email"] and "VK" in with_site.socials
        assert has_all.contacts_from_site == [] and no_site.phone is None
        assert (await load_report(db, with_site.website)).ok  # cached for the card / "Проверить сайт"
        ins = build_insight(with_site, None, c.SERVICES["site"])
        assert "На сайте компании найден телефон, email" in ins.facts
    finally:
        await db.close()


async def test_enrichment_respects_time_budget(tmp_path):
    import asyncio
    from leadly.services.enrich import enrich_contacts

    class Slow:
        async def check(self, url, with_contacts_page=False):
            await asyncio.sleep(5)

    db = Database(tmp_path / "s.db")
    await db.connect()
    try:
        co = _company(website="http://slow.example")
        assert await enrich_contacts([co], Slow(), db, timeout=0.2) == []
        assert co.phone is None
        # one slow site is cut off by the per-site limit, not the whole budget
        t = asyncio.get_running_loop().time()
        assert await enrich_contacts([co], Slow(), db, timeout=10, per_site=0.2) == []
        assert asyncio.get_running_loop().time() - t < 2
    finally:
        await db.close()


async def test_enrichment_is_bounded_and_cached(tmp_path):
    """At most 15 sites per search, at most 8 at once; a site checked once is not fetched again."""
    import asyncio
    from leadly.services.enrich import enrich_contacts
    from leadly.utils import now_iso

    class Counting:
        def __init__(self):
            self.calls, self.now, self.peak = 0, 0, 0

        async def check(self, url, with_contacts_page=False):
            self.calls += 1
            self.now += 1
            self.peak = max(self.peak, self.now)
            await asyncio.sleep(0.05)
            self.now -= 1
            # a contacts page that has no email: must still count as checked
            return SiteReport(url=url, checked_at=now_iso(), ok=True, phones=["+7 900 000-00-00"],
                              contacts_page=url + "/contacts", contacts_checked=with_contacts_page)

    db = Database(tmp_path / "b.db")
    await db.connect()
    try:
        checker = Counting()
        companies = [_company(name=f"Co {i}", website=f"http://co{i}.example", source_id=str(i)) for i in range(30)]
        gained = await enrich_contacts(companies, checker, db)
        assert checker.calls == 15 and checker.peak <= 8 and len(gained) == 15
        again = [_company(name=f"Co {i}", website=f"http://co{i}.example", source_id=str(i)) for i in range(15)]
        assert len(await enrich_contacts(again, checker, db)) == 15  # contacts come from the cache
        assert checker.calls == 15
    finally:
        await db.close()


def test_audit_report_contains_only_observed_facts():
    from datetime import date
    from leadly.services.profile import Profile
    from leadly.services.report import build_audit_html
    report = SiteReport(url="http://x.ru", checked_at="2026-10-29T10:00:00+00:00", ok=True, status=200, https=False,
                        response_ms=900, has_viewport=False, copyright_year=2016, has_form=False, title="X",
                        description=None, analytics=[], booking_widgets=["YCLIENTS"])
    co = _company(name="Салон <Лотос>", website="http://x.ru")
    html = build_audit_html(co, report, c.SERVICES["redesign"], bookable=True,
                            profile=Profile(name="Алексей", portfolio="behance.net/alex"), today=date(2026, 10, 30))
    assert "Экспресс-аудит сайта x.ru" in html and "Салон &lt;Лотос&gt;" in html  # escaped
    for phrase in ("Не защищено", "мобильной адаптации", "2016 год", "формы заявки", "meta description"):
        assert phrase in html, phrase
    assert "Есть онлайн-запись (YCLIENTS)" in html and "Алексей" in html and "https://behance.net/alex" in html
    down = SiteReport(url="http://y.ru", checked_at="2026-10-29T10:00:00+00:00", ok=False, error="домен не найден")
    html = build_audit_html(_company(website="http://y.ru"), down, c.SERVICES["site"])
    assert "не открылся" in html and "Что уже хорошо" not in html
