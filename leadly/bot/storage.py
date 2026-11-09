"""aiogram FSM storage in SQLite, so an unfinished scenario survives a restart."""

from __future__ import annotations

import json
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

from leadly.db import Database


def _key(key: StorageKey) -> str:
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.thread_id or 0}:{key.destiny}"


class SQLiteStorage(BaseStorage):
    def __init__(self, db: Database):
        self.db = db

    async def set_state(self, key: StorageKey, state: str | State | None = None) -> None:
        value = state.state if isinstance(state, State) else state
        await self.db.execute("INSERT INTO fsm (key, state) VALUES (?, ?) "
                              "ON CONFLICT(key) DO UPDATE SET state = excluded.state", _key(key), value)

    async def get_state(self, key: StorageKey) -> str | None:
        row = await self.db.fetchone("SELECT state FROM fsm WHERE key = ?", _key(key))
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        await self.db.execute("INSERT INTO fsm (key, data) VALUES (?, ?) "
                              "ON CONFLICT(key) DO UPDATE SET data = excluded.data",
                              _key(key), json.dumps(data, ensure_ascii=False))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        row = await self.db.fetchone("SELECT data FROM fsm WHERE key = ?", _key(key))
        return json.loads(row["data"]) if row and row["data"] else {}

    async def close(self) -> None:
        pass
