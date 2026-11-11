"""Módulos além das finanças: Saúde (treinos, corpo, hábitos) e Agenda (notas, lembretes, tarefas, faculdade).

Tudo que a tela precisa sai de `resumo()`. As gravações passam pelas funções `act_*`, chamadas pela API.
"""
import base64
import hashlib
import json
from datetime import date, datetime, timedelta

from . import config as C
from . import data, db
from . import validate as V

# ---------------------------------------------------------------- corpo
# campo: (nome, unidade, grupo, melhor) — melhor: +1 subir é bom, -1 descer é bom, 0 neutro
CAMPOS = {
    "peso": ("Peso", "kg", "medidas", 0),
    "altura": ("Altura", "cm", "medidas", 0),
    "biceps_d": ("Bíceps direito", "cm", "medidas", 1),
    "biceps_e": ("Bíceps esquerdo", "cm", "medidas", 1),
    "peito": ("Peito", "cm", "medidas", 1),
    "cintura": ("Cintura", "cm", "medidas", -1),
    "abdomen": ("Abdômen", "cm", "medidas", -1),
    "quadril": ("Quadril", "cm", "medidas", 0),
    "coxa_d": ("Coxa direita (quadríceps)", "cm", "medidas", 1),
    "coxa_e": ("Coxa esquerda (quadríceps)", "cm", "medidas", 1),
    "panturrilha": ("Panturrilha", "cm", "medidas", 1),
    "gordura_pct": ("Gordura corporal", "%", "bio", -1),
    "musculo_kg": ("Massa muscular", "kg", "bio", 1),
    "massa_magra_kg": ("Massa magra", "kg", "bio", 1),
    "agua_pct": ("Água corporal", "%", "bio", 1),
    "visceral": ("Gordura visceral", "", "bio", -1),  # nível, sem unidade
    "ossea_kg": ("Massa óssea", "kg", "bio", 0),
    "tmb": ("Metabolismo basal", "kcal", "bio", 0),
    "idade_metabolica": ("Idade metabólica", "anos", "bio", -1),
}
TIPOS_TREINO = ["Musculação", "Cardio", "Corrida", "Funcional", "Luta", "Esporte"]
TIPOS_FACUL = {"prova": "Prova", "trabalho": "Trabalho", "entrega": "Entrega", "apresentacao": "Apresentação", "outro": "Outro"}
MAX_ARQUIVO = 8 * 1024 * 1024
ASSINATURAS = {"image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",),
               "image/webp": (b"RIFF",), "application/pdf": (b"%PDF-",)}
EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf"}


def _stamp():
    return C.now().strftime("%Y-%m-%d %H:%M")


def semana(t):
    ini = t - timedelta(days=t.weekday())  # segunda
    return ini, ini + timedelta(days=6)


# ---------------------------------------------------------------- arquivos
def act_arquivo(conn, body):
    """Recebe um arquivo em base64 (foto ou PDF, até 8 MB). Devolve o id."""
    mime = V.choice(*data.MIMES)(body.get("mime"))
    nome = (V.text(120, False)(body.get("nome")) or "arquivo").replace("/", "-")
    try:
        raw = base64.b64decode(str(body.get("dados") or ""), validate=True)
    except ValueError:
        raise ValueError("arquivo inválido")
    if not raw:
        raise ValueError("arquivo vazio")
    if len(raw) > MAX_ARQUIVO:
        raise ValueError("arquivo muito grande (máximo 8 MB)")
    if not any(raw.startswith(sig) for sig in ASSINATURAS[mime]) or (mime == "image/webp" and raw[8:12] != b"WEBP"):
        raise ValueError("o conteúdo não bate com o tipo do arquivo")
    h = hashlib.sha256(raw).hexdigest()
    cur = conn.execute("INSERT INTO arquivos(nome, mime, tamanho, hash, criado, dados, enviado) VALUES(?,?,?,?,?,?,0)",
                       (nome, mime, len(raw), h, _stamp(), raw))
    return {"ok": True, "id": cur.lastrowid}


