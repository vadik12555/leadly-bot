"""A busy data source must not look like an empty city, and a blocked bot must not look like a broken site."""

from leadly import catalog as c
from leadly.providers.search.osm import pick_place
from leadly.services.insights import build_insight
from leadly.services.site_check import SiteChecker
from tests.conftest import run_search


def overpass_calls(api) -> int:
    return sum(1 for call in api.calls if call.startswith("overpass:"))


async def test_busy_server_is_retried_without_bothering_the_user(tg, fake_api):
    fake_api.overpass_failures = 2
    m = await run_search(tg)
    assert "Найдено 4" in m.text
    assert overpass_calls(fake_api) == 3


async def test_gives_up_after_three_rounds_and_keeps_the_limit(tg, fake_api):
    fake_api.overpass_status = 504
    m = await run_search(tg)
    assert "Поиск не выполнен" in m.text and "Лимит поиска не списан" in m.text
    assert overpass_calls(fake_api) == 3


async def test_server_timeout_is_not_shown_as_an_empty_city(tg, fake_api):
    fake_api.overpass_remark = "runtime error: Query timed out"
    m = await run_search(tg)
    assert "Поиск не выполнен" in m.text and "Лимит поиска не списан" in m.text


def test_city_is_chosen_by_name_not_by_type():
    orenburg = [{"osm_type": "way", "display_name": "Оренбург, городской округ Оренбург, Оренбургская область"},
                {"osm_type": "relation", "display_name": "Орск, городской округ Орск, Оренбургская область"},
                {"osm_type": "relation", "display_name": "Оренбургская область, Россия"}]
    assert pick_place(orenburg, "Оренбург") is orenburg[0]
    omsk = [{"osm_type": "node", "display_name": "Омск, Россия"},
            {"osm_type": "relation", "display_name": "городской округ Омск, Омская область"}]
    assert pick_place(omsk, "Омск") is omsk[1]
    district = [{"osm_type": "relation", "display_name": "район Хамовники, Москва"}]
    assert pick_place(district, "Москва, Хамовники") is district[0]  # no exact name: still the best guess
    assert pick_place([], "Нигдеград") is None


def test_forbidden_answer_is_not_a_confirmed_problem():
    report = SiteChecker.parse("http://a.ru", "http://a.ru", 403, "", 100, "2026-11-06T10:00:00+00:00")
    assert report.robots_blocked is True
    insight = build_insight(_company(), report, c.SERVICES["site"], True)
    assert not insight.signals


def test_missing_page_is_still_a_confirmed_problem():
    report = SiteChecker.parse("http://a.ru", "http://a.ru", 404, "", 100, "2026-11-06T10:00:00+00:00")
    insight = build_insight(_company(), report, c.SERVICES["site"], True)
    assert insight.signals == ["сайт не открывается"]


def _company():
    from leadly.models import Company
    return Company(source="osm", source_title="OpenStreetMap", source_id="node/1", name="Салон", category="Салоны",
                   city="Казань", website="http://a.ru", fetched_at="2026-11-06T10:00:00+00:00")
