"""Sincronização com o Banco MCP (Open Finance) e automações.

A cada sincronização o app:
  1. lança os gastos do dia a dia (cartão e Pix pequenos), sem duplicar os manuais;
  2. detecta o salário (e o 13º) que caiu na conta;
  3. marca como pagas as faturas e contas fixas que aparecem no extrato;
  4. usa o valor real das faturas (fechadas e a aberta) no lugar da previsão;
  5. calcula o caixa a partir do saldo das contas.
Depois disso, `finance.advance_installments` avança as parcelas das faturas vencidas.
Limites do provedor: ~2 requisições/s; forçar atualização no banco no máximo 1x por hora.
"""
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

from . import config as C
from . import db
from . import finance
from . import projection as P
from .finance import last_payday


def banco_post(path, body):
    data = json.dumps(body).encode()
    for _ in range(5):
        req = urllib.request.Request(C.BANCO_BASE + path, data=data, method="POST",
                                     headers={"Authorization": "Bearer " + C.BANCO_KEY,
                                              "Content-Type": "application/json", "Accept": "application/json",
                                              "User-Agent": "financas-app/2 (+uso pessoal)"})
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
            raise RuntimeError(_explain(e, path))
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise RuntimeError("Não consegui falar com o Banco MCP (%s)" % path)
        res = j.get("result", j) if isinstance(j, dict) else {}
        if isinstance(res, dict) and "Rate-limit" in str(res.get("warning", "")) and not res.get("results"):
            time.sleep(1.2)
            continue
        time.sleep(0.6)  # o provedor limita a ~2 requisições por segundo
        return res
    raise RuntimeError("Banco MCP: limite de requisições, tente de novo em instantes")


HINTS = {
    "key_binding_mismatch": "a chave é temporária (de link de prompt) e foi bloqueada; gere uma chave permanente no painel do MCP.AI",
    "key_scope_mismatch": "a chave não tem acesso ao Banco MCP; gere uma chave com acesso a esse MCP",
    "forbidden": "a chave não tem acesso ao Banco MCP neste workspace",
    "install_required": "só o dono do workspace pode adicionar este MCP",
    "unauthorized": "chave inválida ou ausente",
    "insufficient_credits": "créditos do MCP.AI acabaram",
    "not_connected": "conecte suas credenciais no MCP.AI",
}


def _explain(e, path):
    code, msg = "", ""
    try:
        err = json.loads(e.read().decode("utf-8", "replace")).get("error") or {}
        if isinstance(err, dict):
            code, msg = str(err.get("code") or ""), str(err.get("message") or "")
        else:
            msg = str(err)
    except (ValueError, AttributeError, OSError):
        pass
    hint = HINTS.get(code) or msg[:160] or "sem detalhes"
    return "Banco MCP: HTTP %d%s — %s" % (e.code, f" ({code})" if code else "", hint)


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


CAT_MAP = (("taxi", "transporte"), ("ride", "transporte"), ("transport", "transporte"), ("fuel", "transporte"),
           ("gas station", "transporte"), ("parking", "transporte"), ("grocer", "mercado"), ("supermarket", "mercado"),
           ("eating", "comida"), ("restaurant", "comida"), ("food", "comida"), ("delivery", "comida"), ("bar", "comida"),
           ("house", "casa"), ("home", "casa"), ("utilit", "casa"), ("telecom", "casa"), ("shopping", "compras"),
           ("clothing", "compras"), ("electronic", "compras"), ("online", "compras"), ("entertain", "lazer"),
           ("leisure", "lazer"), ("travel", "lazer"), ("health", "saude"), ("pharma", "saude"), ("drug", "saude"))


def map_category(raw):
    low = str(raw or "").lower()
    for k, v in CAT_MAP:
        if k in low:
            return v
    return "outros" if low else None