def arquivo_nome_remoto(row):
    return f"arquivos/{row['hash']}{EXT.get(row['mime'], '')}"


def ler_arquivo(conn, aid, fetch=None):
    """(bytes, mime, nome) do arquivo. Sem o conteúdo local (servidor reiniciou), busca no backup."""
    row = conn.execute("SELECT * FROM arquivos WHERE id=?", (aid,)).fetchone()
    if not row:
        return None
    raw = row["dados"]
    if raw is None and fetch:
        raw = fetch(arquivo_nome_remoto(row))
        if raw is not None:
            conn.execute("UPDATE arquivos SET dados=?, enviado=1 WHERE id=?", (raw, aid))
    if raw is None:
        return None
    return bytes(raw), row["mime"], row["nome"]


def _apaga_arquivo_solto(conn, aid):
    if not aid:
        return
    em_uso = conn.execute("SELECT 1 FROM treinos WHERE arquivo_id=? UNION SELECT 1 FROM avaliacoes WHERE arquivo_id=?",
                          (aid, aid)).fetchone()
    if not em_uso:
        conn.execute("DELETE FROM arquivos WHERE id=?", (aid,))


# ---------------------------------------------------------------- gravações
def _crud(conn, table, spec, body, optional=()):
    from .web import crud  # mesma regra de validação do resto do app
    return crud(conn, table, spec, body, optional)


def act_treino(conn, body):
    if body.get("delete"):
        tid = V.posint(1, 10**12)(body.get("id"))
        row = conn.execute("SELECT arquivo_id FROM treinos WHERE id=?", (tid,)).fetchone()
        conn.execute("DELETE FROM treinos WHERE id=?", (tid,))
        _apaga_arquivo_solto(conn, row and row["arquivo_id"])
        return {"ok": True}
    if body.get("id"):
        old = conn.execute("SELECT arquivo_id FROM treinos WHERE id=?", (V.posint(1, 10**12)(body["id"]),)).fetchone()
        r = _crud(conn, "treinos", data.TREINO_SPEC, body)
        if old and "arquivo_id" in body and old["arquivo_id"] != body.get("arquivo_id"):
            _apaga_arquivo_solto(conn, old["arquivo_id"])
        return r
    body = {"date": C.today().isoformat(), **body}
    r = _crud(conn, "treinos", data.TREINO_SPEC, body, optional=("foco", "minutos", "nota", "arquivo_id"))
    conn.execute("UPDATE treinos SET criado=? WHERE id=?", (_stamp(), r["id"]))
    return r


def act_avaliacao(conn, body):
    """Medidas ou bioimpedância: data, valores {campo: número}, arquivo opcional (laudo em foto ou PDF)."""
    if body.get("delete"):
        aid = V.posint(1, 10**12)(body.get("id"))
        row = conn.execute("SELECT arquivo_id FROM avaliacoes WHERE id=?", (aid,)).fetchone()
        conn.execute("DELETE FROM avaliacao_valores WHERE avaliacao_id=?", (aid,))
        conn.execute("DELETE FROM avaliacoes WHERE id=?", (aid,))
        _apaga_arquivo_solto(conn, row and row["arquivo_id"])
        return {"ok": True}
    valores = body.get("valores") or {}
    if not isinstance(valores, dict):
        raise ValueError("valores inválidos")
    limpos = {}
    for k, v in valores.items():
        if k not in CAMPOS:
            raise ValueError("medida desconhecida: " + str(k)[:30])
        if v in (None, ""):
            continue
        n = V.num(v)
        if n <= 0:
            raise ValueError(f"{CAMPOS[k][0]}: informe um valor maior que zero")
        limpos[k] = n
    arq = V.opt(V.posint(1, 10**9))(body.get("arquivo_id"))
    if not limpos and not arq:
        raise ValueError("informe pelo menos uma medida ou anexe o laudo")
    d = V.iso_date(body.get("date") or C.today().isoformat())
    tipo = V.choice("medidas", "bio")(body.get("tipo") or "medidas")
    nota = V.text(300, False)(body.get("nota"))
    if body.get("id"):
        aid = V.posint(1, 10**12)(body["id"])
        old = conn.execute("SELECT arquivo_id FROM avaliacoes WHERE id=?", (aid,)).fetchone()
        if not old:
            raise ValueError("avaliação não encontrada")
        conn.execute("UPDATE avaliacoes SET date=?, tipo=?, arquivo_id=?, nota=? WHERE id=?", (d, tipo, arq, nota, aid))
        conn.execute("DELETE FROM avaliacao_valores WHERE avaliacao_id=?", (aid,))
        if old["arquivo_id"] != arq:
            _apaga_arquivo_solto(conn, old["arquivo_id"])
    else:
        aid = conn.execute("INSERT INTO avaliacoes(date, tipo, arquivo_id, nota) VALUES(?,?,?,?)", (d, tipo, arq, nota)).lastrowid
    for k, v in limpos.items():
        conn.execute("INSERT INTO avaliacao_valores(avaliacao_id, campo, valor) VALUES(?,?,?)", (aid, k, v))
    return {"ok": True, "id": aid}


