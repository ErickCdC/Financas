"""Casos reais achados na análise de confiabilidade (nov/2025 a nov/2026)."""
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import bank, db, finance  # noqa: E402
from app import config as C  # noqa: E402
from app import seed as S  # noqa: E402


_ORIG_DIR = C.DATA_DIR


def tearDownModule():
    C.DATA_DIR = _ORIG_DIR  # os outros testes usam o diretório deles


def fresh_db():
    C.DATA_DIR = Path(tempfile.mkdtemp())
    os.environ["NO_SEED"] = "1"
    try:
        db.init()
    finally:
        os.environ.pop("NO_SEED", None)


def spend(conn, d, desc, amount, card="PicPay", ext=None, ignored=0, cat="outros"):
    conn.execute("INSERT INTO spends(date,description,amount,category,source,ext_id,ignored,card) "
                 "VALUES(?,?,?,?,'banco',?,?,?)", (d, desc, amount, cat, ext or f"x:{d}:{desc}:{amount}", ignored, card))


class Subscriptions(unittest.TestCase):
    def setUp(self):
        fresh_db()

    def test_same_value_purchases_are_not_a_subscription(self):
        with db.connect() as conn:
            conn.execute("INSERT INTO recurring(name,amount,card,match) VALUES('Armazenamento Gigi',19.90,'PicPay','')")
            for i, h in enumerate(("20:43", "20:47", "21:22")):
                spend(conn, "2026-10-24", "ROUPA LIMPA*VMT*roupa    .CABO FRIO  BRA", 19.90, ext=f"l{i}")
            bank.ignore_subscription_charges(conn)
            n = conn.execute("SELECT COUNT(*) FROM spends WHERE ignored=0").fetchone()[0]
        self.assertEqual(n, 3)

    def test_repeating_charge_is_ignored_once_a_month(self):
        with db.connect() as conn:
            conn.execute("INSERT INTO recurring(name,amount,card,match) VALUES('Clube iFood',7.95,'PicPay','')")
            spend(conn, "2026-08-05", "IFD*IFOOD CLUB           .Osasco     BRA", 7.95)
            spend(conn, "2026-09-05", "IFD*IFOOD CLUB           .Osasco     BRA", 7.95)
            spend(conn, "2026-09-20", "PADARIA                  .RIO DAS OS BRA", 7.95)
            bank.ignore_subscription_charges(conn)
            rows = {r["date"]: r["ignored"] for r in db.rows(conn, "SELECT date, ignored FROM spends")}
        self.assertEqual(rows, {"2026-08-05": 1, "2026-09-05": 1, "2026-09-20": 0})

    def test_match_words_win_and_old_marks_are_undone(self):
        with db.connect() as conn:
            conn.execute("INSERT INTO recurring(name,amount,card,match) VALUES('Clube iFood',7.95,'PicPay','ifood club')")
            spend(conn, "2026-10-05", "IFD*IFOOD CLUB .Osasco BRA", 7.95)
            spend(conn, "2026-10-06", "HELP DRINK .RIO DAS OS BRA", 7.95, ignored=1, cat="assinatura")
            bank.ignore_subscription_charges(conn)
            rows = {r["description"][:6]: (r["ignored"], r["category"]) for r in db.rows(conn, "SELECT * FROM spends")}
        self.assertEqual(rows["IFD*IF"], (1, "assinatura"))
        self.assertEqual(rows["HELP D"], (0, None))