def reconcile_refunds(conn):
    """Compra + estorno do mesmo valor (até 10 dias) viram 'estornado' e saem do teto."""
    n = 0
    refunds = db.rows(conn, "SELECT * FROM spends WHERE amount < 0 AND ignored = 0 AND LOWER(description) LIKE '%estorno%'")
    for r in refunds:
        merchant = r["description"].lower().replace("estorno de", "").replace("estorno", "").split("-")[0].strip()
        cands = db.rows(conn, "SELECT * FROM spends WHERE amount > 0 AND ignored = 0 AND ABS(amount + ?) < 0.005 "
                              "AND julianday(date) BETWEEN julianday(?) - 10 AND julianday(?) + 10 ORDER BY "
                              "ABS(julianday(date) - julianday(?))", (r["amount"], r["date"], r["date"], r["date"]))
        cands.sort(key=lambda c: 0 if merchant and merchant.split()[0] in c["description"].lower() else 1)
        if cands:
            c = cands[0]
            conn.execute("UPDATE spends SET ignored=1, category='estornado' WHERE id IN (?,?)", (r["id"], c["id"]))
            n += 1
    return n


AUTO_NOTE = "Banco: "  # overrides criados pela sincronização começam com isto (os seus nunca são sobrescritos)
EXCLUDE_INCOME = ("rendimento", "estorno", "resgate", "cashback", "reembolso")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def fetch(cfg, t):
    """Lê contas, transações e faturas. Devolve um dicionário com tudo (sem tocar no banco local)."""
    pay = last_payday(t, int(cfg.get("salario_dia") or 30))
    since = max(pay - timedelta(days=8), t - timedelta(days=45)).isoformat()
    acc = banco_post("/accounts/list", {})
    accounts = acc.get("results") or acc.get("accounts") or []
    out = {"accounts": [], "since": since, "items": set()}
    for a in accounts:
        typ = a.get("type")
        if typ not in ("BANK", "CREDIT"):
            continue
        aid = a.get("account_id") or a.get("id")
        if a.get("item_id"):
            out["items"].add(a["item_id"])
        tx = banco_post("/transactions/list", {"account_id": aid, "from": since, "to": t.isoformat(), "page_size": 500})
        entry = {"id": aid, "type": typ, "bank": str(a.get("bank") or a.get("name") or "?"),
                 "balance": _num(a.get("balance")), "credit": a.get("creditData") or {},
                 "tx": [x for x in (tx.get("results") or []) if not x.get("scheduled")], "bills": None}
        if typ == "CREDIT":
            try:
                entry["bills"] = banco_post("/credit-card-bills/list", {"account_id": aid, "include_open_bill": True, "page_size": 6})
            except RuntimeError as e:
                C.log("faturas %s: %s" % (entry["bank"], e))
        out["accounts"].append(entry)
    return out


def force_refresh(item_ids):
    """Pede ao provedor para buscar dados novos nos bancos (máx. 1x/hora por banco)."""
    if not item_ids:
        return {"ok": False}
    try:
        r = banco_post("/connections/sync", {"items": sorted(item_ids)[:50]})
    except RuntimeError as e:
        return {"ok": False, "erro": str(e)}
    errs = r.get("errors") or [] if isinstance(r, dict) else []
    return {"ok": True, "bloqueados": sum(1 for e in errs if e.get("status") == 409)}


def card_name(cfg, bank):
    b = bank.lower()
    for c in cfg["cartoes"]:
        if c.lower() in b or b in c.lower():
            return c
    return None


def set_paid(conn, month, key, amount):
    cur = conn.execute("SELECT paid, amount FROM payments WHERE month=? AND key=?", (month, key)).fetchone()
    if cur and cur["paid"]:
        return False
    conn.execute("INSERT INTO payments(month,key,paid,amount) VALUES(?,?,1,?) "
                 "ON CONFLICT(month,key) DO UPDATE SET paid=1, amount=COALESCE(payments.amount, excluded.amount)",
                 (month, key, round(amount, 2) if amount else None))
    return True


