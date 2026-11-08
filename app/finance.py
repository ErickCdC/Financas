"""Regras de negócio: painel do mês, teto semanal, parcelas, projeção e resumo para a interface."""
import calendar
import copy
import re
from datetime import date, timedelta

from . import config as C
from . import db
from . import paydays as PD
from . import projection as P


def clamp_day(y, m, d):
    return date(y, m, min(d, calendar.monthrange(y, m)[1]))


def last_payday(t, cfg):
    """Último salário em ou antes de `t` (cfg pode ser a configuração ou um dia fixo)."""
    return PD.last_payday(t, cfg)


def next_payday(t, cfg):
    return PD.next_payday(t, cfg)


def income_month(t, cfg):
    """Salário que paga o que acontece hoje (o próximo a cair). Ex.: em 07/11 é o de 30/11."""
    return PD.income_month(t, cfg)


def due_date(panel_ym, day, cfg):
    """Vencimento de uma conta de referência `panel_ym - 1` (paga com o salário daquele mês).
    Contas que vencem perto do fim do mês (aluguel, internet) ficam no próprio mês de referência;
    as demais (faturas, faculdade) vencem no começo do mês seguinte."""
    if not day:
        return None
    ref = P.add_months(panel_ym, -1)
    ry, rm = map(int, ref.split("-"))
    pay = PD.payday_of(ry, rm, cfg)
    y, m = (ry, rm) if int(day) >= pay.day - 7 else map(int, panel_ym.split("-"))
    return clamp_day(y, m, int(day)).isoformat()


def effective_cfg(cfg, t):
    """Ajustes derivados: o caixa só vale se foi informado depois do último salário."""
    c = copy.deepcopy(cfg)
    if c.get("caixa_data"):
        if c["caixa_data"] < last_payday(t, c).isoformat():
            c["caixa"] = 0.0
    elif c.get("caixa_ref") and c["caixa_ref"] != P.ym_of(t):
        c["caixa"] = 0.0
    return c


def bill_period(cfg, card, y, m):
    """Período de compras da fatura que vence no mês (y, m).
    Nos três cartões, compra feita no dia do fechamento já cai na fatura seguinte (conferido nas faturas
    de out e nov/26), então o período vai do fechamento anterior até a véspera do fechamento."""
    day = (cfg.get("cartao_venc") or {}).get(card)
    if not day:
        return {}
    off = ((cfg.get("ciclo_info") or {}).get(card) or {}).get("fecha_antes") or 7
    due = clamp_day(y, m, int(day))
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
    close = due - timedelta(days=int(off))
    prev_close = clamp_day(py, pm, int(day)) - timedelta(days=int(off))
    # O dia do fechamento varia (PicPay: 26 a 29). Perto do último fechamento real, vale a data do banco:
    # sem isso, uma compra de 27/10 que já estava na fatura de 05/11 entrava de novo na de 05/12.
    fechou = ((cfg.get("ciclo_info") or {}).get(card) or {}).get("fechou")
    if fechou:
        f = date.fromisoformat(fechou)
        if abs((f - prev_close).days) <= 5:
            prev_close = f
        elif abs((f - close).days) <= 5:
            close = f
    return {"compras_de": prev_close.isoformat(), "ate": (close - timedelta(days=1)).isoformat(),
            "fecha": close.isoformat()}


# Pix/transferência paga com o cartão: fora do teto (o dinheiro foi para outra conta), mas entra na fatura.
NA_FATURA = "(ignored=0 OR category='pix_credito')"


def card_purchases(conn, cfg, t, months):
    """Compras no cartão (banco + lançadas à mão com o cartão) que caem em cada fatura.
    Devolve {(cartão, mês do salário): total}. A fatura do salário `ym` vence em ym+1."""
    out = {}
    venc = cfg.get("cartao_venc") or {}
    for card in cfg["cartoes"]:
        if not venc.get(card):
            continue
        for ym in months:
            due = due_date(P.add_months(ym, 1), venc[card], cfg)
            per = bill_period(cfg, card, *map(int, due[:7].split("-")))
            row = conn.execute(f"SELECT COALESCE(SUM(amount),0) FROM spends WHERE {NA_FATURA} AND card=? "
                               "AND date BETWEEN ? AND ?", (card, per["compras_de"], per["ate"])).fetchone()
            out[(card, ym)] = round(row[0], 2)
    return out


