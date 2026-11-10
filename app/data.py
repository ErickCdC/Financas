"""Exportar e restaurar todos os dados (backup manual e automático)."""
from . import db
from . import validate as V

VERSION = 2
MIMES = ("image/jpeg", "image/png", "image/webp", "application/pdf")
TABLES = {
    "recurring": ["id", "name", "amount", "card", "due_day", "match"],
    "installments": ["id", "name", "card", "amount", "total", "paid_count", "last_bill", "original"],
    "planned": ["id", "kind", "name", "amount", "month", "day", "note", "done"],
    "goals": ["id", "name", "target", "saved", "kind", "note", "ordem", "share"],
    "payments": ["month", "key", "paid", "amount"],
    "overrides": ["card", "month", "amount", "note"],
    "spends": ["id", "date", "description", "amount", "category", "source", "ext_id", "ignored", "card"],
    "push_subs": ["id", "endpoint", "p256dh", "auth", "label", "created", "last_ok", "fails"],
    "notif_log": ["key", "sent_at", "title", "body"],
    "cobrancas": ["id", "nome", "pessoa", "valor", "conta_id", "dia_ini", "dia_fim", "match", "nota", "ativo", "mes_seguinte"],
    "cobranca_mes": ["cobranca_id", "month", "cobrado", "recebido", "valor"],
    "arquivos": ["id", "nome", "mime", "tamanho", "hash", "criado"],  # o conteúdo vai à parte (arquivos/<hash>)
    "treinos": ["id", "date", "tipo", "foco", "minutos", "nota", "arquivo_id", "criado"],
    "avaliacoes": ["id", "date", "tipo", "arquivo_id", "nota"],
    "avaliacao_valores": ["avaliacao_id", "campo", "valor"],
    "habitos": ["id", "nome", "unidade", "meta", "passo", "ordem", "ativo"],
    "habito_dia": ["habito_id", "date", "valor"],
    "notas": ["id", "pasta", "titulo", "texto", "quando", "avisos", "feito", "fixada", "criado", "atualizado"],
    "tarefas": ["id", "pasta", "titulo", "prazo", "nota", "importante", "feito_em", "criado"],
    "facul": ["id", "curso", "disciplina", "tipo", "titulo", "data", "nota", "nota_max", "feito", "obs"],
}
SETTING_FIELDS = {
    "salario": V.num, "decimo1": V.num, "decimo2": V.num,
    "decimo1_mes": V.posint(1, 12), "decimo2_mes": V.posint(1, 12),
    "aumento_on": V.boolean, "aumento_pct": V.num, "aumento_from": V.year_month,
    "teto_semanal": V.num, "caixa": V.num, "caixa_ref": V.year_month, "salario_dia": V.posint(1, 31),
    "salario_data": V.iso_date, "ignore_terms": V.str_list, "pix_max": V.num, "ciclo_extra": V.money_dict,
    "cartoes": V.card_list, "cartao_venc": V.day_dict, "notif": V.notif,
    "auto": V.auto, "sync_auto": V.sync_auto, "salario_regra": V.salario_regra,
    "nome": V.text(40, False), "meta_caixinha": V.num, "teto_auto": V.boolean,
    "salario_tipo": V.choice("util", "dia"), "fgts": V.fgts, "metas_modo": V.choice("ordem", "divisao"),
    "saude": V.saude, "agenda": V.agenda,
}
# Guardados pelo app (não editáveis pela tela), mas fazem parte do backup.
INTERNAL_SETTINGS = {"last_sync", "caixa_banco", "cartao_usado", "last_tick", "entradas", "last_force",
                     "eventos_pendentes", "last_auto_sync", "sync_detalhe", "ciclo_info", "caixa_data",
                     "migracoes", "metas_eta", "banco_incidentes"}

RECURRING_SPEC = {"name": V.text(60), "amount": V.num, "card": V.text(30, False), "due_day": V.opt(V.posint(1, 31)),
                  "match": V.text(120, False)}
INSTALLMENT_SPEC = {"name": V.text(60), "card": V.text(30), "amount": V.num,
                    "total": V.posint(1, 600), "paid_count": V.posint(0, 600), "last_bill": V.opt(V.year_month),
                    "original": V.opt(V.num)}
PLANNED_SPEC = {"kind": V.choice("saida", "entrada"), "name": V.text(60), "amount": V.num, "month": V.year_month,
                "day": V.opt(V.posint(1, 31)), "note": V.text(120, False), "done": V.boolint}
GOAL_SPEC = {"name": V.text(60), "target": V.num, "saved": V.num, "kind": V.text(20, False),
             "note": V.text(160, False), "ordem": V.posint(0, 100), "share": V.posint(0, 100)}
