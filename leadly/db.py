"""SQLite storage with versioned migrations (PRAGMA user_version)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

MIGRATIONS: list[str] = [
    # 1 — schema for the commercial MVP
    """
    CREATE TABLE users (
        id              INTEGER PRIMARY KEY,          -- Telegram user id
        username        TEXT,
        first_name      TEXT,
        city            TEXT,
        referred_by     INTEGER,
        bonus_searches  INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL,
        last_seen_at    TEXT NOT NULL
    );
    CREATE INDEX idx_users_last_seen ON users(last_seen_at);

    CREATE TABLE payments (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id             INTEGER NOT NULL,
        plan                TEXT NOT NULL,
        amount              INTEGER NOT NULL,
        currency            TEXT NOT NULL,
        status              TEXT NOT NULL,         -- pending | paid | refunded | failed | manual
        payload             TEXT NOT NULL UNIQUE,
        telegram_charge_id  TEXT UNIQUE,
        provider_charge_id  TEXT,
        error               TEXT,
        created_at          TEXT NOT NULL,
        paid_at             TEXT
    );
    CREATE INDEX idx_payments_user ON payments(user_id);

    CREATE TABLE subscriptions (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id            INTEGER NOT NULL,
        plan               TEXT NOT NULL,
        starts_at          TEXT NOT NULL,
        ends_at            TEXT NOT NULL,
        status             TEXT NOT NULL,          -- active | refunded | cancelled
        source             TEXT NOT NULL,          -- stars | manual
        payment_id         INTEGER REFERENCES payments(id),
        notified_expiring  INTEGER NOT NULL DEFAULT 0,
        notified_expired   INTEGER NOT NULL DEFAULT 0,
        created_at         TEXT NOT NULL
    );
    CREATE INDEX idx_subscriptions_user ON subscriptions(user_id, ends_at);
    CREATE INDEX idx_subscriptions_ends ON subscriptions(status, ends_at);

    CREATE TABLE usage_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        kind        TEXT NOT NULL,
        ref         TEXT NOT NULL,                 -- idempotency key: repeated action = no new charge
        amount      INTEGER NOT NULL,
        period_key  TEXT NOT NULL,                 -- all | YYYY-MM-DD | bonus
        created_at  TEXT NOT NULL,
        UNIQUE (user_id, kind, ref)
    );
    CREATE INDEX idx_usage_period ON usage_events(user_id, kind, period_key);

    CREATE TABLE searches (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id       INTEGER NOT NULL,
        token         TEXT NOT NULL UNIQUE,
        service       TEXT NOT NULL,
        service_title TEXT NOT NULL,
        niche         TEXT NOT NULL,
        niche_title   TEXT NOT NULL,
        subcategory   TEXT,
        cities        TEXT NOT NULL,               -- JSON list
        filters       TEXT NOT NULL,               -- JSON
        providers     TEXT NOT NULL,
        is_demo       INTEGER NOT NULL DEFAULT 0,
        found         INTEGER NOT NULL DEFAULT 0,
        delivered     INTEGER NOT NULL DEFAULT 0,
        status        TEXT NOT NULL,               -- running | ok | error
        error         TEXT,
        created_at    TEXT NOT NULL
    );
    CREATE INDEX idx_searches_user ON searches(user_id, created_at);

    CREATE TABLE search_results (
        search_id    INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
        idx          INTEGER NOT NULL,
        company_key  TEXT NOT NULL,
        company      TEXT NOT NULL,                -- JSON Company
        PRIMARY KEY (search_id, idx)
    );

    CREATE TABLE hidden_companies (
        user_id     INTEGER NOT NULL,
        company_key TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        PRIMARY KEY (user_id, company_key)
    );

    CREATE TABLE leads (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        INTEGER NOT NULL,
        company_key    TEXT NOT NULL,
        company        TEXT NOT NULL,              -- JSON snapshot at save time
        service        TEXT NOT NULL,
        service_title  TEXT NOT NULL,
        niche          TEXT,
        search_id      INTEGER,
        status         TEXT NOT NULL DEFAULT 'new',
        deal_amount    INTEGER,
        contacted_at   TEXT,
        replied_at     TEXT,
        interested_at  TEXT,
        deal_at        TEXT,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
        UNIQUE (user_id, company_key)
    );
    CREATE INDEX idx_leads_user_status ON leads(user_id, status, updated_at);

    CREATE TABLE lead_notes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id     INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
        text        TEXT NOT NULL,
        created_at  TEXT NOT NULL
    );
    CREATE INDEX idx_notes_lead ON lead_notes(lead_id);

    CREATE TABLE site_checks (
        url_key     TEXT PRIMARY KEY,
        report      TEXT NOT NULL,
        checked_at  TEXT NOT NULL
    );

    CREATE TABLE ai_analyses (
        company_key TEXT NOT NULL,
        service     TEXT NOT NULL,
        analysis    TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        PRIMARY KEY (company_key, service)
    );

    CREATE TABLE proposals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        company_key TEXT NOT NULL,
        service     TEXT NOT NULL,
        variant     TEXT NOT NULL,                 -- short | business | fact | edited
        text        TEXT NOT NULL,
        source      TEXT NOT NULL,                 -- ai | template | user
        created_at  TEXT NOT NULL
    );
    CREATE INDEX idx_proposals_company ON proposals(user_id, company_key, variant, id);

    CREATE TABLE referrals (
        invited_id  INTEGER PRIMARY KEY,
        referrer_id INTEGER NOT NULL,
        bonus       INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL
    );
    CREATE INDEX idx_referrals_referrer ON referrals(referrer_id);

    CREATE TABLE geo_cache (
        query       TEXT PRIMARY KEY,
        data        TEXT NOT NULL,
        created_at  TEXT NOT NULL
    );

    CREATE TABLE fsm (
        key    TEXT PRIMARY KEY,
        state  TEXT,
        data   TEXT NOT NULL DEFAULT '{}'
    );
    """,
    # 2 — profile, auto-search, promo codes, trial, follow-ups, Stars subscriptions
    """
    CREATE TABLE profiles (
        user_id        INTEGER PRIMARY KEY,
        name           TEXT,
        specialization TEXT,
        portfolio      TEXT,
        price          TEXT,
        contact        TEXT,
        updated_at     TEXT NOT NULL
    );

    CREATE TABLE watches (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        params       TEXT NOT NULL,               -- JSON: service, niche, sub, cities, site, contacts
        title        TEXT NOT NULL,
        active       INTEGER NOT NULL DEFAULT 1,
        last_run_at  TEXT NOT NULL,
        created_at   TEXT NOT NULL
    );
    CREATE INDEX idx_watches_due ON watches(active, last_run_at);

    CREATE TABLE seen_companies (
        user_id     INTEGER NOT NULL,
        company_key TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        PRIMARY KEY (user_id, company_key)
    );

    CREATE TABLE promo_codes (
        code        TEXT PRIMARY KEY,
        plan        TEXT NOT NULL,
        days        INTEGER NOT NULL,
        max_uses    INTEGER NOT NULL,
        uses        INTEGER NOT NULL DEFAULT 0,
        active      INTEGER NOT NULL DEFAULT 1,
        created_by  INTEGER,
        created_at  TEXT NOT NULL
    );

    CREATE TABLE promo_redemptions (
        code        TEXT NOT NULL,
        user_id     INTEGER NOT NULL,
        created_at  TEXT NOT NULL,
        PRIMARY KEY (code, user_id)
    );

    ALTER TABLE users ADD COLUMN trial_used_at TEXT;
    ALTER TABLE leads ADD COLUMN followup_notified_at TEXT;
    ALTER TABLE payments ADD COLUMN recurring INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE subscriptions ADD COLUMN auto_renew INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE subscriptions ADD COLUMN star_charge_id TEXT;
    """,
    # 3 — message templates, notification settings, channel bonus
    """
    CREATE TABLE templates (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        title       TEXT NOT NULL,
        text        TEXT NOT NULL,
        created_at  TEXT NOT NULL
    );
    CREATE INDEX idx_templates_user ON templates(user_id);

    ALTER TABLE users ADD COLUMN notify_watches INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE users ADD COLUMN notify_followups INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE users ADD COLUMN channel_bonus_at TEXT;
    """,
    # 4 — second follow-up touch, weekly digest
    """
    ALTER TABLE leads ADD COLUMN followup2_at TEXT;
    ALTER TABLE users ADD COLUMN last_digest_at TEXT;
    """,
    # 5 — daily outreach goal
    """
    ALTER TABLE users ADD COLUMN daily_goal INTEGER NOT NULL DEFAULT 5;
    """,
    # 6 — partner program: bloggers get a share of payments made by users who came via their link
    """
    CREATE TABLE partners (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        code         TEXT NOT NULL UNIQUE,
        name         TEXT NOT NULL,
        telegram_id  INTEGER,                      -- the partner's own account: stats and payment notices
        percent      INTEGER NOT NULL,
        active       INTEGER NOT NULL DEFAULT 1,   -- 0: the link no longer brings new users
        created_at   TEXT NOT NULL
    );

    CREATE TABLE partner_users (
        user_id     INTEGER PRIMARY KEY,           -- a user belongs to one partner, the first one
        partner_id  INTEGER NOT NULL,
        created_at  TEXT NOT NULL
    );
    CREATE INDEX idx_partner_users ON partner_users(partner_id);

    CREATE TABLE partner_payouts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        partner_id  INTEGER NOT NULL,
        stars       INTEGER NOT NULL,
        created_at  TEXT NOT NULL
    );
    """,
]


@dataclass
class User:
    id: int
    username: str | None
    first_name: str | None
    city: str | None
    referred_by: int | None
    bonus_searches: int
    created_at: str
    last_seen_at: str
    trial_used_at: str | None = None
    notify_watches: int = 1
    notify_followups: int = 1
    channel_bonus_at: str | None = None
    last_digest_at: str | None = None
    daily_goal: int = 5


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._conn: aiosqlite.Connection | None = None
        # serialises read-check-write sequences (limits, payments) on the single connection
        self.lock = asyncio.Lock()

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, timeout=30)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            await self._conn.execute("PRAGMA journal_mode = WAL")
        await self.migrate()

    async def migrate(self) -> None:
        version = (await self.fetchone("PRAGMA user_version"))[0]
        if version == 0 and await self.fetchone(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"):
            # database from the pre-release prototype: keep it aside, start clean
            for (name,) in await self.fetchall("SELECT name FROM sqlite_master WHERE type='table' "
                                               "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'legacy_%'"):
                await self.conn.execute(f'ALTER TABLE "{name}" RENAME TO "legacy_{name}"')
            for (name,) in await self.fetchall("SELECT name FROM sqlite_master WHERE type='index' "
                                               "AND name NOT LIKE 'sqlite_%'"):
                await self.conn.execute(f'DROP INDEX IF EXISTS "{name}"')
            log.warning("prototype database detected: old tables renamed to legacy_*")
        for number, script in enumerate(MIGRATIONS, start=1):
            if number <= version:
                continue
            log.info("applying database migration %s", number)
            await self.conn.executescript("BEGIN;\n" + script + f"\nPRAGMA user_version = {number};\nCOMMIT;")
        await self.conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def fetchone(self, sql: str, *args: Any) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, *args: Any) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, args) as cur:
            return list(await cur.fetchall())

    async def execute(self, sql: str, *args: Any, commit: bool = True) -> int:
        """Run a statement; returns lastrowid (INSERT) or rowcount."""
        cur = await self.conn.execute(sql, args)
        if commit:
            await self.conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount

    async def executemany(self, sql: str, rows: list[tuple]) -> None:
        await self.conn.executemany(sql, rows)
        await self.conn.commit()

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            try:
                yield self
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise

    # --- users ---------------------------------------------------------------

    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None,
                          now: str) -> tuple[User, bool]:
        row = await self.fetchone("SELECT * FROM users WHERE id = ?", user_id)
        if row is None:
            await self.execute("INSERT OR IGNORE INTO users (id, username, first_name, created_at, last_seen_at) "
                               "VALUES (?, ?, ?, ?, ?)", user_id, username, first_name, now, now)
            return await self.get_user(user_id), True
        if (row["username"], row["first_name"]) != (username, first_name) or row["last_seen_at"][:15] != now[:15]:
            # last_seen is refreshed at most every ~10 minutes (prefix compare on ISO string)
            await self.execute("UPDATE users SET username = ?, first_name = ?, last_seen_at = ? WHERE id = ?",
                               username, first_name, now, user_id)
            row = await self.fetchone("SELECT * FROM users WHERE id = ?", user_id)
        return User(**dict(row)), False

    async def get_user(self, user_id: int) -> User | None:
        row = await self.fetchone("SELECT * FROM users WHERE id = ?", user_id)
        return User(**dict(row)) if row else None