def apply(conn, data, t):
    """Aplica a leitura do banco. Devolve o resumo e a lista de eventos para notificar."""
    cfg = db.get_cfg(conn)
    auto = cfg.get("auto") or {}
    ign = cfg["ignore_terms"]
    events = []
    added = adopted = seen = 0
    caixa_banco, used = 0.0, {}

    # 1) gastos do dia a dia
    for a in data["accounts"]:
        if a["type"] == "BANK":
            caixa_banco += a["balance"]
        else:
            used[a["bank"]] = round(_num(a["credit"].get("usedAmount", a["balance"])), 2)
        for x in a["tx"]:
            seen += 1
            sp = to_spend(a["type"], x, ign, cfg["pix_max"])
            if not sp or not x.get("id"):
                continue
            ext = f"{a['id']}:{x['id']}"
            if conn.execute("SELECT 1 FROM spends WHERE ext_id=?", (ext,)).fetchone():
                continue
            manual = conn.execute("SELECT id FROM spends WHERE source='manual' AND ext_id IS NULL "
                                  "AND date=? AND ABS(amount-?)<0.005 ORDER BY id LIMIT 1", (sp[0], sp[2])).fetchone()
            if manual:
                conn.execute("UPDATE spends SET ext_id=? WHERE id=?", (ext, manual["id"]))
                adopted += 1
                continue
            cur = conn.execute("INSERT OR IGNORE INTO spends(date,description,amount,category,source,ext_id) "
                               "VALUES(?,?,?,?,?,?)",
                               (sp[0], sp[1], sp[2], map_category(x.get("category")), "banco", ext))
            added += cur.rowcount
    estornos = reconcile_refunds(conn)

    # 2) salário e 13º
    dia = int(cfg.get("salario_dia") or 30)
    pay = last_payday(t, dia)
    income_ym = P.ym_of(pay)
    lo, hi = (pay - timedelta(days=7)).isoformat(), (pay + timedelta(days=4)).isoformat()
    minimo = float(cfg["salario"]) * (float((cfg.get("salario_regra") or {}).get("minimo_pct", 60)) / 100)
    termos = [s.lower() for s in (cfg.get("salario_regra") or {}).get("termos", [])]
    incomes = []
    for a in data["accounts"]:
        if a["type"] != "BANK":
            continue
        for x in a["tx"]:
            d = local_date(x.get("date"))
            desc = str(x.get("description") or "")
            amt = _num(x.get("amount"))
            if x.get("type") != "CREDIT" or not d or not lo <= d <= hi or amt < minimo:
                continue
            low = desc.lower()
            if any(w in low for w in EXCLUDE_INCOME) or (termos and not any(w in low for w in termos)):
                continue
            incomes.append({"date": d, "desc": desc[:80], "amount": round(amt, 2), "id": f"{a['id']}:{x.get('id')}"})
    entradas = cfg.get("entradas") or {}
    if incomes:
        total = round(sum(i["amount"] for i in incomes), 2)
        prev = (entradas.get(income_ym) or {}).get("total")
        entradas[income_ym] = {"total": total, "itens": incomes[:6]}
        entradas = {k: entradas[k] for k in sorted(entradas)[-6:]}
        db.put_setting(conn, "entradas", entradas)
        if prev != total and (t - pay).days <= 3:  # não avisa salário antigo na primeira leitura
            events.append((f"salario:{income_ym}:{total}", "Salário recebido", f"Caiu {brl(total)} na conta.", "/#inicio"))
        sal = max(incomes, key=lambda i: i["amount"])["amount"]
        cfg_month = int(income_ym[5:])
        if auto.get("salario", True) and cfg_month not in (cfg["decimo1_mes"], cfg["decimo2_mes"]) and len(incomes) == 1:
            if abs(sal - cfg["salario"]) / max(cfg["salario"], 1) <= 0.6 and abs(sal - cfg["salario"]) >= 0.01:
                db.put_setting(conn, "salario", sal)

    # 3) e 4) faturas: valor real e pagamento
    for a in data["accounts"]:
        if a["type"] != "CREDIT":
            continue
        card = card_name(cfg, a["bank"])
        if not card:
            continue
        bills = a["bills"] or {}
        if auto.get("faturas", True):
            for b in bills.get("results") or []:
                due = str(b.get("dueDate") or "")[:10]
                if len(due) != 10 or due < (t - timedelta(days=40)).isoformat():
                    continue
                total = _num(b.get("totalAmount"))
                _auto_override(conn, card, P.add_months(due[:7], -1), total, "fatura fechada")
                if b.get("payment_status") == "PAID" and set_paid(conn, due[:7], f"bill:{card}", total):
                    events.append((f"paid:{due[:7]}:bill:{card}", f"Fatura {card} paga", f"{brl(total)} confirmado pelo banco.", "/#contas"))
            ob = bills.get("open_bill") or {}
            if ob.get("available") and ob.get("method") == "cycle_dates" and ob.get("due_date"):
                due = str(ob["due_date"])[:10]
                _auto_override(conn, card, P.add_months(due[:7], -1), _num(ob.get("total_amount")), "fatura aberta até agora")
        if auto.get("pagamentos", True):
            for x in a["tx"]:
                low = str(x.get("description") or "").lower()
                is_pay = x.get("operationType") == "PAGAMENTO_FATURA" or x.get("category") == "Credit card payment" \
                    or "pagamento recebido" in low or "pagamento de fatura" in low
                amt = abs(_num(x.get("amount")))
                d = local_date(x.get("date"))
                if not is_pay or x.get("type") != "CREDIT" or not d or amt <= 0:
                    continue
                m = _match_bill(conn, cfg, card, d, t)
                if m and set_paid(conn, m, f"bill:{card}", amt):
                    events.append((f"paid:{m}:bill:{card}", f"Fatura {card} paga", f"Pagamento de {brl(amt)} em {d[8:]}/{d[5:7]}.", "/#contas"))

    # 3) contas fixas pelo extrato (palavras de cada conta)
    if auto.get("pagamentos", True):
        recs = [r for r in db.rows(conn, "SELECT * FROM recurring WHERE card IS NULL OR card=''") if (r.get("match") or "").strip()]
        for a in data["accounts"]:
            if a["type"] != "BANK":
                continue
            for x in a["tx"]:
                if x.get("type") != "DEBIT":
                    continue
                text = (str(x.get("description") or "") + " " + str((x.get("paymentData") or {}).get("receiver") or "")).lower()
                amt = abs(_num(x.get("amount")))
                d = local_date(x.get("date"))
                for r in recs:
                    words = [w.strip().lower() for w in r["match"].split(",") if w.strip()]
                    if not d or not any(w in text for w in words):
                        continue
                    if r["amount"] and not 0.65 * r["amount"] <= amt <= 1.5 * r["amount"]:
                        continue
                    m = _match_fixed(conn, r, d)
                    if m and set_paid(conn, m, f"rec:{r['id']}", amt):
                        events.append((f"paid:{m}:rec:{r['id']}", f"{r['name']} paga", f"{brl(amt)} em {d[8:]}/{d[5:7]}.", "/#contas"))

    # 5) caixa = saldo das contas − contas do mês já cobertas por esse dinheiro e ainda não pagas
    db.put_setting(conn, "caixa_banco", round(caixa_banco, 2))
    db.put_setting(conn, "cartao_usado", used)
    if auto.get("caixa", True):
        cfg2 = db.get_cfg(conn)
        panel = finance.month_panel(conn, cfg2, P.ym_of(t), t)
        pend = sum(i["amount"] or 0 for i in panel["items"] if not i["paid"])
        db.put_setting(conn, "caixa", round(caixa_banco - pend, 2))
        db.put_setting(conn, "caixa_ref", P.ym_of(t))
    finance.advance_installments(conn, db.get_cfg(conn), t)
    db.put_setting(conn, "last_sync", C.now().strftime("%Y-%m-%d %H:%M"))
    return {"ok": True, "novos": added, "ja_lancados": adopted, "vistos": seen, "caixa_banco": round(caixa_banco, 2),
            "eventos": len(events), "estornos": estornos}, events


