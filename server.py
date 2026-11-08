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
        if "metas_share_igual" not in feitas:
            shares = [r[0] for r in conn.execute("SELECT share FROM goals")]
            if len(shares) >= 2 and all(x == 100 for x in shares):  # padrão antigo: 100% em todas
                conn.execute("UPDATE goals SET share=?", (round(100 / len(shares)),))
            feitas.add("metas_share_igual")
        db.put_setting(conn, "migracoes", sorted(feitas))
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