def month_panel(conn, cfg, ym, t, purchases=None, listas=False):
    """Contas de referência `ym - 1` (pagas com o salário daquele mês). `ym` é a chave interna (mês seguinte)."""
    cur = income_month(t, cfg)
    ref = P.add_months(ym, -1)
    y, m = map(int, ym.split("-"))
    recurring = db.rows(conn, "SELECT * FROM recurring ORDER BY id")
    inst = db.rows(conn, "SELECT * FROM installments ORDER BY id")
    ov = {(r["card"], r["month"]): r for r in db.rows(conn, "SELECT * FROM overrides")}
    pays = {r["key"]: r for r in db.rows(conn, "SELECT * FROM payments WHERE month=?", (ym,))}
    venc = cfg.get("cartao_venc") or {}
    items = []

    def due(day):
        return due_date(ym, day, cfg)

    reemb = reembolsadas(conn)
    for r in recurring:
        if r["card"]:
            continue
        p = pays.get(f"rec:{r['id']}")
        has = bool(p and p["amount"] is not None)
        items.append({"key": f"rec:{r['id']}", "group": "fixa", "name": r["name"],
                      "amount": p["amount"] if has else r["amount"], "estimated": not has,
                      "paid": bool(p and p["paid"]), "parts": [], "due": due(r.get("due_day")),
                      **({"reembolso": reemb[r["id"]]} if r["id"] in reemb else {})})
    inc = P.add_months(ym, -1)
    if purchases is None:
        purchases = card_purchases(conn, cfg, t, [inc])
    can_forecast = P.months_between(cur, inc) >= 0 or P.months_between(P.ym_of(t), inc) >= 0
    for c in cfg["cartoes"]:
        p = pays.get(f"bill:{c}")
        has = bool(p and p["amount"] is not None)
        fc = P.card_forecast(c, inc, cur, recurring, inst, ov, cfg["ciclo_extra"], purchases) if can_forecast else None
        amount = p["amount"] if has else (fc["total"] if fc else None)
        per = bill_period(cfg, c, *map(int, due(venc.get(c))[:7].split("-"))) if venc.get(c) else {}
        item = {"key": f"bill:{c}", "group": "fatura", "name": f"Fatura {c}", "card": c,
                "amount": amount, "estimated": not has, "paid": bool(p and p["paid"]),
                "parts": (fc["parts"] if fc and not has else []), "parts_prev": (fc["parts"] if fc else []),
                "used": (cfg.get("cartao_usado") or {}).get(c), "due": due(venc.get(c)),
                "compras": purchases.get((c, inc), 0), "nota": (ov.get((c, inc)) or {}).get("note"), **per}
        if listas and per:
            # as compras que somam em "compras" (mesmo filtro de card_purchases)
            # o app só tem as compras a partir da primeira sincronização: avisa quando o ciclo começou antes
            desde = conn.execute("SELECT MIN(date) FROM spends WHERE card=? AND ext_id IS NOT NULL", (c,)).fetchone()[0]
            if desde and desde > per["compras_de"]:
                item["lista_desde"] = desde
            item["lista"] = db.rows(conn, "SELECT id, date, description, amount, category, source, ignored, card "
                                          f"FROM spends WHERE {NA_FATURA} AND card=? AND date BETWEEN ? AND ? "
                                          "ORDER BY date DESC, id DESC LIMIT 300", (c, per["compras_de"], per["ate"]))
        items.append(item)
    for p in db.rows(conn, "SELECT * FROM planned WHERE kind='saida' AND month=? ORDER BY day, id", (ref,)):
        items.append({"key": f"plan:{p['id']}", "group": "avulsa", "name": p["name"], "amount": p["amount"],
                      "estimated": False, "paid": bool(p["done"]), "parts": [], "due": due(p.get("day")),
                      "note": p.get("note")})
    order = {"fixa": 0, "avulsa": 1, "fatura": 2}
    items.sort(key=lambda i: (order[i["group"]], i["due"] or "9999", i["name"]))
    conta = [i for i in items if not i.get("reembolso")]  # quem reembolsa paga: não sai do seu bolso
    falta = round(sum(i["amount"] for i in conta if not i["paid"] and i["amount"] is not None), 2)
    pago = round(sum(i["amount"] for i in conta if i["paid"] and i["amount"] is not None), 2)
    totais = {g: round(sum(i["amount"] or 0 for i in conta if i["group"] == g), 2) for g in ("fixa", "avulsa", "fatura")}
    sem_valor = sum(1 for i in items if i["amount"] is None and not i["paid"])
    # período em que essas contas são pagas: do salário de referência até a véspera do próximo
    p0 = PD.payday_ym(ref, cfg)
    p1 = PD.payday_ym(ym, cfg) - timedelta(days=1)
    gastos_dia = {r["date"]: round(r["t"], 2) for r in conn.execute(
        "SELECT date, SUM(amount) AS t FROM spends WHERE ignored=0 AND date BETWEEN ? AND ? GROUP BY date",
        (p0.isoformat(), p1.isoformat()))}
    entradas = db.rows(conn, "SELECT * FROM planned WHERE kind='entrada' AND month=? ORDER BY day, id", (ref,))
    return {"ym": ym, "ref": ref, "label": P.label(ref), "items": items, "falta": falta, "pago": pago, "totais": totais,
            "sem_valor": sem_valor, "total": round(falta + pago, 2), "gastos_dia": gastos_dia,
            "salario": p0.isoformat(), "periodo": [p0.isoformat(), p1.isoformat()],
            "entradas_avulsas": entradas}