def act_habito(conn, body):
    if body.get("delete"):
        hid = V.posint(1, 10**12)(body.get("id"))
        conn.execute("DELETE FROM habito_dia WHERE habito_id=?", (hid,))
        conn.execute("DELETE FROM habitos WHERE id=?", (hid,))
        return {"ok": True}
    for k in ("meta", "passo"):
        if k in body and V.num(body[k]) <= 0:
            raise ValueError("meta e passo precisam ser maiores que zero")
    return _crud(conn, "habitos", data.HABITO_SPEC, body, optional=("unidade", "ordem", "ativo"))


def act_habito_dia(conn, body):
    """Marca um hábito num dia: `valor` absoluto ou `delta` (+1 copo, −1 copo)."""
    hid = V.posint(1, 10**12)(body.get("habito_id"))
    d = V.iso_date(body.get("date") or C.today().isoformat())
    if date.fromisoformat(d) > C.today():
        raise ValueError("não dá para marcar um dia que ainda não chegou")
    if not conn.execute("SELECT 1 FROM habitos WHERE id=?", (hid,)).fetchone():
        raise ValueError("hábito não encontrado")
    cur = conn.execute("SELECT valor FROM habito_dia WHERE habito_id=? AND date=?", (hid, d)).fetchone()
    atual = cur["valor"] if cur else 0
    novo = atual + V.num(body["delta"]) if "delta" in body else V.num(body.get("valor"))
    novo = round(min(max(novo, 0), 10000), 2)
    if novo == 0:
        conn.execute("DELETE FROM habito_dia WHERE habito_id=? AND date=?", (hid, d))
    else:
        conn.execute("INSERT INTO habito_dia(habito_id, date, valor) VALUES(?,?,?) "
                     "ON CONFLICT(habito_id, date) DO UPDATE SET valor=excluded.valor", (hid, d, novo))
    return {"ok": True, "valor": novo}


def act_nota(conn, body):
    if body.get("delete"):
        return _crud(conn, "notas", data.NOTA_SPEC, body)
    novo = not body.get("id")
    spec = {**data.NOTA_SPEC, "criado": V.text(20)} if novo else data.NOTA_SPEC
    r = _crud(conn, "notas", spec, {**body, "criado": _stamp()} if novo else body,
              optional=("texto", "quando", "feito", "fixada"))
    nid = body.get("id") or r.get("id")
    sets, args = ["atualizado=?"], [_stamp()]
    if "avisos" in body:
        sets.append("avisos=?")
        args.append(None if body["avisos"] is None else json.dumps(V.dias_antes(body["avisos"])))
    conn.execute(f"UPDATE notas SET {', '.join(sets)} WHERE id=?", (*args, nid))
    return r


