"""Small text helpers for Russian copy."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def join_ru(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " и " + items[-1]


def lower_first(text: str) -> str:
    """Lowercase the first letter unless it starts an acronym or a Latin brand name."""
    if not text:
        return text
    first = text[0]
    if not ("А" <= first <= "Я" or first == "Ё"):
        return text
    if len(text) > 1 and text[1].isupper():
        return text
    return first.lower() + text[1:]


def fmt_money(value: int | float, currency: str = "₽") -> str:
    return f"{int(value):,}".replace(",", " ") + f" {currency}"


def fmt_price_range(lo_k: int, hi_k: int, suffix: str = "", currency: str = "₽") -> str:
    lo = f"{lo_k * 1000:,}".replace(",", " ")
    hi = f"{hi_k * 1000:,}".replace(",", " ")
    return f"{lo}–{hi} {currency}{suffix}"


def stable_seed(*parts: object) -> int:
    raw = "|".join(str(p) for p in parts).encode()
    return int(hashlib.sha256(raw).hexdigest()[:12], 16)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_msk() -> str:
    return datetime.now(MSK).date().isoformat()


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).astimezone(MSK).strftime("%d.%m.%Y")
    except ValueError:
        return iso[:10]


def add_days_iso(days: int, start: str | None = None) -> str:
    base = datetime.fromisoformat(start) if start else datetime.now(timezone.utc)
    return (base + timedelta(days=days)).isoformat(timespec="seconds")


def short(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"
