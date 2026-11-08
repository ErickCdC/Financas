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
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

from . import config as C
from . import db
from . import finance
from . import projection as P
from .finance import last_payday, next_payday


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


PARC_RE = re.compile(r"(parc\w*\s*\d{1,2}\s*/\s*\d{1,2})|(\b\d{1,2}/\d{1,2}\b\s*$)", re.I)


def is_installment(x):
    meta = x.get("creditCardMetadata") or {}
    try:
        if int(meta.get("totalInstallments") or 0) > 1:
            return True
    except (TypeError, ValueError):
        pass
    return bool(PARC_RE.search(str(x.get("description") or "")))


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
    squash = re.sub(r"\s+", "", low)  # o PicPay escreve "ERICKCAMARA", sem espaço
    if not desc or any(term in low or (len(term.replace(" ", "")) >= 6 and term.replace(" ", "") in squash)
                       for term in ignore):
        return None
    d = local_date(x.get("date"))
    if not d:
        return None
    try:
        amount = float(x.get("amount"))
    except (TypeError, ValueError):
        return None
    kind = x.get("type")
    if acct_type == "CREDIT" and is_installment(x):
        return None
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


CARD_TRANSFER_CATS = ("transfers", "same person transfer", "transfer - pix", "transfer - ted")


def card_transfer(acct_type, x, pix_max):
    """Pix/transferência grande paga com o cartão (ex.: Pix no crédito de R$ 1.235,88 em 31/07/26).
    Não é gasto do dia a dia (o dinheiro foi para outra conta), mas entra na fatura."""
    if acct_type != "CREDIT" or x.get("type") != "DEBIT":
        return False
    return str(x.get("category") or "").lower() in CARD_TRANSFER_CATS and _num(x.get("amount")) > pix_max


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
            conn.execute("UPDATE spends SET date=? WHERE id=?", (c["date"], r["id"]))  # estorno no dia da compra
            n += 1
    return n


def _desc_key(desc):
    """Descrição sem cidade/país e espaços repetidos: 'IFD*IFOOD CLUB   .Osasco BRA' -> 'ifd*ifood club'."""
    return re.sub(r"\s+", " ", str(desc or "").lower().split(" .")[0]).strip()[:28]


def ignore_subscription_charges(conn):
    """A cobrança de uma assinatura no cartão já está na previsão da fatura: não conta de novo como gasto.

    Só o valor não basta (três lavagens de R$ 19,90 em 24/10/26 viraram "assinatura"). Vale a cobrança que
    tem uma das palavras da assinatura; sem palavras, a que se repete com a mesma descrição em meses
    diferentes, no máximo uma por mês. O que foi marcado antes e não passa mais volta a contar."""
    keep, cands = set(), {}
    for r in db.rows(conn, "SELECT amount, card, match FROM recurring WHERE card IS NOT NULL AND card != ''"):
        words = [w.strip().lower() for w in (r.get("match") or "").split(",") if w.strip()]
        rows = db.rows(conn, "SELECT id, date, description, ignored, category FROM spends WHERE card=? "
                             "AND source='banco' AND ABS(amount - ?) < 0.02 AND (ignored=0 OR category='assinatura') "
                             "ORDER BY date, id", (r["card"], r["amount"]))
        for c in rows:
            cands[c["id"]] = c
        if words:
            keep |= {c["id"] for c in rows if any(w in c["description"].lower() for w in words)}
            continue
        by_desc = {}
        for c in rows:
            by_desc.setdefault(_desc_key(c["description"]), []).append(c)
        for lst in by_desc.values():
            if len({c["date"][:7] for c in lst}) < 2:
                continue
            months = set()
            for c in lst:
                if c["date"][:7] not in months:
                    months.add(c["date"][:7])
                    keep.add(c["id"])
    n = 0
    for cid, c in cands.items():
        if cid in keep and not c["ignored"]:
            conn.execute("UPDATE spends SET ignored=1, category='assinatura' WHERE id=?", (cid,))
            n += 1
        elif cid not in keep and c["ignored"] and c["category"] == "assinatura":
            conn.execute("UPDATE spends SET ignored=0, category=NULL WHERE id=?", (cid,))
    # sobra de regras antigas: marcadas como assinatura sem nenhuma assinatura daquele valor/cartão
    for c in db.rows(conn, "SELECT id FROM spends WHERE category='assinatura' AND ignored=1 AND source='banco'"):
        if c["id"] not in cands:
            conn.execute("UPDATE spends SET ignored=0, category=NULL WHERE id=?", (c["id"],))
    return n


