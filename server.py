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
