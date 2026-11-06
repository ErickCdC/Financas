"""Servidor HTTP: arquivos do app, API JSON, login e segurança."""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from . import bank, data, db, finance, notify, push
from . import config as C
from . import validate as V
from .backup import BACKUP

SECRET = C.derive("session")
FAILS = {}
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/logo.svg": ("logo.svg", "image/svg+xml"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/icons/icon-192.png": ("icons/icon-192.png", "image/png"),
    "/icons/icon-512.png": ("icons/icon-512.png", "image/png"),
    "/icons/maskable-512.png": ("icons/maskable-512.png", "image/png"),
    "/icons/apple-touch-icon.png": ("icons/apple-touch-icon.png", "image/png"),
    "/icons/badge-96.png": ("icons/badge-96.png", "image/png"),
}
CSP = ("default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


# ---------- sessão ----------
def make_token():
    exp = str(int(time.time()) + C.SESSION_SECONDS)
    return exp + "." + hmac.new(SECRET, exp.encode(), hashlib.sha256).hexdigest()


def verify_token(tok):
    if not tok or "." not in tok:
        return False
    exp, _, sig = tok.partition(".")
    good = hmac.new(SECRET, exp.encode(), hashlib.sha256).hexdigest()
    return exp.isdigit() and hmac.compare_digest(sig, good) and int(exp) > time.time()


def password_ok(pw):
    a = hashlib.sha256(str(pw).encode()).digest()
    b = hashlib.sha256(C.PASSWORD.encode()).digest()
    return hmac.compare_digest(a, b)


# ---------- ações ----------
def crud(conn, table, spec, body, optional=()):
    if body.get("delete"):
        conn.execute(f"DELETE FROM {table} WHERE id=?", (V.posint(1, 10**12)(body.get("id")),))
        return {"ok": True}
    vals = {c: fn(body[c]) for c, fn in spec.items() if c in body}
    if body.get("id"):
        if vals:
            sets = ", ".join(f"{c}=?" for c in vals)
            conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*vals.values(), V.posint(1, 10**12)(body["id"])))
        return {"ok": True}
    for c in spec:
        if c not in vals and c not in optional:
            raise ValueError(f"campo obrigatório: {c}")
    cols = ", ".join(vals)
    cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' for _ in vals)})", tuple(vals.values()))
    return {"ok": True, "id": cur.lastrowid}


def act_settings(conn, body):
    for k, v in body.items():
        if k not in data.SETTING_FIELDS:
            continue
        val = data.SETTING_FIELDS[k](v)
        if k == "notif":
            val = db.merge(db.get_cfg(conn)["notif"], val)
        db.put_setting(conn, k, val)
        if k == "caixa":
            db.put_setting(conn, "caixa_ref", finance.P.ym_of(C.today()))
    return {"ok": True}


def act_payments(conn, body):
    month = V.year_month(body.get("month"))
    key = V.text(40)(body.get("key"))
    if not re.fullmatch(r"(rec:\d+|bill:[^:]{1,30})", key):
        raise ValueError("chave inválida")
    cur = conn.execute("SELECT * FROM payments WHERE month=? AND key=?", (month, key)).fetchone()
    paid = cur["paid"] if cur else 0
    amount = cur["amount"] if cur else None
    if "paid" in body:
        paid = V.boolint(body["paid"])
    if "amount" in body:
        amount = None if body["amount"] in (None, "") else V.num(body["amount"])
    conn.execute("INSERT INTO payments(month,key,paid,amount) VALUES(?,?,?,?) "
                 "ON CONFLICT(month,key) DO UPDATE SET paid=excluded.paid, amount=excluded.amount",
                 (month, key, paid, amount))
    return {"ok": True}