class Bills(unittest.TestCase):
    MP = [  # Mercado Pago, como o banco devolve (mar/26: pago antes de fechar)
        {"dueDate": "2026-02-04T00:00:00Z", "billClosingDate": "2026-01-29T00:00:00Z", "totalAmount": "1029.35",
         "payments": [{"id": "a", "paymentDate": "2026-01-30", "amount": "1029.35", "valueType": "FULL_PAYMENT"},
                      {"id": "b", "paymentDate": "2026-02-20", "amount": "18.39", "valueType": "OTHER_PAYMENT"},
                      {"id": "c", "paymentDate": "2026-02-24", "amount": "150.00", "valueType": "INSTALLMENT_PAYMENT"},
                      {"id": "d", "paymentDate": "2026-02-27", "amount": "1335.45", "valueType": "INSTALLMENT_PAYMENT"}]},
        {"dueDate": "2026-03-05T00:00:00Z", "billClosingDate": "2026-02-28T00:00:00Z", "totalAmount": "148.98",
         "payments": [{"id": "e", "paymentDate": "2026-03-01", "amount": "148.98", "valueType": "FULL_PAYMENT"}]},
    ]

    def test_prepaid_cycle_is_added_back(self):
        self.assertAlmostEqual(bank._prepaid(self.MP, 1), 1485.45, places=2)  # 148,98 + 1.485,45 = 1.634,43
        self.assertEqual(bank._prepaid(self.MP, 0), 0.0)

    def test_late_payment_of_previous_bill_is_not_prepayment(self):
        pic = [{"dueDate": "2026-02-05", "billClosingDate": "2026-01-29", "totalAmount": "47.75", "payments": []},
               {"dueDate": "2026-03-05", "billClosingDate": "2026-02-26", "totalAmount": "30.68",
                "payments": [{"id": "p", "paymentDate": "2026-02-11", "amount": "47.75", "valueType": "FULL_PAYMENT"}]}]
        self.assertEqual(bank._prepaid(pic, 1), 0.0)

    def test_purchase_on_closing_day_goes_to_next_bill(self):
        cfg = dict(S.DEFAULT_SETTINGS, cartao_venc={"Nubank": 1}, ciclo_info={"Nubank": {"fecha_antes": 7}})
        per = finance.bill_period(cfg, "Nubank", 2026, 11)
        # fatura de 03/11: Pichau 5/6 e Ingresso de 24/09 entram; o Uber de 25/10 já é da seguinte
        self.assertEqual((per["compras_de"], per["ate"], per["fecha"]), ("2026-09-24", "2026-10-24", "2026-10-25"))


class Feed(unittest.TestCase):
    def test_own_name_without_space_is_ignored(self):
        x = {"description": "ERICKCAMARA              .Sao Paulo  BRA", "amount": "50", "type": "DEBIT",
             "date": "2026-07-31T12:00:00Z"}
        self.assertIsNone(bank.to_spend("CREDIT", x, ["erick camara"], 200))

    def test_big_transfer_on_card_is_out_of_the_ceiling(self):
        big = {"category": "Transfers", "amount": "1235.88", "type": "DEBIT"}
        small = {"category": "Transfers", "amount": "32.00", "type": "DEBIT"}
        self.assertTrue(bank.card_transfer("CREDIT", big, 200))
        self.assertFalse(bank.card_transfer("CREDIT", small, 200))
        self.assertFalse(bank.card_transfer("BANK", big, 200))


def tx(i, d, desc, amount, typ, **kw):
    return {"id": i, "date": d, "description": desc, "amount": str(amount), "type": typ, "status": "POSTED", **kw}