AUTO_NOTE = "Banco: "  # overrides criados pela sincronização começam com isto (os seus nunca são sobrescritos)
EXCLUDE_INCOME = ("rendimento", "estorno", "resgate", "cashback", "reembolso")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# Progresso da sincronização em andamento (lido pela barra de status do app).
PROGRESS = {"rodando": False, "etapa": "", "feito": 0, "total": 1, "inicio": None, "fim": None,
            "origem": None, "resultado": None, "erro": None}


def _step(label, done=None, total=None):
    PROGRESS["etapa"] = label
    if total is not None:
        PROGRESS["total"] = max(total, 1)
    PROGRESS["feito"] = PROGRESS["feito"] + 1 if done is None else done


def progress():
    p = dict(PROGRESS)
    p["pct"] = round(min(p["feito"] / max(p["total"], 1), 1) * 100)
    return p


def fetch(cfg, t):
    """Lê contas, transações e faturas. Devolve um dicionário com tudo (sem tocar no banco local)."""
    pay = last_payday(t, cfg)
    # 16 dias antes do último salário: pega o 13º, que cai antes do salário de dezembro
    since = max(pay - timedelta(days=16), t - timedelta(days=45)).isoformat()
    _step("Conectando ao banco", done=0, total=8)
    acc = banco_post("/accounts/list", {})
    accounts = [a for a in (acc.get("results") or acc.get("accounts") or []) if a.get("type") in ("BANK", "CREDIT")]
    n_credit = sum(1 for a in accounts if a.get("type") == "CREDIT")
    _step("Contas encontradas", done=1, total=1 + len(accounts) + n_credit + 2)
    out = {"accounts": [], "since": since, "items": set()}
    for a in accounts:
        typ = a.get("type")
        aid = a.get("account_id") or a.get("id")
        if a.get("item_id"):
            out["items"].add(a["item_id"])
        nome = f"{a.get('bank') or a.get('name') or '?'} {'cartão' if typ == 'CREDIT' else 'conta'}"
        PROGRESS["etapa"] = f"Lendo {nome}"
        tx = banco_post("/transactions/list", {"account_id": aid, "from": since, "to": t.isoformat(), "page_size": 500})
        entry = {"id": aid, "type": typ, "bank": str(a.get("bank") or a.get("name") or "?"),
                 "balance": _num(a.get("balance")), "credit": a.get("creditData") or {},
                 "tx": [x for x in (tx.get("results") or []) if not x.get("scheduled")], "bills": None}
        _step(f"Lendo {nome}")
        if typ == "CREDIT":
            PROGRESS["etapa"] = f"Faturas {entry['bank']}"
            try:
                entry["bills"] = banco_post("/credit-card-bills/list", {"account_id": aid, "include_open_bill": True, "page_size": 6})
            except RuntimeError as e:
                C.log("faturas %s: %s" % (entry["bank"], e))
            _step(f"Faturas {entry['bank']}")
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
                # categoria apagada (ex.: deixou de ser tratada como assinatura): volta a do banco
                conn.execute("UPDATE spends SET category=? WHERE ext_id=? AND category IS NULL",
                             (map_category(x.get("category")), ext))
                continue
            cat, ign_flag = map_category(x.get("category")), 0
            if card_transfer(a["type"], x, cfg["pix_max"]):
                cat, ign_flag = "pix_credito", 1
            manual = conn.execute("SELECT id FROM spends WHERE source='manual' AND ext_id IS NULL "
                                  "AND date=? AND ABS(amount-?)<0.005 ORDER BY id LIMIT 1", (sp[0], sp[2])).fetchone()
            if manual:
                conn.execute("UPDATE spends SET ext_id=?, card=COALESCE(card, ?) WHERE id=?",
                             (ext, card_name(cfg, a["bank"]) if a["type"] == "CREDIT" else None, manual["id"]))
                adopted += 1
                continue
            cur = conn.execute("INSERT OR IGNORE INTO spends(date,description,amount,category,source,ext_id,card,ignored) "
                               "VALUES(?,?,?,?,?,?,?,?)",
                               (sp[0], sp[1], sp[2], cat, "banco", ext,
                                card_name(cfg, a["bank"]) if a["type"] == "CREDIT" else None, ign_flag))
            added += cur.rowcount
    estornos = reconcile_refunds(conn)

    # 2) salário e 13º
    pay = last_payday(t, cfg)
    income_ym = P.ym_of(pay)
    decimo_meses = (int(cfg.get("decimo1_mes") or 11), int(cfg.get("decimo2_mes") or 12))
    regra = cfg.get("salario_regra") or {}
    minimo = float(cfg["salario"]) * (float(regra.get("minimo_pct", 50)) / 100)
    termos = [s.lower() for s in regra.get("termos", [])]
    # Janelas: em volta do último salário e, em mês de 13º, os dias antes do próximo salário
    # (em 2025 a 2ª parcela caiu em 19/12, 11 dias antes do salário de 30/12).
    janelas = [(income_ym, pay - timedelta(days=15 if pay.month in decimo_meses else 7), pay + timedelta(days=4),
                pay.month in decimo_meses)]
    nxt = next_payday(t + timedelta(days=1), cfg)
    if nxt.month in decimo_meses and nxt - timedelta(days=15) <= t:
        janelas.append((P.ym_of(nxt), nxt - timedelta(days=15), t, True))
    entradas = cfg.get("entradas") or {}
    incomes = []
    for ym_j, lo_d, hi_d, decimo in janelas:
        lo, hi = lo_d.isoformat(), hi_d.isoformat()
        achados = []
        for a in data["accounts"]:
            if a["type"] != "BANK":
                continue
            for x in a["tx"]:
                d = local_date(x.get("date"))
                desc = str(x.get("description") or "")
                amt = _num(x.get("amount"))
                if x.get("type") != "CREDIT" or not d or not lo <= d <= hi:
                    continue
                low = desc.lower()
                if any(w in low for w in EXCLUDE_INCOME) or (termos and not any(w in low for w in termos)):
                    continue
                proprio = str(x.get("category") or "").lower() in ("same person transfer", "salary", "salário")
                if amt < minimo and not (decimo and proprio and amt >= 0.25 * float(cfg["salario"])):
                    continue
                achados.append({"date": d, "desc": desc[:80], "amount": round(amt, 2), "id": f"{a['id']}:{x.get('id')}"})
        if not achados:
            continue
        total = round(sum(i["amount"] for i in achados), 2)
        prev = (entradas.get(ym_j) or {}).get("total")
        entradas[ym_j] = {"total": total, "itens": achados[:6]}
        if ym_j == income_ym:
            incomes = achados
            if prev != total and (t - pay).days <= 3:  # não avisa salário antigo na primeira leitura
                events.append((f"salario:{income_ym}:{total}", "Salário recebido", f"Caiu {brl(total)} na conta.", "/#inicio"))
        elif prev != total:
            events.append((f"decimo:{ym_j}:{total}", "13º recebido", f"Caiu {brl(total)} na conta.", "/#inicio"))
    if entradas:
        db.put_setting(conn, "entradas", {k: entradas[k] for k in sorted(entradas)[-14:]})  # 14 meses: o FGTS usa
    if incomes:
        sal = max(incomes, key=lambda i: i["amount"])["amount"]
        cfg_month = int(income_ym[5:])
        if auto.get("salario", True) and cfg_month not in (cfg["decimo1_mes"], cfg["decimo2_mes"]) and len(incomes) == 1:
            base = float(cfg["salario"])
            if cfg.get("aumento_on") and income_ym >= (cfg.get("aumento_from") or "9999"):
                # A projeção já soma o aumento em cima do salário: atualizar os dois contaria o aumento duas vezes.
                alvo = base * (1 + float(cfg.get("aumento_pct") or 0) / 100)
                if sal >= alvo * 0.97:
                    db.put_setting(conn, "salario", sal)
                    db.put_setting(conn, "aumento_on", False)
                    events.append((f"aumento:{income_ym}", "O aumento chegou",
                                   f"Caiu {brl(sal)}. A projeção agora usa esse salário.", "/#plano"))
                else:
                    events.append((f"sem_aumento:{income_ym}", "O salário veio sem o aumento",
                                   f"Caiu {brl(sal)}, e a projeção conta com {brl(alvo)}. Se o aumento não vier, "
                                   "desligue em Planejamento.", "/#plano"))
            # só aceita se estiver perto do salário atual (evita confundir com um Pix grande de alguém)
            elif abs(sal - base) / max(base, 1) <= 0.3 and abs(sal - base) >= 0.01:
                db.put_setting(conn, "salario", sal)

    # 3) e 4) faturas: valor real e pagamento
    ciclo = {}
    venc_novo = dict(cfg.get("cartao_venc") or {})
    for a in data["accounts"]:
        if a["type"] != "CREDIT":
            continue
        card = card_name(cfg, a["bank"])
        if not card:
            continue
        bills = a["bills"] or {}
        closes = [str(b.get("billClosingDate") or "")[:10] for b in bills.get("results") or [] if b.get("billClosingDate")]
        offs = []
        for b in bills.get("results") or []:
            try:
                offs.append((date.fromisoformat(str(b["dueDate"])[:10]) - date.fromisoformat(str(b["billClosingDate"])[:10])).days)
            except (KeyError, ValueError, TypeError):
                pass
        ciclo[card] = {"conta": a["id"], "fechou": max(closes) if closes else None,
                       "fecha_antes": max(set(offs), key=offs.count) if offs else None}
        results = sorted([b for b in bills.get("results") or [] if len(str(b.get("dueDate") or "")) >= 10],
                         key=lambda b: str(b["dueDate"]))
        if auto.get("faturas", True):
            # dia de vencimento pelo que o banco mostra (o Nubank vence no dia 1, não no 3)
            dias = [int(str(b["dueDate"])[8:10]) for b in results[-6:]]
            if len(dias) >= 2:
                dia = max(set(dias), key=lambda d: (dias.count(d), -d))
                if venc_novo.get(card) != dia:
                    venc_novo[card] = dia
            for i, b in enumerate(results):
                due = str(b["dueDate"])[:10]
                if due < (t - timedelta(days=40)).isoformat():
                    continue
                total = _num(b.get("totalAmount"))
                pre = _prepaid(results, i)
                real = round(total + pre, 2)
                novo = _auto_override(conn, card, P.add_months(due[:7], -1), real,
                                      "fatura fechada" + (f" + {brl(pre)} pagos antes de fechar" if pre else ""))
                if novo and due >= t.isoformat():
                    events.append((f"fatura:{card}:{due}", f"Fatura {card} fechou: {brl(real)}",
                                   f"Vence em {due[8:10]}/{due[5:7]}. Já está nas contas a pagar.", "/#contas"))
                quitada = b.get("payment_status") == "PAID" or (pre > 0 and total <= 0.5)
                if quitada and set_paid(conn, due[:7], f"bill:{card}", real):
                    events.append((f"paid:{due[:7]}:bill:{card}", f"Fatura {card} paga", f"{brl(real)} confirmado pelo banco.", "/#contas"))
            ob = bills.get("open_bill") or {}
            if ob.get("available") and ob.get("method") == "cycle_dates" and ob.get("due_date"):
                due = str(ob["due_date"])[:10]
                _auto_override(conn, card, P.add_months(due[:7], -1), _num(ob.get("total_amount")), "fatura aberta até agora")
        if auto.get("pagamentos", True):
            # o Mercado Pago repete cada estorno como "Pagamento recebido" do mesmo valor e dia
            estornos_dia = {(local_date(x.get("date")), round(abs(_num(x.get("amount"))), 2)) for x in a["tx"]
                            if x.get("type") == "CREDIT" and (x.get("operationType") == "ESTORNO"
                                                              or "estorno" in str(x.get("description") or "").lower())}
            for x in a["tx"]:
                low = str(x.get("description") or "").lower()
                is_pay = x.get("operationType") == "PAGAMENTO_FATURA" or x.get("category") == "Credit card payment" \
                    or "pagamento recebido" in low or "pagamento de fatura" in low
                amt = abs(_num(x.get("amount")))
                d = local_date(x.get("date"))
                if not is_pay or x.get("type") != "CREDIT" or not d or amt <= 0:
                    continue
                if x.get("operationType") != "ESTORNO" and (d, round(amt, 2)) in estornos_dia:
                    continue
                m = _match_bill(conn, cfg, card, d, t)
                if m and set_paid(conn, m, f"bill:{card}", amt):
                    events.append((f"paid:{m}:bill:{card}", f"Fatura {card} paga", f"Pagamento de {brl(amt)} em {d[8:]}/{d[5:7]}.", "/#contas"))

    if venc_novo != (cfg.get("cartao_venc") or {}):
        db.put_setting(conn, "cartao_venc", venc_novo)
        cfg = db.get_cfg(conn)

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

    # compras do ciclo aberto de cada cartão entram na próxima fatura (substitui o "compras do ciclo" manual)
    if auto.get("faturas", True) and ciclo:
        extra = dict(cfg.get("ciclo_extra") or {})
        for card, info in ciclo.items():
            day = int((cfg.get("cartao_venc") or {}).get(card) or 10)
            since_close = info["fechou"] or (finance.clamp_day(t.year, t.month, day) - timedelta(days=7)).isoformat()
            # compra no dia do fechamento já é da fatura seguinte
            row = conn.execute(f"SELECT COALESCE(SUM(amount),0) FROM spends WHERE {finance.NA_FATURA} "
                               "AND ext_id LIKE ? AND date >= ?", (info["conta"] + ":%", since_close)).fetchone()
            extra[card] = round(row[0], 2)
            info["compras"] = extra[card]
        db.put_setting(conn, "ciclo_extra", extra)
        db.put_setting(conn, "ciclo_info", ciclo)
    for card, info in ciclo.items():  # lançamentos antigos sem cartão
        conn.execute("UPDATE spends SET card=? WHERE card IS NULL AND ext_id LIKE ?", (card, info["conta"] + ":%"))
    ignore_subscription_charges(conn)

    # diagnóstico por conta (mostrado em Ajustes)
    detalhe = []
    for a in data["accounts"]:
        n = conn.execute("SELECT COUNT(*) FROM spends WHERE ext_id LIKE ?", (a["id"] + ":%",)).fetchone()[0]
        detalhe.append({"banco": a["bank"], "tipo": "cartão" if a["type"] == "CREDIT" else "conta",
                        "lidas": len(a["tx"]), "no_app": n})
    db.put_setting(conn, "sync_detalhe", detalhe)

    # 5) caixa = saldo das contas − contas do mês já cobertas por esse dinheiro e ainda não pagas
    db.put_setting(conn, "caixa_banco", round(caixa_banco, 2))
    db.put_setting(conn, "cartao_usado", used)
    if auto.get("caixa", True):
        # contas dos salários que já caíram e ainda não foram pagas saem do saldo
        cfg2 = db.get_cfg(conn)
        x = finance.income_month(t, cfg2)
        # chave interna x = contas de referência x-1, pagas com o salário que já caiu
        panel = finance.month_panel(conn, cfg2, x, t)
        pend = sum(i["amount"] or 0 for i in panel["items"] if not i["paid"])
        db.put_setting(conn, "caixa", round(caixa_banco - pend, 2))
        db.put_setting(conn, "caixa_ref", P.ym_of(t))
        db.put_setting(conn, "caixa_data", t.isoformat())
    finance.advance_installments(conn, db.get_cfg(conn), t)
    db.put_setting(conn, "last_sync", C.now().strftime("%Y-%m-%d %H:%M"))
    return {"ok": True, "novos": added, "ja_lancados": adopted, "vistos": seen, "caixa_banco": round(caixa_banco, 2),
            "eventos": len(events), "estornos": estornos}, events


