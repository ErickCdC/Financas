"""Rotina de notificações: decide o que avisar e quando, e entrega por Web Push.

`tick()` é chamado a cada minuto enquanto o servidor está acordado e, de hora em hora,
pelo GitHub Actions (que também acorda o servidor no Render grátis).
Cada aviso tem uma chave única em notif_log, então nunca sai repetido.
"""
import threading
from datetime import date, datetime, timedelta

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
    if (tag or "") in ("daily", "teto", "week", "grande"):
        msg["actions"] = [{"action": "add", "title": "Lançar gasto"}]
    elif (tag or "") in ("bills", "paid", "payday"):
        msg["actions"] = [{"action": "open", "title": "Ver contas"}]
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
    cfg, _proj, _livre = finance.context(conn, now.date(), n=6)
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
                if (rules.get("metas") or {}).get("on", True) and _proj["months"]:
                    cx = _proj["months"][0]["caixinha"]
                    body += (f" A caixinha deste salário caiu para {brl(cx)}." if cx > 0 else
                             " Este salário já não tem sobra para as metas.")
            else:
                title = f"{top}% do teto da semana usado"
                body = (f"Restam {brl(w['restante'])} até domingo "
                        f"(≈ {brl(w['por_dia_restante'])} por dia).")
            out.append((key, title, body, "/#gastos", [f"teto:{w['inicio']}:{p}" for p in crossed]))

    r = rules.get("bank_events") or {}
    for ev in cfg.get("eventos_pendentes") or []:
        fresh = len(ev) > 4 and ev[4] >= (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
        if r.get("on", True) and fresh:
            out.append((ev[0], ev[1], ev[2], ev[3], []))

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

    r = rules.get("compra_grande") or {}
    if r.get("on"):
        lim = float(r.get("valor") or 100)
        desde = (t - timedelta(days=1)).isoformat()
        for s in db.rows(conn, "SELECT id, date, description, amount, card FROM spends WHERE ignored=0 AND source='banco' "
                               "AND amount >= ? AND date >= ?", (lim, desde)):
            onde = s["card"] or "conta"
            body = (f"{s['description'][:40].strip()} · {onde}. Semana: {brl(w['gasto'])} de {brl(w['teto'])}"
                    f"{' (passou do teto)' if w['gasto'] > w['teto'] else ''}.")
            out.append((f"grande:{s['id']}", f"Compra de {brl(s['amount'])}", body, "/#gastos", []))

    r = rules.get("ritmo") or {}
    if r.get("on") and t.weekday() == int(r.get("weekday", 3)) and due(now, r.get("time", "19:00")) and _proj["months"]:
        m0 = _proj["months"][0]
        since = finance.last_payday(t, cfg)
        dias = max((t - since).days + 1, 1)
        gasto = conn.execute("SELECT COALESCE(SUM(amount),0) FROM spends WHERE ignored=0 AND date BETWEEN ? AND ?",
                             (since.isoformat(), t.isoformat())).fetchone()[0]
        fim = m0["sobra"] - gasto / dias * m0["dias"]
        if fim < 0 and m0["dias"] > 0:
            limite = max(m0["sobra"] / m0["dias"], 0)
            out.append((f"ritmo:{t}", f"No ritmo atual, faltam {brl(-fim)} no fim do mês",
                        f"Você está gastando {brl(gasto / dias)} por dia. Para fechar no zero até o salário, "
                        f"o limite é {brl(limite)} por dia.", "/#inicio", []))

    r = rules.get("metas") or {}
    if r.get("on", True):
        plano = finance.goals_plan(conn, _proj, cfg)
        antes = cfg.get("metas_eta") or {}
        for g in plano["metas"]:
            gid = str(g["id"])
            if g["eta"] == "pronta":
                out.append((f"meta_ok:{gid}", f"Meta alcançada: {g['name']}", f"Você juntou {brl(g['saved'])}.", "/#plano", []))
            elif gid in antes and g["eta"] and antes[gid] and g["eta"] != "pronta" and antes[gid] != "pronta":
                if g["eta"] > antes[gid]:
                    out.append((f"meta:{gid}:{g['eta']}", f"{g['name']} atrasou",
                                f"Com a caixinha de agora, chega em {_mes(g['eta'])} (antes: {_mes(antes[gid])}).", "/#plano", []))
                elif g["eta"] < antes[gid]:
                    out.append((f"meta:{gid}:{g['eta']}", f"{g['name']} adiantou",
                                f"Agora chega em {_mes(g['eta'])} (antes: {_mes(antes[gid])}).", "/#plano", []))

    r = rules.get("relatorio") or {}
    if r.get("on") and due(now, r.get("time", "09:00")) and finance.last_payday(t, cfg) == t:
        ym = finance.P.add_months(finance.income_month(t, cfg), -1)
        try:
            rel = finance.relatorio(conn, cfg, t - timedelta(days=1), ym)
        except ValueError:
            rel = None
        if rel and rel["n"]:
            cats = " · ".join(f"{c['cat']} {brl(c['total'])}" for c in rel["categorias"][:3])
            body = (f"Gastou {brl(rel['total'])} ({brl(rel['por_dia'])}/dia)"
                    f"{', ' + brl(rel['acima_teto']) + ' acima do teto' if rel['acima_teto'] > 0 else ', dentro do teto'}. {cats}.")
            out.append((f"relatorio:{ym}", f"Relatório de {_mes(ym)}", body, f"/#relatorio-{ym}", []))

    r = rules.get("cobrancas") or {}
    if r.get("on") and due(now, r.get("time", "09:00")):
        for c in finance.cobrancas_mes(conn, t)["itens"]:
            if c["recebido"]:
                continue
            if t.isoformat() == c["ini"]:
                out.append((f"cobrar:{c['id']}:{c['ym']}", f"Dia de cobrar {c['pessoa']}",
                            f"{c['nome']}: {brl(c['valor_mes'])}. Prazo até dia {int(c['fim'][8:10])}.", "/#contas/receber", []))
            elif t > date.fromisoformat(c["fim"]):
                out.append((f"atraso:{c['id']}:{c['ym']}", f"{c['pessoa']} ainda não pagou",
                            f"{c['nome']}: {brl(c['valor_mes'])} (prazo era dia {int(c['fim'][8:10])}).", "/#contas/receber", []))

    r = rules["payday"]
    if r.get("on") and due(now, r["time"]):
        if finance.last_payday(t, cfg) == t:
            m = _proj["months"][0]
            body = (f"Planejado para guardar este mês: {brl(m['caixinha'])}. "
                    "Atualize o caixa e marque as contas que pagar.")
            out.append((f"payday:{t}", "Dia de salário", body, "/#contas", []))
    return out


MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def _mes(ym):
    return f"{MESES[int(ym[5:7]) - 1]}/{ym[2:4]}"


def sync_due(cfg, now):
    """Rotina de sincronização: a cada `intervalo` minutos entre `inicio` e `fim`."""
    sa = cfg.get("sync_auto") or {}
    if not sa.get("on"):
        return False
    if not (_at(now, sa.get("inicio", "07:30")) <= now <= _at(now, sa.get("fim", "23:00")) + timedelta(minutes=2)):
        return False
    last = cfg.get("last_auto_sync")
    if not last:
        return True
    try:
        prev = datetime.fromisoformat(last).replace(tzinfo=C.TZ)
    except ValueError:
        return True
    return now - prev >= timedelta(minutes=max(15, int(sa.get("intervalo", 30))) - 3)


def tick(force_sync=False):
    """Roda a rotina. Devolve um resumo do que fez."""
    if not _lock.acquire(blocking=False):
        return {"ok": True, "ocupado": True}
    try:
        now = C.now()
        result = {"ok": True, "enviadas": [], "sync": None, "hora": now.strftime("%Y-%m-%d %H:%M")}
        with db.connect() as conn:
            cfg = db.get_cfg(conn)
            db.put_setting(conn, "last_tick", result["hora"])
        synced = False
        if C.BANCO_KEY and (force_sync or sync_due(cfg, now)):
            from . import bank
            try:  # rede fora de qualquer transação
                result["sync"] = bank.run(force=bool((cfg.get("sync_auto") or {}).get("forcar", True)), origem="automatica")
                synced = True
            except (RuntimeError, ValueError) as e:
                result["sync"] = {"erro": str(e)}
                C.log("sincronização automática: " + str(e))
            with db.connect() as conn:
                db.put_setting(conn, "last_auto_sync", now.strftime("%Y-%m-%dT%H:%M"))
        with db.connect() as conn:
            changed = _run(conn, now, result, synced) or synced
        if changed:  # só dispara o backup quando algo mudou de verdade
            for fn in db.on_write:
                fn()
        return result
    finally:
        _lock.release()


def _run(conn, now, result, synced):
    cfg = db.get_cfg(conn)
    changed = False
    n = cfg["notif"]
    if not synced and (cfg.get("auto") or {}).get("parcelas", True):
        from . import finance as F
        if F.advance_installments(conn, cfg, now.date()):
            changed = True
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
    _guarda_metas(conn, now)
    if cfg.get("eventos_pendentes"):
        keep = n["rules"].get("bank_events", {}).get("on", True)
        left = [e for e in db.get_cfg(conn).get("eventos_pendentes") or [] if keep and not sent(conn, e[0])]
        db.put_setting(conn, "eventos_pendentes", left)
        changed = True
    return changed


def _guarda_metas(conn, now):
    cfg, proj, _ = finance.context(conn, now.date(), n=6)
    atual = {str(g["id"]): g["eta"] for g in finance.goals_plan(conn, proj, cfg)["metas"]}
    if atual != (cfg.get("metas_eta") or {}):
        db.put_setting(conn, "metas_eta", atual)


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
