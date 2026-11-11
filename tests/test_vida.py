"""Módulos Saúde e Agenda: check-in, meta semanal, medidas, hábitos, lembretes e avisos."""
import base64
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config as C  # noqa: E402
from app import data, db, notify, vida  # noqa: E402

_ORIG_DIR = C.DATA_DIR


def tearDownModule():
    C.DATA_DIR = _ORIG_DIR
    os.environ.pop("FAKE_NOW", None)


def fresh():
    C.DATA_DIR = Path(tempfile.mkdtemp())
    os.environ["NO_SEED"] = "1"
    try:
        db.init()
    finally:
        os.environ.pop("NO_SEED", None)
    with db.connect() as conn:
        vida.semear_habitos(conn)


def at(s):
    os.environ["FAKE_NOW"] = s
    return C.now()


RULES = {"agenda": {"on": True, "time": "08:00"}, "treino": {"on": True, "time": "18:00"},
         "habitos": {"on": True, "time": "21:00"}}


class Treinos(unittest.TestCase):
    def setUp(self):
        fresh()
        at("2026-11-12T10:00:00")  # quinta-feira

    def test_checkin_counts_days_and_streak(self):
        with db.connect() as conn:
            for d in ("2026-11-02", "2026-11-03", "2026-11-05", "2026-11-06",  # semana passada: 4 dias, bate a meta
                      "2026-11-09", "2026-11-11"):
                vida.act_treino(conn, {"date": d, "tipo": "Musculação"})
            vida.act_treino(conn, {"tipo": "Cardio"})            # 1 toque: hoje
            vida.act_treino(conn, {"tipo": "Corrida"})           # 2º treino no mesmo dia conta 1 dia
            s = vida.resumo(conn)["saude"]
        w = s["semana"]
        self.assertEqual((w["feitos"], w["faltam"], w["hoje"], w["restam_dias"]), (3, 1, 2, 4))
        self.assertEqual(s["sequencia"], 1)  # a atual ainda não bateu: conta só a anterior
        self.assertEqual(s["mes"], 7)
        self.assertEqual(s["tipos"][0], "Musculação")
        self.assertEqual(len(s["heat"]), 18 * 7 - 3)  # 17 semanas cheias + seg a qui

    def test_treino_notice_only_when_needed(self):
        with db.connect() as conn:
            vida.act_treino(conn, {"date": "2026-11-09", "tipo": "Musculação"})
            vida.act_treino(conn, {"date": "2026-11-10", "tipo": "Musculação"})
            # quinta 18h, 2 de 4, faltam 2 e restam 4 dias: ainda não precisa
            self.assertEqual([k for k, *_ in vida.avisos(conn, at("2026-11-12T18:05:00"), RULES, notify.due)
                              if k.startswith("treino")], [])
            # sábado 18h, faltam 2 e restam 2 dias: avisa
            got = [x for x in vida.avisos(conn, at("2026-11-14T18:05:00"), RULES, notify.due) if x[0].startswith("treino")]
            self.assertEqual(got[0][1], "Hoje é dia de treinar")

    def test_rotina_abc_checkin_and_progressao(self):
        with db.connect() as conn:
            vida.semear_rotina(conn)
            self.assertEqual(vida.proximo_dia(conn), "A")  # ninguém treinou ainda: começa no A
            ex_a = vida.rotina(conn)["A"][0]["exercicio"]
            vida.act_treino(conn, {"tipo": "Musculação", "dia": "A",
                                   "series": [{"exercicio": ex_a, "carga": 40, "reps": "10"}]})
            self.assertEqual(vida.proximo_dia(conn), "B")  # roda pro próximo dia da grade
            r = vida.rotina(conn)
            self.assertEqual(r["A"][0]["ultimo"]["carga"], 40)
            self.assertIsNone(r["B"][0]["ultimo"])  # ainda não fez nenhum do dia B
            hist = vida.progresso_exercicio(conn, ex_a)
            self.assertEqual([h["carga"] for h in hist], [40])
            vida.act_treino(conn, {"tipo": "Corrida"})  # treino fora da grade não muda a rotação
            self.assertEqual(vida.proximo_dia(conn), "B")
            # resumo() expõe tudo isso pra tela
            s = vida.resumo(conn)["saude"]
            self.assertEqual(s["proximo_dia"], "B")
            self.assertIn("A", s["rotina"])

    def test_sugestao_plato_apos_n_treinos(self):
        with db.connect() as conn:
            vida.semear_rotina(conn)
            ex_a = vida.rotina(conn)["A"][0]["exercicio"]
            for _ in range(4):  # 4 ciclos ABC = 12 treinos da grade, carga do exercício A nunca sobe
                vida.act_treino(conn, {"tipo": "Musculação", "dia": "A",
                                       "series": [{"exercicio": ex_a, "carga": 20, "reps": "10"}]})
                vida.act_treino(conn, {"tipo": "Musculação", "dia": "B"})
                vida.act_treino(conn, {"tipo": "Musculação", "dia": "C"})
            s = vida.resumo(conn)["saude"]
            self.assertEqual(s["sugestao"]["tipo"], "plato")
            self.assertEqual(s["sugestao"]["exercicio"], ex_a)
            # com a sugestão pendente, mais um treino não cria outra
            vida.act_treino(conn, {"tipo": "Musculação", "dia": "A",
                                   "series": [{"exercicio": ex_a, "carga": 20, "reps": "10"}]})
            self.assertEqual(vida.resumo(conn)["saude"]["sugestao"]["tipo"], "plato")
            vida.act_sugestao(conn, {"ok": True})
            self.assertIsNone(vida.resumo(conn)["saude"]["sugestao"])

    def test_sugestao_inconsistencia_de_dia(self):
        with db.connect() as conn:
            vida.semear_rotina(conn)
            for _ in range(6):  # A e B em dia, C nunca: 12 treinos da grade
                vida.act_treino(conn, {"tipo": "Musculação", "dia": "A"})
                vida.act_treino(conn, {"tipo": "Musculação", "dia": "B"})
            s = vida.resumo(conn)["saude"]
            self.assertEqual(s["sugestao"]["tipo"], "inconsistencia")
            self.assertEqual(s["sugestao"]["dia"], "C")

    def test_peso_reminder_weekly(self):
        with db.connect() as conn:
            got = [x for x in vida.avisos(conn, at("2026-11-15T19:05:00"), RULES, notify.due)  # domingo
                   if x[0].startswith("peso")]
            self.assertEqual(got[0][1], "Hora de se pesar")
            vida.act_avaliacao(conn, {"date": "2026-11-12", "valores": {"peso": 62}})
            got = [x for x in vida.avisos(conn, at("2026-11-15T19:05:00"), RULES, notify.due) if x[0].startswith("peso")]
            self.assertEqual(got, [])  # pesou essa semana: sem aviso