def week_info(conn, cfg, t):
    ws, we = P.week_range(t)
    q = "SELECT COALESCE(SUM(amount),0) FROM spends WHERE ignored=0 AND date BETWEEN ? AND ?"
    spent = conn.execute(q, (ws.isoformat(), we.isoformat())).fetchone()[0]
    prev = conn.execute(q, ((ws - timedelta(days=7)).isoformat(), (ws - timedelta(days=1)).isoformat())).fetchone()[0]
    since = last_payday(t, cfg)
    since_total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM spends WHERE ignored=0 AND date>=?",
                               (since.isoformat(),)).fetchone()[0]
    lista = db.rows(conn, "SELECT * FROM spends WHERE date>=? ORDER BY date DESC, id DESC LIMIT 500",
                    ((since - timedelta(days=35)).isoformat(),))
    teto = float(cfg["teto_semanal"])
    days = []
    for i in range(7):
        d = ws + timedelta(days=i)
        v = conn.execute("SELECT COALESCE(SUM(amount),0) FROM spends WHERE ignored=0 AND date=?",
                         (d.isoformat(),)).fetchone()[0]
        days.append({"date": d.isoformat(), "total": round(v, 2)})
    dias_rest = (we - t).days + 1
    restante = max(teto - spent, 0)
    cats = db.rows(conn, "SELECT COALESCE(NULLIF(category,''),'outros') AS cat, ROUND(SUM(amount),2) AS total "
                         "FROM spends WHERE ignored=0 AND date>=? GROUP BY cat ORDER BY total DESC",
                   (since.isoformat(),))
    return {"inicio": ws.isoformat(), "fim": we.isoformat(), "hoje": t.isoformat(),
            "gasto": round(spent, 2), "teto": teto, "restante": round(restante, 2),
            "estourou": round(max(spent - teto, 0), 2), "pct": round(spent / teto * 100, 1) if teto else 0,
            "por_dia_restante": round(restante / dias_rest, 2) if dias_rest > 0 else 0,
            "dias_restantes": dias_rest, "semana_passada": round(prev, 2), "dias": days,
            "desde": since.isoformat(), "desde_total": round(since_total, 2),
            "proximo_salario": next_payday(t + timedelta(days=1), cfg).isoformat(),
            "categorias": cats, "lista": lista}


def parcelas(conn, t):
    cur = P.ym_of(t)
    out = []
    for i in db.rows(conn, "SELECT * FROM installments ORDER BY id"):
        rest = i["total"] - i["paid_count"]
        base = i.get("last_bill") or cur
        out.append({**i, "restante": rest, "restante_valor": round(rest * i["amount"], 2),
                    "ultima_fatura": P.label(P.add_months(base, rest)) if rest > 0 else None})
    return out


def settled_month(conn, cfg, card, t):
    """Mês de vencimento da fatura mais recente já resolvida (paga ou vencida há 3+ dias)."""
    day = (cfg.get("cartao_venc") or {}).get(card) or 10
    best = None
    for k in range(-3, 2):
        ym = P.add_months(P.ym_of(t), k)
        due = clamp_day(int(ym[:4]), int(ym[5:]), int(day))
        row = conn.execute("SELECT paid FROM payments WHERE month=? AND key=?", (ym, f"bill:{card}")).fetchone()
        if (row and row["paid"]) or (t - due).days >= 3:
            best = ym
    return best


def advance_installments(conn, cfg, t):
    """Conta as parcelas das faturas que já foram pagas ou venceram. Devolve quantas avançaram."""
    moved = 0
    for card in cfg["cartoes"]:
        x = settled_month(conn, cfg, card, t)
        if not x:
            continue
        for i in db.rows(conn, "SELECT * FROM installments WHERE card=? AND paid_count < total", (card,)):
            last = i.get("last_bill") or x
            add = P.months_between(last, x)
            if add > 0:
                conn.execute("UPDATE installments SET paid_count=?, last_bill=? WHERE id=?",
                             (min(i["total"], i["paid_count"] + add), x, i["id"]))
                moved += 1
            elif not i.get("last_bill"):
                conn.execute("UPDATE installments SET last_bill=? WHERE id=?", (x, i["id"]))
    return moved


def upcoming(conn, cfg, t, days=10):
    """Contas não pagas que vencem nos próximos `days` dias (inclui atrasadas do mês)."""
    out = []
    seen = set()
    x = income_month(t, cfg)
    for ym in (x, P.add_months(x, 1)):
        for i in month_panel(conn, cfg, ym, t)["items"]:
            if i["paid"] or not i["due"] or i["amount"] in (None, 0):
                continue
            d = date.fromisoformat(i["due"])
            delta = (d - t).days
            if -7 <= delta <= days and (ym, i["key"]) not in seen:
                seen.add((ym, i["key"]))
                out.append({**i, "month": ym, "dias": delta})
    out.sort(key=lambda x: x["due"])
    return out


def resolve_month(conn, cfg, t, month):
    """Abre nas contas do salário anterior se ainda faltar pagar algo; senão, nas do próximo salário."""
    cur = income_month(t, cfg)
    if month:
        return month
    p = month_panel(conn, cfg, cur, t)
    if p["items"] and p["falta"] == 0 and p["sem_valor"] == 0:
        return P.add_months(cur, 1)
    return cur


