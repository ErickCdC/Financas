"""SQLite: esquema, migrações leves e leitura das configurações."""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager

from . import config as C
from . import seed as S

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recurring(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, amount REAL NOT NULL, card TEXT,
  due_day INTEGER);
CREATE TABLE IF NOT EXISTS installments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, card TEXT NOT NULL,
  amount REAL NOT NULL, total INTEGER NOT NULL, paid_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS payments(
  month TEXT NOT NULL, key TEXT NOT NULL, paid INTEGER NOT NULL DEFAULT 0, amount REAL,
  PRIMARY KEY(month, key));
CREATE TABLE IF NOT EXISTS overrides(
  card TEXT NOT NULL, month TEXT NOT NULL, amount REAL NOT NULL, note TEXT,
  PRIMARY KEY(card, month));
CREATE TABLE IF NOT EXISTS spends(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, description TEXT NOT NULL,
  amount REAL NOT NULL, category TEXT, source TEXT NOT NULL DEFAULT 'manual',
  ext_id TEXT UNIQUE, ignored INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS spends_date ON spends(date);
CREATE TABLE IF NOT EXISTS push_subs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT NOT NULL UNIQUE, p256dh TEXT NOT NULL,
  auth TEXT NOT NULL, label TEXT, created TEXT NOT NULL, last_ok TEXT, fails INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS notif_log(key TEXT PRIMARY KEY, sent_at TEXT NOT NULL, title TEXT, body TEXT);
"""

# Chamado depois de qualquer escrita, para o backup automático (definido em backup.py).
on_write = []
_lock = threading.Lock()


def path():
    return C.DATA_DIR / "financas.db"


@contextmanager
def connect(write=False):
    conn = sqlite3.connect(path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if write:
        for fn in on_write:
            fn()


def migrate(conn):
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(recurring)")}
    if "due_day" not in cols:
        conn.execute("ALTER TABLE recurring ADD COLUMN due_day INTEGER")


def is_empty(conn):
    return conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0


def init(restore=None):
    """Cria o banco. Se estiver vazio, tenta `restore(conn)` (backup) e só então os dados iniciais."""
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _lock, connect() as conn:
        migrate(conn)
        if not is_empty(conn):
            return "existente"
        if restore and restore(conn):
            return "restaurado"
        if os.environ.get("NO_SEED") == "1":
            for k, v in S.DEFAULT_SETTINGS.items():
                put_setting(conn, k, v)
        else:
            S.seed(conn)
        return "novo"


def rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def get_cfg(conn):
    cfg = json.loads(json.dumps(S.DEFAULT_SETTINGS))
    for r in conn.execute("SELECT key, value FROM settings"):
        try:
            val = json.loads(r["value"])
        except ValueError:
            continue
        if isinstance(cfg.get(r["key"]), dict) and isinstance(val, dict) and r["key"] == "notif":
            cfg[r["key"]] = merge(cfg[r["key"]], val)
        else:
            cfg[r["key"]] = val
    return cfg


def merge(base, over):
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(base[k], v) if isinstance(base.get(k), dict) and isinstance(v, dict) else v
    return out


def put_setting(conn, key, value):
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