def _auto_override(conn, card, income_ym, total, label):
    cur = conn.execute("SELECT note FROM overrides WHERE card=? AND month=?", (card, income_ym)).fetchone()
    if cur and not str(cur["note"] or "").startswith(AUTO_NOTE):
        return  # valor informado por você: o banco não sobrescreve
    if total <= 0:
        return False
    if cur and conn.execute("SELECT ABS(amount - ?) < 0.005 FROM overrides WHERE card=? AND month=?",
                            (total, card, income_ym)).fetchone()[0]:
        return False
    conn.execute("INSERT INTO overrides(card,month,amount,note) VALUES(?,?,?,?) "
                 "ON CONFLICT(card,month) DO UPDATE SET amount=excluded.amount, note=excluded.note",
                 (card, income_ym, round(total, 2), AUTO_NOTE + label))
    return True


def _prepaid(results, i):
    """Quanto foi pago da fatura `results[i]` antes de ela fechar (entre o vencimento anterior e o fechamento).
    O Mercado Pago informa a fatura como R$ 0,00 (ou só o resto) quando o salário cai antes do dia 29 e você paga
    o ciclo ainda aberto: em mar/26 veio R$ 148,98 de R$ 1.634,43. Estornos repetidos como pagamento (OTHER_PAYMENT)
    e o pagamento atrasado da fatura anterior não contam."""
    if i == 0:
        return 0.0
    b, prev = results[i], results[i - 1]
    fecha = str(b.get("billClosingDate") or "")[:10]
    antes = str(prev.get("dueDate") or "")[:10]
    if len(fecha) != 10 or len(antes) != 10:
        return 0.0
    pags = {}
    for r in results:
        for p in r.get("payments") or []:
            pags[p.get("id") or f"{p.get('paymentDate')}:{p.get('amount')}"] = p
    prev_total = _num(prev.get("totalAmount"))
    em_dia = any(str(p.get("paymentDate") or "")[:10] <= antes and abs(_num(p.get("amount")) - prev_total) <= 0.5
                 for p in pags.values())
    total, resolvida = 0.0, em_dia or prev_total <= 0.5
    for p in sorted(pags.values(), key=lambda p: str(p.get("paymentDate") or "")):
        d = str(p.get("paymentDate") or "")[:10]
        if p.get("valueType") == "OTHER_PAYMENT" or not antes < d <= fecha:
            continue
        if not resolvida and abs(_num(p.get("amount")) - prev_total) <= 0.5:
            resolvida = True  # era a fatura anterior, paga depois do vencimento
            continue
        total += _num(p.get("amount"))
    return round(total, 2)


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


