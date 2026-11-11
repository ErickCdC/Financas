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
  due_day INTEGER, match TEXT);
CREATE TABLE IF NOT EXISTS installments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, card TEXT NOT NULL,
  amount REAL NOT NULL, total INTEGER NOT NULL, paid_count INTEGER NOT NULL DEFAULT 0, last_bill TEXT, original REAL);
CREATE TABLE IF NOT EXISTS payments(
  month TEXT NOT NULL, key TEXT NOT NULL, paid INTEGER NOT NULL DEFAULT 0, amount REAL,
  PRIMARY KEY(month, key));
CREATE TABLE IF NOT EXISTS overrides(
  card TEXT NOT NULL, month TEXT NOT NULL, amount REAL NOT NULL, note TEXT,
  PRIMARY KEY(card, month));
CREATE TABLE IF NOT EXISTS spends(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, description TEXT NOT NULL,
  amount REAL NOT NULL, category TEXT, source TEXT NOT NULL DEFAULT 'manual',
  ext_id TEXT UNIQUE, ignored INTEGER NOT NULL DEFAULT 0, card TEXT);
CREATE INDEX IF NOT EXISTS spends_date ON spends(date);
CREATE TABLE IF NOT EXISTS push_subs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT NOT NULL UNIQUE, p256dh TEXT NOT NULL,
  auth TEXT NOT NULL, label TEXT, created TEXT NOT NULL, last_ok TEXT, fails INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS planned(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL CHECK(kind IN ('saida','entrada')), name TEXT NOT NULL,
  amount REAL NOT NULL, month TEXT NOT NULL, day INTEGER, note TEXT, done INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS goals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, target REAL NOT NULL, saved REAL NOT NULL DEFAULT 0,
  kind TEXT, note TEXT, ordem INTEGER NOT NULL DEFAULT 0, share INTEGER NOT NULL DEFAULT 100);
CREATE TABLE IF NOT EXISTS notif_log(key TEXT PRIMARY KEY, sent_at TEXT NOT NULL, title TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS cobrancas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, pessoa TEXT NOT NULL, valor REAL, conta_id INTEGER,
  dia_ini INTEGER NOT NULL DEFAULT 1, dia_fim INTEGER NOT NULL DEFAULT 5, match TEXT, nota TEXT,
  ativo INTEGER NOT NULL DEFAULT 1, mes_seguinte INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS cobranca_mes(
  cobranca_id INTEGER NOT NULL, month TEXT NOT NULL, cobrado TEXT, recebido TEXT, valor REAL,
  PRIMARY KEY(cobranca_id, month));
-- arquivos enviados (bioimpedância, fotos): o conteúdo fica aqui e, com backup ligado, no GitHub
CREATE TABLE IF NOT EXISTS arquivos(
  id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, mime TEXT NOT NULL, tamanho INTEGER NOT NULL,
  hash TEXT NOT NULL, criado TEXT NOT NULL, dados BLOB, enviado INTEGER NOT NULL DEFAULT 0);
-- módulo Saúde
CREATE TABLE IF NOT EXISTS treinos(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, tipo TEXT NOT NULL, foco TEXT, minutos INTEGER,
  nota TEXT, arquivo_id INTEGER, criado TEXT);
CREATE INDEX IF NOT EXISTS treinos_date ON treinos(date);
-- grade de treino ABC (o que fazer em cada dia) e o que foi feito de fato em cada exercício
CREATE TABLE IF NOT EXISTS rotina(
  id INTEGER PRIMARY KEY AUTOINCREMENT, dia TEXT NOT NULL, ordem INTEGER NOT NULL DEFAULT 0,
  exercicio TEXT NOT NULL, series INTEGER, reps TEXT);
CREATE TABLE IF NOT EXISTS treino_series(
  id INTEGER PRIMARY KEY AUTOINCREMENT, treino_id INTEGER NOT NULL, ordem INTEGER NOT NULL DEFAULT 0,
  exercicio TEXT NOT NULL, carga REAL, reps TEXT);
CREATE INDEX IF NOT EXISTS treino_series_treino ON treino_series(treino_id);
CREATE TABLE IF NOT EXISTS avaliacoes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, tipo TEXT NOT NULL, arquivo_id INTEGER, nota TEXT);
CREATE TABLE IF NOT EXISTS avaliacao_valores(
  avaliacao_id INTEGER NOT NULL, campo TEXT NOT NULL, valor REAL NOT NULL, PRIMARY KEY(avaliacao_id, campo));
