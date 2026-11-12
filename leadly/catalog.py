"""Static catalog: services, niches (with data-source mappings), filters, CRM statuses."""

from __future__ import annotations

from dataclasses import dataclass, field

# --- services ---------------------------------------------------------------

# what kind of evidence matters for the service when analysing a company
GROUP_SITE = "site"  # site, landing, redesign
GROUP_ADS = "ads"
GROUP_SEO = "seo"
GROUP_DESIGN = "design"
GROUP_OTHER = "other"


@dataclass(frozen=True)
class Service:
    code: str
    emoji: str
    title: str
    group: str
    pitch: str  # "я занимаюсь …" for proposals

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.title}"


SERVICES: dict[str, Service] = {s.code: s for s in [
    Service("site", "🌐", "Разработка сайта", GROUP_SITE, "занимаюсь разработкой сайтов для бизнеса"),
    Service("landing", "📄", "Создание лендинга", GROUP_SITE, "делаю лендинги для бизнеса"),
    Service("redesign", "🎨", "Редизайн сайта", GROUP_SITE, "занимаюсь редизайном и обновлением сайтов"),
    Service("ads", "📣", "Настройка рекламы", GROUP_ADS, "настраиваю интернет-рекламу"),
    Service("seo", "📈", "SEO", GROUP_SEO, "занимаюсь SEO-продвижением сайтов"),
    Service("design", "✏️", "Дизайн", GROUP_DESIGN, "занимаюсь дизайном для бизнеса"),
]}


def custom_service(title: str) -> Service:
    title = " ".join(title.split())[:60] or "Моя услуга"
    return Service("custom", "🧩", title, GROUP_OTHER, f"занимаюсь направлением «{title}»")


def get_service(code: str, custom_title: str | None = None) -> Service:
    return custom_service(custom_title or "") if code == "custom" else SERVICES[code]


# --- niches -----------------------------------------------------------------


@dataclass(frozen=True)
class Subcategory:
    code: str
    title: str
    osm: tuple[tuple[str, str], ...]  # OSM tag pairs (key, value)
    query: str  # text query for search APIs like 2GIS


@dataclass(frozen=True)
class Niche:
    code: str
    emoji: str
    title: str
    bookable: bool  # clients usually book appointments
    subcategories: tuple[Subcategory, ...] = field(default_factory=tuple)
    note: str = ""  # caveat shown to the user about source coverage

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.title}"

    def subcategory(self, code: str | None) -> Subcategory | None:
        return next((s for s in self.subcategories if s.code == code), None)


def _s(code, title, osm, query):
    return Subcategory(code, title, tuple(osm), query)


NICHES: dict[str, Niche] = {n.code: n for n in [
    Niche("beauty", "💇", "Салоны красоты", True, (
        _s("salon", "Салоны красоты", [("shop", "beauty")], "салон красоты"),
        _s("hair", "Парикмахерские", [("shop", "hairdresser")], "парикмахерская"),
        _s("massage", "Массаж", [("shop", "massage")], "массажный салон"),
    )),
    Niche("dental", "🦷", "Стоматологии", True, (
        _s("dentist", "Стоматологии", [("amenity", "dentist"), ("healthcare", "dentist")], "стоматология"),
    )),
    Niche("auto", "🚗", "Автосервисы", True, (
        _s("repair", "Автосервисы", [("shop", "car_repair")], "автосервис"),
        _s("tyres", "Шиномонтаж", [("shop", "tyres")], "шиномонтаж"),
        _s("wash", "Автомойки", [("amenity", "car_wash")], "автомойка"),
    )),
    Niche("food", "🍽", "Рестораны и кафе", True, (
        _s("restaurant", "Рестораны", [("amenity", "restaurant")], "ресторан"),
        _s("cafe", "Кафе и кофейни", [("amenity", "cafe")], "кафе"),
        _s("fastfood", "Фастфуд", [("amenity", "fast_food")], "фастфуд"),
    )),
    Niche("realty", "🏠", "Агентства недвижимости", False, (
        _s("agent", "Агентства недвижимости", [("office", "estate_agent")], "агентство недвижимости"),
    )),
    Niche("legal", "⚖️", "Юридические компании", False, (
        _s("lawyer", "Юристы и адвокаты", [("office", "lawyer")], "юридические услуги"),
        _s("notary", "Нотариусы", [("office", "notary")], "нотариус"),
    )),
    Niche("fitness", "🏋️", "Фитнес-клубы", True, (
        _s("fitness", "Фитнес-клубы", [("leisure", "fitness_centre")], "фитнес-клуб"),
        _s("sports", "Спортивные центры", [("leisure", "sports_centre")], "спортивный центр"),
    )),
    Niche("repair", "🔨", "Ремонтные компании", False, (
        _s("builder", "Ремонт и строительство",
           [("craft", "builder"), ("office", "construction_company")], "ремонт квартир"),
        _s("crafts", "Мастера (электрик, сантехник…)",
           [("craft", "electrician"), ("craft", "plumber"), ("craft", "carpenter")], "сантехник электрик"),
        _s("windows", "Окна и двери", [("craft", "window_construction")], "пластиковые окна"),
    )),
    Niche("shops", "🛍", "Интернет-магазины", False, (
        _s("clothes", "Одежда и обувь", [("shop", "clothes"), ("shop", "shoes")], "магазин одежды"),
        _s("gifts", "Подарки и цветы", [("shop", "gift"), ("shop", "florist")], "магазин подарков"),
        _s("home", "Мебель и товары для дома", [("shop", "furniture"), ("shop", "interior_decoration")],
           "магазин мебели"),
        _s("electronics", "Электроника", [("shop", "electronics"), ("shop", "mobile_phone")],
           "магазин электроники"),
    ), note="В картах есть только магазины с офлайн-точкой; чисто онлайн-магазины туда обычно не попадают."),
]}

