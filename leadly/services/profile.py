"""Executor profile: makes proposals ready to send (name, portfolio, prices, contact)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from leadly.db import Database
from leadly.utils import now_iso

# field -> (label, max length, example)
FIELDS: dict[str, tuple[str, int, str]] = {
    "name": ("Имя", 60, "Алексей"),
    "specialization": ("Чем занимаешься", 140, "делаю сайты на Tilda для салонов красоты"),
    "portfolio": ("Портфолио", 200, "https://behance.net/alex или t.me/alex_works"),
    "price": ("Цены", 80, "от 30 000 ₽, срок 7–10 дней"),
    "contact": ("Контакт для клиентов", 80, "@alex_web или +7 900 000-00-00"),
}


@dataclass
class Profile:
    name: str | None = None
    specialization: str | None = None
    portfolio: str | None = None
    price: str | None = None
    contact: str | None = None

    @property
    def filled(self) -> int:
        return sum(1 for v in asdict(self).values() if v)

    @property
    def is_empty(self) -> bool:
        return self.filled == 0

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v}


class ProfileService:
    def __init__(self, db: Database):
        self.db = db

    async def get(self, user_id: int) -> Profile:
        row = await self.db.fetchone("SELECT * FROM profiles WHERE user_id = ?", user_id)
        if not row:
            return Profile()
        return Profile(**{k: row[k] for k in FIELDS})

    async def set(self, user_id: int, field: str, value: str | None) -> str | None:
        """Saves a field. Returns an error message for invalid input, otherwise None."""
        if field not in FIELDS:
            return "Неизвестное поле."
        label, limit, _ = FIELDS[field]
        value = " ".join((value or "").split()) or None
        if value in ("-", "—"):
            value = None
        if value and len(value) > limit:
            return f"Слишком длинно: максимум {limit} символов."
        if value and field == "portfolio" and "." not in value and not value.startswith("@"):
            return "Нужна ссылка, например https://behance.net/alex или t.me/alex_works."
        await self.db.execute("INSERT INTO profiles (user_id, updated_at) VALUES (?, ?) "
                              "ON CONFLICT(user_id) DO NOTHING", user_id, now_iso())
        await self.db.execute(f"UPDATE profiles SET {field} = ?, updated_at = ? WHERE user_id = ?",
                              value, now_iso(), user_id)
        return None
