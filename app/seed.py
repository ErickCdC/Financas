"""Valores iniciais, tirados da nossa conversa. Tudo editável dentro do app."""
import json

DEFAULT_SETTINGS = {
    "salario": 2372.0,          # líquido de outubro (sem aumento)
    "decimo1": 1280.0,          # 13º, 1ª parcela (estimativa)
    "decimo2": 1100.0,          # 13º, 2ª parcela líquida (estimativa)
    "decimo1_mes": 11,
    "decimo2_mes": 12,
    "aumento_on": False,        # contar com o aumento de 25%?
    "aumento_pct": 25.0,
    "aumento_from": "2027-01",
    "teto_semanal": 50.0,        # plano de nov/26: fechar o mês sem ficar no vermelho
    "caixa": 214.74,            # saldo somado em 05/11/2026
    "salario_data": "2026-10-30",  # legado; o app calcula pelo dia do salário
    "salario_dia": 30,
    "caixa_ref": "2026-11",     # mês a que o caixa se refere
    "cartao_venc": {"Nubank": 3, "Mercado Pago": 4, "PicPay": 5},
    "cartoes": ["Nubank", "Mercado Pago", "PicPay"],
    "ciclo_extra": {"Nubank": 27.94, "Mercado Pago": 66.98},
    "ignore_terms": ["pichau", "activeaplus", "damari", "netinjection", "claude", "melimais",
                     "nu pagamentos", "picpay instituicao", "pagamento de fatura",
                     "pagamento recebido", "erick camara", "giga mais", "principia", "i-conecta"],
    "pix_max": 200.0,
    "last_sync": None,
    "caixa_banco": None,
    "cartao_usado": {},
    "nome": "Erick",
    "meta_caixinha": 0.0,   # quanto guardar por mês (usado pelo teto automático)
    "teto_auto": False,     # se ligado, o teto semanal se ajusta para sobrar a meta
    # Automação a cada sincronização com o banco
    "auto": {"caixa": True, "salario": True, "pagamentos": True, "faturas": True, "parcelas": True},
    "salario_regra": {"minimo_pct": 60, "termos": []},  # crédito >= 60% do salário perto do dia do salário
    "sync_auto": {"on": True, "inicio": "07:30", "fim": "23:00", "intervalo": 30, "forcar": True},
    "notif": {
        "enabled": True,
        "quiet_start": "22:30",
        "quiet_end": "07:30",
        "rules": {
            "daily_log": {"on": True, "time": "20:30"},
            "teto_alert": {"on": True, "pcts": [80, 100]},
            "bills_due": {"on": True, "time": "09:00", "days_before": 2},
            "weekly_summary": {"on": True, "time": "08:30", "weekday": 0},
            "payday": {"on": True, "time": "10:00"},
            "auto_sync": {"on": False, "time": "12:30"},
            "bank_events": {"on": True},
        },
    },
}

RECURRING = [  # nome, valor, cartão (None = paga direto da conta)
    ("Aluguel", 850.00, None),
    ("Faculdade", 235.23, None),
    ("Luz", 123.00, None),
    ("Internet (Giga Mais)", 93.33, None),
    ("Claude", 113.85, "Mercado Pago"),
    ("Clube iFood", 7.95, "PicPay"),
    ("Armazenamento Gigi", 19.90, "PicPay"),
]

INSTALLMENTS = [  # nome, cartão, valor, total, já pagas
    ("Kit PC (Pichau)", "Nubank", 178.33, 6, 5),
    ("ActiveAplus", "Mercado Pago", 428.02, 2, 1),
    ("NetInjection", "Mercado Pago", 39.21, 3, 2),
    ("Presente Gigi (Damari)", "Mercado Pago", 141.90, 12, 7),
    ("RecargaPay (pix crédito do aluguel)", "PicPay", 344.62, 3, 0),
]