class Corpo(unittest.TestCase):
    def setUp(self):
        fresh()
        at("2026-11-12T10:00:00")

    def test_measures_delta_and_bmi(self):
        with db.connect() as conn:
            vida.act_avaliacao(conn, {"date": "2026-10-01", "valores": {"peso": 80, "altura": 178, "biceps_d": "35,5"}})
            vida.act_avaliacao(conn, {"date": "2026-11-10", "valores": {"peso": "78,4", "biceps_d": 36.2, "cintura": ""}})
            c = vida.resumo(conn)["saude"]["corpo"]
        self.assertEqual(c["ultimo"]["peso"]["delta"], -1.6)
        self.assertEqual(c["ultimo"]["biceps_d"]["valor"], 36.2)
        self.assertNotIn("cintura", c["ultimo"])
        self.assertEqual(c["imc"], 24.7)

    def test_bio_with_file_and_cleanup(self):
        pdf = b"%PDF-1.4\n%fake laudo\n"
        with db.connect() as conn:
            arq = vida.act_arquivo(conn, {"nome": "bio.pdf", "mime": "application/pdf",
                                          "dados": base64.b64encode(pdf).decode()})["id"]
            aid = vida.act_avaliacao(conn, {"tipo": "bio", "arquivo_id": arq, "valores": {"gordura_pct": 18.5}})["id"]
            self.assertEqual(vida.ler_arquivo(conn, arq)[0], pdf)
            vida.act_avaliacao(conn, {"id": aid, "delete": True})
            self.assertIsNone(vida.ler_arquivo(conn, arq))  # laudo sem dono é apagado

    def test_rejects_fake_files_and_unknown_fields(self):
        with db.connect() as conn:
            with self.assertRaises(ValueError):
                vida.act_arquivo(conn, {"nome": "x.pdf", "mime": "application/pdf", "dados": base64.b64encode(b"<html>").decode()})
            with self.assertRaises(ValueError):
                vida.act_avaliacao(conn, {"valores": {"senha": 1}})
            with self.assertRaises(ValueError):
                vida.act_avaliacao(conn, {"valores": {}})

    def test_backup_keeps_metadata_not_bytes(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
        with db.connect() as conn:
            arq = vida.act_arquivo(conn, {"nome": "foto.png", "mime": "image/png", "dados": base64.b64encode(png).decode()})["id"]
            vida.act_treino(conn, {"tipo": "Musculação", "arquivo_id": arq})
            dump = data.export(conn)
            self.assertNotIn("dados", dump["arquivos"][0])
            data.restore(conn, dump)
            self.assertIsNone(vida.ler_arquivo(conn, arq))  # sem backup no GitHub não há de onde buscar
            got = vida.ler_arquivo(conn, arq, fetch=lambda path: png if path.endswith(".png") else None)
            self.assertEqual(got[0], png)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM treinos").fetchone()[0], 1)


class ArquivoHttp(unittest.TestCase):
    def test_download_name_is_ascii(self):
        import re
        nome = "laudo 🏋️ bioimpedância.pdf"
        self.assertEqual(re.sub(r"[^A-Za-z0-9._ -]", "_", nome).encode("latin-1").decode(), "laudo __ bioimped_ncia.pdf")


class Habitos(unittest.TestCase):
    def setUp(self):
        fresh()
        at("2026-11-12T22:00:00")

    def test_steps_streak_and_notice(self):
        with db.connect() as conn:
            agua = conn.execute("SELECT id FROM habitos WHERE nome='Água'").fetchone()[0]
            for d in ("2026-11-10", "2026-11-11"):
                vida.act_habito_dia(conn, {"habito_id": agua, "date": d, "valor": 8})
            for _ in range(3):
                vida.act_habito_dia(conn, {"habito_id": agua, "delta": 1})
            vida.act_habito_dia(conn, {"habito_id": agua, "delta": -5})  # não fica negativo
            h = next(x for x in vida.resumo(conn)["saude"]["habitos"] if x["id"] == agua)
            self.assertEqual((h["hoje"], h["sequencia"]), (0, 2))
            with self.assertRaises(ValueError):
                vida.act_habito_dia(conn, {"habito_id": agua, "date": "2026-11-13", "valor": 1})
            got = [x for x in vida.avisos(conn, at("2026-11-12T21:10:00"), RULES, notify.due) if x[0].startswith("habitos")]
            self.assertIn("Água 0/8 copos", got[0][2])

    def test_semear_refeicoes_is_idempotent(self):
        with db.connect() as conn:
            vida.semear_refeicoes(conn)
            nomes = [r[0] for r in conn.execute("SELECT nome FROM habitos ORDER BY ordem")]
            self.assertEqual(nomes, ["Água", "Sono", "Café da manhã", "Lanche da manhã", "Almoço",
                                      "Pré-treino", "Pós-treino", "Jantar", "Ceia"])
            cafe = conn.execute("SELECT id, meta, passo FROM habitos WHERE nome='Café da manhã'").fetchone()
            self.assertEqual((cafe[1], cafe[2]), (1, 1))  # check de 1 toque, igual Água/Sono
            vida.act_habito_dia(conn, {"habito_id": cafe[0], "delta": 1})
            vida.semear_refeicoes(conn)  # rodar de novo não duplica nem reseta o que já foi marcado
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM habitos WHERE nome='Café da manhã'").fetchone()[0], 1)
            h = next(x for x in vida.resumo(conn)["saude"]["habitos"] if x["id"] == cafe[0])
            self.assertEqual(h["hoje"], 1)


class Agenda(unittest.TestCase):
    def setUp(self):
        fresh()
        at("2026-11-12T08:05:00")

    def test_reminders_days_before_and_near_time(self):
        with db.connect() as conn:
            vida.act_nota(conn, {"pasta": "Trabalho", "titulo": "Auditoria ISM", "quando": "2026-11-15", "avisos": [3, 1]})
            vida.act_nota(conn, {"pasta": "Pessoal", "titulo": "Dentista", "quando": "2026-11-12 09:00"})
            vida.act_nota(conn, {"pasta": "Trabalho", "titulo": "Sem data"})
            keys = [k for k, *_ in vida.avisos(conn, at("2026-11-12T08:05:00"), RULES, notify.due)]
            self.assertIn("nota:1:3:2026-11-15", keys)
            self.assertIn("nota:2:0:2026-11-12T09:00", keys)
            self.assertIn("notah:2:2026-11-12T09:00", keys)  # 55 min antes
            self.assertEqual([k for k in keys if k.startswith("nota:1")], ["nota:1:3:2026-11-15"])
            later = [k for k, *_ in vida.avisos(conn, at("2026-11-14T08:10:00"), RULES, notify.due)]
            self.assertIn("nota:1:1:2026-11-15", later)
            prox = vida.resumo(conn)["agenda"]["proximos"]
        self.assertEqual([p["titulo"] for p in prox], ["Dentista", "Auditoria ISM"])
        self.assertEqual(prox[0]["hora"], "09:00")

    def test_tasks_toggle_and_daily_notice(self):
        with db.connect() as conn:
            a = vida.act_tarefa(conn, {"pasta": "Trabalho", "titulo": "Enviar relatório OVID", "prazo": "2026-11-11"})["id"]
            vida.act_tarefa(conn, {"pasta": "Pessoal", "titulo": "Pagar boleto", "prazo": "2026-11-12"})
            vida.act_tarefa(conn, {"pasta": "Pessoal", "titulo": "Sem prazo"})
            got = [x for x in vida.avisos(conn, C.now(), RULES, notify.due) if x[0].startswith("tarefas")]
            self.assertEqual(got[0][1], "2 tarefas para resolver")
            self.assertIn("1 atrasada(s)", got[0][2])
            vida.act_tarefa(conn, {"id": a, "feito": True})
            row = conn.execute("SELECT feito_em FROM tarefas WHERE id=?", (a,)).fetchone()
            self.assertEqual(row[0], "2026-11-12")
            got = [x for x in vida.avisos(conn, C.now(), RULES, notify.due) if x[0].startswith("tarefas")]
            self.assertTrue(got[0][1].startswith("Tarefa para hoje"))

    def test_exam_countdown(self):
        with db.connect() as conn:
            vida.act_facul(conn, {"curso": "Engenharia Elétrica (IFF)", "disciplina": "Circuitos II", "tipo": "prova",
                                  "data": "2026-11-15T19:00"})
            keys = [(k, t) for k, t, *_ in vida.avisos(conn, at("2026-11-12T08:05:00"), RULES, notify.due) if k.startswith("facul")]
            self.assertEqual(keys, [("facul:1:3:2026-11-15T19:00", "Prova de Circuitos II em 3 dias")])
            with self.assertRaises(ValueError):
                vida.act_facul(conn, {"curso": "X", "disciplina": "Y", "tipo": "festa", "data": "2026-11-15"})

    def test_settings_validation(self):
        from app import validate as V
        self.assertEqual(V.agenda({"pastas": "Trabalho, Pessoal, trabalho", "avisos": "1 3 3"}),
                         {"pastas": ["Trabalho", "Pessoal"], "avisos": [3, 1]})
        self.assertEqual(V.quando("2026-11-12 9:00".replace(" 9", " 09")), "2026-11-12T09:00")
        with self.assertRaises(ValueError):
            V.saude({"meta_semana": 9})


class Painel(unittest.TestCase):
    def test_build_includes_module_notices(self):
        fresh()
        now = at("2026-11-12T08:05:00")
        with db.connect() as conn:
            vida.act_nota(conn, {"pasta": "Trabalho", "titulo": "Reunião", "quando": "2026-11-12"})
            keys = [k for k, *_ in notify.build(conn, now)]
        self.assertIn("nota:1:0:2026-11-12", keys)
        self.assertIsInstance(datetime.fromisoformat("2026-11-12T08:05"), datetime)
        self.assertEqual(date(2026, 11, 12).weekday(), 3)


if __name__ == "__main__":
    unittest.main()
