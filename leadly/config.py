"""Settings: secrets from .env, product config (plans, limits) from config/plans.json."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# usage kinds (keys in plans.json "limits")
SEARCHES = "searches"
RESULTS = "results"
CARD_OPENS = "card_opens"
SITE_CHECKS = "site_checks"
AI_ANALYSES = "ai_analyses"
PROPOSALS = "proposals"
SAVED_LEADS = "saved_leads"
WATCHES = "watches"
USAGE_KINDS = (SEARCHES, RESULTS, CARD_OPENS, SITE_CHECKS, AI_ANALYSES, PROPOSALS)


@dataclass(frozen=True)
class Limit:
    amount: int | None  # None = unlimited
    period: str = "total"  # total | day


@dataclass(frozen=True)
class Plan:
    code: str
    title: str
    tier: int
    price_stars: int
    price_rub_hint: int
    period_days: int
    results_per_search: int
    limits: dict[str, Limit]
    perks: list[str] = field(default_factory=list)
    watch_interval_days: int = 7
    year_price_stars: int = 0

    def limit(self, kind: str) -> Limit:
        return self.limits.get(kind, Limit(None))

    @property
    def is_paid(self) -> bool:
        return self.price_stars > 0


@dataclass(frozen=True)
class ProductConfig:
    plans: dict[str, Plan]
    features: dict[str, list[str]]
    referral_bonus_referrer: int
    referral_bonus_invited: int
    reminder_days: int
    trial_plan: str | None = None
    trial_days: int = 0
    followup_days: int = 3
    channel_bonus: int = 0
    partner_percent: int = 30

    def plan(self, code: str | None) -> Plan:
        return self.plans.get(code or "free") or self.plans["free"]

    def allows(self, plan: Plan, feature: str) -> bool:
        return plan.code in self.features.get(feature, [])

    @property
    def referral_enabled(self) -> bool:
        return bool(self.referral_bonus_referrer or self.referral_bonus_invited)

    @property
    def lowest_price(self) -> int:
        return min((p.price_stars for p in self.plans.values() if p.is_paid), default=0)


def load_product_config(path: Path) -> ProductConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    plans = {}
    for code, p in raw["plans"].items():
        limits = {k: Limit(v.get("amount"), v.get("period", "total")) for k, v in p.get("limits", {}).items()}
        plans[code] = Plan(
            code=code, title=p["title"], tier=int(p.get("tier", 0)),
            price_stars=int(p.get("price_stars", 0)), price_rub_hint=int(p.get("price_rub_hint", 0)),
            period_days=int(p.get("period_days", 30)), results_per_search=int(p.get("results_per_search", 15)),
            limits=limits, perks=list(p.get("perks", [])),
            watch_interval_days=int(p.get("watch_interval_days", 7)),
            year_price_stars=int(p.get("year_price_stars", 0)),
        )
    if "free" not in plans:
        raise ValueError("plans.json: нужен тариф 'free'")
    ref = raw.get("referral", {})
    return ProductConfig(
        plans=plans,
        features={k: list(v) for k, v in raw.get("features", {}).items()},
        referral_bonus_referrer=int(ref.get("bonus_searches_for_referrer", 0)),
        referral_bonus_invited=int(ref.get("bonus_searches_for_invited", 0)),
        reminder_days=int(raw.get("subscription_reminder_days", 3)),
        trial_plan=(raw.get("trial") or {}).get("plan"),
        trial_days=int((raw.get("trial") or {}).get("days", 0)),
        followup_days=int(raw.get("followup_days", 3)),
        channel_bonus=int(raw.get("channel_bonus_searches", 0)),
        partner_percent=int(raw.get("partner_percent", 30)),
    )


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    bot_token: str
    bot_username: str
    admin_ids: frozenset[int]
    db_path: Path
    log_file: Path | None
    log_level: str
    telegram_proxy: str | None
    # data sources
    search_providers: list[str]
    overpass_urls: list[str]
    nominatim_url: str
    osm_contact: str
    twogis_api_key: str
    # AI
    ai_provider: str  # none | anthropic | openai_compat
    anthropic_api_key: str
    anthropic_model: str
    openai_compat_base_url: str
    openai_compat_api_key: str
    openai_compat_model: str
    # payments
    payments_enabled: bool
    support_contact: str
    operator_name: str
    channel_username: str
    plans_path: Path
    product: ProductConfig

    def with_product(self, product: ProductConfig) -> "Settings":
        return replace(self, product=product)


def load_settings(env_file: str | Path | None = None, **overrides) -> Settings:
    load_dotenv(env_file or BASE_DIR / ".env")
    plans_path = Path(os.getenv("PLANS_CONFIG", BASE_DIR / "config" / "plans.json"))
    log_file = os.getenv("LOG_FILE", str(BASE_DIR / "data" / "leadly.log"))
    values = dict(
        bot_token=os.getenv("BOT_TOKEN", "").strip(),
        bot_username=os.getenv("BOT_USERNAME", "Lead1HuntBot").lstrip("@"),
        admin_ids=frozenset(int(x) for x in _csv(os.getenv("ADMIN_IDS", "")) if x.lstrip("-").isdigit()),
        db_path=Path(os.getenv("DB_PATH", BASE_DIR / "data" / "leadly.db")),
        log_file=Path(log_file) if log_file else None,
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        telegram_proxy=os.getenv("TELEGRAM_PROXY") or None,
        search_providers=_csv(os.getenv("SEARCH_PROVIDERS", "osm")),
        overpass_urls=_csv(os.getenv("OVERPASS_URLS",
                                     "https://overpass-api.de/api/interpreter,"
                                     "https://overpass.private.coffee/api/interpreter")),
        nominatim_url=os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org").rstrip("/"),
        osm_contact=os.getenv("OSM_CONTACT", ""),
        twogis_api_key=os.getenv("TWOGIS_API_KEY", ""),
        ai_provider=os.getenv("AI_PROVIDER", "none").strip().lower(),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"),
        openai_compat_base_url=os.getenv("OPENAI_COMPAT_BASE_URL", "").rstrip("/"),
        openai_compat_api_key=os.getenv("OPENAI_COMPAT_API_KEY", ""),
        openai_compat_model=os.getenv("OPENAI_COMPAT_MODEL", ""),
        payments_enabled=os.getenv("PAYMENTS_ENABLED", "true").lower() in ("1", "true", "yes"),
        support_contact=os.getenv("SUPPORT_CONTACT", ""),
        operator_name=os.getenv("OPERATOR_NAME", ""),
        channel_username=os.getenv("CHANNEL_USERNAME", "").strip().lstrip("@").removeprefix("https://t.me/"),
        plans_path=plans_path,
        product=load_product_config(plans_path),
    )
    values.update(overrides)
    return Settings(**values)