PAYMENTS_NOV = {  # contas de novembro, todas já pagas
    "rec:1": 850.00, "rec:2": 235.23, "rec:3": 123.00, "rec:4": 93.33,
    "bill:Nubank": 502.21, "bill:Mercado Pago": 1062.98, "bill:PicPay": 755.15,
}

OVERRIDES = []  # faturas reais vêm do banco; informe à mão em Planejamento se precisar

SPENDS = [  # desde o salário (30/10), conferido com o banco em 05/11. Uber com estorno não entra.
    ("2026-10-30", "Uber", 8.94, "transporte", "manual"),
    ("2026-10-30", "Uber", 0.85, "transporte", "manual"),
    ("2026-10-30", "Supermercado Mancebo", 12.55, "mercado", "manual"),
    ("2026-10-31", "Mercearia", 12.00, "mercado", "manual"),
    ("2026-10-31", "MP*CarlosJoseDas", 45.00, "outros", "manual"),
    ("2026-10-31", "Supermercado Aliança", 18.98, "mercado", "manual"),
    ("2026-10-31", "Help Gas", 152.00, "casa", "manual"),
    ("2026-11-01", "Conveniência Osorio's", 14.50, "mercado", "manual"),
    ("2026-11-01", "Embapel", 13.89, "mercado", "manual"),
    ("2026-11-01", "Lorena Duarte", 34.00, "outros", "manual"),
    ("2026-11-01", "Lorena Duarte", 32.00, "outros", "manual"),
    ("2026-11-01", "Catarina Oliveira", 20.00, "mercado", "manual"),
    ("2026-11-02", "Mercado Livre", 21.98, "casa", "manual"),
    ("2026-11-03", "Help Drink", 7.50, "lazer", "manual"),
]


# Palavras que identificam o pagamento de cada conta fixa no extrato (editável no app).
MATCH = {"Aluguel": "isabela raissa", "Faculdade": "principia", "Internet (Giga Mais)": "giga mais"}


# Valor original de compras parceladas com juros (para mostrar quanto custou o crédito).
ORIGINAL = {"RecargaPay (pix crédito do aluguel)": 850.00}

# Metas em potes separados: primeiro a reserva, depois a casa.
GOALS = [
    ("Reserva de emergência", 4400.00, "reserva", "≈ 3 meses de contas fixas e assinaturas"),
    ("Casa própria (MCMV)", 45000.00, "casa", "Entrada + custos de fechamento + reserva pós-mudança (imóvel de R$ 250 mil)"),
]


def seed(conn):
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, json.dumps(v)))
    for name, amount, card in RECURRING:
        conn.execute("INSERT INTO recurring(name,amount,card,due_day,match) VALUES(?,?,?,NULL,?)",
                     (name, amount, card, MATCH.get(name)))
    for name, card, amount, total, paid in INSTALLMENTS:
        # last_bill: mês de vencimento da última fatura já contada em "paid_count" (nov/26 já foi paga)
        conn.execute("INSERT INTO installments(name,card,amount,total,paid_count,last_bill,original) "
                     "VALUES(?,?,?,?,?,'2026-11',?)", (name, card, amount, total, paid, ORIGINAL.get(name)))
    for i, (name, target, kind, note) in enumerate(GOALS):
        conn.execute("INSERT INTO goals(name,target,saved,kind,note,ordem) VALUES(?,?,0,?,?,?)", (name, target, kind, note, i))
    for key, amount in PAYMENTS_NOV.items():
        conn.execute("INSERT INTO payments(month,key,paid,amount) VALUES('2026-11',?,1,?)", (key, amount))
    for card, month, amount, note in OVERRIDES:
        conn.execute("INSERT INTO overrides(card,month,amount,note) VALUES(?,?,?,?)",
                     (card, month, amount, note))
    for d, desc, amount, cat, src in SPENDS:
        conn.execute("INSERT INTO spends(date,description,amount,category,source) VALUES(?,?,?,?,?)",
                     (d, desc, amount, cat, src))