CREATE TABLE IF NOT EXISTS habitos(
  id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, unidade TEXT, meta REAL NOT NULL DEFAULT 1,
  passo REAL NOT NULL DEFAULT 1, ordem INTEGER NOT NULL DEFAULT 0, ativo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS habito_dia(
  habito_id INTEGER NOT NULL, date TEXT NOT NULL, valor REAL NOT NULL, PRIMARY KEY(habito_id, date));
-- módulo Agenda
CREATE TABLE IF NOT EXISTS notas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, pasta TEXT NOT NULL, titulo TEXT NOT NULL, texto TEXT, quando TEXT,
  avisos TEXT, feito INTEGER NOT NULL DEFAULT 0, fixada INTEGER NOT NULL DEFAULT 0, criado TEXT NOT NULL, atualizado TEXT);
CREATE TABLE IF NOT EXISTS tarefas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, pasta TEXT NOT NULL, titulo TEXT NOT NULL, prazo TEXT, nota TEXT,
  importante INTEGER NOT NULL DEFAULT 0, feito_em TEXT, criado TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS facul(
  id INTEGER PRIMARY KEY AUTOINCREMENT, curso TEXT NOT NULL, disciplina TEXT NOT NULL, tipo TEXT NOT NULL,
  titulo TEXT, data TEXT NOT NULL, nota REAL, nota_max REAL, feito INTEGER NOT NULL DEFAULT 0, obs TEXT);
"""

# Ajustes que são dicionários: o salvo é mesclado sobre o padrão (chaves novas ganham o valor padrão).
DICT_SETTINGS = ("notif", "auto", "sync_auto", "salario_regra", "fgts", "saude", "agenda")

# Chamado depois de qualquer escrita, para o backup automático (definido em backup.py).
on_write = []
_lock = threading.Lock()


def path():
    return C.DATA_DIR / "financas.db"


@contextmanager
def connect(write=False):
    conn = sqlite3.connect(path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
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
    if "match" not in cols:
        conn.execute("ALTER TABLE recurring ADD COLUMN match TEXT")
    scols = {r[1] for r in conn.execute("PRAGMA table_info(spends)")}
    if "card" not in scols:
        conn.execute("ALTER TABLE spends ADD COLUMN card TEXT")
    icols = {r[1] for r in conn.execute("PRAGMA table_info(installments)")}
    if "last_bill" not in icols:
        conn.execute("ALTER TABLE installments ADD COLUMN last_bill TEXT")
    if "original" not in icols:
        conn.execute("ALTER TABLE installments ADD COLUMN original REAL")
    ccols = {r[1] for r in conn.execute("PRAGMA table_info(cobrancas)")}
    if "mes_seguinte" not in ccols:
        conn.execute("ALTER TABLE cobrancas ADD COLUMN mes_seguinte INTEGER NOT NULL DEFAULT 0")
    tcols = {r[1] for r in conn.execute("PRAGMA table_info(treinos)")}
    if "dia" not in tcols:  # letra da rotina (A/B/C) quando o treino segue a grade
        conn.execute("ALTER TABLE treinos ADD COLUMN dia TEXT")


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
        if isinstance(cfg.get(r["key"]), dict) and isinstance(val, dict) and r["key"] in DICT_SETTINGS:
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
