#!/usr/bin/env python3
"""App de finanças pessoais (uso único). Só biblioteca padrão do Python, SQLite local."""
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import projection as P  # noqa: E402
import seed as S  # noqa: E402

DATA_DIR = Path(os.environ.get("DATA_DIR", BASE / "data"))
PASSWORD = os.environ.get("APP_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8080"))
HOST = os.environ.get("HOST", "0.0.0.0")
BANCO_KEY = os.environ.get("BANCO_MCP_KEY", "")
BANCO_BASE = os.environ.get("BANCO_MCP_BASE", "https://api.mcp.ai/api/openfinance").rstrip("/")
TZ = timezone(timedelta(hours=float(os.environ.get("TZ_OFFSET_HOURS", "-3"))))
SESSION_SECONDS = 30 * 86400
STATIC_FILES = {"/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8")}
SECRET = b""

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recurring(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, amount REAL NOT NULL, card TEXT);
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
"""


def today():
    f = os.environ.get("FAKE_TODAY")
    return date.fromisoformat(f) if f else datetime.now(TZ).date()


# ---------- banco de dados ----------
@contextmanager
def db():
    conn = sqlite3.connect(DATA_DIR / "financas.db", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
        fresh = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
        if fresh:
            if os.environ.get("NO_SEED") == "1":
                for k, v in S.DEFAULT_SETTINGS.items():
                    conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, json.dumps(v)))
            else:
                S.seed(conn)


def load_secret():
    global SECRET
    path = DATA_DIR / "secret.key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        os.chmod(path, 0o600)
    SECRET = path.read_text().strip().encode()


def rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def get_cfg(conn):
    cfg = json.loads(json.dumps(S.DEFAULT_SETTINGS))
    for r in conn.execute("SELECT key, value FROM settings"):
        try:
            cfg[r["key"]] = json.loads(r["value"])
        except ValueError:
            pass
    return cfg


# ---------- validação ----------
def num(v):
    if isinstance(v, bool):
        raise ValueError("número inválido")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError("número inválido")
    if not math.isfinite(f) or abs(f) > 1e9:
        raise ValueError("número inválido")
    return round(f, 2)


def text(maxlen=80, required=True):
    def fn(v):
        s = str(v).strip() if v is not None else ""
        if required and not s:
            raise ValueError("texto vazio")
        if len(s) > maxlen:
            raise ValueError("texto muito longo")
        return s or None
    return fn


def posint(lo, hi):
    def fn(v):
        if isinstance(v, bool):
            raise ValueError("inteiro inválido")
        try:
            i = int(v)
        except (TypeError, ValueError):
            raise ValueError("inteiro inválido")
        if not lo <= i <= hi:
            raise ValueError("valor fora do limite")
        return i
    return fn


def boolint(v):
    return 1 if v in (True, 1, "1", "true") else 0


def iso_date(v):
    try:
        return date.fromisoformat(str(v)).isoformat()
    except ValueError:
        raise ValueError("data inválida")


def year_month(v):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(v)):
        raise ValueError("mês inválido (use AAAA-MM)")
    return str(v)


def str_list(v):
    if isinstance(v, str):
        v = [x for x in re.split(r"[,\n]", v)]
    if not isinstance(v, list) or len(v) > 100:
        raise ValueError("lista inválida")
    return [str(x).strip().lower()[:60] for x in v if str(x).strip()]


def money_dict(v):
    if not isinstance(v, dict) or len(v) > 20:
        raise ValueError("valores inválidos")
    return {str(k)[:30]: num(x) for k, x in v.items()}


SETTING_FIELDS = {
    "salario": num, "decimo1": num, "decimo2": num,
    "decimo1_mes": posint(1, 12), "decimo2_mes": posint(1, 12),
    "aumento_on": lambda v: bool(boolint(v)), "aumento_pct": num, "aumento_from": year_month,
    "teto_semanal": num, "caixa": num, "salario_data": iso_date,
    "ignore_terms": str_list, "pix_max": num, "ciclo_extra": money_dict,
}
RECURRING_SPEC = {"name": text(60), "amount": num, "card": text(30, False)}
INSTALLMENT_SPEC = {"name": text(60), "card": text(30), "amount": num,
                    "total": posint(1, 600), "paid_count": posint(0, 600)}
SPEND_SPEC = {"date": iso_date, "description": text(120), "amount": num,
              "category": text(30, False), "ignored": boolint}


def crud(conn, table, spec, body, optional=()):
    if body.get("delete"):
        conn.execute(f"DELETE FROM {table} WHERE id=?", (int(body["id"]),))
        return
    vals = {c: fn(body[c]) for c, fn in spec.items() if c in body}
    if body.get("id"):
        if vals:
            sets = ", ".join(f"{c}=?" for c in vals)
            conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*vals.values(), int(body["id"])))
        return
    for c in spec:
        if c not in vals and c not in optional:
            raise ValueError(f"campo obrigatório: {c}")
    cols = ", ".join(vals)
    qs = ", ".join("?" for _ in vals)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", tuple(vals.values()))


# ---------- resumo ----------
def month_panel(conn, cfg, ym, t):
    cur = P.ym_of(t)
    recurring = rows(conn, "SELECT * FROM recurring ORDER BY id")
    inst = rows(conn, "SELECT * FROM installments ORDER BY id")
    ov = {(r["card"], r["month"]): r for r in rows(conn, "SELECT * FROM overrides")}
    pays = {r["key"]: r for r in rows(conn, "SELECT * FROM payments WHERE month=?", (ym,))}
    items = []
    for r in recurring:
        if r["card"]:
            continue
        p = pays.get(f"rec:{r['id']}")
        has = bool(p and p["amount"] is not None)
        items.append({"key": f"rec:{r['id']}", "group": "fixa", "name": r["name"],
                      "amount": p["amount"] if has else r["amount"],
                      "estimated": not has, "paid": bool(p and p["paid"]), "parts": []})
    income_month = P.add_months(ym, -1)
    can_forecast = P.months_between(cur, income_month) >= 0
    for c in cfg["cartoes"]:
        p = pays.get(f"bill:{c}")
        has = bool(p and p["amount"] is not None)
        fc = P.card_forecast(c, income_month, cur, recurring, inst, ov, cfg["ciclo_extra"]) if can_forecast else None
        amount = p["amount"] if has else (fc["total"] if fc else None)
        items.append({"key": f"bill:{c}", "group": "fatura", "name": f"Fatura {c}",
                      "amount": amount, "estimated": not has, "paid": bool(p and p["paid"]),
                      "parts": (fc["parts"] if fc and not has else []),
                      "used": cfg.get("cartao_usado", {}).get(c)})
    falta = round(sum(i["amount"] for i in items if not i["paid"] and i["amount"] is not None), 2)
    pago = round(sum(i["amount"] for i in items if i["paid"] and i["amount"] is not None), 2)
    sem_valor = sum(1 for i in items if i["amount"] is None and not i["paid"])
    return {"ym": ym, "label": P.label(ym), "items": items, "falta": falta, "pago": pago,
            "sem_valor": sem_valor}


def week_info(conn, cfg, t):
    ws, we = P.week_range(t)
    spent = conn.execute("SELECT COALESCE(SUM(amount),0) FROM spends WHERE ignored=0 AND date BETWEEN ? AND ?",
                         (ws.isoformat(), we.isoformat())).fetchone()[0]
    since = cfg["salario_data"]
    since_total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM spends WHERE ignored=0 AND date>=?",
                               (since,)).fetchone()[0]
    lista = rows(conn, "SELECT * FROM spends WHERE date>=? ORDER BY date DESC, id DESC LIMIT 300", (since,))
    teto = cfg["teto_semanal"]
    return {"inicio": ws.isoformat(), "fim": we.isoformat(), "hoje": t.isoformat(),
            "gasto": round(spent, 2), "teto": teto, "restante": round(max(teto - spent, 0), 2),
            "estourou": round(max(spent - teto, 0), 2), "proxima": teto,
            "desde": since, "desde_total": round(since_total, 2), "lista": lista}


def build_summary(conn, month=None):
    t = today()
    cfg = get_cfg(conn)
    cur = P.ym_of(t)
    if month:
        month = year_month(month)
    else:
        month = cur
        p = month_panel(conn, cfg, cur, t)
        if p["items"] and p["falta"] == 0 and p["sem_valor"] == 0:
            month = P.add_months(cur, 1)
    recurring = rows(conn, "SELECT * FROM recurring ORDER BY id")
    inst = rows(conn, "SELECT * FROM installments ORDER BY id")
    ov_rows = rows(conn, "SELECT * FROM overrides ORDER BY month, card")
    ov = {(r["card"], r["month"]): r for r in ov_rows}
    parcelas = []
    for i in inst:
        rest = i["total"] - i["paid_count"]
        parcelas.append({**i, "restante": rest, "restante_valor": round(rest * i["amount"], 2),
                         "ultima_fatura": P.label(P.add_months(cur, rest)) if rest > 0 else None})
    return {
        "hoje": t.isoformat(), "mes": month_panel(conn, cfg, month, t),
        "semana": week_info(conn, cfg, t), "parcelas": parcelas,
        "parcelas_total_restante": round(sum(p["restante_valor"] for p in parcelas), 2),
        "recorrentes": recurring, "overrides": ov_rows,
        "proj": P.project(cfg, recurring, inst, ov, t),
        "config": {k: cfg[k] for k in list(SETTING_FIELDS) + ["cartoes", "last_sync", "caixa_banco", "cartao_usado"]},
        "sync_disponivel": bool(BANCO_KEY),
    }


# ---------- ações ----------
def act_settings(conn, body):
    for k, v in body.items():
        if k in SETTING_FIELDS:
            conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (k, json.dumps(SETTING_FIELDS[k](v))))
    return {"ok": True}


def act_payments(conn, body):
    month = year_month(body.get("month"))
    key = text(40)(body.get("key"))
    if not re.fullmatch(r"(rec:\d+|bill:[^:]{1,30})", key):
        raise ValueError("chave inválida")
    cur = conn.execute("SELECT * FROM payments WHERE month=? AND key=?", (month, key)).fetchone()
    paid = cur["paid"] if cur else 0
    amount = cur["amount"] if cur else None
    if "paid" in body:
        paid = boolint(body["paid"])
    if "amount" in body:
        amount = None if body["amount"] is None else num(body["amount"])
    conn.execute("INSERT INTO payments(month,key,paid,amount) VALUES(?,?,?,?) "
                 "ON CONFLICT(month,key) DO UPDATE SET paid=excluded.paid, amount=excluded.amount",
                 (month, key, paid, amount))
    return {"ok": True}


def act_overrides(conn, body):
    card = text(30)(body.get("card"))
    month = year_month(body.get("month"))
    if body.get("delete"):
        conn.execute("DELETE FROM overrides WHERE card=? AND month=?", (card, month))
    else:
        conn.execute("INSERT INTO overrides(card,month,amount,note) VALUES(?,?,?,?) "
                     "ON CONFLICT(card,month) DO UPDATE SET amount=excluded.amount, note=excluded.note",
                     (card, month, num(body.get("amount")), text(120, False)(body.get("note"))))
    return {"ok": True}


EXPORT_TABLES = {
    "recurring": ["id", "name", "amount", "card"],
    "installments": ["id", "name", "card", "amount", "total", "paid_count"],
    "payments": ["month", "key", "paid", "amount"],
    "overrides": ["card", "month", "amount", "note"],
    "spends": ["id", "date", "description", "amount", "category", "source", "ext_id", "ignored"],
}


def do_export(conn):
    out = {"versao": 1, "settings": get_cfg(conn)}
    for t, cols in EXPORT_TABLES.items():
        out[t] = rows(conn, f"SELECT {', '.join(cols)} FROM {t}")
    return out


def do_import(conn, body):
    if not isinstance(body, dict) or body.get("versao") != 1:
        raise ValueError("arquivo de backup inválido")
    clean = {}
    for k, fn in SETTING_FIELDS.items():
        if k in body.get("settings", {}):
            clean[k] = fn(body["settings"][k])
    specs = {
        "recurring": {"id": posint(1, 10**9), **RECURRING_SPEC},
        "installments": {"id": posint(1, 10**9), **INSTALLMENT_SPEC},
        "payments": {"month": year_month, "key": text(40), "paid": boolint, "amount": lambda v: None if v is None else num(v)},
        "overrides": {"card": text(30), "month": year_month, "amount": num, "note": text(120, False)},
        "spends": {"id": posint(1, 10**9), **SPEND_SPEC, "source": text(10), "ext_id": text(120, False)},
    }
    parsed = {}
    for t, spec in specs.items():
        lst = body.get(t, [])
        if not isinstance(lst, list) or len(lst) > 20000:
            raise ValueError(f"lista inválida: {t}")
        parsed[t] = [{c: fn(r.get(c)) if c in r else None for c, fn in spec.items()} for r in lst]
    for t in EXPORT_TABLES:
        conn.execute(f"DELETE FROM {t}")
    for k, v in clean.items():
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (k, json.dumps(v)))
    for t, lst in parsed.items():
        for r in lst:
            cols = ", ".join(r)
            conn.execute(f"INSERT INTO {t} ({cols}) VALUES ({', '.join('?' for _ in r)})", tuple(r.values()))
    return {"ok": True}


# ---------- sincronização com o Banco MCP (opcional) ----------
def banco_post(path, body):
    data = json.dumps(body).encode()
    for _ in range(5):
        req = urllib.request.Request(BANCO_BASE + path, data=data, method="POST",
                                     headers={"Authorization": "Bearer " + BANCO_KEY,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    wait = float(e.headers.get("Retry-After") or 2)
                except ValueError:
                    wait = 2
                time.sleep(min(wait, 10))
                continue
            raise RuntimeError("Banco MCP respondeu HTTP %d (%s)" % (e.code, path))
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise RuntimeError("Não consegui falar com o Banco MCP (%s)" % path)
        res = j.get("result", j) if isinstance(j, dict) else {}
        if isinstance(res, dict) and "Rate-limit" in str(res.get("warning", "")) and not res.get("results"):
            time.sleep(1.2)
            continue
        time.sleep(0.6)  # o provedor limita a ~2 requisições por segundo
        return res
    raise RuntimeError("Banco MCP: limite de requisições, tente de novo em instantes")


def local_date(s):
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.date().isoformat()
    return dt.astimezone(TZ).date().isoformat()


def to_spend(acct_type, x, ignore, pix_max):
    """Decide se uma transação do banco vira 'gasto livre'. Devolve (data, descrição, valor) ou None."""
    desc = str(x.get("description") or "").strip()[:120]
    low = desc.lower()
    if not desc or any(term in low for term in ignore):
        return None
    d = local_date(x.get("date"))
    if not d:
        return None
    try:
        amount = float(x.get("amount"))
    except (TypeError, ValueError):
        return None
    kind = x.get("type")
    if acct_type == "CREDIT":
        if kind == "DEBIT" and amount > 0:
            return d, desc, round(amount, 2)
        if kind == "CREDIT" and "estorno" in low and amount < 0:
            return d, desc, round(amount, 2)
        return None
    if acct_type == "BANK":
        if kind != "DEBIT" or x.get("operationType") != "PIX":
            return None
        v = abs(amount)
        if 0 < v <= pix_max:
            return d, desc, round(v, 2)
    return None


def do_sync(conn):
    if not BANCO_KEY:
        raise ValueError("BANCO_MCP_KEY não está configurada no servidor")
    cfg = get_cfg(conn)
    t = today()
    since = max(date.fromisoformat(cfg["salario_data"]), t - timedelta(days=45)).isoformat()
    acc = banco_post("/accounts/list", {})
    accounts = acc.get("results") or acc.get("accounts") or []
    caixa, used, added, adopted, seen = 0.0, {}, 0, 0, 0
    for a in accounts:
        aid = a.get("account_id") or a.get("id")
        typ = a.get("type")
        bank = str(a.get("bank") or a.get("name") or "?")
        if typ == "BANK":
            caixa += float(a.get("balance") or 0)
        elif typ == "CREDIT":
            cd = a.get("creditData") or {}
            used[bank] = round(float(cd.get("usedAmount", a.get("balance") or 0)), 2)
        else:
            continue
        tx = banco_post("/transactions/list", {"account_id": aid, "from": since, "to": t.isoformat(), "page_size": 200})
        for x in tx.get("results") or []:
            seen += 1
            sp = to_spend(typ, x, cfg["ignore_terms"], cfg["pix_max"])
            if not sp or not x.get("id"):
                continue
            ext = f"{aid}:{x['id']}"
            if conn.execute("SELECT 1 FROM spends WHERE ext_id=?", (ext,)).fetchone():
                continue
            # Se você já lançou isto à mão (mesma data e valor), o lançamento manual é "adotado":
            # ganha o id do banco e não vira duplicata no teto semanal.
            manual = conn.execute("SELECT id FROM spends WHERE source='manual' AND ext_id IS NULL "
                                  "AND date=? AND ABS(amount-?)<0.005 ORDER BY id LIMIT 1",
                                  (sp[0], sp[2])).fetchone()
            if manual:
                conn.execute("UPDATE spends SET ext_id=? WHERE id=?", (ext, manual["id"]))
                adopted += 1
                continue
            cur = conn.execute("INSERT OR IGNORE INTO spends(date,description,amount,category,source,ext_id) "
                               "VALUES(?,?,?,?,?,?)",
                               (sp[0], sp[1], sp[2], str(x.get("category") or "")[:30] or None, "banco", ext))
            added += cur.rowcount
    for k, v in (("caixa_banco", round(caixa, 2)), ("cartao_usado", used),
                 ("last_sync", datetime.now(TZ).strftime("%Y-%m-%d %H:%M"))):
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (k, json.dumps(v)))
    return {"ok": True, "novos": added, "ja_lancados": adopted, "vistos": seen, "caixa_banco": round(caixa, 2)}


# ---------- autenticação ----------
FAILS = {}


def make_token():
    exp = str(int(time.time()) + SESSION_SECONDS)
    return exp + "." + hmac.new(SECRET, exp.encode(), hashlib.sha256).hexdigest()


def verify_token(tok):
    if not tok or "." not in tok:
        return False
    exp, _, sig = tok.partition(".")
    good = hmac.new(SECRET, exp.encode(), hashlib.sha256).hexdigest()
    return exp.isdigit() and hmac.compare_digest(sig, good) and int(exp) > time.time()


def password_ok(pw):
    a = hashlib.sha256(str(pw).encode()).digest()
    b = hashlib.sha256(PASSWORD.encode()).digest()
    return hmac.compare_digest(a, b)


POST_ROUTES = {
    "/api/settings": act_settings,
    "/api/payments": act_payments,
    "/api/overrides": act_overrides,
    "/api/recurring": lambda c, b: crud(c, "recurring", RECURRING_SPEC, b, optional=("card",)) or {"ok": True},
    "/api/installments": lambda c, b: crud(c, "installments", INSTALLMENT_SPEC, b, optional=("paid_count",)) or {"ok": True},
    "/api/spends": lambda c, b: crud(c, "spends", SPEND_SPEC, b, optional=("category", "ignored")) or {"ok": True},
    "/api/sync": lambda c, b: do_sync(c),
    "/api/import": do_import,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "Financas"
    sys_version = ""

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- util
    def _headers(self, ctype, length, extra=None):
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                         "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                         "base-uri 'none'; form-action 'self'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _json(self, status, obj, extra=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        h = {"Cache-Control": "no-store"}
        h.update(extra or {})
        self._headers("application/json; charset=utf-8", len(body), h)
        self.wfile.write(body)

    def _authed(self):
        tok = None
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "session":
                tok = v
        return verify_token(tok)

    def _secure_cookie(self):
        return os.environ.get("COOKIE_SECURE") == "1" or self.headers.get("X-Forwarded-Proto") == "https"

    def _read_json(self, limit):
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ValueError("Content-Type deve ser application/json")
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("corpo muito grande")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    # -- GET
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path == "/health":
            body = b"ok"
            self.send_response(200)
            self._headers("text/plain", len(body), {"Cache-Control": "no-store"})
            self.wfile.write(body)
            return
        if path in STATIC_FILES:
            name, ctype = STATIC_FILES[path]
            body = (BASE / "static" / name).read_bytes()
            self.send_response(200)
            self._headers(ctype, len(body), {"Cache-Control": "no-cache"})
            self.wfile.write(body)
            return
        if not path.startswith("/api/"):
            return self._json(404, {"error": "não encontrado"})
        if not self._authed():
            return self._json(401, {"error": "login necessário"})
        try:
            with db() as conn:
                if path == "/api/summary":
                    q = parse_qs(url.query)
                    return self._json(200, build_summary(conn, (q.get("month") or [None])[0]))
                if path == "/api/export":
                    name = "financas-backup-%s.json" % today().isoformat()
                    return self._json(200, do_export(conn),
                                      {"Content-Disposition": 'attachment; filename="%s"' % name})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        return self._json(404, {"error": "não encontrado"})

    # -- POST
    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/login":
                ip = self.client_address[0]
                fails = FAILS.get(ip, (0, 0))
                if fails[0] >= 5 and fails[1] > time.time():
                    return self._json(429, {"error": "muitas tentativas, espere 1 minuto"})
                body = self._read_json(4096)
                if not password_ok(body.get("password", "")):
                    cnt = fails[0] + 1
                    FAILS[ip] = (cnt, time.time() + 60 if cnt >= 5 else 0)
                    time.sleep(0.5)
                    return self._json(401, {"error": "senha incorreta"})
                FAILS.pop(ip, None)
                cookie = "session=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=%d" % (make_token(), SESSION_SECONDS)
                if self._secure_cookie():
                    cookie += "; Secure"
                return self._json(200, {"ok": True}, {"Set-Cookie": cookie})
            if path == "/api/logout":
                return self._json(200, {"ok": True}, {"Set-Cookie": "session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
            if not self._authed():
                return self._json(401, {"error": "login necessário"})
            fn = POST_ROUTES.get(path)
            if not fn:
                return self._json(404, {"error": "não encontrado"})
            body = self._read_json(6_000_000 if path == "/api/import" else 200_000)
            if not isinstance(body, dict):
                raise ValueError("corpo inválido")
            with db() as conn:
                result = fn(conn, body)
            return self._json(200, result or {"ok": True})
        except (ValueError, TypeError, KeyError) as e:
            return self._json(400, {"error": str(e) or "pedido inválido"})
        except RuntimeError as e:
            return self._json(502, {"error": str(e)})
        except sqlite3.Error as e:
            sys.stderr.write("erro de banco: %s\n" % e)
            return self._json(500, {"error": "erro no banco de dados"})


def main():
    if not PASSWORD or len(PASSWORD) < 8:
        sys.exit("Defina APP_PASSWORD com pelo menos 8 caracteres (o app guarda dados financeiros).")
    init_db()
    load_secret()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("Finanças rodando em http://%s:%d (dados em %s)" % (HOST, PORT, DATA_DIR), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
