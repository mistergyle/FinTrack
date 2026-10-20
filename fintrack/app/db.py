"""SQLite-Schicht: Schema, Verbindung, Seed-Daten."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    bank TEXT NOT NULL DEFAULT 'other',      -- ing | comdirect | other
    iban TEXT,
    blz TEXT,
    fints_url TEXT,
    fints_user TEXT,                         -- Zugangsnummer/Login (PIN wird NIE gespeichert)
    fints_tan_mechanism TEXT,
    last_sync TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('expense','income','transfer')),
    color TEXT NOT NULL DEFAULT '#9aa0a6',
    parent_id INTEGER REFERENCES categories(id) ON DELETE SET NULL   -- Unterkategorie (eine Ebene)
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    pattern TEXT NOT NULL,                   -- Teilstring (Wortanfang) oder "re:<regex>"
    direction TEXT NOT NULL DEFAULT 'any' CHECK (direction IN ('any','in','out')),
    priority INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user','default')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    booking_date TEXT NOT NULL,              -- ISO YYYY-MM-DD
    value_date TEXT,
    payee TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    booking_text TEXT NOT NULL DEFAULT '',
    counterparty_iban TEXT,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'EUR',
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    category_source TEXT NOT NULL DEFAULT 'rule' CHECK (category_source IN ('rule','manual')),
    hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(booking_date);
CREATE INDEX IF NOT EXISTS idx_tx_cat ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_tx_acc ON transactions(account_id);
CREATE TABLE IF NOT EXISTS budgets (
    category_id INTEGER PRIMARY KEY REFERENCES categories(id) ON DELETE CASCADE,
    monthly_cents INTEGER NOT NULL CHECK (monthly_cents >= 0)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def db_path() -> str:
    return os.environ.get("FINANZ_DB", "data/finanz.db")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    path = db_path()
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def session():
    """Eine Verbindung mit Commit/Rollback."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    from .categorizer import seed_defaults

    with session() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(categories)")}
        if "parent_id" not in cols:   # Migration älterer Datenbanken
            conn.execute("ALTER TABLE categories ADD COLUMN parent_id INTEGER REFERENCES categories(id) ON DELETE SET NULL")
        seed_defaults(conn)
        if get_setting(conn, "migr_retire_sonstige", "") != "1":     # einmalig
            from .category_tree import retire_category
            retire_category(conn)
            set_setting(conn, "migr_retire_sonstige", "1")


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