def act_tarefa(conn, body):
    if "feito" in body and body.get("id") and len(body) <= 2:  # marcar / desmarcar
        conn.execute("UPDATE tarefas SET feito_em=? WHERE id=?",
                     (C.today().isoformat() if V.boolint(body["feito"]) else None, V.posint(1, 10**12)(body["id"])))
        return {"ok": True}
    if body.get("id") or body.get("delete"):
        return _crud(conn, "tarefas", data.TAREFA_SPEC, body)
    return _crud(conn, "tarefas", {**data.TAREFA_SPEC, "criado": V.text(20)}, {**body, "criado": _stamp()},
                 optional=("prazo", "nota", "importante"))


def act_facul(conn, body):
    return _crud(conn, "facul", data.FACUL_SPEC, body, optional=("titulo", "nota", "nota_max", "feito", "obs"))


# ---------------------------------------------------------------- leitura
def _avisos(row, padrao):
    try:
        v = json.loads(row["avisos"]) if row.get("avisos") else None
    except ValueError:
        v = None
    return padrao if v is None else v


def saude(conn, cfg, t):
    meta = int((cfg.get("saude") or {}).get("meta_semana") or 4)
    ini, fim = semana(t)
    dias_treino = {}
    for r in conn.execute("SELECT date, COUNT(*) n FROM treinos WHERE date >= ? GROUP BY date",
                          ((t - timedelta(days=400)).isoformat(),)):
        dias_treino[r["date"]] = r["n"]
    week = [{"date": (ini + timedelta(days=i)).isoformat(), "n": dias_treino.get((ini + timedelta(days=i)).isoformat(), 0)}
            for i in range(7)]
    feitos = sum(1 for d in week if d["n"])
    restam = (fim - t).days + 1  # dias até domingo, contando hoje
    # sequência: semanas seguidas batendo a meta (a atual só conta se já bateu)
    seq, w = 0, ini if feitos >= meta else ini - timedelta(days=7)
    while True:
        n = sum(1 for i in range(7) if dias_treino.get((w + timedelta(days=i)).isoformat()))
        if n < meta:
            break
        seq += 1
        w -= timedelta(days=7)
    heat_ini = ini - timedelta(weeks=17)
    heat = [dias_treino.get((heat_ini + timedelta(days=i)).isoformat(), 0) for i in range((t - heat_ini).days + 1)]
    ult = conn.execute("SELECT date FROM treinos ORDER BY date DESC LIMIT 1").fetchone()
    tipos = [r[0] for r in conn.execute("SELECT tipo FROM treinos GROUP BY tipo ORDER BY COUNT(*) DESC LIMIT 8")]
    treinos = db.rows(conn, "SELECT t.*, a.mime AS arquivo_mime FROM treinos t LEFT JOIN arquivos a ON a.id = t.arquivo_id "
                            "ORDER BY t.date DESC, t.id DESC LIMIT 120")
    ultimo_tipo = treinos[0]["tipo"] if treinos else TIPOS_TREINO[0]
    return {
        "meta_semana": meta,
        "semana": {"inicio": ini.isoformat(), "fim": fim.isoformat(), "dias": week, "feitos": feitos,
                   "faltam": max(meta - feitos, 0), "restam_dias": restam, "hoje": dias_treino.get(t.isoformat(), 0)},
        "sequencia": seq,
        "mes": sum(1 for d in dias_treino if d[:7] == t.isoformat()[:7]),
        "ano": sum(1 for d in dias_treino if d[:4] == t.isoformat()[:4]),
        "sem_treinar": (t - date.fromisoformat(ult["date"])).days if ult else None,
        "heat_inicio": heat_ini.isoformat(), "heat": heat,
        "tipos": tipos + [x for x in TIPOS_TREINO if x not in tipos],
        "ultimo_tipo": ultimo_tipo,
        "treinos": treinos,
        "corpo": corpo(conn),
        "habitos": habitos(conn, t),
    }


