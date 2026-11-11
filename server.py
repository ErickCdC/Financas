#!/usr/bin/env python3
"""Finanças — app pessoal. Ponto de entrada: `APP_PASSWORD=... python3 server.py`."""
import signal
import sys
import threading
from http.server import ThreadingHTTPServer

from app import config as C
from app import db, notify
from app.backup import BACKUP
from app.web import Handler


def main():
    if not C.PASSWORD or len(C.PASSWORD) < 8:
        sys.exit("Defina APP_PASSWORD com pelo menos 8 caracteres (o app guarda dados financeiros).")
    state = db.init(restore=BACKUP.restore)
    C.log(f"banco: {state} · backup no GitHub: {'ativo' if BACKUP.enabled else 'desligado'}"
          f"{' (PAUSADO: ' + BACKUP.last_error + ')' if BACKUP.blocked else ''}")
    with db.connect() as conn:  # ajustes de dados antigos
        from app import bank, seed
        from app import projection as P
        for name, words in seed.MATCH.items():
            conn.execute("UPDATE recurring SET match=? WHERE name=? AND (match IS NULL OR match='')", (words, name))
        for r in db.rows(conn, "SELECT id, category FROM spends WHERE source='banco'"):
            if r["category"] and r["category"] not in ("mercado", "comida", "transporte", "casa", "lazer",
                                                        "compras", "saude", "outros", "estornado"):
                conn.execute("UPDATE spends SET category=? WHERE id=?", (bank.map_category(r["category"]), r["id"]))
        bank.reconcile_refunds(conn)
        bank.ignore_subscription_charges(conn)
        # estornos já casados antes: põe no mesmo dia da compra
        for r in db.rows(conn, "SELECT id, amount, date FROM spends WHERE category='estornado' AND amount < 0"):
            c = conn.execute("SELECT date FROM spends WHERE category='estornado' AND amount > 0 AND ABS(amount + ?) < 0.005 "
                             "AND julianday(date) BETWEEN julianday(?) - 10 AND julianday(?) + 10 ORDER BY date LIMIT 1",
                             (r["amount"], r["date"], r["date"])).fetchone()
            if c and c["date"] != r["date"]:
                conn.execute("UPDATE spends SET date=? WHERE id=?", (c["date"], r["id"]))
        # parcelas que entraram como gasto saem do teto
        for r in db.rows(conn, "SELECT id, description FROM spends WHERE source='banco' AND ignored=0"):
            if bank.PARC_RE.search(r["description"]):
                conn.execute("UPDATE spends SET ignored=1, category='parcela' WHERE id=?", (r["id"],))
        # migrações que rodam uma vez só
        feitas = set(db.get_cfg(conn).get("migracoes") or [])
        if "salario_minimo_50" not in feitas:
            regra = db.get_cfg(conn).get("salario_regra") or {}
            if regra.get("minimo_pct") == 60:  # set/26 caiu R$ 1.390 (59% do salário) e passaria batido
                db.put_setting(conn, "salario_regra", {**regra, "minimo_pct": 50})
            feitas.add("salario_minimo_50")
        if "fgts_libera_aprendiz" not in feitas:
            f = db.get_cfg(conn).get("fgts") or {}
            if f.get("libera") == "2028-05-12":  # o contrato de aprendiz (2024) também conta nos 3 anos
                db.put_setting(conn, "fgts", {**f, "libera": "2027-10-25"})
            feitas.add("fgts_libera_aprendiz")
        if "netinjection_valor" not in feitas:  # parcela real no Mercado Pago: R$ 39,19
            conn.execute("UPDATE installments SET amount=39.19 WHERE name LIKE 'NetInjection%' AND ABS(amount-39.21)<0.005")
            feitas.add("netinjection_valor")
        if "metas_share_igual" not in feitas:
            shares = [r[0] for r in conn.execute("SELECT share FROM goals")]
            if len(shares) >= 2 and all(x == 100 for x in shares):  # padrão antigo: 100% em todas
                conn.execute("UPDATE goals SET share=?", (round(100 / len(shares)),))
            feitas.add("metas_share_igual")
        if "assinaturas_nov26" not in feitas:  # lista conferida com o extrato e confirmada em 08/11/2026
            conn.execute("DELETE FROM recurring WHERE name IN ('Clube iFood', 'Armazenamento Gigi')")
            for name, amount, card, words in seed.NOVAS_NOV26:
                first = words.split(",")[0]
                if not conn.execute("SELECT 1 FROM recurring WHERE lower(name)=lower(?) OR lower(COALESCE(match,'')) LIKE ?",
                                    (name, f"%{first}%")).fetchone():
                    conn.execute("INSERT INTO recurring(name,amount,card,match) VALUES(?,?,?,?)", (name, amount, card, words))
            feitas.add("assinaturas_nov26")
        if "cobranca_valter" not in feitas:  # o Valter reembolsa a I-Conecta (R$ 115 por Pix, entre o dia 1 e o 5)
            conta = conn.execute("SELECT id FROM recurring WHERE name='I-Conecta'").fetchone()
            if conta and not conn.execute("SELECT 1 FROM cobrancas WHERE conta_id=?", (conta[0],)).fetchone():
                conn.execute("INSERT INTO cobrancas(nome, pessoa, valor, conta_id, dia_ini, dia_fim, match, nota, mes_seguinte) "
                             "VALUES('I-Conecta', 'Valter', 115, ?, 1, 5, 'valter luis', 'Internet da casa dele', 1)", (conta[0],))
            feitas.add("cobranca_valter")
        if "cobranca_mes_seguinte" not in feitas:  # a I-Conecta de novembro o Valter paga entre 1 e 5 de dezembro
            for c in db.rows(conn, "SELECT id FROM cobrancas WHERE pessoa='Valter' AND nome='I-Conecta'"):
                conn.execute("UPDATE cobrancas SET mes_seguinte=1 WHERE id=?", (c["id"],))
                for r in db.rows(conn, "SELECT * FROM cobranca_mes WHERE cobranca_id=? ORDER BY month", (c["id"],)):
                    conn.execute("DELETE FROM cobranca_mes WHERE cobranca_id=? AND month=?", (c["id"], r["month"]))
                    if r["recebido"]:  # Pix gravado no mês em que caiu: pertence ao mês anterior
                        conn.execute("INSERT OR REPLACE INTO cobranca_mes(cobranca_id, month, cobrado, recebido, valor) "
                                     "VALUES(?,?,?,?,?)", (c["id"], P.add_months(r["month"], -1), r["cobrado"], r["recebido"], r["valor"]))
            feitas.add("cobranca_mes_seguinte")
        if "habitos_padrao" not in feitas:  # módulo Saúde: começa com água e sono
            from app import vida
            vida.semear_habitos(conn)
            feitas.add("habitos_padrao")
        if "habitos_refeicoes" not in feitas:  # cronograma de alimentação virou check-in de refeição
            from app import vida
            vida.semear_refeicoes(conn)
            feitas.add("habitos_refeicoes")
        db.put_setting(conn, "migracoes", sorted(feitas))
        bank.ignore_subscription_charges(conn)  # de novo, com a lista já atualizada
        # valor fixo antigo do PicPay (limite usado, não a fatura)
        conn.execute("DELETE FROM overrides WHERE note LIKE 'Valor do cartão em 05/11%' OR note LIKE 'Valor que o app mostra%'")
    if state == "novo" and BACKUP.enabled and not BACKUP.blocked:
        BACKUP.schedule()  # primeiro backup

    stop = threading.Event()
    threading.Thread(target=notify.loop, args=(stop,), daemon=True).start()
    srv = ThreadingHTTPServer((C.HOST, C.PORT), Handler)
    srv.daemon_threads = True

    def shutdown(*_):
        # O Render manda SIGTERM antes de desligar: salva o backup pendente.
        stop.set()
        if BACKUP.enabled and BACKUP._timer:
            BACKUP._timer.cancel()
            BACKUP.flush()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    C.log("Finanças rodando em http://%s:%d (dados em %s)" % (C.HOST, C.PORT, C.DATA_DIR))
    srv.serve_forever()


if __name__ == "__main__":
    main()
