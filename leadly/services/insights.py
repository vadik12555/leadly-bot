"""Turns observed data into facts, unknowns and grounded hypotheses. No guessing.

Rule: absence of data in a source is reported as "not found in the source",
never as "the company doesn't have it". Opportunities are worded as hypotheses
and are only derived from things we actually observed.
"""

from __future__ import annotations

from datetime import date

from leadly import catalog as c
from leadly.models import Company, Insight, SiteReport
from leadly.utils import fmt_date

OLD_SITE_YEARS = 3
SLOW_MS = 4000


def build_insight(company: Company, report: SiteReport | None, service: c.Service,
                  bookable: bool = False, today: date | None = None) -> Insight:
    year = (today or date.today()).year
    src = company.source_title
    facts: list[str] = []
    unknown: list[str] = []
    opps: list[str] = []
    signals: list[str] = []  # short labels of problems confirmed by the site check

    # --- source data ---
    if company.website:
        facts.append(f"{src}: указан сайт {company.domain}")
    else:
        facts.append(f"{src}: сайт не указан")
        unknown.append("Есть ли у компании сайт — не проверено (в источнике данных может не быть)")
    from_site = set(company.contacts_from_site)
    in_source = [x for x, key, ok in (("телефон", "phone", company.phone), ("email", "email", company.email))
                 if ok and key not in from_site]
    on_site = [x for x, key in (("телефон", "phone"), ("email", "email")) if key in from_site]
    if in_source:
        facts.append(f"{src}: есть публичный " + ", ".join(in_source))
    if on_site:
        facts.append("На сайте компании найден " + ", ".join(on_site))
    if not in_source and not on_site:
        unknown.append("Публичные контакты в источнике не найдены")

    # --- website check ---
    if company.website and report is None:
        unknown.append("Сайт ещё не проверялся — нажми «🌐 Проверить сайт»")
    elif report is not None and report.robots_blocked:
        unknown.append("Сайт запретил автоматическую проверку — оцени его вручную")
    elif report is not None and not report.ok:
        when = fmt_date(report.checked_at)
        facts.append(f"Проверка {when}: сайт не открылся — {report.error}")
        if service.group in (c.GROUP_SITE, c.GROUP_SEO, c.GROUP_ADS):
            opps.append("Сайт не открывался при проверке. Если проблема не временная — клиенты не могут "
                        "его открыть; можно предложить восстановление или новый сайт")
            signals.append("сайт не открывается")
    elif report is not None and report.ok:
        when = fmt_date(report.checked_at)
        facts.append(f"Проверка {when}: сайт открывается (код {report.status}, "
                     f"{report.response_ms / 1000:.1f} с)")
        if report.cms:
            facts.append(f"Платформа сайта: {report.cms}")
        if report.https is False:
            facts.append("Сайт открывается без HTTPS")
            if service.group in (c.GROUP_SITE, c.GROUP_SEO):
                opps.append("Нет HTTPS — браузеры помечают сайт как «Не защищён»; можно предложить "
                            "перевод на HTTPS в рамках обновления")
                signals.append("нет HTTPS")
        if report.has_viewport is False:
            facts.append("На главной нет meta viewport")
            if service.group in (c.GROUP_SITE, c.GROUP_DESIGN, c.GROUP_SEO):
                opps.append("Без meta viewport страница, скорее всего, неудобна на телефоне — "
                            "стоит открыть её с мобильного и убедиться")
                signals.append("нет мобильной версии")
        if report.copyright_year and report.copyright_year <= year - OLD_SITE_YEARS:
            facts.append(f"В подвале сайта указан © {report.copyright_year}")
            if service.group in (c.GROUP_SITE, c.GROUP_DESIGN):
                opps.append(f"Год в подвале — {report.copyright_year}: сайт мог давно не обновляться "
                            "(гипотеза, проверь дизайн и актуальность цен)")
                signals.append(f"© {report.copyright_year} в подвале")
        if report.response_ms and report.response_ms > SLOW_MS:
            facts.append(f"Главная загружалась {report.response_ms / 1000:.1f} с")
            if service.group in (c.GROUP_SITE, c.GROUP_SEO):
                opps.append("Медленная загрузка главной — возможная тема для оптимизации")
                signals.append("медленный сайт")
        if service.group == c.GROUP_SEO:
            if not report.title:
                facts.append("У главной страницы нет заголовка (title)")
                opps.append("Нет title — базовая SEO-ошибка, которую легко показать клиенту")
                signals.append("нет title")
            if not report.description:
                facts.append("Нет meta description на главной")
                opps.append("Нет meta description — сниппет в поиске формируется случайно")
                signals.append("нет description")
        if service.group == c.GROUP_ADS:
            if report.analytics:
                facts.append("Найдены счётчики: " + ", ".join(report.analytics))
            else:
                facts.append("Счётчики Яндекс Метрики / Google Analytics на главной не найдены")
                opps.append("Аналитика на главной не найдена — без неё рекламу нельзя измерить; "
                            "можно предложить настройку аналитики и рекламы")
                signals.append("нет аналитики")
        if bookable:
            if report.booking_widgets:
                facts.append("Есть онлайн-запись: " + ", ".join(report.booking_widgets))
            else:
                facts.append("Известные виджеты онлайн-записи (YCLIENTS, DIKIDI и др.) на главной не найдены")
                unknown.append("Онлайн-запись может быть на другой странице или через мессенджер")
                if service.group in (c.GROUP_SITE, c.GROUP_OTHER):
                    opps.append("На главной не найдена онлайн-запись — уточни, как клиенты записываются; "
                                "если только по телефону, можно предложить запись на сайте")
        if report.has_form is False and service.group == c.GROUP_SITE:
            facts.append("На главной странице нет HTML-формы")
            opps.append("Нет формы заявки на главной — можно предложить форму или квиз для заявок")
            signals.append("нет формы заявки")

    # --- hypotheses when the site is unknown ---
    if not company.website and service.group in (c.GROUP_SITE, c.GROUP_ADS, c.GROUP_SEO):
        opps.append("Если сайта действительно нет — можно предложить сайт или лендинг "
                    "(сначала уточни у компании)")

    confirmed = len(signals)
    if confirmed >= 2:
        confidence = "высокая"
    elif confirmed == 1:
        confidence = "средняя"
    else:
        confidence = "низкая"
    return Insight(facts=facts, not_checked=unknown, opportunities=opps, confidence=confidence,
                   confirmed_opportunities=confirmed, signals=signals)