def act_overrides(conn, body):
    card = V.text(30)(body.get("card"))
    month = V.year_month(body.get("month"))
    if body.get("delete"):
        conn.execute("DELETE FROM overrides WHERE card=? AND month=?", (card, month))
    else:
        conn.execute("INSERT INTO overrides(card,month,amount,note) VALUES(?,?,?,?) "
                     "ON CONFLICT(card,month) DO UPDATE SET amount=excluded.amount, note=excluded.note",
                     (card, month, V.num(body.get("amount")), V.text(120, False)(body.get("note"))))
    return {"ok": True}


def act_spends(conn, body):
    r = crud(conn, "spends", data.SPEND_SPEC, body, optional=("category", "ignored"))
    # Confere o teto logo depois de um lançamento (sem travar a resposta).
    threading.Thread(target=_safe_tick, daemon=True).start()
    return r


def _safe_tick():
    time.sleep(1)
    try:
        notify.tick()
    except Exception as e:
        C.log("tick após lançamento: %r" % e)


def act_subscribe(conn, body):
    sub = body.get("subscription") or {}
    endpoint = V.text(1000)(sub.get("endpoint"))
    keys = sub.get("keys") or {}
    if not push.endpoint_allowed(endpoint):
        raise ValueError("serviço de notificação não reconhecido")
    p256dh, auth = V.text(200)(keys.get("p256dh")), V.text(100)(keys.get("auth"))
    if len(push.unb64u(p256dh)) != 65 or len(push.unb64u(auth)) < 16:
        raise ValueError("chaves de notificação inválidas")
    label = V.text(80, False)(body.get("label")) or "Aparelho"
    conn.execute("INSERT INTO push_subs(endpoint,p256dh,auth,label,created) VALUES(?,?,?,?,?) "
                 "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth, "
                 "label=excluded.label, fails=0",
                 (endpoint, p256dh, auth, label, C.now().strftime("%Y-%m-%d %H:%M")))
    return {"ok": True}


def act_unsubscribe(conn, body):
    if body.get("id"):
        conn.execute("DELETE FROM push_subs WHERE id=?", (V.posint(1, 10**9)(body["id"]),))
    else:
        conn.execute("DELETE FROM push_subs WHERE endpoint=?", (V.text(1000)(body.get("endpoint")),))
    return {"ok": True}


POST_ROUTES = {
    "/api/settings": act_settings,
    "/api/payments": act_payments,
    "/api/overrides": act_overrides,
    "/api/recurring": lambda c, b: crud(c, "recurring", data.RECURRING_SPEC, b, optional=("card", "due_day")),
    "/api/installments": lambda c, b: crud(c, "installments", data.INSTALLMENT_SPEC, b, optional=("paid_count",)),
    "/api/spends": act_spends,
    "/api/sync": lambda c, b: bank.sync(c),
    "/api/import": data.restore,
    "/api/push/subscribe": act_subscribe,
    "/api/push/unsubscribe": act_unsubscribe,
    "/api/push/test": lambda c, b: notify.test(c),
}


def summary_extra():
    return {"sync_disponivel": bool(C.BANCO_KEY), "backup": BACKUP.status(),
            "agendador": bool(C.CRON_TOKEN), "push_key": push.vapid_public_b64()}