def context(conn, t, n=6):
    """Configuração efetiva + projeção. Com teto automático, o teto da semana sai da projeção."""
    raw = db.get_cfg(conn)
    cfg = effective_cfg(raw, t)
    recurring = [r for r in db.rows(conn, "SELECT * FROM recurring ORDER BY id")
                 if r["card"] or r["id"] not in reembolsadas(conn)]  # reembolsada: quem paga é outra pessoa
    inst = db.rows(conn, "SELECT * FROM installments ORDER BY id")
    ov = {(r["card"], r["month"]): r for r in db.rows(conn, "SELECT * FROM overrides")}
    planned = db.rows(conn, "SELECT * FROM planned ORDER BY month, day, id")
    start = income_month(t, cfg)
    purchases = card_purchases(conn, cfg, t, [start, P.add_months(start, 1)])
    proj = P.project(cfg, recurring, inst, ov, t, n=n, planned=planned, start=start,
                     payday=lambda ym: PD.payday_ym(ym, cfg), purchases=purchases)
    m0 = proj["months"][0]
    livre = max(m0["sobra"] - m0["meta"], 0.0)
    por_dia = livre / max(m0["dias"], 1)
    if cfg.get("teto_auto"):
        ws, we = P.week_range(t)
        q = "SELECT COALESCE(SUM(amount),0) FROM spends WHERE ignored=0 AND date BETWEEN ? AND ?"
        gasto = conn.execute(q, (ws.isoformat(), we.isoformat())).fetchone()[0]
        end = min(we, PD.payday_ym(start, cfg) - timedelta(days=1))
        cfg["teto_semanal"] = round(gasto + por_dia * ((end - t).days + 1), 2)
    return cfg, proj, {"livre": round(livre, 2), "por_dia": round(por_dia, 2), "dias": m0["dias"],
                       "sobra": m0["sobra"], "meta": m0["meta"], "auto": bool(cfg.get("teto_auto"))}


NORM_RE = re.compile(r"[^a-z0-9 ]")


def merchant(desc):
    """Nome curto do estabelecimento: 'UBER *TRIP' -> 'uber', '99* POP' -> '99', 'HELP GAS .RIO' -> 'help gas'."""
    d = str(desc or "").lower()
    d = re.sub(r"^(pagamento com pix|pix enviado|pagamento de conta|mp\*|pg \*|pag\*)\s*", "", d)
    if re.match(r"^99\b|^99\*", d):
        return "99"
    d = d.split(".")[0].split("*")[0]
    d = NORM_RE.sub(" ", d)
    words = [w for w in d.split() if not w.isdigit()][:2]
    return " ".join(words) or "outros"


def insights(conn, cfg, t, proj, livre):
    """Raio-X: leitura rápida do que está acontecendo, com números do próprio app."""
    out = []
    since = last_payday(t, cfg)
    days = max((t - since).days + 1, 1)
    q = "SELECT COALESCE(SUM(amount),0) FROM spends WHERE ignored=0 AND date BETWEEN ? AND ?"
    gasto = conn.execute(q, (since.isoformat(), t.isoformat())).fetchone()[0]
    # ritmo
    ritmo = gasto / days
    m0 = proj["months"][0]
    fim = m0["sobra"] - ritmo * m0["dias"]
    out.append({"id": "ritmo", "icon": "chart", "tone": "pos" if fim >= m0["meta"] else "neg",
                "title": "No ritmo atual",
                "value": fim, "text": f"Gastando {brl(ritmo)}/dia (média desde {since:%d/%m}), você termina o mês "
                f"{'com ' + brl(fim) + ' sobrando' if fim >= 0 else 'faltando ' + brl(-fim)}."})
    # comparação com o ciclo anterior, mesmo número de dias
    prev_since = last_payday(since - timedelta(days=1), cfg)
    prev_end = prev_since + timedelta(days=days - 1)
    prev = conn.execute(q, (prev_since.isoformat(), prev_end.isoformat())).fetchone()[0]
    if prev > 0:
        diff = gasto - prev
        out.append({"id": "comparativo", "icon": "calendar", "tone": "neg" if diff > 0 else "pos",
                    "title": f"Comparado aos primeiros {days} dias do salário anterior", "value": diff,
                    "text": f"{brl(gasto)} agora contra {brl(prev)} antes ({'+' if diff > 0 else '−'}{brl(abs(diff))})."})
    # transporte
    rows = db.rows(conn, "SELECT description, amount, category FROM spends WHERE ignored=0 AND date BETWEEN ? AND ?",
                   (since.isoformat(), t.isoformat()))
    tr = [r for r in rows if r["category"] == "transporte" or merchant(r["description"]) in ("uber", "99")]
    if tr:
        tot = sum(r["amount"] for r in tr)
        out.append({"id": "transporte", "icon": "car", "tone": "warn" if tot > 0.25 * max(gasto, 1) else "",
                    "title": "Transporte (Uber/99)", "value": tot,
                    "text": f"{len(tr)} corrida(s), {brl(tot)} desde o salário — média de {brl(tot / len(tr))} por corrida."})
    # principais estabelecimentos
    by = {}
    for r in rows:
        k = merchant(r["description"])
        by[k] = by.get(k, 0) + r["amount"]
    top = sorted(by.items(), key=lambda x: -x[1])[:3]
    if top:
        out.append({"id": "top", "icon": "tag", "tone": "", "title": "Onde mais gastou", "value": top[0][1],
                    "text": " · ".join(f"{k.title()} {brl(v)}" for k, v in top)})
    # assinaturas
    subs = db.rows(conn, "SELECT name, amount FROM recurring WHERE card IS NOT NULL AND card != ''")
    if subs:
        tot = sum(r["amount"] for r in subs)
        pct = tot / max(float(cfg["salario"]), 1) * 100
        maior = max(subs, key=lambda r: r["amount"])
        out.append({"id": "assinaturas", "icon": "card", "tone": "warn" if pct >= 5 else "",
                    "title": "Assinaturas", "value": tot,
                    "text": f"{brl(tot)}/mês ({pct:.0f}% do salário, {brl(tot * 12)} por ano). A maior é {maior['name']} ({brl(maior['amount'])})."})
    # custo do crédito parcelado
    juros = []
    for i in db.rows(conn, "SELECT name, amount, total, original FROM installments WHERE original IS NOT NULL AND original > 0"):
        pago = i["amount"] * i["total"]
        if pago > i["original"] + 0.5:
            juros.append((i["name"], pago - i["original"], (pago / i["original"] - 1) * 100))
    for name, j, pct in juros:
        out.append({"id": "juros", "icon": "alert", "tone": "neg", "title": f"Juros: {name}", "value": j,
                    "text": f"Você vai pagar {brl(j)} a mais ({pct:.0f}%) por esse crédito. Evitar Pix no crédito e parcelamento com juros é a economia mais rápida."})
    return out