CUSTOM_NICHE_KEYS = ("shop", "office", "amenity", "craft", "healthcare", "leisure")


def custom_niche(title: str) -> Niche:
    title = " ".join(title.split())[:50] or "Другие компании"
    return Niche("custom", "🔎", title, False, (), note="Поиск по названию компаний — результаты могут быть неполными.")


def get_niche(code: str, custom_title: str | None = None) -> Niche:
    return custom_niche(custom_title or "") if code == "custom" else NICHES[code]


# --- geography ----------------------------------------------------------------

POPULAR_CITIES: tuple[str, ...] = (
    "Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Новосибирск",
    "Нижний Новгород", "Краснодар", "Самара", "Ростов-на-Дону", "Уфа",
    "Челябинск", "Омск", "Красноярск", "Воронеж", "Пермь", "Волгоград", "Тюмень",
)
MAX_CITIES = 3

# city centres (lat, lon) — to suggest the nearest big city when a search finds too little
CITY_CENTERS: dict[str, tuple[float, float]] = {
    "Москва": (55.756, 37.617), "Санкт-Петербург": (59.934, 30.335), "Казань": (55.796, 49.109),
    "Екатеринбург": (56.839, 60.606), "Новосибирск": (55.008, 82.936), "Нижний Новгород": (56.297, 43.936),
    "Краснодар": (45.036, 38.975), "Самара": (53.196, 50.100), "Ростов-на-Дону": (47.236, 39.702),
    "Уфа": (54.739, 55.972), "Челябинск": (55.164, 61.437), "Омск": (54.989, 73.324),
    "Красноярск": (56.015, 92.893), "Воронеж": (51.661, 39.200), "Пермь": (58.011, 56.250),
    "Волгоград": (48.708, 44.513), "Тюмень": (57.153, 65.534),
}


def nearest_big_city(lat: float, lon: float, exclude: str = "") -> str | None:
    """The closest city from the buttons, other than `exclude` (equirectangular distance is enough here)."""
    import math
    best, best_d = None, float("inf")
    for name, (clat, clon) in CITY_CENTERS.items():
        if name.lower() == exclude.strip().lower():
            continue
        dx = math.radians(clon - lon) * math.cos(math.radians((clat + lat) / 2))
        d = dx * dx + math.radians(clat - lat) ** 2
        if d < best_d:
            best, best_d = name, d
    return best

# --- result filters -------------------------------------------------------------

SITE_ANY, SITE_YES, SITE_NO = "any", "yes", "no"
SITE_FILTERS = {
    SITE_ANY: "Любые",
    SITE_YES: "🌐 Сайт указан",
    SITE_NO: "🚫 Сайт не найден в источнике",
}

# --- CRM statuses -----------------------------------------------------------------


@dataclass(frozen=True)
class Status:
    code: str
    emoji: str
    title: str
    stage: int  # funnel depth used for statistics; 0 = outside the funnel

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.title}"


STATUSES: dict[str, Status] = {s.code: s for s in [
    Status("new", "🆕", "Новый", 0),
    Status("check", "🔍", "Нужно проверить", 0),
    Status("proposal", "📝", "Подготовлено предложение", 0),
    Status("contacted", "💬", "Связался", 1),
    Status("replied", "👀", "Ответили", 2),
    Status("interested", "🔥", "Есть интерес", 3),
    Status("deal", "💰", "Сделка", 4),
    Status("refused", "❌", "Отказ", 0),
]}
STAGE_COLUMNS = {1: "contacted_at", 2: "replied_at", 3: "interested_at", 4: "deal_at"}
