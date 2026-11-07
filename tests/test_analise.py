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