def dividir(valor, metas, modo, falta):
    """Reparte `valor` entre as metas que ainda faltam. `falta` ({id: quanto falta}) é atualizado.
    modo "ordem": enche uma meta por vez, na ordem de prioridade.
    modo "divisao": cada meta recebe sua % (campo share), proporcional entre as que não terminaram;
    a parte de quem termina vai para as outras. Devolve ({id: valor}, o que sobrou sem meta)."""
    partes, resto = {}, max(float(valor or 0), 0.0)
    ativos = [g for g in metas if falta.get(g["id"], 0) > 0.005]
    if modo == "divisao":
        while resto > 0.005 and ativos:
            pesos = {g["id"]: max(float(g.get("share") or 0), 0) for g in ativos}
            tot = sum(pesos.values())
            if tot <= 0:
                pesos, tot = {g["id"]: 1.0 for g in ativos}, float(len(ativos))
            dado = 0.0
            for g in ativos:
                v = min(resto * pesos[g["id"]] / tot, falta[g["id"]])
                partes[g["id"]] = partes.get(g["id"], 0) + v
                falta[g["id"]] -= v
                dado += v
            resto -= dado
            ativos = [g for g in ativos if falta[g["id"]] > 0.005]
            if dado < 0.005:
                break
    else:
        for g in ativos:
            if resto <= 0.005:
                break
            v = min(resto, falta[g["id"]])
            partes[g["id"]] = v
            falta[g["id"]] -= v
            resto -= v
    return {k: round(v, 2) for k, v in partes.items() if v > 0.004}, round(max(resto, 0), 2)


def goals_plan(conn, proj, cfg=None):
    """Para onde vai a caixinha prevista de cada salário e quando cada meta chega."""
    goals = db.rows(conn, "SELECT * FROM goals ORDER BY ordem, id")
    modo = (cfg or {}).get("metas_modo") or "ordem"
    months = proj["months"]
    series = [max(m["caixinha"], 0) for m in months]
    tail = series[-3:] if len(series) >= 3 else series
    steady = sum(tail) / len(tail) if tail else 0
    cur = months[0]["ym"] if months else None
    falta = {g["id"]: max(g["target"] - g["saved"], 0) for g in goals}
    eta, alocacao, recebe = {}, [], {g["id"]: [] for g in goals}
    for i in range(600):
        if not cur or not any(v > 0.005 for v in falta.values()):
            break
        v = series[i] if i < len(series) else steady
        if i >= len(series) and v <= 0:
            break
        partes, livre = dividir(v, goals, modo, falta)
        ym = P.add_months(cur, i)
        if i < len(series):
            alocacao.append({"ym": ym, "total": round(v, 2), "livre": livre,
                             "partes": [{"id": g["id"], "valor": partes[g["id"]]} for g in goals if partes.get(g["id"])]})
            for g in goals:
                recebe[g["id"]].append(partes.get(g["id"], 0))
        for gid, val in partes.items():
            if val > 0 and falta[gid] <= 0.005 and gid not in eta:
                eta[gid] = ym
    for i in range(len(alocacao), len(series)):  # metas todas prontas: a caixinha fica livre
        alocacao.append({"ym": P.add_months(cur, i), "total": round(series[i], 2), "livre": round(series[i], 2), "partes": []})
    out = []
    for g in goals:
        e = "pronta" if g["target"] - g["saved"] <= 0 else eta.get(g["id"])
        pct = 0 if g["target"] <= 0 else min(g["saved"] / g["target"] * 100, 100)
        r = recebe[g["id"]]
        out.append({**g, "falta": round(max(g["target"] - g["saved"], 0), 2), "pct": round(pct, 1), "eta": e,
                    "meses": (P.months_between(cur, e) + 1) if e and e != "pronta" and cur else None,
                    "proximo": round(r[0], 2) if r else 0, "no_periodo": round(sum(r), 2)})
    return {"metas": out, "aporte_medio": round(steady, 2), "metas_modo": modo, "alocacao": alocacao}


def reembolsadas(conn):
    """Contas fixas que outra pessoa reembolsa todo mês: {id da conta: {pessoa, cobranca}}."""
    return {r["conta_id"]: {"pessoa": r["pessoa"], "cobranca": r["id"]} for r in db.rows(
        conn, "SELECT id, conta_id, pessoa FROM cobrancas WHERE ativo=1 AND conta_id IS NOT NULL")}


