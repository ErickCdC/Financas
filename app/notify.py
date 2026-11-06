"""Rotina de notificações: decide o que avisar e quando, e entrega por Web Push.

`tick()` é chamado a cada minuto enquanto o servidor está acordado e, de hora em hora,
pelo GitHub Actions (que também acorda o servidor no Render grátis).
Cada aviso tem uma chave única em notif_log, então nunca sai repetido.
"""
import threading
from datetime import datetime, timedelta

from . import config as C
from . import db, finance, push

GRACE = timedelta(hours=3)  # atraso máximo aceito para um aviso com horário marcado
_lock = threading.Lock()
WEEKDAYS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def brl(v):
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("−R$ " if v < 0 else "R$ ") + s


def _at(now, hhmm):
    h, m = map(int, hhmm.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def in_quiet(now, start, end):
    s, e = _at(now, start).time(), _at(now, end).time()
    t = now.time()
    return (s <= t or t < e) if s > e else (s <= t < e)


def due(now, hhmm):
    at = _at(now, hhmm)
    return at <= now <= at + GRACE


def sent(conn, key):
    return conn.execute("SELECT 1 FROM notif_log WHERE key=?", (key,)).fetchone() is not None


def mark(conn, key, title="", body=""):
    conn.execute("INSERT OR IGNORE INTO notif_log(key,sent_at,title,body) VALUES(?,?,?,?)",
                 (key, C.now().strftime("%Y-%m-%d %H:%M"), title, body))


def deliver(conn, title, body, url="/", tag=None):
    subs = db.rows(conn, "SELECT * FROM push_subs")
    ok = 0
    stamp = C.now().strftime("%Y-%m-%d %H:%M")
    msg = {"title": title, "body": body, "url": url, "tag": tag or url}
    for s in subs:
        r = push.send(s, msg)
        if r == "ok":
            ok += 1
            conn.execute("UPDATE push_subs SET last_ok=?, fails=0 WHERE id=?", (stamp, s["id"]))
        elif r == "gone" or s["fails"] >= 9:
            conn.execute("DELETE FROM push_subs WHERE id=?", (s["id"],))
        else:
            conn.execute("UPDATE push_subs SET fails=fails+1 WHERE id=?", (s["id"],))
            C.log("push falhou: " + r)
    return ok


def build(conn, now):
    """Lista de (chave, título, texto, url) que deveriam sair agora."""
    cfg = finance.effective_cfg(db.get_cfg(conn), now.date())
    rules = cfg["notif"]["rules"]
    t = now.date()
    w = finance.week_info(conn, cfg, t)
    out = []

    r = rules["teto_alert"]
    if r.get("on") and w["teto"] > 0:
        crossed = [p for p in r.get("pcts", []) if w["pct"] >= p]
        if crossed:
            top = max(crossed)
            key = f"teto:{w['inicio']}:{top}"
            if top >= 100:
                title = "Teto da semana estourado"
                body = f"Você gastou {brl(w['gasto'])} de {brl(w['teto'])}. Passou {brl(w['estourou'])}."
            else:
                title = f"{top}% do teto da semana usado"
                body = (f"Restam {brl(w['restante'])} até domingo "
                        f"(≈ {brl(w['por_dia_restante'])} por dia).")
            out.append((key, title, body, "/#gastos", [f"teto:{w['inicio']}:{p}" for p in crossed]))

    r = rules["daily_log"]
    if r.get("on") and due(now, r["time"]):
        hoje = next((d["total"] for d in w["dias"] if d["date"] == t.isoformat()), 0)
        body = (f"Hoje: {brl(hoje)} lançados. Restam {brl(w['restante'])} na semana."
                if hoje else f"Nada lançado hoje. Restam {brl(w['restante'])} na semana.")
        out.append((f"daily:{t}", "Lançou seus gastos de hoje?", body, "/#gastos", []))

    r = rules["bills_due"]
    if r.get("on") and due(now, r["time"]):
        lim = int(r.get("days_before", 2))
        items = [i for i in finance.upcoming(conn, cfg, t, days=lim) if i["dias"] <= lim]
        if items:
            def quando(i):
                return "atrasada" if i["dias"] < 0 else "hoje" if i["dias"] == 0 else \
                    "amanhã" if i["dias"] == 1 else f"em {i['dias']} dias"
            total = sum(i["amount"] for i in items)
            if len(items) == 1:
                i = items[0]
                title = f"{i['name']} vence {quando(i)}"
                body = f"{brl(i['amount'])}{' (previsto)' if i['estimated'] else ''}. Toque para marcar como paga."
            else:
                title = f"{len(items)} contas vencendo · {brl(total)}"
                body = " · ".join(f"{i['name']} {quando(i)}" for i in items[:4])
            out.append((f"bills:{t}", title, body, "/#contas", []))

    r = rules["weekly_summary"]
    if r.get("on") and t.weekday() == int(r.get("weekday", 0)) and due(now, r["time"]):
        prev = w["semana_passada"]
        diff = prev - w["teto"]
        body = (f"Semana passada: {brl(prev)} ({'dentro' if diff <= 0 else 'acima'} do teto"
                f"{'' if diff <= 0 else ' em ' + brl(diff)}). Esta semana: teto de {brl(w['teto'])}.")
        out.append((f"week:{w['inicio']}", "Resumo da semana", body, "/#inicio", []))

    r = rules["payday"]
    if r.get("on") and due(now, r["time"]):
        dia = int(cfg.get("salario_dia") or 30)
        if finance.last_payday(t, dia) == t:
            from . import projection as P
            proj = P.project(cfg, db.rows(conn, "SELECT * FROM recurring"),
                             db.rows(conn, "SELECT * FROM installments"),
                             {(o["card"], o["month"]): o for o in db.rows(conn, "SELECT * FROM overrides")}, t, n=1)
            m = proj["months"][0]
            body = (f"Planejado para guardar este mês: {brl(m['caixinha'])}. "
                    "Atualize o caixa e marque as contas que pagar.")
            out.append((f"payday:{t}", "Dia de salário", body, "/#contas", []))
    return out


def tick(force_sync=False):
    """Roda a rotina. Devolve um resumo do que fez."""
    if not _lock.acquire(blocking=False):
        return {"ok": True, "ocupado": True}
    try:
        now = C.now()
        result = {"ok": True, "enviadas": [], "sync": None, "hora": now.strftime("%Y-%m-%d %H:%M")}
        with db.connect() as conn:
            changed = _run(conn, now, result, force_sync)
        if changed:  # só dispara o backup quando algo mudou de verdade
            for fn in db.on_write:
                fn()
        return result
    finally:
        _lock.release()


def _run(conn, now, result, force_sync):
    cfg = db.get_cfg(conn)
    db.put_setting(conn, "last_tick", result["hora"])
    changed = False
    n = cfg["notif"]
    rs = n["rules"]["auto_sync"]
    if C.BANCO_KEY and (force_sync or (rs.get("on") and due(now, rs["time"]) and not sent(conn, f"sync:{now.date()}"))):
        from . import bank
        try:
            result["sync"] = bank.sync(conn)
            mark(conn, f"sync:{now.date()}")
            changed = True
        except (RuntimeError, ValueError) as e:
            result["sync"] = {"erro": str(e)}
    if not n.get("enabled"):
        return changed
    if in_quiet(now, n["quiet_start"], n["quiet_end"]):
        result["silencio"] = True
        return changed
    if not conn.execute("SELECT 1 FROM push_subs LIMIT 1").fetchone():
        return changed
    for key, title, body, url, also in build(conn, now):
        if sent(conn, key):
            continue
        if deliver(conn, title, body, url, tag=key.split(":")[0]):
            mark(conn, key, title, body)
            for k in also:
                mark(conn, k)
            result["enviadas"].append(title)
            changed = True
    return changed


def test(conn):
    if not conn.execute("SELECT 1 FROM push_subs LIMIT 1").fetchone():
        raise ValueError("nenhum aparelho com notificações ativadas")
    n = deliver(conn, "Notificações funcionando", "É assim que os avisos do Finanças vão aparecer.", "/#inicio", "teste")
    if not n:
        conn.commit()  # mantém a limpeza de inscrições mortas mesmo devolvendo erro
        raise RuntimeError("o serviço de push recusou o envio; desative e ative de novo neste aparelho")
    return {"ok": True, "aparelhos": n}


def loop(stop):
    """Laço em segundo plano enquanto o servidor está acordado."""
    while not stop.wait(60):
        try:
            tick()
        except Exception as e:  # nunca derruba o servidor
            C.log("erro na rotina de notificações: %r" % e)


__all__ = ["tick", "test", "loop", "build", "in_quiet", "due", "datetime"]
