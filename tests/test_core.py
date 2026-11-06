import os
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("APP_PASSWORD", "teste12345")
import projection as P  # noqa: E402
import seed as S  # noqa: E402
import server  # noqa: E402

TODAY = date(2026, 11, 4)


def base():
    cfg = dict(S.DEFAULT_SETTINGS)
    rec = [{"id": i + 1, "name": n, "amount": a, "card": c} for i, (n, a, c) in enumerate(S.RECURRING)]
    inst = [{"id": i + 1, "name": n, "card": c, "amount": a, "total": t, "paid_count": p}
            for i, (n, c, a, t, p) in enumerate(S.INSTALLMENTS)]
    ov = {(c, m): {"card": c, "month": m, "amount": a, "note": n} for c, m, a, n in S.OVERRIDES}
    return cfg, rec, inst, ov


class Projection(unittest.TestCase):
    def test_add_months(self):
        self.assertEqual(P.add_months("2026-11", 2), "2027-01")
        self.assertEqual(P.add_months("2026-01", -1), "2025-12")

    def test_first_bill_matches_hand_calculation(self):
        cfg, rec, inst, ov = base()
        r = P.project(cfg, rec, inst, ov, TODAY)
        nov = r["months"][0]
        cards = {c["card"]: c["total"] for c in nov["cartoes"]}
        self.assertAlmostEqual(cards["Nubank"], 206.27, places=2)          # Pichau 6/6 + compras
        self.assertAlmostEqual(cards["Mercado Pago"], 789.96, places=2)    # 428,02+141,90+39,21+113,85+66,98
        self.assertAlmostEqual(cards["PicPay"], 1338.00, places=2)         # valor informado
        self.assertAlmostEqual(nov["fixas"], 1301.56, places=2)
        self.assertAlmostEqual(nov["entra"], 2372 + 1280 + 214.74, places=2)

    def test_installments_drop_off(self):
        cfg, rec, inst, ov = base()
        r = P.project(cfg, rec, inst, ov, TODAY)
        dez = {c["card"]: c["total"] for c in r["months"][1]["cartoes"]}
        self.assertAlmostEqual(dez["Nubank"], 0.0, places=2)               # Pichau acabou
        self.assertAlmostEqual(dez["Mercado Pago"], 141.90 + 113.85, places=2)
        self.assertAlmostEqual(dez["PicPay"], 7.95 + 19.90 + 344.62, places=2)

    def test_raise_only_from_january(self):
        cfg, rec, inst, ov = base()
        cfg["aumento_on"] = True
        r = P.project(cfg, rec, inst, ov, TODAY)
        self.assertAlmostEqual(r["months"][1]["salario"], 2372.0, places=2)
        self.assertAlmostEqual(r["months"][2]["salario"], 2372 * 1.25, places=2)

    def test_accumulated_is_sum(self):
        cfg, rec, inst, ov = base()
        r = P.project(cfg, rec, inst, ov, TODAY)
        self.assertAlmostEqual(r["acumulado"], sum(m["caixinha"] for m in r["months"]), delta=0.05)

    def test_week_range_is_monday_to_sunday(self):
        ws, we = P.week_range(TODAY)  # quarta 04/11
        self.assertEqual((ws.isoformat(), we.isoformat()), ("2026-11-02", "2026-11-08"))


class BankRules(unittest.TestCase):
    ign = S.DEFAULT_SETTINGS["ignore_terms"]

    def test_card_debit_is_spend(self):
        x = {"description": "Uber", "amount": "8.94", "type": "DEBIT", "date": "2026-10-30T22:28:22.780Z"}
        self.assertEqual(server.to_spend("CREDIT", x, self.ign, 200), ("2026-10-30", "Uber", 8.94))

    def test_refund_is_negative(self):
        x = {"description": "Estorno de Uber - NuPay", "amount": "-10.95", "type": "CREDIT", "date": "2026-10-30T00:00:00.001Z"}
        self.assertEqual(server.to_spend("CREDIT", x, self.ign, 200)[2], -10.95)

    def test_card_payment_and_installments_ignored(self):
        pay = {"description": "Pagamento recebido", "amount": "-1062.98", "type": "CREDIT", "date": "2026-10-30T03:00:00.000Z"}
        par = {"description": "Parcela 6/6 PICHAU INFORMATICA", "amount": "178.33", "type": "DEBIT", "date": "2026-10-29T10:00:00Z"}
        self.assertIsNone(server.to_spend("CREDIT", pay, self.ign, 200))
        self.assertIsNone(server.to_spend("CREDIT", par, self.ign, 200))

    def test_bank_pix_small_only(self):
        small = {"description": "Pix enviado MERCEARIA", "amount": "-12.00", "type": "DEBIT", "operationType": "PIX", "date": "2026-10-31T15:26:13Z"}
        rent = {"description": "Pix enviado Fulano", "amount": "-850.00", "type": "DEBIT", "operationType": "PIX", "date": "2026-10-31T15:16:14Z"}
        selft = {"description": "Pix recebido ERICK CAMARA DE CASTRO", "amount": "850.00", "type": "CREDIT", "operationType": "PIX", "date": "2026-10-31T15:14:46Z"}
        self.assertEqual(server.to_spend("BANK", small, self.ign, 200)[2], 12.0)
        self.assertIsNone(server.to_spend("BANK", rent, self.ign, 200))
        self.assertIsNone(server.to_spend("BANK", selft, self.ign, 200))

    def test_local_date_uses_brazil_time(self):
        self.assertEqual(server.local_date("2026-11-01T02:30:00.000Z"), "2026-10-31")


if __name__ == "__main__":
    unittest.main()
