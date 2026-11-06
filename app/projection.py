"""Cálculos do app: previsão de faturas e projeção mês a mês.

Regra de tempo (a mesma que você já vive hoje): o dinheiro que entra no mês M
paga as faturas do ciclo de M (que vencem em M+1) e as contas fixas de M+1.
"""
import calendar
from datetime import timedelta

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def ym_of(d):
    return f"{d.year:04d}-{d.month:02d}"


def add_months(ym, n):
    y, m = map(int, ym.split("-"))
    t = y * 12 + (m - 1) + n
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def months_between(a, b):
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return (yb - ya) * 12 + (mb - ma)


def label(ym):
    y, m = map(int, ym.split("-"))
    return f"{MESES[m - 1]}/{str(y)[2:]}"


def week_range(d):
    start = d - timedelta(days=d.weekday())  # segunda-feira
    return start, start + timedelta(days=6)


def card_forecast(card, income_month, current_month, recurring, installments, overrides, cycle_extra):
    """Quanto vai na fatura de `card` paga com o dinheiro de `income_month`."""
    idx = months_between(current_month, income_month)
    ov = overrides.get((card, income_month))
    if ov:
        note = ov.get("note") or "Valor informado por você"
        return {"card": card, "total": round(ov["amount"], 2), "source": "informado",
                "parts": [{"name": note, "amount": round(ov["amount"], 2), "kind": "informado"}]}
    parts = []
    for r in recurring:
        if r.get("card") == card:
            parts.append({"name": r["name"], "amount": r["amount"], "kind": "assinatura"})
    if idx >= 0:
        for i in installments:
            if i["card"] != card:
                continue
            restante = i["total"] - i["paid_count"]
            # offset = quantas faturas depois da última já contada (fatura vence em income_month + 1)
            offset = months_between(i.get("last_bill") or current_month, income_month) + 1
            if 1 <= offset <= restante:
                n = i["paid_count"] + offset
                parts.append({"name": f"{i['name']} ({n}/{i['total']})",
                              "amount": i["amount"], "kind": "parcela"})
    if idx == 0:
        extra = cycle_extra.get(card, 0)
        if extra:
            parts.append({"name": "Compras do ciclo atual", "amount": extra, "kind": "variavel"})
    total = round(sum(p["amount"] for p in parts), 2)
    return {"card": card, "total": total, "parts": parts, "source": "previsto"}


def project(cfg, recurring, installments, overrides, today, n=3):
    cur = ym_of(today)
    fixas = round(sum(r["amount"] for r in recurring if not r.get("card")), 2)
    months = []
    acc = 0.0
    for k in range(n):
        ym = add_months(cur, k)
        y, m = map(int, ym.split("-"))
        salario = cfg["salario"]
        if cfg.get("aumento_on") and ym >= cfg["aumento_from"]:
            salario = salario * (1 + cfg["aumento_pct"] / 100.0)
        decimo = 0.0
        if m == cfg["decimo1_mes"]:
            decimo += cfg["decimo1"]
        if m == cfg["decimo2_mes"]:
            decimo += cfg["decimo2"]
        caixa = cfg["caixa"] if k == 0 else 0.0
        recebido = 0.0
        if k == 0:
            # Se o salário deste mês já caiu, ele já está no caixa: só entra o que falta receber.
            recebido = float(((cfg.get("entradas") or {}).get(ym) or {}).get("total") or 0)
            falta = max(salario + decimo - recebido, 0.0)
            salario, decimo = (min(salario, falta), max(falta - salario, 0.0)) if recebido else (salario, decimo)
        entra = salario + decimo + caixa
        cards = [card_forecast(c, ym, cur, recurring, installments, overrides, cfg["ciclo_extra"])
                 for c in cfg["cartoes"]]
        cart = round(sum(c["total"] for c in cards), 2)
        dim = calendar.monthrange(y, m)[1]
        dias = dim - today.day + 1 if k == 0 else dim
        semanas = max(dias, 1) / 7.0
        sobra = entra - cart - fixas
        livre = cfg["teto_semanal"] * semanas
        caixinha = sobra - livre
        acc += caixinha
        months.append({
            "ym": ym, "label": label(ym),
            "entra": round(entra, 2), "salario": round(salario, 2), "decimo": round(decimo, 2),
            "caixa": round(caixa, 2), "recebido": round(recebido, 2),
            "cartoes": cards, "cart_total": cart, "fixas": fixas,
            "sobra": round(sobra, 2), "semanas": round(semanas, 1),
            "livre": round(livre, 2), "caixinha": round(caixinha, 2),
            "teto_max": round(max(0.0, sobra) / semanas, 2),
        })
    return {"months": months, "acumulado": round(acc, 2), "fixas": fixas}
