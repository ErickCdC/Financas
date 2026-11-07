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
        self.assertAlmostEqual(cards["PicPay"], 7.95 + 19.90 + 344.62, places=2)  # assinaturas + RecargaPay 1/3
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


class BusinessDayPayday(unittest.TestCase):
    cfg = {"salario_tipo": "util", "salario_dia": 30}

    def test_last_business_day(self):
        from app import paydays as PD
        got = {(y, m): PD.payday_of(y, m, self.cfg).isoformat() for y, m in
               [(2026, 10), (2026, 11), (2026, 12), (2027, 1), (2027, 2), (2027, 4)]}
        self.assertEqual(got, {(2026, 10): "2026-10-30", (2026, 11): "2026-11-30", (2026, 12): "2026-12-30",
                               (2027, 1): "2027-01-29", (2027, 2): "2027-02-26", (2027, 4): "2027-04-30"})

    def test_income_month_switches_on_payday(self):
        from app import paydays as PD
        self.assertEqual(PD.income_month(date(2026, 11, 7), self.cfg), "2026-11")
        self.assertEqual(PD.income_month(date(2026, 11, 30), self.cfg), "2026-12")

    def test_rent_due_at_month_end_belongs_to_reference_month(self):
        # contas de outubro (chave interna 2026-11): aluguel dia 31 vence em outubro; faculdade dia 5, em novembro
        self.assertEqual(finance.due_date("2026-11", 31, self.cfg), "2026-10-31")
        self.assertEqual(finance.due_date("2026-11", 5, self.cfg), "2026-11-05")


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

    def test_card_installment_is_not_daily_spend(self):
        meta = {"description": "RecargaPay  *EPARC01/03", "amount": "344.62", "type": "DEBIT", "date": "2026-10-31T12:14:42Z",
                "creditCardMetadata": {"installmentNumber": 1, "totalInstallments": 3}}
        self.assertIsNone(bank.to_spend("CREDIT", meta, self.ign, 200))
        self.assertIsNone(bank.to_spend("CREDIT", {**meta, "creditCardMetadata": {}}, self.ign, 200))  # pelo texto
        burger = {"description": "BB BURGER                .CASIMIRO D BRA", "amount": "12.50", "type": "DEBIT", "date": "2026-11-04T18:12:12Z"}
        self.assertEqual(bank.to_spend("CREDIT", burger, self.ign, 200)[2], 12.5)

    def test_bank_pix_small_only(self):
        small = {"description": "Pix enviado MERCEARIA", "amount": "-12.00", "type": "DEBIT", "operationType": "PIX", "date": "2026-10-31T15:26:13Z"}
        rent = {"description": "Pix enviado Fulano", "amount": "-850.00", "type": "DEBIT", "operationType": "PIX", "date": "2026-10-31T15:16:14Z"}
        self.assertEqual(bank.to_spend("BANK", small, self.ign, 200)[2], 12.0)
        self.assertIsNone(bank.to_spend("BANK", rent, self.ign, 200))

    def test_local_date_uses_brazil_time(self):
        self.assertEqual(bank.local_date("2026-11-01T02:30:00.000Z"), "2026-10-31")


if __name__ == "__main__":
    unittest.main()


class Planning(unittest.TestCase):
    def test_planned_expense_hits_salary_before_it(self):
        cfg, rec, inst, ov = base()
        planned = [{"kind": "saida", "name": "Caução", "amount": 600, "month": "2026-11", "done": 0},
                   {"kind": "entrada", "name": "Horas extras", "amount": 300, "month": "2026-12", "done": 0},
                   {"kind": "saida", "name": "Já paga", "amount": 999, "month": "2026-12", "done": 1}]
        a = P.project(cfg, rec, inst, ov, TODAY)["months"]
        b = P.project(cfg, rec, inst, ov, TODAY, planned=planned)["months"]
        self.assertAlmostEqual(a[0]["caixinha"] - b[0]["caixinha"], 600, places=2)   # conta de nov sai do salário de nov
        self.assertAlmostEqual(b[1]["caixinha"] - a[1]["caixinha"], 300, places=2)   # entrada de dez entra no salário de dez

    def test_auto_teto_leaves_the_goal(self):
        cfg, rec, inst, ov = base()
        cfg.update(teto_auto=True, meta_caixinha=300)
        for m in P.project(cfg, rec, inst, ov, TODAY)["months"]:
            if m["sobra"] > 300:
                self.assertTrue(300 <= m["caixinha"] < 300 + 5 * m["semanas_x"] + 0.01, m)

    def test_merchant_names(self):
        self.assertEqual(finance.merchant("99* POP 06NOV"), "99")
        self.assertEqual(finance.merchant("HELP GAS                 .RIO DAS OS BRA"), "help gas")
        self.assertEqual(finance.merchant("Pagamento com Pix OSORIO S 2 LOJA"), "osorio s")
