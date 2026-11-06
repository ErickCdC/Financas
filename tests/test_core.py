import os
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import bank, finance  # noqa: E402
from app import projection as P  # noqa: E402
from app import seed as S  # noqa: E402

TODAY = date(2026, 11, 4)


def base():
    rec = [dict(zip(("name", "amount", "card"), r)) for r in S.RECURRING]
    inst = [dict(zip(("name", "card", "amount", "total", "paid_count"), i)) for i in S.INSTALLMENTS]
    ov = {(c, m): {"amount": a, "note": n} for c, m, a, n in S.OVERRIDES}
    return dict(S.DEFAULT_SETTINGS), rec, inst, ov


class Projection(unittest.TestCase):
    def test_november_numbers(self):
        cfg, rec, inst, ov = base()
        nov = P.project(cfg, rec, inst, ov, TODAY)["months"][0]
        cards = {c["card"]: c["total"] for c in nov["cartoes"]}
        self.assertAlmostEqual(cards["Nubank"], 206.27, places=2)
        self.assertAlmostEqual(cards["Mercado Pago"], 789.96, places=2)
        self.assertAlmostEqual(cards["PicPay"], 1338.00, places=2)
        self.assertAlmostEqual(nov["fixas"], 1301.56, places=2)
        self.assertAlmostEqual(nov["entra"], 2372 + 1280 + 214.74, places=2)

    def test_installments_drop_off(self):
        cfg, rec, inst, ov = base()
        dez = {c["card"]: c["total"] for c in P.project(cfg, rec, inst, ov, TODAY)["months"][1]["cartoes"]}
        self.assertAlmostEqual(dez["Nubank"], 0, places=2)
        self.assertAlmostEqual(dez["PicPay"], 7.95 + 19.90 + 344.62, places=2)

    def test_raise_only_when_enabled(self):
        cfg, rec, inst, ov = base()
        jan = P.project(cfg, rec, inst, ov, TODAY)["months"][2]
        self.assertAlmostEqual(jan["salario"], 2372, places=2)
        cfg["aumento_on"] = True
        jan = P.project(cfg, rec, inst, ov, TODAY)["months"][2]
        self.assertAlmostEqual(jan["salario"], 2372 * 1.25, places=2)

    def test_week_range_is_monday_to_sunday(self):
        ws, we = P.week_range(TODAY)
        self.assertEqual((ws.isoformat(), we.isoformat()), ("2026-11-02", "2026-11-08"))


class Paydays(unittest.TestCase):
    def test_last_and_next(self):
        self.assertEqual(finance.last_payday(date(2026, 11, 5), 30), date(2026, 10, 30))
        self.assertEqual(finance.last_payday(date(2026, 11, 30), 30), date(2026, 11, 30))
        self.assertEqual(finance.next_payday(date(2027, 2, 1), 30), date(2027, 2, 28))  # fevereiro curto

    def test_stale_cash_is_ignored(self):
        cfg = dict(S.DEFAULT_SETTINGS)
        self.assertEqual(finance.effective_cfg(cfg, date(2026, 11, 20))["caixa"], 214.74)
        self.assertEqual(finance.effective_cfg(cfg, date(2026, 12, 2))["caixa"], 0.0)


class BankRules(unittest.TestCase):
    ign = S.DEFAULT_SETTINGS["ignore_terms"]

    def test_card_debit_is_spend(self):
        x = {"description": "Uber", "amount": "8.94", "type": "DEBIT", "date": "2026-10-30T22:28:22.780Z"}
        self.assertEqual(bank.to_spend("CREDIT", x, self.ign, 200), ("2026-10-30", "Uber", 8.94))

    def test_refund_is_negative(self):
        x = {"description": "Estorno de Uber - NuPay", "amount": "-10.95", "type": "CREDIT", "date": "2026-10-30T00:00:00.001Z"}
        self.assertEqual(bank.to_spend("CREDIT", x, self.ign, 200)[2], -10.95)

    def test_card_payment_and_installments_ignored(self):
        pay = {"description": "Pagamento recebido", "amount": "-1062.98", "type": "CREDIT", "date": "2026-10-30T03:00:00.000Z"}
        par = {"description": "Parcela 6/6 PICHAU INFORMATICA", "amount": "178.33", "type": "DEBIT", "date": "2026-10-29T10:00:00Z"}
        self.assertIsNone(bank.to_spend("CREDIT", pay, self.ign, 200))
        self.assertIsNone(bank.to_spend("CREDIT", par, self.ign, 200))

    def test_bank_pix_small_only(self):
        small = {"description": "Pix enviado MERCEARIA", "amount": "-12.00", "type": "DEBIT", "operationType": "PIX", "date": "2026-10-31T15:26:13Z"}
        rent = {"description": "Pix enviado Fulano", "amount": "-850.00", "type": "DEBIT", "operationType": "PIX", "date": "2026-10-31T15:16:14Z"}
        self.assertEqual(bank.to_spend("BANK", small, self.ign, 200)[2], 12.0)
        self.assertIsNone(bank.to_spend("BANK", rent, self.ign, 200))

    def test_local_date_uses_brazil_time(self):
        self.assertEqual(bank.local_date("2026-11-01T02:30:00.000Z"), "2026-10-31")


if __name__ == "__main__":
    unittest.main()
