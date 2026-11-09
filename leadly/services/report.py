"""Client-facing express audit of a company website: a self-contained HTML file the user forwards.

Contains only what the site check observed, explained in plain language for a business owner.
"""

from __future__ import annotations

from datetime import date
from html import escape

from leadly import catalog as c
from leadly.models import Company, SiteReport
from leadly.utils import fmt_date

OLD_SITE_YEARS = 3
SLOW_MS = 4000

ADVICE = {
    c.GROUP_SITE: "Обновить сайт: мобильная версия, понятная структура услуг и цен, форма заявки и онлайн-запись.",
    c.GROUP_DESIGN: "Обновить визуальную часть: современный дизайн, удобный для телефона, единый стиль.",
    c.GROUP_SEO: "Привести в порядок базовое SEO: заголовки, описания страниц, скорость, HTTPS — чтобы сайт "
                 "находили в поиске.",
    c.GROUP_ADS: "Настроить аналитику и рекламу так, чтобы было видно, сколько заявок приносит каждый рубль.",
    c.GROUP_OTHER: "Обсудить, какие из найденных пунктов важны для вашего бизнеса в первую очередь.",
}


def _findings(report: SiteReport, bookable: bool, today: date) -> tuple[list[str], list[str]]:
    bad: list[str] = []
    good: list[str] = []
    if report.robots_blocked:
        bad.append("Сайт закрыт от автоматической проверки, поэтому оценка неполная.")
        return bad, good
    if not report.ok:
        bad.append(f"Сайт не открылся при проверке ({report.error}). Если это не временный сбой, клиенты, "
                   "которые ищут вас в интернете, не смогут его открыть.")
        return bad, good
    good.append(f"Сайт открывается (загрузка главной — {report.response_ms / 1000:.1f} с).")
    if report.https is False:
        bad.append("Сайт работает без защищённого соединения (HTTPS) — браузеры показывают посетителям "
                   "пометку «Не защищено».")
    elif report.https:
        good.append("Есть защищённое соединение (HTTPS).")
    if report.has_viewport is False:
        bad.append("Нет мобильной адаптации (meta viewport) — на телефоне страница, скорее всего, "
                   "отображается мелко и неудобно, а многие клиенты заходят именно с телефона.")
    elif report.has_viewport:
        good.append("Страница настроена для показа на мобильных устройствах.")
    if report.copyright_year and report.copyright_year <= today.year - OLD_SITE_YEARS:
        bad.append(f"В подвале сайта указан {report.copyright_year} год — посетители могут решить, что сайт "
                   "и информация на нём давно не обновлялись.")
    if report.response_ms and report.response_ms > SLOW_MS:
        bad.append(f"Главная страница загружалась {report.response_ms / 1000:.1f} с — часть посетителей "
                   "не дожидается загрузки.")
    if report.has_form is False:
        bad.append("На главной нет формы заявки — оставить заявку можно только по телефону или в мессенджере.")
    if bookable:
        if report.booking_widgets:
            good.append("Есть онлайн-запись (" + ", ".join(report.booking_widgets) + ").")
        else:
            bad.append("На главной странице не найдена онлайн-запись.")
    if report.analytics:
        good.append("Установлена аналитика (" + ", ".join(report.analytics) + ").")
    else:
        bad.append("Счётчики аналитики (Яндекс Метрика, Google Analytics) на главной не найдены — сложно "
                   "понять, откуда приходят клиенты.")
    if not report.title:
        bad.append("У главной страницы нет заголовка (title) — это ухудшает показ в поиске.")
    if not report.description:
        bad.append("Нет описания страницы (meta description) — поисковик сам выбирает, какой текст показать.")
    return bad, good


def build_audit_html(company: Company, report: SiteReport, service: c.Service, bookable: bool = False,
                     profile=None, today: date | None = None) -> str:
    today = today or date.today()
    bad, good = _findings(report, bookable, today)
    e = escape
    domain = e(company.domain or report.url)
    author = ""
    if profile and (profile.name or profile.contact or profile.portfolio):
        parts = [f"<strong>{e(profile.name)}</strong>" if profile.name else "",
                 e(profile.specialization) if profile.specialization else "",
                 f'<a href="{e(profile.portfolio if profile.portfolio.startswith("http") else "https://" + profile.portfolio)}">'
                 f'{e(profile.portfolio)}</a>' if profile.portfolio else "",
                 e(profile.contact) if profile.contact else ""]
        author = "<section class=author><h2>Кто подготовил</h2><p>" + "<br>".join(p for p in parts if p) + "</p></section>"
    bad_html = "".join(f"<li class=bad>{e(x)}</li>" for x in bad) or "<li class=ok>Явных проблем на главной " \
                                                                     "странице не найдено.</li>"
    good_html = "".join(f"<li class=ok>{e(x)}</li>" for x in good)
    advice = e(ADVICE.get(service.group, ADVICE[c.GROUP_OTHER]))
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Экспресс-аудит сайта {domain}</title>
<style>
:root{{--bg:#f6f7f4;--card:#fff;--fg:#1d2420;--muted:#5f6b63;--accent:#2f7d5b;--bad:#b4472f;--line:#e3e7e1}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111613;--card:#1b221e;--fg:#e7ece8;--muted:#9aa89f;--accent:#62c596;--bad:#f08a6e;--line:#2b342f}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
main{{max-width:720px;margin:0 auto;padding:24px 16px 48px}}
header{{border-bottom:3px solid var(--accent);padding-bottom:12px;margin-bottom:20px}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:18px;margin:24px 0 8px}}
.meta{{color:var(--muted);font-size:14px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:4px 18px 12px;margin:14px 0}}
ul{{padding-left:0;list-style:none;margin:0}} li{{padding:8px 0 8px 30px;position:relative;border-bottom:1px solid var(--line)}}
li:last-child{{border-bottom:0}} li::before{{position:absolute;left:4px;top:8px}}
li.bad::before{{content:"⚠️"}} li.ok::before{{content:"✅"}}
a{{color:var(--accent)}} .note{{color:var(--muted);font-size:13px;margin-top:20px}}
</style></head><body><main>
<header><h1>Экспресс-аудит сайта {domain}</h1>
<div class=meta>{e(company.name)} · {e(company.city)} · проверено {e(fmt_date(report.checked_at))}</div></header>
<section><h2>Что стоит улучшить</h2><ul>{bad_html}</ul></section>
{f"<section><h2>Что уже хорошо</h2><ul>{good_html}</ul></section>" if good_html else ""}
<section><h2>Что можно сделать</h2><p>{advice}</p></section>
{author}
<p class=note>Проверялась только главная страница сайта автоматически. Некоторые элементы (например, онлайн-запись)
могут находиться на других страницах — это стоит уточнить. Отчёт подготовлен с помощью Leadly.</p>
</main></body></html>"""
