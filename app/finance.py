"""Regras de negócio: painel do mês, teto semanal, parcelas, projeção e resumo para a interface."""
import calendar
import copy
from datetime import date, timedelta

from . import config as C
from . import db
from . import projection as P


def clamp_day(y, m, d):
    return date(y, m, min(d, calendar.monthrange(y, m)[1]))


def last_payday(t, dia):
    cand = clamp_day(t.year, t.month, dia)
    if cand <= t:
        return cand
    y, m = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    return clamp_day(y, m, dia)


def next_payday(t, dia):
    cand = clamp_day(t.year, t.month, dia)
    if cand >= t:
        return cand
    y, m = (t.year, t.month + 1) if t.month < 12 else (t.year + 1, 1)
    return clamp_day(y, m, dia)


def effective_cfg(cfg, t):
    """Ajustes derivados: o caixa só vale para o mês a que se refere."""
    c = copy.deepcopy(cfg)
    if c.get("caixa_ref") and c["caixa_ref"] != P.ym_of(t):
        c["caixa"] = 0.0
    return c


def month_panel(conn, cfg, ym, t):
    cur = P.ym_of(t)
    y, m = map(int, ym.split("-"))
    recurring = db.rows(conn, "SELECT * FROM recurring ORDER BY id")
    inst = db.rows(conn, "SELECT * FROM installments ORDER BY id")
    ov = {(r["card"], r["month"]): r for r in db.rows(conn, "SELECT * FROM overrides")}
    pays = {r["key"]: r for r in db.rows(conn, "SELECT * FROM payments WHERE month=?", (ym,))}
    venc = cfg.get("cartao_venc") or {}
    items = []

    def due(day):
        return clamp_day(y, m, int(day)).isoformat() if day else None

    for r in recurring:
        if r["card"]:
            continue
        p = pays.get(f"rec:{r['id']}")
        has = bool(p and p["amount"] is not None)
        items.append({"key": f"rec:{r['id']}", "group": "fixa", "name": r["name"],
                      "amount": p["amount"] if has else r["amount"], "estimated": not has,
                      "paid": bool(p and p["paid"]), "parts": [], "due": due(r.get("due_day"))})
    income_month = P.add_months(ym, -1)
    can_forecast = P.months_between(cur, income_month) >= 0
    for c in cfg["cartoes"]:
        p = pays.get(f"bill:{c}")
        has = bool(p and p["amount"] is not None)
        fc = P.card_forecast(c, income_month, cur, recurring, inst, ov, cfg["ciclo_extra"]) if can_forecast else None
        amount = p["amount"] if has else (fc["total"] if fc else None)
        items.append({"key": f"bill:{c}", "group": "fatura", "name": f"Fatura {c}", "card": c,
                      "amount": amount, "estimated": not has, "paid": bool(p and p["paid"]),
                      "parts": (fc["parts"] if fc and not has else []),
                      "used": (cfg.get("cartao_usado") or {}).get(c), "due": due(venc.get(c))})
    items.sort(key=lambda i: (i["group"] != "fixa", i["due"] or "9999", i["name"]))
    falta = round(sum(i["amount"] for i in items if not i["paid"] and i["amount"] is not None), 2)
    pago = round(sum(i["amount"] for i in items if i["paid"] and i["amount"] is not None), 2)
    sem_valor = sum(1 for i in items if i["amount"] is None and not i["paid"])
    return {"ym": ym, "label": P.label(ym), "items": items, "falta": falta, "pago": pago,
            "sem_valor": sem_valor, "total": round(falta + pago, 2)}


def week_info(conn, cfg, t):
    ws, we = P.week_range(t)
    q = "SELECT COALESCE(SUM(amount),0) FROM spends WHERE ignored=0 AND date BETWEEN ? AND ?"
    spent = conn.execute(q, (ws.isoformat(), we.isoformat())).fetchone()[0]
    prev = conn.execute(q, ((ws - timedelta(days=7)).isoformat(), (ws - timedelta(days=1)).isoformat())).fetchone()[0]
    since = last_payday(t, int(cfg.get("salario_dia") or 30))
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
            "proximo_salario": next_payday(t + timedelta(days=1), int(cfg.get("salario_dia") or 30)).isoformat(),
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
    for ym in (P.ym_of(t), P.add_months(P.ym_of(t), 1)):
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
    cur = P.ym_of(t)
    if month:
        return month
    p = month_panel(conn, cfg, cur, t)
    if p["items"] and p["falta"] == 0 and p["sem_valor"] == 0:
        return P.add_months(cur, 1)
    return cur


def summary(conn, month=None, extra=None):
    t = C.today()
    raw = db.get_cfg(conn)
    cfg = effective_cfg(raw, t)
    month = resolve_month(conn, cfg, t, month)
    recurring = db.rows(conn, "SELECT * FROM recurring ORDER BY id")
    inst = db.rows(conn, "SELECT * FROM installments ORDER BY id")
    ov_rows = db.rows(conn, "SELECT * FROM overrides ORDER BY month, card")
    ov = {(r["card"], r["month"]): r for r in ov_rows}
    parc = parcelas(conn, t)
    proj = P.project(cfg, recurring, inst, ov, t, n=6)
    public_cfg = {k: raw.get(k) for k in (
        "salario", "decimo1", "decimo2", "decimo1_mes", "decimo2_mes", "aumento_on", "aumento_pct",
        "aumento_from", "teto_semanal", "caixa", "caixa_ref", "salario_dia", "ignore_terms", "pix_max",
        "ciclo_extra", "cartoes", "cartao_venc", "last_sync", "caixa_banco", "cartao_usado", "notif",
        "auto", "sync_auto", "salario_regra", "entradas", "last_force", "last_tick", "sync_detalhe", "ciclo_info")}
    public_cfg["caixa_desatualizado"] = bool(raw.get("caixa_ref") and raw["caixa_ref"] != P.ym_of(t))
    out = {
        "hoje": t.isoformat(), "mes": month_panel(conn, cfg, month, t),
        "semana": week_info(conn, cfg, t), "parcelas": parc,
        "parcelas_total_restante": round(sum(p["restante_valor"] for p in parc), 2),
        "parcelas_mensal": round(sum(p["amount"] for p in parc if p["restante"] > 0), 2),
        "vencimentos": upcoming(conn, cfg, t),
        "recorrentes": recurring, "overrides": ov_rows, "proj": proj, "config": public_cfg,
        "notificacoes": db.rows(conn, "SELECT * FROM notif_log ORDER BY sent_at DESC LIMIT 20"),
        "aparelhos": db.rows(conn, "SELECT id, label, created, last_ok, fails FROM push_subs ORDER BY id"),
    }
    out.update(extra or {})
    return out