class Apply(unittest.TestCase):
    def setUp(self):
        fresh_db()
        with db.connect() as conn:
            for k, v in S.DEFAULT_SETTINGS.items():
                db.put_setting(conn, k, v)

    def run_apply(self, accounts, t):
        with db.connect() as conn:
            res, events = bank.apply(conn, {"accounts": accounts}, t)
            return res, events, db.get_cfg(conn), db.rows(conn, "SELECT * FROM payments")

    def test_mirrored_refund_does_not_pay_the_bill(self):
        card = {"id": "mp", "type": "CREDIT", "bank": "Mercado Pago", "balance": 0, "credit": {}, "bills": None, "tx": [
            tx("r", "2026-10-30T03:00:00Z", "DL*AliExpress BR", -4.74, "CREDIT", operationType="ESTORNO"),
            tx("p", "2026-10-30T03:00:00Z", "Pagamento recebido", -4.74, "CREDIT", operationType="PAGAMENTO_FATURA",
               category="Credit card payment")]}
        _, _, _, pays = self.run_apply([card], date(2026, 11, 1))
        self.assertFalse([p for p in pays if p["key"] == "bill:Mercado Pago" and p["paid"]])

    def test_due_day_learned_from_bills(self):
        bills = {"results": [{"dueDate": d, "billClosingDate": c, "totalAmount": "1", "payments": []} for d, c in
                             (("2026-09-01", "2026-08-25"), ("2026-10-01", "2026-09-24"), ("2026-11-03", "2026-10-25"))]}
        card = {"id": "nu", "type": "CREDIT", "bank": "Nubank", "balance": 0, "credit": {}, "bills": bills, "tx": []}
        _, _, cfg, _ = self.run_apply([card], date(2026, 11, 7))
        self.assertEqual(cfg["cartao_venc"]["Nubank"], 1)
        self.assertEqual(cfg["ciclo_info"]["Nubank"]["fecha_antes"], 7)

    def test_low_salary_and_thirteenth_are_seen(self):
        conta = {"id": "c", "type": "BANK", "bank": "Mercado Pago", "balance": 0, "credit": {}, "bills": None, "tx": [
            tx("s", "2026-09-30T10:45:51Z", "Transferência recebida ERICK CAMARA DE CASTRO", 1390, "CREDIT",
               category="Same person transfer")]}
        _, _, cfg, _ = self.run_apply([conta], date(2026, 10, 2))
        self.assertEqual(cfg["entradas"]["2026-09"]["total"], 1390)
        # 13º: 2ª parcela 11 dias antes do salário de dezembro, ainda antes do salário cair
        conta["tx"] = [tx("d", "2026-12-18T10:40:37Z", "Transferência recebida ERICK CAMARA DE CASTRO", 914, "CREDIT",
                          category="Same person transfer")]
        _, events, cfg, _ = self.run_apply([conta], date(2026, 12, 20))
        self.assertEqual(cfg["entradas"]["2026-12"]["total"], 914)
        self.assertTrue(any(e[1] == "13º recebido" for e in events))


if __name__ == "__main__":
    unittest.main()


class Fgts(unittest.TestCase):
    F = {"saldo": 1285.0, "data": "2026-11-08", "mensal": 205.19, "libera": "2028-05-12", "rende_pct": 0}  # data fixa só para o teste

    def test_deposits_on_the_20th_and_13th_in_december(self):
        self.assertEqual(finance.fgts_saldo(self.F, date(2026, 11, 19)), 1285.0)
        self.assertEqual(finance.fgts_saldo(self.F, date(2026, 11, 20)), 1490.19)
        self.assertEqual(finance.fgts_saldo(self.F, date(2026, 12, 20)), round(1285 + 3 * 205.19, 2))
        # 18 depósitos + 2 de 13º até a liberação
        self.assertEqual(finance.fgts_saldo(self.F, date(2028, 5, 12)), round(1285 + 20 * 205.19, 2))

    def test_plan_and_validation(self):
        plan = finance.fgts_plan({"fgts": self.F}, date(2026, 11, 8))
        self.assertEqual(plan["hoje"], 1285.0)
        self.assertEqual(plan["marcos"][1], {"data": "2028-05-12", "saldo": plan["na_liberacao"], "libera": True})
        self.assertEqual([m["data"] for m in plan["marcos"]], ["2027-05-08", "2028-05-12", "2029-05-12"])
        from app import validate as V
        self.assertEqual(V.fgts({"saldo": "1.285,00".replace(".", ""), "libera": ""}), {"saldo": 1285.0, "libera": None})
        with self.assertRaises(ValueError):
            V.fgts({"saldo": -1})