def corpo(conn):
    avs = db.rows(conn, "SELECT a.*, f.nome AS arquivo_nome, f.mime AS arquivo_mime FROM avaliacoes a "
                        "LEFT JOIN arquivos f ON f.id = a.arquivo_id ORDER BY a.date DESC, a.id DESC")
    vals = {}
    for r in conn.execute("SELECT avaliacao_id, campo, valor FROM avaliacao_valores"):
        vals.setdefault(r["avaliacao_id"], {})[r["campo"]] = r["valor"]
    hist = {}
    for a in avs:
        a["valores"] = vals.get(a["id"], {})
        for k, v in a["valores"].items():
            hist.setdefault(k, []).append([a["date"], v])
    ultimo = {}
    for k, pts in hist.items():
        pts.sort()
        ant = pts[-2][1] if len(pts) > 1 else None
        ultimo[k] = {"valor": pts[-1][1], "date": pts[-1][0], "anterior": ant,
                     "delta": round(pts[-1][1] - ant, 2) if ant is not None else None}
    imc = None
    if "peso" in ultimo and "altura" in ultimo and ultimo["altura"]["valor"] > 0:
        imc = round(ultimo["peso"]["valor"] / (ultimo["altura"]["valor"] / 100) ** 2, 1)
    return {"campos": {k: {"nome": n, "unidade": u, "grupo": g, "melhor": b} for k, (n, u, g, b) in CAMPOS.items()},
            "ultimo": ultimo, "historico": hist, "imc": imc, "avaliacoes": avs[:60]}


def habitos(conn, t):
    desde = (t - timedelta(days=60)).isoformat()
    marcas = {}
    for r in conn.execute("SELECT habito_id, date, valor FROM habito_dia WHERE date >= ?", (desde,)):
        marcas.setdefault(r["habito_id"], {})[r["date"]] = r["valor"]
    out = []
    for h in db.rows(conn, "SELECT * FROM habitos ORDER BY ativo DESC, ordem, id"):
        m = marcas.get(h["id"], {})
        ok = lambda d: m.get(d.isoformat(), 0) >= h["meta"]  # noqa: E731
        d = t if ok(t) else t - timedelta(days=1)
        seq = 0
        while seq < 60 and ok(d):
            seq += 1
            d -= timedelta(days=1)
        semana_v = [m.get((t - timedelta(days=6 - i)).isoformat(), 0) for i in range(7)]
        out.append({**h, "hoje": m.get(t.isoformat(), 0), "dias": m, "semana": semana_v, "sequencia": seq,
                    "semana_ok": sum(1 for v in semana_v if v >= h["meta"])})
    return out


def agenda(conn, cfg, t):
    ag = cfg.get("agenda") or {}
    padrao = ag.get("avisos") or [3, 1]
    notas = db.rows(conn, "SELECT * FROM notas ORDER BY fixada DESC, COALESCE(atualizado, criado) DESC LIMIT 800")
    for n in notas:
        n["avisos"] = _avisos(n, padrao)
    limite = (t - timedelta(days=30)).isoformat()
    tarefas = db.rows(conn, "SELECT * FROM tarefas WHERE feito_em IS NULL OR feito_em >= ? "
                            "ORDER BY feito_em IS NOT NULL, prazo IS NULL, prazo, importante DESC, id", (limite,))
    facul = db.rows(conn, "SELECT * FROM facul WHERE data >= ? OR feito = 0 ORDER BY data",
                    ((t - timedelta(days=200)).isoformat(),))
    return {"pastas": ag.get("pastas") or ["Pessoal"], "cursos": ag.get("cursos") or [], "avisos": padrao,
            "notas": notas, "tarefas": tarefas, "facul": facul, "proximos": proximos(notas, tarefas, facul, t)}