COBRANCA_SPEC = {"nome": V.text(60), "pessoa": V.text(60), "valor": V.opt(V.num), "conta_id": V.opt(V.posint(1, 10**9)),
                 "dia_ini": V.posint(1, 31), "dia_fim": V.posint(1, 31), "match": V.text(120, False),
                 "nota": V.text(160, False), "ativo": V.boolint, "mes_seguinte": V.boolint}
TREINO_SPEC = {"date": V.iso_date, "tipo": V.text(30), "foco": V.text(80, False), "minutos": V.opt(V.posint(1, 600)),
               "nota": V.text(300, False), "arquivo_id": V.opt(V.posint(1, 10**9))}
HABITO_SPEC = {"nome": V.text(40), "unidade": V.text(15, False), "meta": V.num, "passo": V.num,
               "ordem": V.posint(0, 100), "ativo": V.boolint}
NOTA_SPEC = {"pasta": V.text(30), "titulo": V.text(120), "texto": V.text(5000, False), "quando": V.opt(V.quando),
             "feito": V.boolint, "fixada": V.boolint}
TAREFA_SPEC = {"pasta": V.text(30), "titulo": V.text(160), "prazo": V.opt(V.iso_date), "nota": V.text(1000, False),
               "importante": V.boolint}
FACUL_SPEC = {"curso": V.text(60), "disciplina": V.text(60), "tipo": V.choice("prova", "trabalho", "entrega", "apresentacao", "outro"),
              "titulo": V.text(120, False), "data": V.quando, "nota": V.opt(V.num), "nota_max": V.opt(V.num),
              "feito": V.boolint, "obs": V.text(500, False)}
SPEND_SPEC = {"date": V.iso_date, "description": V.text(120), "amount": V.num,
              "category": V.text(30, False), "ignored": V.boolint, "card": V.text(30, False)}


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
        "planned": {"id": V.posint(1, 10**9), **PLANNED_SPEC},
        "goals": {"id": V.posint(1, 10**9), **GOAL_SPEC},
        "notif_log": {"key": V.text(120), "sent_at": V.text(20), "title": V.text(200, False), "body": V.text(500, False)},
        "cobrancas": {"id": V.posint(1, 10**9), **COBRANCA_SPEC},
        "cobranca_mes": {"cobranca_id": V.posint(1, 10**9), "month": V.year_month, "cobrado": V.opt(V.iso_date),
                         "recebido": V.opt(V.iso_date), "valor": V.opt(V.num)},
        "arquivos": {"id": V.posint(1, 10**9), "nome": V.text(120), "mime": V.choice(*MIMES), "tamanho": V.posint(0, 10**8),
                     "hash": V.hexhash, "criado": V.text(20)},
        "treinos": {"id": V.posint(1, 10**9), **TREINO_SPEC, "criado": V.text(20, False)},
        "avaliacoes": {"id": V.posint(1, 10**9), "date": V.iso_date, "tipo": V.choice("medidas", "bio"),
                       "arquivo_id": V.opt(V.posint(1, 10**9)), "nota": V.text(300, False)},
        "avaliacao_valores": {"avaliacao_id": V.posint(1, 10**9), "campo": V.text(30), "valor": V.num},
        "habitos": {"id": V.posint(1, 10**9), **HABITO_SPEC},
        "habito_dia": {"habito_id": V.posint(1, 10**9), "date": V.iso_date, "valor": V.num},
        "notas": {"id": V.posint(1, 10**9), **NOTA_SPEC, "avisos": V.text(60, False), "criado": V.text(20),
                  "atualizado": V.text(20, False)},
        "tarefas": {"id": V.posint(1, 10**9), **TAREFA_SPEC, "feito_em": V.opt(V.iso_date), "criado": V.text(20)},
        "facul": {"id": V.posint(1, 10**9), **FACUL_SPEC},
    }
    parsed = {}
    for t, spec in specs.items():
        lst = body.get(t, [])  # backups antigos não têm as tabelas novas: lista vazia
        if not isinstance(lst, list) or len(lst) > 50000:
            raise ValueError(f"lista inválida: {t}")
        parsed[t] = [{c: fn(r.get(c)) for c, fn in spec.items() if c in r} for r in lst if isinstance(r, dict)]
    for t in TABLES:
        conn.execute(f"DELETE FROM {t}")
    # arquivos: o conteúdo já está no GitHub (enviado=1) e é buscado quando alguém abrir
    for k, v in clean.items():
        db.put_setting(conn, k, v)
    for t, lst in parsed.items():
        for r in lst:
            if not r:
                continue
            if t == "arquivos":
                r = {**r, "enviado": 1}
            cols = ", ".join(r)
            conn.execute(f"INSERT OR REPLACE INTO {t} ({cols}) VALUES ({', '.join('?' for _ in r)})", tuple(r.values()))
    return {"ok": True}