def _auto_override(conn, card, income_ym, total, label):
    cur = conn.execute("SELECT note FROM overrides WHERE card=? AND month=?", (card, income_ym)).fetchone()
    if cur and not str(cur["note"] or "").startswith(AUTO_NOTE):
        return  # valor informado por você: o banco não sobrescreve
    if total <= 0:
        return
    conn.execute("INSERT INTO overrides(card,month,amount,note) VALUES(?,?,?,?) "
                 "ON CONFLICT(card,month) DO UPDATE SET amount=excluded.amount, note=excluded.note",
                 (card, income_ym, round(total, 2), AUTO_NOTE + label))


def _match_bill(conn, cfg, card, d, t):
    """Qual fatura (mês de vencimento) um pagamento feito em `d` quita."""
    day = (cfg.get("cartao_venc") or {}).get(card)
    pd = date.fromisoformat(d)
    for k in range(-1, 3):
        ym = P.add_months(P.ym_of(pd), k)
        due = finance.clamp_day(int(ym[:4]), int(ym[5:]), int(day)) if day else finance.clamp_day(int(ym[:4]), int(ym[5:]), 10)
        if -25 <= (due - pd).days <= 7 and (due - pd).days >= -25:
            if (due - pd).days < -7:
                continue
            row = conn.execute("SELECT paid FROM payments WHERE month=? AND key=?", (ym, f"bill:{card}")).fetchone()
            if not (row and row["paid"]):
                return ym
    return None


