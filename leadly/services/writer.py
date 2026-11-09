"""Template proposals (no AI): built only from confirmed facts, no invented claims."""

from __future__ import annotations

import random

from leadly import catalog as c
from leadly.models import Company, Insight, SiteReport
from leadly.utils import stable_seed

VARIANTS = {"short": "Короткое", "business": "Деловое", "fact": "По факту"}
NO_FACT_NOTE = ("Подтверждённых проблем пока не найдено, поэтому честное предложение «по факту» "
                "составить нельзя. Проверь сайт или используй деловой вариант.")


def _observation(company: Company, report: SiteReport | None, service: c.Service) -> str | None:
    """One concrete, verified observation suitable for a first message, or None."""
    if report is None:
        return None
    if not report.ok and not report.robots_blocked:
        return f"ваш сайт {company.domain} у меня не открылся ({report.error})"
    if report.ok:
        if report.has_viewport is False and service.group in (c.GROUP_SITE, c.GROUP_DESIGN, c.GROUP_SEO):
            return "на главной странице сайта нет мобильной адаптации (meta viewport)"
        if report.https is False and service.group in (c.GROUP_SITE, c.GROUP_SEO):
            return "сайт открывается без HTTPS, и браузер помечает его как «Не защищён»"
        if service.group == c.GROUP_ADS and not report.analytics:
            return "на главной странице я не нашёл счётчиков аналитики"
        if service.group == c.GROUP_SEO and not report.description:
            return "у главной страницы нет meta description для поисковой выдачи"
        if report.copyright_year and service.group in (c.GROUP_SITE, c.GROUP_DESIGN):
            return f"в подвале сайта указан {report.copyright_year} год"
    return None


def _signature(profile) -> str:
    if not profile or not (profile.name or profile.contact):
        return ""
    parts = [x for x in (profile.name, profile.contact) if x]
    return "\n\n" + ", ".join(parts)


def template_proposals(company: Company, report: SiteReport | None, insight: Insight,
                       service: c.Service, round_: int = 0, profile=None) -> dict[str, str | None]:
    rnd = random.Random(stable_seed(company.key, service.code, round_))
    hello = rnd.choice(["Здравствуйте!", "Добрый день!"])
    obs = _observation(company, report, service)
    pitch = (profile.specialization if profile and profile.specialization else service.pitch)
    name = profile.name if profile and profile.name else "[Ваше имя]"
    portfolio = f" Примеры работ: {profile.portfolio}." if profile and profile.portfolio else ""
    price = f" По стоимости: {profile.price}." if profile and profile.price else ""
    sign = _signature(profile)

    short = (f"{hello} Я {pitch}. "
             + (f"Заметил, что {obs}. " if obs else "")
             + rnd.choice(["Могу показать, как это можно улучшить. Интересно?",
                           "Если актуально — расскажу, что можно сделать, за 5 минут.",
                           "Подскажите, актуальна ли для вас эта тема?"])
             + portfolio + sign)

    business = (f"{hello} Меня зовут {name}, я {pitch}. "
                f"Пишу вам, потому что работаю с компаниями из сферы «{company.category.lower()}». "
                + rnd.choice([
                    f"Хотел уточнить, рассматриваете ли вы сейчас направление «{service.title.lower()}»?",
                    f"Подскажите, актуальна ли для вас сейчас услуга «{service.title.lower()}»?",
                ])
                + (portfolio or " Если да — пришлю примеры работ и ориентировочные сроки.")
                + price + " Спасибо!" + sign)

    if obs:
        fact = (f"{hello} Я {pitch}. Посмотрел ваш сайт {company.domain} и заметил, что {obs}. "
                "Это легко проверить самостоятельно. Если хотите, бесплатно опишу, что можно поправить "
                "и сколько это займёт — без обязательств." + portfolio + sign)
    else:
        fact = None  # no verified observation -> no "fact-based" message
    return {"short": short, "business": business, "fact": fact}


def followup_text(company: Company, service: c.Service, profile=None) -> str:
    """Short second touch for a company that didn't answer the first message."""
    hello = "Добрый день!"
    who = f"{profile.name}, " if profile and profile.name else ""
    return (f"{hello} Это {who}писал(а) вам несколько дней назад по поводу «{service.title.lower()}». "
            "Понимаю, что сообщений много, поэтому коротко: если тема актуальна — с радостью покажу примеры "
            "и отвечу на вопросы. Если нет — просто дайте знать, больше не побеспокою." + _signature(profile))


def final_touch_text(company: Company, service: c.Service, profile=None) -> str:
    """Third and last touch: polite close, leaves the door open."""
    who = f"{profile.name}" if profile and profile.name else "я"
    return ("Добрый день! Не хочу надоедать, поэтому пишу в последний раз по поводу "
            f"«{service.title.lower()}». Если вопрос станет актуальным — буду рад помочь, контакты ниже. "
            f"Хорошего дня! — {who}" + _signature(profile))