class Handler(BaseHTTPRequestHandler):
    server_version = "Financas"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        C.log("%s - %s" % (self.address_string(), fmt % args))

    def _send(self, status, body, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", CSP)
        if self._secure():
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj, extra=None):
        h = {"Cache-Control": "no-store"}
        h.update(extra or {})
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", h)

    def _secure(self):
        return C.PUBLIC_URL.startswith("https://") or self.headers.get("X-Forwarded-Proto") == "https" or \
            os.environ.get("COOKIE_SECURE") == "1"

    def _authed(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "session" and verify_token(v):
                return True
        return False

    def _read_json(self, limit):
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ValueError("Content-Type deve ser application/json")
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("corpo muito grande")
        raw = self.rfile.read(n) if n else b"{}"
        body = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(body, dict):
            raise ValueError("corpo inválido")
        return body

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path == "/health":
            return self._send(200, b"ok", "text/plain", {"Cache-Control": "no-store"})
        if path in STATIC:
            name, ctype = STATIC[path]
            f = C.STATIC / name
            if not f.is_file():
                return self._json(404, {"error": "não encontrado"})
            extra = {"Cache-Control": "no-cache"}
            if path == "/sw.js":
                extra["Service-Worker-Allowed"] = "/"
            elif path.startswith("/icons/") or path.endswith(".svg"):
                extra["Cache-Control"] = "public, max-age=604800"
            return self._send(200, f.read_bytes(), ctype, extra)
        if not path.startswith("/api/"):
            return self._json(404, {"error": "não encontrado"})
        if not self._authed():
            return self._json(401, {"error": "login necessário"})
        try:
            with db.connect() as conn:
                if path == "/api/summary":
                    month = (parse_qs(url.query).get("month") or [None])[0]
                    return self._json(200, finance.summary(conn, V.year_month(month) if month else None, summary_extra()))
                if path == "/api/export":
                    name = "financas-backup-%s.json" % C.today().isoformat()
                    return self._json(200, data.export(conn), {"Content-Disposition": 'attachment; filename="%s"' % name})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        return self._json(404, {"error": "não encontrado"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/login":
                return self._login()
            if path == "/api/logout":
                return self._json(200, {"ok": True}, {"Set-Cookie": "session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
            if path == "/api/cron":
                tok = self.headers.get("X-Cron-Token") or ""
                if not C.CRON_TOKEN or not hmac.compare_digest(tok.encode(), C.CRON_TOKEN.encode()):
                    time.sleep(0.5)
                    return self._json(401, {"error": "token inválido"})
                self._read_json(4096)
                r = notify.tick()
                if BACKUP.enabled and BACKUP.blocked:
                    BACKUP.retry_restore()
                return self._json(200, r)
            if not self._authed():
                return self._json(401, {"error": "login necessário"})
            if path == "/api/notify/run":
                self._read_json(4096)
                return self._json(200, notify.tick())
            if path == "/api/backup/now":
                self._read_json(4096)
                if not BACKUP.enabled:
                    raise ValueError("backup automático não configurado (GITHUB_TOKEN e GITHUB_REPO)")
                ok = BACKUP.flush()
                return self._json(200 if ok else 502, {"ok": ok, **BACKUP.status()})
            fn = POST_ROUTES.get(path)
            if not fn:
                return self._json(404, {"error": "não encontrado"})
            body = self._read_json(8_000_000 if path == "/api/import" else 200_000)
            with db.connect(write=True) as conn:
                result = fn(conn, body)
            return self._json(200, result or {"ok": True})
        except (ValueError, TypeError, KeyError) as e:
            return self._json(400, {"error": str(e) or "pedido inválido"})
        except RuntimeError as e:
            return self._json(502, {"error": str(e)})
        except sqlite3.Error as e:
            C.log("erro de banco: %s" % e)
            return self._json(500, {"error": "erro no banco de dados"})

    def _login(self):
        ip = self.client_address[0]
        now = time.time()
        for k in (ip, "*"):  # por IP e global (o proxy pode esconder o IP real)
            cnt, until = FAILS.get(k, (0, 0))
            limit = 5 if k == ip else 20
            if cnt >= limit and until > now:
                return self._json(429, {"error": "muitas tentativas, espere 1 minuto"})
        body = self._read_json(4096)
        if not password_ok(body.get("password", "")):
            for k, limit in ((ip, 5), ("*", 20)):
                cnt, until = FAILS.get(k, (0, 0))
                cnt = 1 if until and until <= now else cnt + 1
                FAILS[k] = (cnt, now + 60 if cnt >= limit else 0)
            time.sleep(0.5)
            return self._json(401, {"error": "senha incorreta"})
        FAILS.pop(ip, None)
        cookie = "session=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=%d" % (make_token(), C.SESSION_SECONDS)
        if self._secure():
            cookie += "; Secure"
        return self._json(200, {"ok": True}, {"Set-Cookie": cookie})
