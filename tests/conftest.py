import json

import pytest
from aiohttp import web

from pathlib import Path

from leadly.config import load_product_config, load_settings
from leadly.devtools.fake_telegram import TelegramSimulator
from leadly.providers.search import OSMSearchProvider, SearchAggregator

# fixed tariffs for tests (with a free tier and bonuses), independent of the live config/plans.json
TEST_PLANS = Path(__file__).parent / "plans.json"

OVERPASS_ELEMENTS = [
    {"type": "node", "id": 101, "tags": {"shop": "beauty", "name": "Студия «Лотос»", "website": "lotos-kzn.ru",
                                         "phone": "+7 843 111-22-33", "addr:street": "улица Баумана",
                                         "addr:housenumber": "5", "opening_hours": "Mo-Su 10:00-21:00"}},
    {"type": "way", "id": 202, "center": {"lat": 55.7, "lon": 49.1},
     "tags": {"shop": "hairdresser", "name": "Парикмахерская Стиль", "contact:phone": "+7 843 444-55-66"}},
    {"type": "node", "id": 303, "tags": {"shop": "beauty", "name": "Без контактов"}},
    {"type": "node", "id": 404, "tags": {"shop": "beauty"}},  # no name -> skipped
    {"type": "node", "id": 505, "tags": {"shop": "massage", "name": "Массаж Плюс", "email": "info@massage.example",
                                         "website": "https://massage-plus.ru"}},
]


@pytest.fixture
def settings(tmp_path):
    return load_settings(env_file=tmp_path / "none.env", db_path=tmp_path / "test.db", log_file=None,
                         bot_token="", search_providers=["osm"], ai_provider="none", payments_enabled=True,
                         admin_ids=frozenset({999}), support_contact="@support_test",
                         plans_path=TEST_PLANS, product=load_product_config(TEST_PLANS))


class FakeAPI:
    """Local HTTP server imitating Nominatim + Overpass (and anything a test adds)."""

    def __init__(self):
        self.app = web.Application()
        self.calls: list[str] = []
        self.overpass_status = 200
        self.overpass_failures = 0  # how many next requests answer 504
        self.overpass_remark = None
        self.elements = list(OVERPASS_ELEMENTS)
        self.app.router.add_get("/nominatim/search", self.nominatim)
        self.app.router.add_post("/overpass", self.overpass)
        self.runner: web.AppRunner | None = None
        self.base = ""

    async def nominatim(self, request):
        self.calls.append("nominatim:" + request.query.get("q", ""))
        if request.query.get("q") == "Нигдеград":
            return web.json_response([])
        return web.json_response([{"osm_type": "relation", "osm_id": 1234, "display_name": "Казань, Татарстан",
                                   "boundingbox": ["55.6", "55.9", "48.8", "49.4"]}])

    async def overpass(self, request):
        form = await request.post()
        self.calls.append("overpass:" + form.get("data", ""))
        if self.overpass_status != 200:
            return web.Response(status=self.overpass_status, text="busy")
        if self.overpass_failures > 0:
            self.overpass_failures -= 1
            return web.Response(status=504, text="busy")
        if self.overpass_remark:
            return web.json_response({"elements": [], "remark": self.overpass_remark})
        return web.json_response({"elements": self.elements})

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"

    async def stop(self):
        await self.runner.cleanup()


@pytest.fixture
async def fake_api():
    api = FakeAPI()
    await api.start()
    yield api
    await api.stop()


def osm_aggregator(api: FakeAPI) -> SearchAggregator:
    provider = OSMSearchProvider([f"{api.base}/overpass"], f"{api.base}/nominatim")
    provider.nominatim_pause = 0
    provider.overpass_pause = 0
    return SearchAggregator([provider])


class StubChecker:
    """No network in tests: every site 'opens' and shows no problems and no contacts."""

    def __init__(self):
        self.calls: list[str] = []

    async def check(self, url, with_contacts_page=False):
        from leadly.models import SiteReport
        from leadly.utils import now_iso
        self.calls.append(url)
        return SiteReport(url=url, checked_at=now_iso(), ok=True, status=200, final_url=url, https=True,
                          response_ms=300, title="Сайт", description="Описание", has_viewport=True,
                          has_form=True, analytics=["Яндекс Метрика"])


@pytest.fixture
async def tg(settings, fake_api):
    sim = await TelegramSimulator.create(settings, search_sources=osm_aggregator(fake_api), site_checker=StubChecker())
    yield sim
    await sim.close()


async def press_find(tg):
    """The menu's search button: the red «🎁 N бесплатных поисков — попробовать» while the free searches last,
    «🔎 Найти клиентов» otherwise."""
    trial = any("— попробовать" in btn.text for btn in tg.last.buttons)
    return await tg.press("— попробовать" if trial else "Найти клиентов")


async def run_search(tg, service="Разработка сайта", niche="Салоны красоты", sub="Все категории", city="Казань"):
    await tg.send("/start")
    await press_find(tg)
    await tg.press(service)
    await tg.press(niche)
    if sub:
        await tg.press(sub)
    await tg.press(city)
    return await tg.press("🔍 Найти")