def run(force=False, origem="manual"):
    """Sincroniza agora. A leitura do banco (rede) acontece sem o SQLite travado;
    a gravação é uma transação curta. Com `force`, pede dados novos (máx. 1x/hora)."""
    if not C.BANCO_KEY:
        raise ValueError("BANCO_MCP_KEY não está configurada no servidor")
    if not _sync_lock.acquire(blocking=False):
        return {"ok": True, "ocupado": True}
    PROGRESS.update(rodando=True, etapa="Começando", feito=0, total=8, inicio=C.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    fim=None, origem=origem, resultado=None, erro=None)
    try:
        t = C.today()
        with db.connect() as conn:
            cfg = db.get_cfg(conn)
        data = fetch(cfg, t)
        PROGRESS["etapa"] = "Atualizando contas, faturas e parcelas"
        with db.connect(write=True) as conn:
            result, events = apply(conn, data, t)
            if events:
                stamp = C.now().strftime("%Y-%m-%d %H:%M")
                pend = (db.get_cfg(conn).get("eventos_pendentes") or []) + [list(e) + [stamp] for e in events]
                db.put_setting(conn, "eventos_pendentes", pend[-20:])
        _step("Gravado")
        if force:
            last = cfg.get("last_force")
            if not last or (C.now() - datetime.fromisoformat(last).replace(tzinfo=C.TZ)) >= timedelta(minutes=61):
                PROGRESS["etapa"] = "Pedindo dados novos ao banco"
                result["atualizacao"] = force_refresh(data["items"])
                with db.connect() as conn:
                    db.put_setting(conn, "last_force", C.now().strftime("%Y-%m-%dT%H:%M"))
        PROGRESS.update(feito=PROGRESS["total"], etapa="Concluído", resultado=result)
        return result
    except Exception as e:
        PROGRESS.update(erro=str(e)[:200], etapa="Falhou")
        raise
    finally:
        PROGRESS.update(rodando=False, fim=C.now().strftime("%Y-%m-%dT%H:%M:%S"))
        _sync_lock.release()