def proximos(notas, tarefas, facul, t, dias=60):
    """Tudo que tem data: atrasado, hoje e os próximos `dias`."""
    out = []
    fim = t + timedelta(days=dias)

    def add(kind, item_id, titulo, quando, sub, atrasa):
        d = date.fromisoformat(quando[:10])
        n = (d - t).days
        if d > fim or (n < 0 and not atrasa(n)):
            return
        out.append({"kind": kind, "id": item_id, "titulo": titulo, "date": quando[:10],
                    "hora": quando[11:16] if len(quando) > 10 else None, "sub": sub, "dias": n})
    for n in notas:
        if n["quando"] and not n["feito"]:
            add("nota", n["id"], n["titulo"], n["quando"], n["pasta"], lambda k: k >= -2)
    for x in tarefas:
        if x["prazo"] and not x["feito_em"]:
            add("tarefa", x["id"], x["titulo"], x["prazo"], x["pasta"], lambda k: True)
    for f in facul:
        if not f["feito"]:
            nome = f"{TIPOS_FACUL.get(f['tipo'], 'Prova')} de {f['disciplina']}"
            add("facul", f["id"], nome + (f" · {f['titulo']}" if f["titulo"] else ""), f["data"], f["curso"], lambda k: k >= -7)
    out.sort(key=lambda i: (i["date"], i["hora"] or "99"))
    return out


def resumo(conn, t=None):
    t = t or C.today()
    cfg = db.get_cfg(conn)
    return {"hoje": t.isoformat(), "saude": saude(conn, cfg, t), "agenda": agenda(conn, cfg, t),
            "tipos_facul": TIPOS_FACUL}


# ---------------------------------------------------------------- notificações
def _dias_txt(n):
    return "hoje" if n == 0 else "amanhã" if n == 1 else f"em {n} dias"