def _match_fixed(conn, r, d):
    pd = date.fromisoformat(d)
    if r.get("due_day"):
        for k in (0, 1):
            ym = P.add_months(P.ym_of(pd), k)
            due = finance.clamp_day(int(ym[:4]), int(ym[5:]), int(r["due_day"]))
            if -10 <= (due - pd).days <= 25:
                row = conn.execute("SELECT paid FROM payments WHERE month=? AND key=?", (ym, f"rec:{r['id']}")).fetchone()
                if not (row and row["paid"]):
                    return ym
        return None
    return P.add_months(P.ym_of(pd), 1 if pd.day >= 20 else 0)


def brl(v):
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("−R$ " if v < 0 else "R$ ") + s


_sync_lock = threading.Lock()


def run(force=False):
    """Sincroniza agora. A leitura do banco (rede) acontece sem o SQLite travado;
    a gravação é uma transação curta. Com `force`, pede dados novos (máx. 1x/hora)."""
    if not C.BANCO_KEY:
        raise ValueError("BANCO_MCP_KEY não está configurada no servidor")
    if not _sync_lock.acquire(blocking=False):
        return {"ok": True, "ocupado": True}
    try:
        t = C.today()
        with db.connect() as conn:
            cfg = db.get_cfg(conn)
        data = fetch(cfg, t)
        with db.connect(write=True) as conn:
            result, events = apply(conn, data, t)
            if events:
                stamp = C.now().strftime("%Y-%m-%d %H:%M")
                pend = (db.get_cfg(conn).get("eventos_pendentes") or []) + [list(e) + [stamp] for e in events]
                db.put_setting(conn, "eventos_pendentes", pend[-20:])
        if force:
            last = cfg.get("last_force")
            if not last or (C.now() - datetime.fromisoformat(last).replace(tzinfo=C.TZ)) >= timedelta(minutes=61):
                result["atualizacao"] = force_refresh(data["items"])
                with db.connect() as conn:
                    db.put_setting(conn, "last_force", C.now().strftime("%Y-%m-%dT%H:%M"))
        return result
    finally:
        _sync_lock.release()