def cobranca_valor(conn, c):
    """Valor fixo da cobrança ou, sem valor, o último valor pago da conta ligada."""
    if c.get("valor") is not None:
        return float(c["valor"])
    if c.get("conta_id"):
        row = conn.execute("SELECT amount FROM payments WHERE key=? AND amount IS NOT NULL ORDER BY month DESC LIMIT 1",
                           (f"rec:{c['conta_id']}",)).fetchone()
        if row:
            return float(row[0])
        row = conn.execute("SELECT amount FROM recurring WHERE id=?", (c["conta_id"],)).fetchone()
        if row:
            return float(row[0])
    return 0.0


def janela_cobranca(c, ym):
    """Quando cobrar a referência `ym`: dia_ini..dia_fim do mesmo mês ou do seguinte (ex.: a I-Conecta de
    novembro o Valter paga entre 1 e 5 de dezembro)."""
    y, m = map(int, P.add_months(ym, 1 if c.get("mes_seguinte") else 0).split("-"))
    return clamp_day(y, m, int(c["dia_ini"])), clamp_day(y, m, int(c["dia_fim"]))


def cobrancas_mes(conn, t, ym=None):
    """Cobranças de um mês de referência: quanto, de quem, quando cobrar e em que pé está."""
    ym = ym or P.ym_of(t)
    st = {r["cobranca_id"]: r for r in db.rows(conn, "SELECT * FROM cobranca_mes WHERE month=?", (ym,))}
    contas = {r["id"]: r["name"] for r in db.rows(conn, "SELECT id, name FROM recurring")}
    itens = []
    for c in db.rows(conn, "SELECT * FROM cobrancas WHERE ativo=1 ORDER BY dia_ini, id"):
        ini, fim = janela_cobranca(c, ym)
        s = st.get(c["id"]) or {}
        estado = ("recebido" if s.get("recebido") else "atrasado" if t > fim else
                  "cobrado" if s.get("cobrado") else "futuro" if t < ini else "cobrar")
        itens.append({**c, "valor_mes": round(cobranca_valor(conn, c), 2), "ym": ym, "ini": ini.isoformat(),
                      "fim": fim.isoformat(), "estado": estado, "cobrado": s.get("cobrado"),
                      "recebido": s.get("recebido"), "recebido_valor": s.get("valor"),
                      "conta": contas.get(c.get("conta_id"))})
    total = round(sum(i["valor_mes"] for i in itens), 2)
    recebido = round(sum((i["recebido_valor"] or i["valor_mes"]) for i in itens if i["recebido"]), 2)
    return {"ym": ym, "itens": itens, "total": total, "recebido": recebido,
            "pendente": round(sum(i["valor_mes"] for i in itens if not i["recebido"]), 2)}


def cobrancas_pendentes(conn, t):
    """O que dá para cobrar agora ou já atrasou (janela aberta nos últimos 40 dias), de qualquer referência."""
    out = []
    for ym in (P.add_months(P.ym_of(t), -1), P.ym_of(t)):
        for i in cobrancas_mes(conn, t, ym)["itens"]:
            if i["estado"] in ("cobrar", "atrasado") and (t - date.fromisoformat(i["ini"])).days <= 40:
                out.append(i)
    return out


def _acima_teto(conn, cfg, t):
    """Quanto passou do teto desde o salário (já sai da caixinha deste salário)."""
    since = last_payday(t, cfg)
    gasto = conn.execute("SELECT COALESCE(SUM(amount),0) FROM spends WHERE ignored=0 AND date BETWEEN ? AND ?",
                         (since.isoformat(), t.isoformat())).fetchone()[0]
    return round(max(gasto - float(cfg["teto_semanal"]) / 7 * ((t - since).days + 1), 0), 2)


def _ciclo(cfg, ym):
    """Ciclo do salário `ym`: do salário anterior até a véspera deste."""
    return PD.payday_ym(P.add_months(ym, -1), cfg), PD.payday_ym(ym, cfg) - timedelta(days=1)