class FgtsFollowsSalary(unittest.TestCase):
    F = {"saldo": 1000.0, "data": "2026-06-01", "libera": "2027-10-25", "rende_pct": 0}

    def test_gross_from_net_matches_payslips(self):
        self.assertAlmostEqual(finance.bruto_de_liquido(2372), 2579.87, places=1)  # holerite out: 2.579,30 (+troco)
        self.assertAlmostEqual(finance.bruto_de_liquido(3174), 3480.23, places=1)  # holerite mai: 3.479,92

    def test_overtime_month_and_raise(self):
        cfg = {"salario": 2372, "fgts": self.F, "entradas": {"2026-06": {"total": 2698}, "2026-07": {"total": 2380}}}
        dep = finance.fgts_deposito(cfg)
        self.assertAlmostEqual(dep(2026, 7), 0.08 * finance.bruto_de_liquido(2698), places=2)  # jun com hora extra
        self.assertAlmostEqual(dep(2026, 9), 0.08 * finance.bruto_de_liquido(2372), places=2)  # ago: salário atual
        antes = dep(2027, 2)
        cfg.update(aumento_on=True, aumento_pct=10, aumento_from="2027-01")
        self.assertAlmostEqual(finance.fgts_deposito(cfg)(2027, 2), 0.08 * finance.bruto_de_liquido(2372 * 1.1), places=2)
        self.assertGreater(finance.fgts_deposito(cfg)(2027, 2), antes)


class Caixinha(unittest.TestCase):
    G = [{"id": 1, "share": 70, "target": 1000, "saved": 900}, {"id": 2, "share": 30, "target": 5000, "saved": 0},
         {"id": 3, "share": 0, "target": 300, "saved": 0}]

    def falta(self):
        return {g["id"]: g["target"] - g["saved"] for g in self.G}

    def test_one_at_a_time_spills_to_next(self):
        partes, livre = finance.dividir(400, self.G, "ordem", self.falta())
        self.assertEqual(partes, {1: 100, 2: 300})
        self.assertEqual(livre, 0)

    def test_percent_split_gives_finished_share_to_others(self):
        f = self.falta()
        partes, livre = finance.dividir(400, self.G, "divisao", f)
        self.assertEqual(partes[1], 100)  # 70% seria 280, mas só faltavam 100
        self.assertAlmostEqual(partes[2], 300, places=2)  # o resto vai para quem continua
        self.assertNotIn(3, partes)  # 0% não recebe enquanto outra meta com % estiver aberta
        self.assertEqual(livre, 0)

    def test_everything_done_leaves_free_money(self):
        partes, livre = finance.dividir(500, self.G[:1], "ordem", {1: 100})
        self.assertEqual((partes, livre), ({1: 100}, 400))

    def test_plan_with_new_goal_changes_eta(self):
        fresh_db()
        months = [{"ym": f"2026-{m:02d}", "caixinha": 500} for m in (11, 12)] + [{"ym": "2027-01", "caixinha": 500}]
        with db.connect() as conn:
            conn.execute("INSERT INTO goals(name,target,saved,ordem,share) VALUES('Reserva',1000,0,0,50)")
            p1 = finance.goals_plan(conn, {"months": months}, {"metas_modo": "ordem"})
            conn.execute("INSERT INTO goals(name,target,saved,ordem,share) VALUES('Viagem',1000,0,1,50)")
            p2 = finance.goals_plan(conn, {"months": months}, {"metas_modo": "divisao"})
        self.assertEqual(p1["metas"][0]["eta"], "2026-12")
        self.assertEqual([m["eta"] for m in p2["metas"]], ["2027-02", "2027-02"])  # 250 + 250 por salário
        self.assertEqual(p2["alocacao"][0]["partes"], [{"id": 1, "valor": 250.0}, {"id": 2, "valor": 250.0}])
