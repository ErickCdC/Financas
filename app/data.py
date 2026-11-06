"""Exportar e restaurar todos os dados (backup manual e automático)."""
from . import db
from . import validate as V

VERSION = 2
TABLES = {
    "recurring": ["id", "name", "amount", "card", "due_day", "match"],
    "installments": ["id", "name", "card", "amount", "total", "paid_count", "last_bill"],
    "payments": ["month", "key", "paid", "amount"],
    "overrides": ["card", "month", "amount", "note"],
    "spends": ["id", "date", "description", "amount", "category", "source", "ext_id", "ignored"],
    "push_subs": ["id", "endpoint", "p256dh", "auth", "label", "created", "last_ok", "fails"],
    "notif_log": ["key", "sent_at", "title", "body"],
}
SETTING_FIELDS = {
    "salario": V.num, "decimo1": V.num, "decimo2": V.num,
    "decimo1_mes": V.posint(1, 12), "decimo2_mes": V.posint(1, 12),
    "aumento_on": V.boolean, "aumento_pct": V.num, "aumento_from": V.year_month,
    "teto_semanal": V.num, "caixa": V.num, "caixa_ref": V.year_month, "salario_dia": V.posint(1, 31),
    "salario_data": V.iso_date, "ignore_terms": V.str_list, "pix_max": V.num, "ciclo_extra": V.money_dict,
    "cartoes": V.card_list, "cartao_venc": V.day_dict, "notif": V.notif,
    "auto": V.auto, "sync_auto": V.sync_auto, "salario_regra": V.salario_regra,
}
# Guardados pelo app (não editáveis pela tela), mas fazem parte do backup.
INTERNAL_SETTINGS = {"last_sync", "caixa_banco", "cartao_usado", "last_tick", "entradas", "last_force",
                     "eventos_pendentes", "last_auto_sync"}

RECURRING_SPEC = {"name": V.text(60), "amount": V.num, "card": V.text(30, False), "due_day": V.opt(V.posint(1, 31)),
                  "match": V.text(120, False)}
INSTALLMENT_SPEC = {"name": V.text(60), "card": V.text(30), "amount": V.num,
                    "total": V.posint(1, 600), "paid_count": V.posint(0, 600), "last_bill": V.opt(V.year_month)}
SPEND_SPEC = {"date": V.iso_date, "description": V.text(120), "amount": V.num,
              "category": V.text(30, False), "ignored": V.boolint}


def export(conn):
    cfg = db.get_cfg(conn)
    out = {"versao": VERSION, "settings": {k: cfg[k] for k in list(SETTING_FIELDS) + sorted(INTERNAL_SETTINGS) if k in cfg}}
    for t, cols in TABLES.items():
        out[t] = db.rows(conn, f"SELECT {', '.join(cols)} FROM {t}")
    return out


def _any(v):
    return v


def restore(conn, body):
    if not isinstance(body, dict) or body.get("versao") not in (1, 2):
        raise ValueError("arquivo de backup inválido")
    settings = body.get("settings") or {}
    clean = {}
    for k, fn in SETTING_FIELDS.items():
        if k in settings and settings[k] is not None:
            clean[k] = fn(settings[k]) if k != "notif" else db.merge(db.get_cfg(conn)["notif"], V.notif(settings[k]))
    for k in INTERNAL_SETTINGS:
        if k in settings:
            clean[k] = settings[k]
    nullable_num = V.opt(V.num)
    specs = {
        "recurring": {"id": V.posint(1, 10**9), **RECURRING_SPEC},
        "installments": {"id": V.posint(1, 10**9), **INSTALLMENT_SPEC},
        "payments": {"month": V.year_month, "key": V.text(40), "paid": V.boolint, "amount": nullable_num},
        "overrides": {"card": V.text(30), "month": V.year_month, "amount": V.num, "note": V.text(120, False)},
        "spends": {"id": V.posint(1, 10**9), **SPEND_SPEC, "source": V.text(10), "ext_id": V.text(120, False)},
        "push_subs": {"id": V.posint(1, 10**9), "endpoint": V.text(1000), "p256dh": V.text(200), "auth": V.text(100),
                      "label": V.text(80, False), "created": V.text(20), "last_ok": V.text(20, False),
                      "fails": V.posint(0, 1000)},
        "notif_log": {"key": V.text(120), "sent_at": V.text(20), "title": V.text(200, False), "body": V.text(500, False)},
    }
    parsed = {}
    for t, spec in specs.items():
        lst = body.get(t, [])
        if not isinstance(lst, list) or len(lst) > 50000:
            raise ValueError(f"lista inválida: {t}")
        parsed[t] = [{c: fn(r.get(c)) for c, fn in spec.items() if c in r} for r in lst if isinstance(r, dict)]
    for t in TABLES:
        conn.execute(f"DELETE FROM {t}")
    for k, v in clean.items():
        db.put_setting(conn, k, v)
    for t, lst in parsed.items():
        for r in lst:
            if not r:
                continue
            cols = ", ".join(r)
            conn.execute(f"INSERT OR REPLACE INTO {t} ({cols}) VALUES ({', '.join('?' for _ in r)})", tuple(r.values()))
    return {"ok": True}