def relatorio(conn, cfg, t, ym=None):
    """Relatório de um ciclo de salário: quanto, onde, em quê, contra o teto e contra o ciclo anterior."""
    ym = ym or income_month(t, cfg)
    ini, fim = _ciclo(cfg, ym)
    ate = min(fim, t)
    if ate < ini:
        raise ValueError("esse ciclo ainda não começou")
    dias = (ate - ini).days + 1
    q = "SELECT * FROM spends WHERE ignored=0 AND date BETWEEN ? AND ? ORDER BY amount DESC"
    itens = db.rows(conn, q, (ini.isoformat(), ate.isoformat()))
    total = round(sum(s["amount"] for s in itens), 2)
    # ciclo anterior, no mesmo ponto (mesmo número de dias) e inteiro
    pini, pfim = _ciclo(cfg, P.add_months(ym, -1))
    pate = min(pini + timedelta(days=dias - 1), pfim)
    ant = db.rows(conn, q, (pini.isoformat(), pate.isoformat()))
    ant_total = round(sum(s["amount"] for s in ant), 2)
    ant_inteiro = conn.execute("SELECT COALESCE(SUM(amount),0) FROM spends WHERE ignored=0 AND date BETWEEN ? AND ?",
                               (pini.isoformat(), pfim.isoformat())).fetchone()[0]
    tem_anterior = conn.execute("SELECT 1 FROM spends WHERE date <= ? LIMIT 1", (pini.isoformat(),)).fetchone() is not None
    desde = conn.execute("SELECT MIN(date) FROM spends").fetchone()[0]

    def por_cat(lst):
        out = {}
        for s in lst:
            c = s.get("category") or "outros"
            out[c] = out.get(c, 0) + s["amount"]
        return out
    cat, cat_ant = por_cat(itens), por_cat(ant)
    categorias = sorted(({"cat": c, "total": round(v, 2), "anterior": round(cat_ant.get(c, 0), 2)} for c, v in cat.items()),
                        key=lambda x: -x["total"])
    meios = {}
    for s in itens:
        k = s.get("card") or "Conta (Pix e débito)"
        meios[k] = meios.get(k, 0) + s["amount"]
    teto = float(cfg["teto_semanal"])
    semanas, d = [], ini
    while d <= ate:
        ws, we = P.week_range(d)
        a, b = max(ws, ini), min(we, ate)
        g = sum(s["amount"] for s in itens if a.isoformat() <= s["date"] <= b.isoformat())
        lim = teto * ((min(we, fim) - a).days + 1) / 7  # semana cortada pelo salário: teto proporcional
        semanas.append({"inicio": a.isoformat(), "fim": b.isoformat(), "gasto": round(g, 2), "teto": round(lim, 2),
                        "acima": round(max(g - lim, 0), 2), "aberta": b == t and we > t})
        d = we + timedelta(days=1)
    teto_ate_hoje = teto / 7 * dias
    assin = db.rows(conn, "SELECT name, amount, card FROM recurring WHERE card IS NOT NULL AND card != '' ORDER BY amount DESC")
    return {
        "ym": ym, "inicio": ini.isoformat(), "fim": fim.isoformat(), "ate": ate.isoformat(), "dias": dias,
        "aberto": fim > t, "total": total, "por_dia": round(total / dias, 2),
        "teto_semanal": teto, "teto_periodo": round(teto_ate_hoje, 2), "acima_teto": round(max(total - teto_ate_hoje, 0), 2),
        "semanas_acima": sum(1 for w in semanas if w["acima"] > 0 and not w["aberta"]),
        "anterior": {"ym": P.add_months(ym, -1), "mesmo_ponto": ant_total, "inteiro": round(ant_inteiro, 2),
                     "tem_dados": tem_anterior},
        "categorias": categorias, "semanas": semanas,
        "maiores": [{k: s[k] for k in ("id", "date", "description", "amount", "category", "card", "source", "ignored")}
                    for s in itens[:6]],
        "meios": sorted(({"nome": k, "total": round(v, 2)} for k, v in meios.items()), key=lambda x: -x["total"]),
        "assinaturas": {"total": round(sum(a["amount"] for a in assin), 2), "itens": assin},
        "n": len(itens), "dados_desde": desde if desde and desde > ini.isoformat() else None,
    }


# INSS 2026 (confere com os holerites: R$ 2.579,30 -> R$ 207,81; R$ 3.479,92 -> R$ 306,17).
# Muda todo janeiro com o salário mínimo; o erro de usar a tabela antiga é de poucos reais.
INSS_FAIXAS = ((1621.00, 0.075), (2902.84, 0.09), (4354.27, 0.12), (8475.55, 0.14))


def inss(bruto):
    total, antes = 0.0, 0.0
    for teto, aliq in INSS_FAIXAS:
        if bruto <= antes:
            break
        total += (min(bruto, teto) - antes) * aliq
        antes = teto
    return total


def bruto_de_liquido(liq):
    """Salário bruto a partir do líquido. Até R$ 5 mil o único desconto é o INSS (IR isento desde 2026)."""
    lo, hi = float(liq), float(liq) * 1.25 + 10
    for _ in range(50):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if mid - inss(mid) < liq else (lo, mid)
    return round(hi, 2)


def fgts_saldo(f, ate, deposito=None):
    """Saldo estimado do FGTS em `ate`. `deposito(y, m)` é o que cai no dia 20 do mês (y, m), referente ao
    salário do mês anterior; em dezembro cai também o do 13º. Rende ~3% ao ano, creditado todo dia 10."""
    try:
        d0 = date.fromisoformat(f.get("data") or "")
    except ValueError:
        return None
    if deposito is None:
        mensal = float(f.get("mensal") or 0)
        deposito = lambda y, m: mensal * (2 if m == 12 else 1)  # noqa: E731
    saldo = float(f.get("saldo") or 0)
    juros = (1 + float(f.get("rende_pct") or 0) / 100) ** (1 / 12) - 1
    y, m = d0.year, d0.month
    while date(y, m, 1) <= ate:
        if d0 < date(y, m, 10) <= ate:
            saldo *= 1 + juros
        if d0 < date(y, m, 20) <= ate:
            saldo += deposito(y, m)
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return round(saldo, 2)


