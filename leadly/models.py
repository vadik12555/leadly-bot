"""Domain models shared by providers, services and the Telegram layer."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any
from urllib.parse import urlsplit


def _from_dict(cls, data: dict[str, Any]):
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    if "://" not in url:
        url = "http://" + url
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host or None


def phone_digits(phone: str | None) -> str | None:
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else None


def normalize_name(name: str) -> str:
    name = re.sub(r"[«»\"'`“”„]", "", name.lower())
    return re.sub(r"\s+", " ", name).strip()


@dataclass
class Company:
    """A business returned by a SearchProvider. Only data the source actually returned."""

    source: str  # provider code: osm | 2gis | demo
    source_title: str  # "OpenStreetMap"
    source_id: str
    name: str
    category: str
    city: str
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    source_url: str | None = None
    socials: dict[str, str] = field(default_factory=dict)
    opening_hours: str | None = None
    fetched_at: str = ""
    is_demo: bool = False
    contacts_from_site: list[str] = field(default_factory=list)  # "phone"/"email" found on the company site
    signals: list[str] = field(default_factory=list)  # problems confirmed by a site check (for ranking)
    cms: str | None = None  # site platform detected by the check (Tilda, WordPress…)

    @property
    def domain(self) -> str | None:
        return domain_of(self.website)

    def keys(self) -> list[str]:
        """All identities used for de-duplication, strongest first."""
        out = []
        if self.domain:
            out.append(f"d:{self.domain}")
        if phone_digits(self.phone):
            out.append(f"p:{phone_digits(self.phone)}")
        out.append(f"n:{normalize_name(self.name)}|{self.city.lower()}")
        return out

    @property
    def key(self) -> str:
        return self.keys()[0]

    @property
    def has_contacts(self) -> bool:
        return bool(self.phone or self.email or self.socials)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Company":
        return _from_dict(cls, data)


@dataclass
class SearchQuery:
    niche: str
    niche_title: str
    subcategory: str | None
    city: str
    limit: int = 50


@dataclass
class SiteReport:
    """Result of fetching a company's public website. Every field is observed, not guessed."""

    url: str
    checked_at: str
    ok: bool
    error: str | None = None
    status: int | None = None
    final_url: str | None = None
    https: bool | None = None
    response_ms: int | None = None
    title: str | None = None
    description: str | None = None
    has_viewport: bool | None = None
    copyright_year: int | None = None
    cms: str | None = None
    booking_widgets: list[str] = field(default_factory=list)
    has_form: bool | None = None
    analytics: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    socials: dict[str, str] = field(default_factory=dict)
    contacts_page: str | None = None
    robots_blocked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SiteReport":
        return _from_dict(cls, data)


@dataclass
class Insight:
    """What we know about a company with respect to the user's service."""

    facts: list[str]  # confirmed observations with their source
    not_checked: list[str]  # what is unknown / not verified
    opportunities: list[str]  # hypotheses grounded in facts
    confidence: str  # низкая | средняя | высокая
    confirmed_opportunities: int = 0
    signals: list[str] = field(default_factory=list)


@dataclass
class AIAnalysis:
    summary: str
    checked: list[str]
    opportunities: list[dict[str, str]]  # {"text": ..., "confidence": ...}
    recommendation: str
    confidence: str
    provider: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AIAnalysis":
        return _from_dict(cls, data)