def avisos(conn, now, rules, due):
    """(chave, título, texto, url, também) dos módulos Saúde e Agenda que deveriam sair agora."""
    t = now.date()
    cfg = db.get_cfg(conn)
    out = []
    r = rules.get("agenda") or {}
    if r.get("on", True):
        ag = agenda(conn, cfg, t)
        padrao = ag["avisos"]
        no_horario = due(now, r.get("time", "08:00"))
        for n in ag["notas"]:
            if not n["quando"] or n["feito"]:
                continue
            d = date.fromisoformat(n["quando"][:10])
            k = (d - t).days
            hora = n["quando"][11:16] if len(n["quando"]) > 10 else ""
            if no_horario and (k == 0 or k in (n["avisos"] if n["avisos"] is not None else padrao)):
                titulo = f"{'Hoje' if k == 0 else 'Amanhã' if k == 1 else f'Em {k} dias'}: {n['titulo']}"
                corpo = " · ".join(x for x in (n["pasta"], f"{d.strftime('%d/%m')}{' às ' + hora if hora else ''}",
                                                (n["texto"] or "").strip()[:80]) if x)
                out.append((f"nota:{n['id']}:{k}:{n['quando']}", titulo, corpo, "/#lembretes", []))
            if hora:  # perto da hora marcada
                at = datetime.fromisoformat(n["quando"]).replace(tzinfo=now.tzinfo)
                if at - timedelta(minutes=90) <= now <= at + timedelta(minutes=10):
                    out.append((f"notah:{n['id']}:{n['quando']}", f"Às {hora}: {n['titulo']}",
                                " · ".join(x for x in (n["pasta"], (n["texto"] or "").strip()[:100]) if x), "/#lembretes", []))
        if no_horario:
            tar = [x for x in ag["tarefas"] if not x["feito_em"] and x["prazo"] and x["prazo"] <= (t + timedelta(days=1)).isoformat()]
            if tar:
                atras = [x for x in tar if x["prazo"] < t.isoformat()]
                hoje = [x for x in tar if x["prazo"] == t.isoformat()]
                if len(tar) == 1:
                    x = tar[0]
                    titulo = f"Tarefa {'atrasada' if x in atras else 'para hoje' if x in hoje else 'para amanhã'}: {x['titulo']}"
                    corpo = x["pasta"] + (f" · {x['nota'][:80]}" if x["nota"] else "")
                else:
                    titulo = f"{len(tar)} tarefas para resolver"
                    partes = [f"{len(atras)} atrasada(s)" if atras else "", f"{len(hoje)} para hoje" if hoje else "",
                              f"{len(tar) - len(atras) - len(hoje)} para amanhã" if len(tar) > len(atras) + len(hoje) else ""]
                    corpo = " · ".join(p for p in partes if p) + ": " + ", ".join(x["titulo"] for x in tar[:3])
                out.append((f"tarefas:{t}", titulo, corpo, "/#tarefas", []))
            for f in ag["facul"]:
                if f["feito"]:
                    continue
                k = (date.fromisoformat(f["data"][:10]) - t).days
                if k in (7, 3, 1, 0):
                    nome = f"{TIPOS_FACUL.get(f['tipo'], 'Prova')} de {f['disciplina']}"
                    hora = f" às {f['data'][11:16]}" if len(f["data"]) > 10 else ""
                    out.append((f"facul:{f['id']}:{k}:{f['data']}", f"{nome} {_dias_txt(k)}",
                                f"{f['curso']} · {date.fromisoformat(f['data'][:10]).strftime('%d/%m')}{hora}"
                                + (f" · {f['titulo']}" if f["titulo"] else ""), "/#facul", []))
    r = rules.get("treino") or {}
    if r.get("on", True) and due(now, r.get("time", "18:00")):
        s = saude(conn, cfg, t)
        w = s["semana"]
        if not w["hoje"]:
            if w["faltam"] and w["faltam"] >= w["restam_dias"]:
                out.append((f"treino:{t}", "Hoje é dia de treinar",
                            f"Faltam {w['faltam']} treino(s) para a meta de {s['meta_semana']} e restam {w['restam_dias']} dia(s) na semana.",
                            "/#treino", []))
            elif s["sem_treinar"] is not None and s["sem_treinar"] >= 3 and w["faltam"]:
                out.append((f"treino:{t}", f"{s['sem_treinar']} dias sem treinar",
                            f"Esta semana: {w['feitos']} de {s['meta_semana']}. Faz o check-in quando voltar.", "/#treino", []))
    r = rules.get("habitos") or {}
    if r.get("on", True) and due(now, r.get("time", "21:00")):
        falta = [h for h in habitos(conn, t) if h["ativo"] and h["hoje"] < h["meta"]]
        if falta:
            def fmt(v):
                return f"{v:g}".replace(".", ",")
            corpo = " · ".join(f"{h['nome']} {fmt(h['hoje'])}/{fmt(h['meta'])}{' ' + h['unidade'] if h['unidade'] else ''}" for h in falta[:4])
            out.append((f"habitos:{t}", "Hábitos de hoje", corpo, "/#habitos", []))
    return out


def semear_habitos(conn):
    from . import seed
    if not conn.execute("SELECT 1 FROM habitos LIMIT 1").fetchone():
        for i, (nome, un, meta, passo) in enumerate(seed.HABITOS_PADRAO):
            conn.execute("INSERT INTO habitos(nome, unidade, meta, passo, ordem) VALUES(?,?,?,?,?)", (nome, un, meta, passo, i))


def semear_refeicoes(conn):
    """Acrescenta os hábitos de refeição do cronograma de alimentação, sem duplicar os que já existem."""
    from . import seed
    base = conn.execute("SELECT COALESCE(MAX(ordem), -1) FROM habitos").fetchone()[0] + 1
    for i, (nome, un, meta, passo) in enumerate(seed.HABITOS_REFEICOES):
        if not conn.execute("SELECT 1 FROM habitos WHERE nome=?", (nome,)).fetchone():
            conn.execute("INSERT INTO habitos(nome, unidade, meta, passo, ordem) VALUES(?,?,?,?,?)",
                         (nome, un, meta, passo, base + i))
