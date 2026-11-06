"""Sincronização opcional com o Banco MCP (Open Finance)."""
import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

from . import config as C
from . import db
from .finance import last_payday


def banco_post(path, body):
    data = json.dumps(body).encode()
    for _ in range(5):
        req = urllib.request.Request(C.BANCO_BASE + path, data=data, method="POST",
                                     headers={"Authorization": "Bearer " + C.BANCO_KEY,
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
    return dt.astimezone(C.TZ).date().isoformat()


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


def sync(conn):
    if not C.BANCO_KEY:
        raise ValueError("BANCO_MCP_KEY não está configurada no servidor")
    cfg = db.get_cfg(conn)
    t = C.today()
    since = max(last_payday(t, int(cfg.get("salario_dia") or 30)), t - timedelta(days=45)).isoformat()
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
            # Lançamento manual com a mesma data e valor é "adotado": não vira duplicata.
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
    db.put_setting(conn, "caixa_banco", round(caixa, 2))
    db.put_setting(conn, "cartao_usado", used)
    db.put_setting(conn, "last_sync", C.now().strftime("%Y-%m-%d %H:%M"))
    return {"ok": True, "novos": added, "ja_lancados": adopted, "vistos": seen, "caixa_banco": round(caixa, 2)}


__all__ = ["sync", "to_spend", "local_date", "date"]