def fgts_deposito(cfg):
    """Depósito de FGTS de cada mês, acompanhando o salário: 8% do bruto. Mês que já caiu usa o líquido
    que entrou de verdade (com hora extra); os próximos usam o salário atual, com o aumento programado."""
    entradas = cfg.get("entradas") or {}
    decimos = (int(cfg.get("decimo1_mes") or 11), int(cfg.get("decimo2_mes") or 12))

    def salario_de(ym):
        rec = (entradas.get(ym) or {}).get("total")
        if rec and int(ym[5:]) not in decimos:  # em mês de 13º o total mistura salário e 13º
            return float(rec)
        sal = float(cfg.get("salario") or 0)
        if cfg.get("aumento_on") and ym >= (cfg.get("aumento_from") or "9999"):
            sal *= 1 + float(cfg.get("aumento_pct") or 0) / 100
        return sal

    def deposito(y, m):
        ref = P.add_months(f"{y:04d}-{m:02d}", -1)  # o dia 20 traz o FGTS do salário do mês anterior
        valor = 0.08 * bruto_de_liquido(salario_de(ref))
        if m == 12:  # FGTS do 13º (as duas parcelas, juntas)
            valor += 0.08 * bruto_de_liquido(salario_de(f"{y:04d}-12"))
        return round(valor, 2)
    return deposito


def fgts_plan(cfg, t):
    f = cfg.get("fgts") or {}
    dep = fgts_deposito(cfg)
    hoje = fgts_saldo(f, t, dep)
    if hoje is None:
        return None

    def depois(d, meses):
        y, m = divmod(d.month - 1 + meses, 12)
        return clamp_day(d.year + y, m + 1, d.day)
    libera = f.get("libera")
    # daqui a 6 meses, na liberação e um ano depois dela (ou daqui a 6 e 12 meses, sem data)
    datas = [depois(t, 6)] + ([date.fromisoformat(libera), depois(date.fromisoformat(libera), 12)] if libera else [depois(t, 12)])
    marcos = [{"data": d.isoformat(), "saldo": fgts_saldo(f, d, dep), **({"libera": True} if d.isoformat() == libera else {})}
              for d in sorted(set(datas)) if d > t]
    return {**f, "mensal": round(0.08 * bruto_de_liquido(float(cfg.get("salario") or 0)), 2), "hoje": hoje, "marcos": marcos,
            "na_liberacao": next((x["saldo"] for x in marcos if x.get("libera")), None)}


def brl(v):
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("−R$ " if v < 0 else "R$ ") + s


def summary(conn, month=None, extra=None):
    t = C.today()
    raw = db.get_cfg(conn)
    cfg, proj, livre = context(conn, t)
    month = resolve_month(conn, cfg, t, month)
    reemb = reembolsadas(conn)
    recurring = [{**r, **({"reembolso": reemb[r["id"]]} if r["id"] in reemb else {})}
                 for r in db.rows(conn, "SELECT * FROM recurring ORDER BY id")]
    ov_rows = db.rows(conn, "SELECT * FROM overrides ORDER BY month, card")
    parc = parcelas(conn, t)
    public_cfg = {k: raw.get(k) for k in (
        "salario", "decimo1", "decimo2", "decimo1_mes", "decimo2_mes", "aumento_on", "aumento_pct",
        "aumento_from", "teto_semanal", "caixa", "caixa_ref", "salario_dia", "ignore_terms", "pix_max",
        "ciclo_extra", "cartoes", "cartao_venc", "last_sync", "caixa_banco", "cartao_usado", "notif",
        "auto", "sync_auto", "salario_regra", "entradas", "last_force", "last_tick", "sync_detalhe", "ciclo_info",
        "nome", "meta_caixinha", "teto_auto", "salario_tipo", "caixa_data", "fgts", "metas_modo")}
    public_cfg["teto_efetivo"] = cfg["teto_semanal"]
    x = income_month(t, cfg)
    paydays = {P.add_months(x, k): PD.payday_ym(P.add_months(x, k), cfg).isoformat() for k in range(-4, 10)}
    venc = cfg.get("cartao_venc") or {}
    for mo in proj["months"]:  # vencimento de cada fatura prevista
        for k in mo["cartoes"]:
            k["due"] = due_date(P.add_months(mo["ym"], 1), venc.get(k["card"]), cfg)
    public_cfg["caixa_desatualizado"] = bool(raw.get("caixa_ref") and raw["caixa_ref"] != P.ym_of(t))
    out = {
        "hoje": t.isoformat(), "mes": month_panel(conn, cfg, month, t, listas=True),
        "semana": week_info(conn, cfg, t), "parcelas": parc,
        "parcelas_total_restante": round(sum(p["restante_valor"] for p in parc), 2),
        "parcelas_mensal": round(sum(p["amount"] for p in parc if p["restante"] > 0), 2),
        "vencimentos": upcoming(conn, cfg, t),
        "recorrentes": recurring, "overrides": ov_rows, "proj": proj, "config": public_cfg,
        "ciclo": x, "paydays": paydays,
        "proximas": month_panel(conn, cfg, P.add_months(x, 1), t, listas=True),
        "livre": livre, "insights": insights(conn, cfg, t, proj, livre), **goals_plan(conn, proj, cfg),
        "fgts": fgts_plan(cfg, t), "acima_teto": _acima_teto(conn, cfg, t), "cobrancas": cobrancas_mes(conn, t),
        "cobrancas_pendentes": cobrancas_pendentes(conn, t),
        "avulsos": db.rows(conn, "SELECT * FROM planned ORDER BY month, day, id"),
        "notificacoes": db.rows(conn, "SELECT * FROM notif_log ORDER BY sent_at DESC LIMIT 20"),
        "aparelhos": db.rows(conn, "SELECT id, label, created, last_ok, fails FROM push_subs ORDER BY id"),
    }
    out.update(extra or {})
    return out
