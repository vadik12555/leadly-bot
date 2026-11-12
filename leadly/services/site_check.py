"""Public website check: fetch the homepage once and record only what is observed.

Safety: only http(s); hostnames resolving to private/loopback/link-local addresses
are refused (checked inside the connector's resolver, so redirects and DNS
rebinding are covered); size and time limits; robots.txt is respected.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from html import unescape
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import aiohttp
from aiohttp.abc import AbstractResolver
from aiohttp.resolver import DefaultResolver

from leadly.models import SiteReport
from leadly.utils import now_iso

log = logging.getLogger(__name__)

BOT_UA = "LeadlyBot/1.0 (site check on user request)"
MAX_BYTES = 1_500_000
MAX_REDIRECTS = 5

BOOKING_WIDGETS = {
    "YCLIENTS": ("yclients.com", "yclients.ru"),
    "DIKIDI": ("dikidi.net", "dikidi.ru"),
    "Sonline": ("sonline.su",),
    "Altegio": ("altegio.com",),
    "Арника": ("arnica.pro",),
    "ПроДокторов": ("prodoctorov.ru/widget",),
}
CMS_MARKERS = {
    "Tilda": ("tildacdn.com", "tilda.ws", "tildaapi"),
    "WordPress": ("wp-content/", "wp-includes/"),
    "1С-Битрикс": ("/bitrix/",),
    "Wix": ("wixstatic.com", "wix.com"),
    "Joomla": ("/media/jui/", "joomla"),
    "OpenCart": ("catalog/view/theme",),
    "InSales": ("insales",),
    "Taplink": ("taplink.cc", "taplink.ru"),
}
ANALYTICS = {
    "Яндекс Метрика": ("mc.yandex.ru/metrika", "mc.yandex.ru/watch", "ym("),
    "Google Analytics / Tag Manager": ("googletagmanager.com", "google-analytics.com", "gtag("),
    "Пиксель VK": ("vk.com/rtrg", "top-fwz1.mail.ru"),
}
SOCIALS = {
    "VK": r"https?://(?:www\.)?vk\.com/[\w.\-]+",
    "Telegram": r"https?://(?:www\.)?t\.me/[\w\-]+",
    "WhatsApp": r"https?://(?:wa\.me|api\.whatsapp\.com)/[\w?=&+%\-]+",
}
EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)*\.[a-zа-я]{2,}", re.I)
TEL_RE = re.compile(r'href=["\']tel:([+\d\s()\-]{6,25})["\']', re.I)


class BlockedAddress(aiohttp.ClientError):
    pass


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_reserved or addr.is_unspecified)


class PublicOnlyResolver(AbstractResolver):
    """DNS resolver that refuses non-public addresses (SSRF protection)."""

    def __init__(self, allow_private: bool = False):
        self._inner = DefaultResolver()
        self.allow_private = allow_private

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        infos = await self._inner.resolve(host, port, family)
        if not self.allow_private:
            infos = [i for i in infos if _is_public(i["host"])]
            if not infos:
                raise BlockedAddress(f"{host} resolves to a non-public address")
        return infos

    async def close(self) -> None:
        await self._inner.close()


def normalize_url(url: str) -> str | None:
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return url


def url_key(url: str) -> str:
    parts = urlsplit(url if "://" in url else "http://" + url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    return host + (parts.path.rstrip("/") or "")


class SiteChecker:
    def __init__(self, timeout: float = 15, allow_private: bool = False):
        self.timeout = timeout
        self.allow_private = allow_private

    def _session(self) -> aiohttp.ClientSession:
        connector = aiohttp.TCPConnector(resolver=PublicOnlyResolver(self.allow_private), limit=10)
        # trust_env=False: a proxy would resolve hostnames itself and bypass the SSRF resolver
        return aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=self.timeout),
                                     headers={"User-Agent": BOT_UA, "Accept-Language": "ru,en;q=0.8"})

    async def check(self, url: str, with_contacts_page: bool = False) -> SiteReport:
        normalized = normalize_url(url)
        checked = now_iso()
        if not normalized:
            return SiteReport(url=url, checked_at=checked, ok=False, error="некорректный адрес сайта")
        try:
            async with self._session() as session:
                if not await self._robots_allows(session, normalized):
                    return SiteReport(url=normalized, checked_at=checked, ok=False, robots_blocked=True,
                                      error="сайт запрещает автоматическую проверку (robots.txt)")
                started = time.monotonic()
                status, final_url, html = await self._fetch(session, normalized)
                elapsed = int((time.monotonic() - started) * 1000)
                report = self.parse(normalized, final_url, status, html, elapsed, checked)
                if with_contacts_page and report.contacts_page and report.ok:
                    report.contacts_checked = True
                    try:
                        _, _, contacts_html = await self._fetch(session, report.contacts_page)
                        extra = self.parse(report.contacts_page, report.contacts_page, 200, contacts_html, 0, checked)
                        report.emails = sorted(set(report.emails) | set(extra.emails))
                        report.phones = sorted(set(report.phones) | set(extra.phones))
                        report.socials = {**extra.socials, **report.socials}
                    except Exception as e:  # contacts page is optional
                        log.info("contacts page fetch failed: %s", type(e).__name__)
                return report
        except BlockedAddress:
            return SiteReport(url=normalized, checked_at=checked, ok=False,
                              error="адрес ведёт во внутреннюю сеть — проверка запрещена")
        except asyncio.TimeoutError:
            return SiteReport(url=normalized, checked_at=checked, ok=False,
                              error=f"сайт не ответил за {int(self.timeout)} с")
        except aiohttp.ClientConnectorCertificateError:
            return SiteReport(url=normalized, checked_at=checked, ok=False, https=True,
                              error="ошибка SSL-сертификата")
        except aiohttp.ClientConnectorError as e:
            reason = "домен не найден" if isinstance(e.os_error, socket.gaierror) else "не удалось подключиться"
            return SiteReport(url=normalized, checked_at=checked, ok=False, error=reason)
        except aiohttp.InvalidURL:
            return SiteReport(url=normalized, checked_at=checked, ok=False, error="некорректное перенаправление")
        except aiohttp.TooManyRedirects:
            return SiteReport(url=normalized, checked_at=checked, ok=False, error="слишком много перенаправлений")
        except aiohttp.ClientError as e:
            return SiteReport(url=normalized, checked_at=checked, ok=False,
                              error=f"ошибка соединения ({type(e).__name__})")

    async def _robots_allows(self, session: aiohttp.ClientSession, url: str) -> bool:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        self._check_host(robots_url)
        try:
            async with session.get(robots_url, allow_redirects=False,
                                   timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status != 200:
                    return True
                text = (await resp.content.read(200_000)).decode("utf-8", "replace")
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError):
            return True  # no robots.txt reachable -> no restrictions declared
        parser = RobotFileParser()
        parser.parse(text.splitlines())
        return parser.can_fetch("LeadlyBot", url)

    def _check_host(self, url: str) -> None:
        """IP-literal hosts never reach the resolver, so they are validated here."""
        host = urlsplit(url).hostname or ""
        try:
            ip = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            return  # a hostname: PublicOnlyResolver validates every resolved address
        if not self.allow_private and not _is_public(str(ip)):
            raise BlockedAddress(f"{host} is not a public address")

    async def _fetch(self, session: aiohttp.ClientSession, url: str) -> tuple[int, str, str]:
        # redirects are followed manually so that every hop is validated
        for _ in range(MAX_REDIRECTS + 1):
            if urlsplit(url).scheme.lower() not in ("http", "https"):
                raise aiohttp.InvalidURL(url)
            self._check_host(url)
            async with session.get(url, allow_redirects=False) as resp:
                if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                    url = urljoin(str(resp.url), resp.headers["Location"])
                    continue
                raw = await resp.content.read(MAX_BYTES)
                charset = resp.charset
                if not charset:
                    m = re.search(rb'<meta[^>]+charset=["\']?([\w\-]+)', raw[:4000], re.I)
                    charset = m.group(1).decode() if m else "utf-8"
                try:
                    html = raw.decode(charset, "replace")
                except LookupError:
                    html = raw.decode("utf-8", "replace")
                return resp.status, str(resp.url), html
        raise aiohttp.TooManyRedirects(None, ())

    @staticmethod
    def parse(url: str, final_url: str, status: int, html: str, elapsed_ms: int, checked: str) -> SiteReport:
        low = html.lower()
        ok = 200 <= status < 400
        report = SiteReport(url=url, checked_at=checked, ok=ok, status=status, final_url=final_url,
                            https=final_url.lower().startswith("https://"), response_ms=elapsed_ms,
                            error=None if ok else f"сайт ответил кодом {status}")
        if status in (401, 403, 429):  # the site refused the bot: that says nothing about the site being broken
            report.robots_blocked = True
            report.error = "сайт ограничил автоматическую проверку"
        if not ok:
            return report
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        report.title = unescape(" ".join(m.group(1).split()))[:150] if m and m.group(1).strip() else None
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]*content=["\']([^"\']*)', html, re.I)
        report.description = unescape(m.group(1).strip())[:200] if m and m.group(1).strip() else None
        report.has_viewport = bool(re.search(r'<meta[^>]+name=["\']viewport["\']', html, re.I))
        years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–—]\s*)?((?:19|20)\d{2})",
                                            html, re.I)]
        report.copyright_year = max(years) if years else None
        report.cms = next((name for name, marks in CMS_MARKERS.items() if any(mk in low for mk in marks)), None)
        report.booking_widgets = [n for n, marks in BOOKING_WIDGETS.items() if any(mk in low for mk in marks)]
        report.has_form = "<form" in low
        report.analytics = [n for n, marks in ANALYTICS.items() if any(mk in low for mk in marks)]
        emails = {e.lower() for e in EMAIL_RE.findall(html)
                  if not re.search(r"\.(png|jpe?g|gif|svg|webp)$", e, re.I) and "example" not in e.lower()}
        report.emails = sorted(emails)[:5]
        report.phones = sorted({" ".join(p.split()) for p in TEL_RE.findall(html)})[:5]
        for name, pattern in SOCIALS.items():
            m = re.search(pattern, html, re.I)
            if m:
                report.socials[name] = m.group(0)
        m = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>\s*(?:контакт|contact)', html, re.I) or \
            re.search(r'href=["\']([^"\']*(?:contact|kontakt|kontakty)[^"\']*)["\']', html, re.I)
        if m and not m.group(1).startswith(("mailto:", "tel:", "#", "javascript:")):
            report.contacts_page = urljoin(final_url, m.group(1))
        return report
